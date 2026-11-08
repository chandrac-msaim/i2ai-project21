"""Schedule lifecycle (D15): draft -> submitted -> approved | rejected, with soft discard.

    build(...)                   new draft; an open draft is replaced ('superseded') only with replace=True
    discard(...)                 a draft -> 'discarded' (row kept, discarded_at set)
    withdraw(...)                a submitted schedule without a decision -> back to 'draft'
    resubmit_from_rejected(...)  new draft version copying the rejected one's items (previous_schedule_id)
    submit(...)                  a non-empty draft -> 'submitted' (the student sends it to the advisor)
    add_item / remove_item / swap_item   the student edits a draft (locked AI core stays); marks edited_by_student
    reset(...)                   a draft goes back to ai_baseline, the draft exactly as the AI built it
    approve(...) / reject(...)   a submitted schedule -> 'approved' / 'rejected'; ONLY an advisor (checked here)

Approved schedules are locked: nothing here changes them. Every action writes schedule_changes and audit_log
(CLAUDE.md rule 3). No FastAPI imports: the API and the agents' tools both call this module. The functions
flush but never commit; the caller owns the transaction.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Advisor, AuditLog, Schedule, ScheduleChange, ScheduleItem
from app.services.terms import label_of

Actor = Literal["student", "ai", "advisor"]


class ScheduleItemDraft(BaseModel):
    course_code: str
    section_id: str | None
    units: Decimal
    item_type: str  # core | elective
    is_locked: bool
    is_conditional: bool = False
    needs_approval: bool = False
    match_score: float | None = None
    reason: str | None = None


class SavedSchedule(BaseModel):
    schedule_id: uuid.UUID
    version: int
    superseded_id: uuid.UUID | None


class ScheduleError(ValueError):
    """Base class: the requested lifecycle step is not allowed."""


class DraftExists(ScheduleError):
    """An open draft exists for this student and term and replace=False (the student has not confirmed)."""

    def __init__(self, schedule_id: uuid.UUID, term_code: str, term_label: str | None = None) -> None:
        super().__init__(f"A draft schedule for {term_label or term_code} already exists; confirm to replace it")
        self.schedule_id = schedule_id


class ScheduleConflict(ScheduleError):
    """The student already sent a schedule for this term to the advisor (withdraw it first)."""


class ScheduleLocked(ScheduleError):
    """Approved schedules cannot be changed."""


class InvalidTransition(ScheduleError):
    """The schedule's status does not allow this step."""


class ItemError(ScheduleError):
    """The edit is not allowed (locked AI course, course already in the schedule)."""


class ItemNotFound(LookupError):
    """The course is not in the schedule."""


class NotAdvisor(PermissionError):
    """Only an advisor can approve or reject a schedule (rules decide, only an advisor approves)."""


# ---------- steps ----------

def open_schedule(db: Session, student_id: str, term_code: str) -> Schedule | None:
    """The student's one open (draft or submitted) schedule for the term, if any."""
    return db.scalar(select(Schedule).where(
        Schedule.student_id == student_id, Schedule.term_code == term_code,
        Schedule.status.in_(("draft", "submitted"))))


def build(db: Session, student_id: str, term_code: str, items: list[ScheduleItemDraft], *,
          ai_explanation: str | None = None, load_rationale: str | None = None, run_id: uuid.UUID | None = None,
          replace: bool = False, actor: Actor = "ai") -> SavedSchedule:
    """Create a new draft (version + 1).

    An approved schedule for the term is locked: ScheduleLocked. With an open draft and replace=False: DraftExists. With replace=True the old draft becomes 'superseded' and the
    new one points to it with previous_schedule_id (schedule_changes action 'rebuild', else 'build'). A submitted
    schedule is never replaced: ScheduleConflict.
    """
    if db.scalar(select(Schedule.schedule_id).where(
            Schedule.student_id == student_id, Schedule.term_code == term_code, Schedule.status == "approved")):
        raise ScheduleLocked(f"The schedule for {label_of(db, term_code)} is approved and locked")
    existing = open_schedule(db, student_id, term_code)
    if existing is not None and existing.status == "submitted":
        raise ScheduleConflict(f"A schedule for {label_of(db, term_code)} is already with the advisor")
    if existing is not None and not replace:
        raise DraftExists(existing.schedule_id, term_code, label_of(db, term_code))
    superseded_id = None
    if existing is not None:
        existing.status = "superseded"
        superseded_id = existing.schedule_id
        db.flush()  # one open schedule per student and term (partial unique index)
    schedule = _new_version(db, student_id, term_code, items, previous=superseded_id,
                            ai_explanation=ai_explanation, load_rationale=load_rationale, run_id=run_id,
                            baseline=snapshot(items))
    _record(db, schedule, "rebuild" if superseded_id else "build", actor,
            {"version": schedule.version, "superseded": _s(superseded_id)})
    return SavedSchedule(schedule_id=schedule.schedule_id, version=schedule.version, superseded_id=superseded_id)


def discard(db: Session, schedule: Schedule, by_student: bool = True) -> Schedule:
    """A draft becomes 'discarded' (never deleted). Only drafts can be discarded."""
    _require_not_approved(schedule)
    if schedule.status != "draft":
        raise InvalidTransition(f"Only a draft can be discarded (this one is {schedule.status})")
    schedule.status = "discarded"
    schedule.discarded_at = datetime.now(timezone.utc)
    db.flush()
    _record(db, schedule, "discard", _actor(by_student), {"version": schedule.version})
    return schedule


def withdraw(db: Session, schedule: Schedule, by_student: bool = True) -> Schedule:
    """A submitted schedule without a decision goes back to 'draft' (submitted_at cleared)."""
    _require_not_approved(schedule)
    if schedule.status != "submitted" or schedule.decided_at is not None:
        raise InvalidTransition(f"Only a submitted schedule without a decision can be withdrawn "
                                f"(this one is {schedule.status})")
    schedule.status = "draft"
    schedule.submitted_at = None
    db.flush()
    _record(db, schedule, "withdraw", _actor(by_student), {"version": schedule.version})
    return schedule


def resubmit_from_rejected(db: Session, schedule: Schedule, by_student: bool = True) -> Schedule:
    """New draft version copying the items of a rejected schedule; the rejected one stays as history."""
    if schedule.status != "rejected":
        _require_not_approved(schedule)
        raise InvalidTransition(f"Only a rejected schedule can be resubmitted (this one is {schedule.status})")
    existing = open_schedule(db, schedule.student_id, schedule.term_code)
    if existing is not None:
        raise DraftExists(existing.schedule_id, schedule.term_code)
    items = [ScheduleItemDraft(course_code=i.course_code, section_id=i.section_id, units=i.units,
                               item_type=i.item_type, is_locked=i.is_locked, is_conditional=i.is_conditional,
                               needs_approval=i.needs_approval,
                               match_score=float(i.match_score) if i.match_score is not None else None,
                               reason=i.reason) for i in schedule.items]
    new = _new_version(db, schedule.student_id, schedule.term_code, items, previous=schedule.schedule_id,
                       ai_explanation=schedule.ai_explanation, load_rationale=schedule.load_rationale, run_id=None,
                       added_by="student", baseline=schedule.ai_baseline)
    new.edited_by_student = False
    _record(db, new, "resubmit", _actor(by_student), {"version": new.version, "from": str(schedule.schedule_id)})
    return new


def submit(db: Session, schedule: Schedule, by_student: bool = True) -> Schedule:
    """A draft with at least one course becomes 'submitted'. The caller has already run the compliance gate."""
    _require_not_approved(schedule)
    if schedule.status != "draft":
        raise InvalidTransition(f"Only a draft can be submitted (this one is {schedule.status})")
    if not schedule.items:
        raise InvalidTransition("An empty schedule cannot be submitted")
    schedule.status = "submitted"
    schedule.submitted_at = datetime.now(timezone.utc)
    db.flush()
    _record(db, schedule, "submit", _actor(by_student), {"version": schedule.version})
    return schedule


def approve(db: Session, schedule: Schedule, advisor_id: str | None, comment: str | None = None) -> Schedule:
    return _decide(db, schedule, "approved", advisor_id, comment)


def reject(db: Session, schedule: Schedule, advisor_id: str | None, comment: str | None = None) -> Schedule:
    return _decide(db, schedule, "rejected", advisor_id, comment)


def _decide(db: Session, schedule: Schedule, status: str, advisor_id: str | None, comment: str | None) -> Schedule:
    """Only an existing advisor can decide, and only a submitted schedule. Writes audit_log (no schedule_changes
    action exists for a decision: the schedule row holds decided_by / decided_at / advisor_comment)."""
    if not advisor_id or db.get(Advisor, advisor_id) is None:
        raise NotAdvisor("Only an advisor can approve or reject a schedule")
    _require_not_approved(schedule)
    if schedule.status != "submitted":
        raise InvalidTransition(f"Only a submitted schedule can be decided (this one is {schedule.status})")
    schedule.status = status
    schedule.decided_by = advisor_id
    schedule.decided_at = datetime.now(timezone.utc)
    schedule.advisor_comment = comment
    db.flush()
    _record(db, schedule, status.removesuffix("d"), "advisor", {"version": schedule.version,
                                                                "advisor_id": advisor_id}, change=False)
    return schedule


def reset(db: Session, schedule: Schedule, by_student: bool = True) -> Schedule:
    """Give a draft back exactly as the AI built it (ai_baseline); drops the student's edits."""
    _require_editable(schedule)
    if not schedule.ai_baseline:
        raise InvalidTransition("This schedule has no saved AI draft to go back to")
    db.execute(delete(ScheduleItem).where(ScheduleItem.schedule_id == schedule.schedule_id))
    db.expire(schedule, ["items"])
    db.add_all(ScheduleItem(
        schedule_id=schedule.schedule_id, course_code=i["course_code"], section_id=i["section_id"],
        units=Decimal(str(i["units"])), item_type=i["item_type"], is_locked=i["is_locked"], added_by="ai",
        is_conditional=i["is_conditional"], needs_approval=i["needs_approval"],
        match_score=_score(i.get("match_score")), reason=i.get("reason")) for i in schedule.ai_baseline)
    schedule.edited_by_student = False
    db.flush()
    db.expire(schedule, ["items"])
    _record(db, schedule, "reset", _actor(by_student), {"version": schedule.version})
    return schedule


def add_item(db: Session, schedule: Schedule, course_code: str, section_id: str | None, units: Decimal,
             item_type: str = "elective") -> ScheduleItem:
    """The student adds a course (not locked, so it can be removed again). The rule engine judges the result."""
    _require_editable(schedule)
    if _find(schedule, course_code) is not None:
        raise ItemError(f"{course_code} is already in this schedule")
    item = ScheduleItem(schedule_id=schedule.schedule_id, course_code=course_code, section_id=section_id,
                        units=units, item_type=item_type, is_locked=False, added_by="student")
    db.add(item)
    _edited(db, schedule)
    _record(db, schedule, "add", "student", {"course_code": course_code}, course_code=course_code)
    return item


def remove_item(db: Session, schedule: Schedule, course_code: str) -> None:
    _require_editable(schedule)
    item = _find(schedule, course_code)
    if item is None:
        raise ItemNotFound(f"{course_code} is not in this schedule")
    if item.is_locked:
        raise ItemError(f"{course_code} is a locked core course and cannot be removed")
    db.execute(delete(ScheduleItem).where(ScheduleItem.schedule_id == schedule.schedule_id,
                                          ScheduleItem.course_code == course_code))
    _edited(db, schedule)
    _record(db, schedule, "remove", "student", {"course_code": course_code}, course_code=course_code)


def swap_item(db: Session, schedule: Schedule, old_course: str, new_course: str, section_id: str | None,
              units: Decimal, item_type: str = "elective") -> ScheduleItem:
    """Replace one unlocked course by another. The change records both, so the advisor sees the AI's original."""
    _require_editable(schedule)
    old = _find(schedule, old_course)
    if old is None:
        raise ItemNotFound(f"{old_course} is not in this schedule")
    if old.is_locked:
        raise ItemError(f"{old_course} is a locked core course and cannot be swapped")
    if old_course == new_course or _find(schedule, new_course) is not None:
        raise ItemError(f"{new_course} is already in this schedule")
    db.execute(delete(ScheduleItem).where(ScheduleItem.schedule_id == schedule.schedule_id,
                                          ScheduleItem.course_code == old_course))
    item = ScheduleItem(schedule_id=schedule.schedule_id, course_code=new_course, section_id=section_id,
                        units=units, item_type=item_type, is_locked=False, added_by="student")
    db.add(item)
    _edited(db, schedule)
    _record(db, schedule, "swap", "student", {"course_code": new_course, "replaced": old_course},
            course_code=new_course, replaced_course_code=old_course)
    return item


def snapshot(items: list[ScheduleItemDraft]) -> list[dict]:
    """The draft as the AI built it, as stored in schedules.ai_baseline."""
    return [{"course_code": i.course_code, "section_id": i.section_id, "item_type": i.item_type,
             "units": float(i.units), "is_locked": i.is_locked, "is_conditional": i.is_conditional,
             "needs_approval": i.needs_approval, "match_score": i.match_score, "reason": i.reason} for i in items]


# ---------- internals ----------

def _require_editable(schedule: Schedule) -> None:
    _require_not_approved(schedule)
    if schedule.status != "draft":
        raise InvalidTransition(f"Only a draft can be edited (this one is {schedule.status})")


def _find(schedule: Schedule, course_code: str) -> ScheduleItem | None:
    return next((i for i in schedule.items if i.course_code == course_code), None)


def _edited(db: Session, schedule: Schedule) -> None:
    schedule.edited_by_student = True
    db.flush()
    db.expire(schedule, ["items"])


def _new_version(db: Session, student_id: str, term_code: str, items: list[ScheduleItemDraft], *,
                 previous: uuid.UUID | None, ai_explanation: str | None, load_rationale: str | None,
                 run_id: uuid.UUID | None, added_by: str = "ai",
                 baseline: list[dict] | None = None) -> Schedule:
    version = (db.scalar(select(func.max(Schedule.version)).where(
        Schedule.student_id == student_id, Schedule.term_code == term_code)) or 0) + 1
    schedule = Schedule(
        student_id=student_id, term_code=term_code, version=version, status="draft", previous_schedule_id=previous,
        ai_explanation=ai_explanation, load_rationale=load_rationale, built_by_run_id=run_id, ai_baseline=baseline,
        items=[ScheduleItem(course_code=i.course_code, section_id=i.section_id, units=i.units,
                            item_type=i.item_type, is_locked=i.is_locked, added_by=added_by,
                            is_conditional=i.is_conditional, needs_approval=i.needs_approval,
                            match_score=_score(i.match_score), reason=i.reason) for i in items])
    db.add(schedule)
    db.flush()
    return schedule


def _record(db: Session, schedule: Schedule, action: str, actor: Actor, details: dict, change: bool = True,
            course_code: str | None = None, replaced_course_code: str | None = None) -> None:
    if change:
        db.add(ScheduleChange(schedule_id=schedule.schedule_id, action=action, actor=actor,
                              course_code=course_code, replaced_course_code=replaced_course_code))
    db.add(AuditLog(
        actor_type="agent" if actor == "ai" else "user",
        actor_id=("schedule" if actor == "ai" else schedule.student_id if actor == "student"
                  else details.get("advisor_id", "advisor")),
        action=f"schedule_{action}", entity_type="schedule", entity_id=str(schedule.schedule_id),
        details={"student_id": schedule.student_id, "term_code": schedule.term_code, **details,
                 "at": datetime.now(timezone.utc).isoformat()}))
    db.flush()


def _require_not_approved(schedule: Schedule) -> None:
    if schedule.status == "approved":
        raise ScheduleLocked("Approved schedules are locked and cannot be changed")


def _actor(by_student: bool) -> Actor:
    return "student" if by_student else "advisor"


def _score(value: float | None) -> Decimal | None:
    """numeric(4,3): clamp to 0..1 and round to 3 places."""
    return None if value is None else Decimal(str(round(min(max(value, 0.0), 1.0), 3)))


def _s(value: uuid.UUID | None) -> str | None:
    return str(value) if value else None
