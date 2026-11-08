"""Read models for the schedule screens (live rule checks included) and the student's item edits.

Everything here reads through the rule engine and the catalog tools; nothing decides eligibility by itself.
Functions flush but never commit: the API route owns the transaction.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.compliance_agent import aggregate
from app.agents.core_plan import clashes_with, preference_key, usable
from app.agents.schedule_agent import days_times
from app.models import (
    Course,
    CourseOffering,
    ElectiveSuggestion,
    RiskFlag,
    Schedule,
    ScheduleItem,
    Student,
)
from app.rules.engine import CandidateSchedule, CheckResult, ScheduleItemIn
from app.rules.repository import validate_schedule
from app.services import schedule_lifecycle as lifecycle
from app.services.terms import label_of, term_labels
from app.tools.catalog import SectionOut, get_term_sections
from app.tools.student import get_program_requirements, get_student_history


class InvalidEdit(ValueError):
    """The requested edit cannot be built (unknown course, not offered, no seat)."""


class EditBlocked(ValueError):
    """An add or swap would make the draft break a blocking rule it did not break before. The route rolls the
    edit back, so the draft is unchanged; `reasons` are the new rule failures (rule engine text)."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__(reasons[0] if len(reasons) == 1 else "; ".join(reasons))
        self.reasons = reasons


def blocking_key(reason: str) -> str:
    """'TIME_CONFLICT 95-767: ...' -> 'TIME_CONFLICT 95-767': the rule and course, without the changing numbers."""
    return reason.split(":", 1)[0]


class ItemView(BaseModel):
    course_code: str
    title: str
    units: Decimal
    item_type: str  # core | elective
    is_locked: bool
    added_by: str  # ai | student
    section_id: str | None
    section_code: str | None
    session_code: str | None
    days_times: str | None
    modality: str | None
    location: str | None
    seats_left: int | None
    is_conditional: bool
    needs_approval: bool
    match_score: float | None
    reason: str | None
    in_ai_draft: bool | None = None  # False = added by the student
    ai_original_course: str | None = None  # swapped in for this AI course
    issues: list[str] = []  # rule results that failed or carry a flag for this course


class CheckView(BaseModel):
    code: str
    passed: bool
    detail: str
    advisor_flag: bool = False  # failed without blocking: a flag the advisor decides on


class FlagView(BaseModel):
    flag_id: int
    flag_code: str
    severity: str
    message: str
    rule_text: str | None
    suggested_action: str | None
    status: str


class ScheduleView(BaseModel):
    schedule_id: uuid.UUID
    student_id: str
    term_code: str
    term_label: str  # "Spring 2027": show this, never the code
    version: int
    status: str
    previous_schedule_id: uuid.UUID | None
    edited_by_student: bool
    created_at: datetime
    submitted_at: datetime | None
    decided_at: datetime | None
    discarded_at: datetime | None
    advisor_comment: str | None
    ai_explanation: str | None
    load_rationale: str | None
    total_units: Decimal
    core_units: Decimal
    elective_units: Decimal
    max_units: int
    max_units_extended: int | None = None  # 55..this only if extended_allowed
    extended_units_min_qpa: Decimal | None = None
    extended_allowed: bool = False  # the student's QPA is strictly above extended_units_min_qpa
    items: list[ItemView]
    checks: list[CheckView]
    blocking: list[str]  # reasons the schedule cannot be submitted or approved right now
    valid: bool
    flags: list[FlagView]
    can_edit: bool
    can_submit: bool
    can_reset: bool
    can_discard: bool
    can_withdraw: bool
    can_resubmit: bool


class HistoryItem(BaseModel):
    schedule_id: uuid.UUID
    term_code: str
    term_label: str  # "Spring 2027": show this, never the code
    version: int
    status: str
    previous_schedule_id: uuid.UUID | None
    courses: int
    total_units: Decimal
    edited_by_student: bool
    created_at: datetime
    submitted_at: datetime | None
    decided_at: datetime | None
    discarded_at: datetime | None
    advisor_comment: str | None


class SuggestionView(BaseModel):
    course_code: str
    title: str
    units: Decimal | None
    rank: int
    group_code: str
    matched_interest: str | None
    similarity: float | None
    popularity: float | None
    fit: float | None
    final_score: float
    flags: list[str]
    reason: str | None
    in_schedule: bool


class SuggestionGroups(BaseModel):
    schedule_id: uuid.UUID
    term_code: str
    term_label: str
    basis: str | None  # interests | popularity; None = no suggestions yet
    interest_match: list[SuggestionView]
    popular: list[SuggestionView]


# ---------- the schedule view ----------

def schedule_view(db: Session, schedule: Schedule, today: date, max_units: int | None = None) -> ScheduleView:
    rows = db.execute(
        select(ScheduleItem, Course.title).join(Course, Course.course_code == ScheduleItem.course_code)
        .where(ScheduleItem.schedule_id == schedule.schedule_id)
        .order_by(ScheduleItem.item_type.desc(), ScheduleItem.course_code)).all()
    sections = {s.section_id: s for secs in get_term_sections(
        db, schedule.term_code, [i.course_code for i, _ in rows]).values() for s in secs}

    live = schedule.status in ("draft", "submitted")
    results: list[CheckResult] = []
    if live and rows:
        results = validate_schedule(db, CandidateSchedule(
            student_id=schedule.student_id, term_code=schedule.term_code,
            items=[ScheduleItemIn(course_code=i.course_code, section_id=i.section_id, units=i.units)
                   for i, _ in rows]), today)
    baseline = {b["course_code"] for b in (schedule.ai_baseline or [])}
    swaps = _swap_origins(db, schedule, baseline)

    items = []
    for item, title in rows:
        sec = sections.get(item.section_id) if item.section_id else None
        issues = [f"{r.code}: {r.detail}" for r in results
                  if r.course_code == item.course_code and (not r.passed or r.flag)]
        items.append(ItemView(
            course_code=item.course_code, title=title, units=item.units, item_type=item.item_type,
            is_locked=item.is_locked, added_by=item.added_by, section_id=item.section_id,
            section_code=sec.section_code if sec else None, session_code=sec.session_code if sec else None,
            days_times=days_times(sec) if sec else None, modality=sec.modality if sec else None,
            location=sec.location if sec else None, seats_left=sec.seats_left if sec else None,
            is_conditional=item.is_conditional, needs_approval=item.needs_approval,
            match_score=float(item.match_score) if item.match_score is not None else None, reason=item.reason,
            in_ai_draft=(item.course_code in baseline) if baseline else None,
            ai_original_course=swaps.get(item.course_code), issues=issues))

    checks = [CheckView(code=c.code, passed=c.passed, detail=c.detail,
                        advisor_flag=not c.passed and not any(r.blocking for r in results if r.code == c.code))
              for c in aggregate(results)] if results else []
    blocking = [f"{r.code}{' ' + r.course_code if r.course_code else ''}: {r.detail}"
                for r in results if r.blocking]
    core = sum((i.units for i in items if i.item_type == "core"), Decimal(0))
    elective = sum((i.units for i in items if i.item_type == "elective"), Decimal(0))
    flags = [FlagView(flag_id=f.flag_id, flag_code=f.flag_code, severity=f.severity, message=f.message,
                      rule_text=f.rule_text, suggested_action=f.suggested_action, status=f.status)
             for f in db.scalars(select(RiskFlag).where(RiskFlag.schedule_id == schedule.schedule_id,
                                                        RiskFlag.status == "open").order_by(RiskFlag.flag_id))]
    draft, valid = schedule.status == "draft", not blocking
    program = get_program_requirements(db, db.get(Student, schedule.student_id).track_code)
    qpa = get_student_history(db, schedule.student_id).qpa
    return ScheduleView(
        max_units_extended=program.max_units_extended, extended_units_min_qpa=program.extended_units_min_qpa,
        extended_allowed=qpa is not None and qpa > program.extended_units_min_qpa,
        schedule_id=schedule.schedule_id, student_id=schedule.student_id, term_code=schedule.term_code,
        term_label=label_of(db, schedule.term_code), version=schedule.version, status=schedule.status, previous_schedule_id=schedule.previous_schedule_id,
        edited_by_student=schedule.edited_by_student, created_at=schedule.created_at,
        submitted_at=schedule.submitted_at, decided_at=schedule.decided_at, discarded_at=schedule.discarded_at,
        advisor_comment=schedule.advisor_comment, ai_explanation=schedule.ai_explanation,
        load_rationale=schedule.load_rationale, total_units=core + elective, core_units=core,
        elective_units=elective, max_units=max_units or _max_units(db, schedule.student_id), items=items,
        checks=checks, blocking=blocking, valid=valid, flags=flags, can_edit=draft,
        can_submit=draft and bool(items) and valid,
        can_reset=draft and bool(schedule.ai_baseline) and schedule.edited_by_student, can_discard=draft,
        can_withdraw=schedule.status == "submitted" and schedule.decided_at is None,
        can_resubmit=schedule.status == "rejected")


def _max_units(db: Session, student_id: str) -> int:
    return get_program_requirements(db, db.get(Student, student_id).track_code).max_units_per_term


def _swap_origins(db: Session, schedule: Schedule, baseline: set[str]) -> dict[str, str]:
    """current course -> the AI's course it replaced (following chains of swaps back to the AI draft)."""
    from app.models import ScheduleChange
    swaps = {c.course_code: c.replaced_course_code for c in db.scalars(
        select(ScheduleChange).where(ScheduleChange.schedule_id == schedule.schedule_id,
                                     ScheduleChange.action == "swap").order_by(ScheduleChange.change_id))}
    origin: dict[str, str] = {}
    for new, old in swaps.items():
        seen = set()
        while old not in baseline and old in swaps and old not in seen:
            seen.add(old)
            old = swaps[old]
        if old in baseline:
            origin[new] = old
    return origin


# ---------- history and suggestions ----------

def history(db: Session, student_id: str, term_code: str | None = None) -> list[HistoryItem]:
    query = (select(Schedule, func.count(ScheduleItem.course_code), func.coalesce(func.sum(ScheduleItem.units), 0))
             .outerjoin(ScheduleItem, ScheduleItem.schedule_id == Schedule.schedule_id)
             .where(Schedule.student_id == student_id).group_by(Schedule.schedule_id)
             .order_by(Schedule.term_code.desc(), Schedule.version.desc()))
    if term_code:
        query = query.where(Schedule.term_code == term_code)
    labels = term_labels(db)
    return [HistoryItem(
        schedule_id=s.schedule_id, term_code=s.term_code, term_label=labels.get(s.term_code, s.term_code),
        version=s.version, status=s.status,
        previous_schedule_id=s.previous_schedule_id, courses=n, total_units=Decimal(units),
        edited_by_student=s.edited_by_student, created_at=s.created_at, submitted_at=s.submitted_at,
        decided_at=s.decided_at, discarded_at=s.discarded_at, advisor_comment=s.advisor_comment)
        for s, n, units in db.execute(query).all()]


def current_schedule(db: Session, student_id: str, term_code: str) -> Schedule | None:
    """The open (draft / submitted) schedule of the term, else the newest decided one."""
    return (lifecycle.open_schedule(db, student_id, term_code)
            or db.scalar(select(Schedule).where(Schedule.student_id == student_id, Schedule.term_code == term_code,
                                                Schedule.status.in_(("approved", "rejected")))
                         .order_by(Schedule.version.desc()).limit(1)))


def suggestions(db: Session, schedule: Schedule) -> SuggestionGroups:
    in_schedule = set(db.scalars(select(ScheduleItem.course_code).where(
        ScheduleItem.schedule_id == schedule.schedule_id)))
    rows = db.execute(
        select(ElectiveSuggestion, Course.title, Course.units_default)
        .join(Course, Course.course_code == ElectiveSuggestion.course_code)
        .where(ElectiveSuggestion.student_id == schedule.student_id,
               ElectiveSuggestion.term_code == schedule.term_code)
        .order_by(ElectiveSuggestion.rank)).all()
    views = [SuggestionView(
        course_code=e.course_code, title=title, units=units, rank=e.rank, group_code=e.group_code,
        matched_interest=e.matched_interest, similarity=_f(e.similarity), popularity=_f(e.popularity),
        fit=_f(e.fit), final_score=float(e.final_score), flags=list(e.flags or []), reason=e.reason,
        in_schedule=e.course_code in in_schedule) for e, title, units in rows]
    return SuggestionGroups(
        schedule_id=schedule.schedule_id, term_code=schedule.term_code, term_label=label_of(db, schedule.term_code),
        basis=rows[0][0].basis if rows else None,
        interest_match=[v for v in views if v.group_code == "interest_match"],
        popular=[v for v in views if v.group_code == "popular"])


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


# ---------- the student's edits: add / remove / swap ----------

def edit_items(db: Session, schedule: Schedule, action: str, course_code: str | None, section_id: str | None,
               replace_course_code: str | None) -> None:
    """Apply one edit through the lifecycle service; the caller re-validates with schedule_view()."""
    if action == "remove":
        if not course_code:
            raise InvalidEdit("course_code is required")
        lifecycle.remove_item(db, schedule, course_code)
        return
    if action not in ("add", "swap"):
        raise InvalidEdit("action must be add, remove or swap")
    if not course_code or (action == "swap" and not replace_course_code):
        raise InvalidEdit("course_code (and replace_course_code for a swap) are required")
    course = db.get(Course, course_code)
    if course is None or not db.scalar(select(CourseOffering.course_code).where(
            CourseOffering.course_code == course_code, CourseOffering.term_code == schedule.term_code,
            CourseOffering.status == "offered")):
        raise InvalidEdit(f"{course_code} is not offered in {label_of(db, schedule.term_code)}")
    units = course.units_default or course.units_min
    if units is None:
        raise InvalidEdit(f"{course_code} has no default units")
    history_ = get_student_history(db, schedule.student_id)
    program = get_program_requirements(db, history_.track_code)
    item_type = "core" if course_code in {c.course_code for c in program.core} else "elective"
    section = _choose_section(db, schedule, course_code, section_id, replace_course_code, history_)
    if action == "add":
        lifecycle.add_item(db, schedule, course_code, section.section_id, units, item_type)
    else:
        lifecycle.swap_item(db, schedule, replace_course_code, course_code, section.section_id, units, item_type)


def _choose_section(db: Session, schedule: Schedule, course_code: str, section_id: str | None,
                    leaving: str | None, history_) -> SectionOut:
    found = [s for s in get_term_sections(db, schedule.term_code, [course_code]).get(course_code, []) if usable(s)]
    if section_id is not None:
        match = next((s for s in found if s.section_id == section_id), None)
        if match is None:
            raise InvalidEdit(f"{section_id} is not an offered section of {course_code} with seats")
        return match
    if not found:
        raise InvalidEdit(f"{course_code} has no section with seats in {label_of(db, schedule.term_code)}")
    others = [i.section_id for i in schedule.items if i.section_id and i.course_code != leaving]
    taken = [s for secs in get_term_sections(db, schedule.term_code, [i.course_code for i in schedule.items
                                                                       if i.course_code != leaving]).values()
             for s in secs if s.section_id in others]
    found.sort(key=lambda s: preference_key(s, history_, 3))
    return next((s for s in found if not clashes_with(s, taken)), found[0])  # a clash is flagged by the rules
