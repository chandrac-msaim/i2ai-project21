"""Loads the facts the rule engine needs (RuleContext) from the database. Read-only."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    CoreRequirement,
    Course,
    CourseOffering,
    CoursePrerequisite,
    CourseType,
    CourseWaiver,
    ElectiveRequirement,
    Enrollment,
    GradeScale,
    RuleSetting,
    Section,
    SectionMeeting,
    Student,
    Term,
    TermSession,
    Track,
)
from app.rules.engine import (
    CandidateSchedule,
    CheckResult,
    CourseInfo,
    Meeting,
    RuleContext,
    ScheduleItemIn,
    SectionInfo,
    validate,
)
from app.models.views import StudentProgress
from app.services.terms import registration_status


def load_context(db: Session, schedule: CandidateSchedule, today: date | None = None) -> RuleContext:
    """All facts for validating `schedule` on `today` (default: settings.today())."""
    today = today or get_settings().today()
    student = db.get(Student, schedule.student_id)
    if student is None:
        raise ValueError(f"Unknown student {schedule.student_id}")
    track = db.get(Track, student.track_code)
    electives = db.get(ElectiveRequirement, student.track_code)
    term = db.get(Term, schedule.term_code)
    reg = {t.term_code: t.status for t in registration_status(db, today)}

    completed, in_progress, prior_ug = _history(db, student.student_id, term.seq if term else None)
    codes = {i.course_code for i in schedule.items}
    section_ids = {i.section_id for i in schedule.items if i.section_id}

    return RuleContext(
        student_id=student.student_id,
        track_code=student.track_code,
        term_code=schedule.term_code,
        term_label=f"{term.season} {term.year}" if term else "",
        term_registration=reg.get(schedule.term_code),
        max_units_per_term=Decimal(track.max_units_per_term),
        max_undergrad_units=Decimal(electives.max_undergrad_units if electives else 12),
        max_units_extended=Decimal(track.max_units_extended),
        extended_units_min_qpa=track.extended_units_min_qpa,
        qpa=db.scalar(select(StudentProgress.qpa).where(StudentProgress.student_id == student.student_id)),
        completed=completed,
        in_progress=in_progress,
        prior_undergrad_units=prior_ug,
        courses=_courses(db, codes, student.track_code),
        offered_courses=set(db.scalars(
            select(CourseOffering.course_code).where(
                CourseOffering.term_code == schedule.term_code,
                CourseOffering.status == "offered",
                CourseOffering.course_code.in_(codes)))),
        sections=_sections(db, section_ids),
        severities={r.rule_code: r.severity for r in db.scalars(select(RuleSetting).where(RuleSetting.active))
                    if r.severity},
    )


def validate_schedule(db: Session, schedule: CandidateSchedule, today: date | None = None) -> list[CheckResult]:
    return validate(schedule, load_context(db, schedule, today))


def _history(db: Session, student_id: str, target_seq: int | None) -> tuple[set[str], set[str], Decimal]:
    """(passed or waived courses, courses in progress before the target term, undergrad units taken)."""
    rows = db.execute(
        select(Enrollment.course_code, Enrollment.status, Enrollment.units, Term.seq,
               GradeScale.is_passing, Course.course_number)
        .join(Term, Term.term_code == Enrollment.term_code)
        .join(Course, Course.course_code == Enrollment.course_code)
        .outerjoin(GradeScale, GradeScale.grade == Enrollment.grade)
        .where(Enrollment.student_id == student_id)
    ).all()
    completed = {r.course_code for r in rows if r.status == "completed" and r.is_passing}
    in_progress = {r.course_code for r in rows if r.status == "in_progress"
                   and (target_seq is None or r.seq < target_seq)}
    prior_ug = sum((r.units for r in rows if r.course_number < 600
                    and (r.status == "in_progress" or (r.status == "completed" and r.is_passing))),
                   Decimal(0))
    completed |= set(db.scalars(select(CourseWaiver.course_code).where(
        CourseWaiver.student_id == student_id, CourseWaiver.status == "approved")))
    return completed, in_progress - completed, prior_ug


def _courses(db: Session, codes: set[str], track_code: str) -> dict[str, CourseInfo]:
    options: dict[str, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    for p in db.scalars(select(CoursePrerequisite).where(CoursePrerequisite.course_code.in_(codes))):
        options[p.course_code][p.option_no].add(p.required_course_code)
    overrides = {c.course_code: c for c in db.scalars(select(CoreRequirement).where(
        CoreRequirement.track_code == track_code, CoreRequirement.course_code.in_(codes)))}
    approval = dict(db.execute(select(CourseType.type_code, CourseType.needs_approval)).tuples().all())

    result = {}
    for c in db.scalars(select(Course).where(Course.course_code.in_(codes))):
        core = overrides.get(c.course_code)
        result[c.course_code] = CourseInfo(
            course_code=c.course_code,
            title=c.title,
            course_number=c.course_number,
            units_default=c.units_default,
            type_code=c.type_code,
            needs_approval=approval.get(c.type_code, False),
            prereq_options=tuple(frozenset(o) for _, o in sorted(options[c.course_code].items())),
            catalog_prereqs_apply=core.catalog_prereqs_apply if core else True,
            prereq_override_note=core.prereq_override_note if core else None,
        )
    return result


def _sections(db: Session, section_ids: set[str]) -> dict[str, SectionInfo]:
    meetings: dict[str, list[Meeting]] = defaultdict(list)
    for m in db.scalars(select(SectionMeeting).where(SectionMeeting.section_id.in_(section_ids))):
        meetings[m.section_id].append(Meeting(m.day_of_week, m.start_time, m.end_time))
    rows = db.execute(
        select(Section, TermSession.start_date, TermSession.end_date)
        .join(TermSession, (TermSession.term_code == Section.term_code)
              & (TermSession.session_code == Section.session_code))
        .where(Section.section_id.in_(section_ids))
    ).all()
    return {
        s.section_id: SectionInfo(
            section_id=s.section_id, course_code=s.course_code, term_code=s.term_code,
            session_code=s.session_code, session_start=start, session_end=end, status=s.status,
            seats_left=None if s.capacity is None else s.seats_left,  # no capacity = no limit
            is_time_tba=s.is_time_tba, meetings=tuple(meetings[s.section_id]),
        )
        for s, start, end in rows
    }


def course_check_results(db: Session, student_id: str, term_code: str, course_codes: list[str],
                         today: date | None = None) -> dict[str, list[CheckResult]]:
    """Every rule result for each course taken alone in `term_code` (its first section with seats).

    One context load for all courses; each course is then validated as a one-course schedule.
    """
    if not course_codes:
        return {}
    sections = _open_section_per_course(db, term_code, set(course_codes))
    items = [ScheduleItemIn(course_code=c, section_id=sections.get(c)) for c in course_codes]
    ctx = load_context(db, CandidateSchedule(student_id=student_id, term_code=term_code, items=items), today)
    return {item.course_code: validate(CandidateSchedule(student_id=student_id, term_code=term_code, items=[item]),
                                       ctx)
            for item in items}


def course_blockers(db: Session, student_id: str, term_code: str, course_codes: list[str],
                    today: date | None = None) -> dict[str, list[CheckResult]]:
    """Blocking rule failures for each course taken alone in `term_code`. An empty list means the course is
    eligible on its own (it may still carry flags such as conditional or petition)."""
    return {code: [r for r in results if r.blocking]
            for code, results in course_check_results(db, student_id, term_code, course_codes, today).items()}


def _open_section_per_course(db: Session, term_code: str, codes: set[str]) -> dict[str, str]:
    """course_code -> an offered section with seats (most seats first; no capacity limit counts as most)."""
    rows = db.execute(
        select(Section.course_code, Section.section_id)
        .where(Section.term_code == term_code, Section.course_code.in_(codes), Section.status == "offered",
               (Section.capacity.is_(None)) | (Section.seats_left > 0))
        .order_by(Section.course_code, Section.capacity.is_(None).desc(), Section.seats_left.desc(),
                  Section.section_id)
    ).all()
    result: dict[str, str] = {}
    for code, section_id in rows:
        result.setdefault(code, section_id)
    return result
