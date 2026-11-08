"""Agent output tools: save and read degree audits, elective suggestions, suggested interests and draft schedules.

The only write tools agents use (CLAUDE.md rule 2). State changes that a person would ask about later
(new draft, superseded draft, suggested interests) also write audit_log (rule 3).
"""
import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    Course,
    DegreeAudit,
    ElectiveSuggestion,
    StudentInterest,
)
from app.services import schedule_lifecycle
from app.services.schedule_lifecycle import SavedSchedule, ScheduleConflict, ScheduleItemDraft  # noqa: F401
from app.tools.student import InterestLabel


class SuggestionIn(BaseModel):
    course_code: str
    rank: int
    similarity: float | None
    popularity: float | None
    fit: float | None = None
    final_score: float
    basis: str  # interests | popularity
    matched_interest: str | None = None
    group_code: str = "popular"  # interest_match | popular
    flags: list[str] = []  # time_clash | few_seats
    reason: str | None


class SuggestionOut(SuggestionIn):
    title: str
    units_default: Decimal | None


def save_degree_audit(db: Session, student_id: str, run_id: uuid.UUID, result: dict[str, Any]) -> uuid.UUID:
    """Store an audit as the student's one current audit (older ones stay, is_current = false)."""
    db.execute(update(DegreeAudit).where(DegreeAudit.student_id == student_id, DegreeAudit.is_current)
               .values(is_current=False))
    db.flush()  # the partial unique index allows one current audit per student
    audit = DegreeAudit(student_id=student_id, run_id=run_id, result=result, is_current=True)
    db.add(audit)
    db.flush()
    return audit.audit_id


def get_current_audit(db: Session, student_id: str) -> dict[str, Any] | None:
    audit = db.scalar(select(DegreeAudit).where(DegreeAudit.student_id == student_id, DegreeAudit.is_current))
    return dict(audit.result) if audit else None


def save_elective_suggestions(db: Session, student_id: str, term_code: str, run_id: uuid.UUID,
                              items: list[SuggestionIn]) -> int:
    """Replace the student's suggestions for `term_code` (agent_runs keeps the history)."""
    db.execute(delete(ElectiveSuggestion).where(ElectiveSuggestion.student_id == student_id,
                                                ElectiveSuggestion.term_code == term_code))
    db.add_all(ElectiveSuggestion(
        student_id=student_id, term_code=term_code, run_id=run_id, course_code=i.course_code, rank=i.rank,
        similarity=_score(i.similarity), popularity=_score(i.popularity), fit=_score(i.fit),
        final_score=_score(i.final_score), basis=i.basis, matched_interest=i.matched_interest,
        group_code=i.group_code, flags=list(i.flags), reason=i.reason)
        for i in items)
    db.flush()
    return len(items)


def get_elective_suggestions(db: Session, student_id: str, term_code: str) -> list[SuggestionOut]:
    rows = db.execute(
        select(ElectiveSuggestion, Course.title, Course.units_default)
        .join(Course, Course.course_code == ElectiveSuggestion.course_code)
        .where(ElectiveSuggestion.student_id == student_id, ElectiveSuggestion.term_code == term_code)
        .order_by(ElectiveSuggestion.rank)
    ).all()
    return [SuggestionOut(course_code=s.course_code, rank=s.rank, basis=s.basis, reason=s.reason,
                          similarity=_float(s.similarity), popularity=_float(s.popularity), fit=_float(s.fit),
                          matched_interest=s.matched_interest, group_code=s.group_code, flags=list(s.flags or []),
                          final_score=float(s.final_score), title=title, units_default=units)
            for s, title, units in rows]


def save_suggested_interests(db: Session, student_id: str, items: list[tuple[str, str]]) -> list[InterestLabel]:
    """Save (label, source) pairs as status='suggested'. Labels the student already has (any status) are skipped.

    Never confirms anything: only the student can confirm an interest (DESIGN D13).
    """
    have = {label.lower() for label in db.scalars(select(StudentInterest.label)
                                                  .where(StudentInterest.student_id == student_id))}
    saved: list[InterestLabel] = []
    for label, source in items:
        if label.lower() in have:
            continue
        have.add(label.lower())
        db.add(StudentInterest(student_id=student_id, label=label, source=source, status="suggested"))
        saved.append(InterestLabel(label=label, source=source, status="suggested"))
    if saved:
        db.add(AuditLog(actor_type="agent", actor_id="electives", action="interests_suggested",
                        entity_type="student", entity_id=student_id,
                        details={"labels": [s.label for s in saved]}))
    db.flush()
    return saved


class OpenSchedule(BaseModel):
    schedule_id: uuid.UUID
    version: int
    status: str  # draft | submitted


def get_open_schedule(db: Session, student_id: str, term_code: str) -> OpenSchedule | None:
    """The student's open (draft or submitted) schedule for the term, if any."""
    found = schedule_lifecycle.open_schedule(db, student_id, term_code)
    return None if found is None else OpenSchedule(schedule_id=found.schedule_id, version=found.version,
                                                    status=found.status)


def save_schedule(db: Session, student_id: str, term_code: str, run_id: uuid.UUID, items: list[ScheduleItemDraft],
                  ai_explanation: str, load_rationale: str, replace: bool = False) -> SavedSchedule:
    """Save a new draft through the lifecycle service (D15). An open draft is replaced only with replace=True
    (the student confirmed); otherwise DraftExists. A submitted schedule blocks the build (ScheduleConflict)."""
    return schedule_lifecycle.build(db, student_id, term_code, items, ai_explanation=ai_explanation,
                                    load_rationale=load_rationale, run_id=run_id, replace=replace, actor="ai")


def _score(value: float | None) -> Decimal | None:
    """numeric(4,3): clamp to 0..1 and round to 3 places."""
    return None if value is None else Decimal(str(round(min(max(value, 0.0), 1.0), 3)))


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)
