"""Schedule routes for the student screens. Role: student for changes; the schedule's student, their advisor or an
admin for reading.

The route order matters: fixed paths (/build, /history, /current) come before /{schedule_id}.
"""
import uuid
from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel

from app.agents import build_draft
from app.api.deps import DB, LLM, AnyUser, StudentUser, Today, default_term, own_schedule, visible_schedule
from app.models import Schedule
from app.services import schedule_lifecycle as lifecycle
from app.services import schedule_views as sv
from app.services.schedule_review import submit_for_review

router = APIRouter(prefix="/schedules", tags=["schedules"])


class BuildRequest(BaseModel):
    term_code: str
    replace: bool = False  # true only after the student confirmed replacing the open draft


class ItemEdit(BaseModel):
    action: Literal["add", "remove", "swap"]
    course_code: str | None = None  # the course to add / remove / swap in
    section_id: str | None = None  # optional; the best section without a clash is chosen otherwise
    replace_course_code: str | None = None  # swap: the course that leaves


@router.post("/build", response_model=sv.ScheduleView, status_code=status.HTTP_201_CREATED)
def build(body: BuildRequest, user: StudentUser, db: DB, llm: LLM, today: Today) -> sv.ScheduleView:
    """Agents 1-3 build the draft for a term that can be planned today (else 400 with the reason).
    An open draft needs replace=true (else 409 draft_exists with its schedule_id)."""
    result = build_draft(db, user.student_id, body.term_code, llm=llm, today=today, replace=body.replace)
    return sv.schedule_view(db, db.get(Schedule, result.schedule.output.schedule_id), today)


@router.get("/history", response_model=list[sv.HistoryItem])
def history(user: StudentUser, db: DB, term: str | None = None) -> list[sv.HistoryItem]:
    """Every version, newest first, with its status: the "Previous versions" list."""
    return sv.history(db, user.student_id, term)


@router.get("/current", response_model=sv.ScheduleView | None)
def current(user: StudentUser, db: DB, today: Today, term: str | None = None) -> sv.ScheduleView | None:
    """The open schedule of the term (default: the first term open for planning), else its newest decided one."""
    term_code = default_term(db, today, term)
    schedule = sv.current_schedule(db, user.student_id, term_code) if term_code else None
    return sv.schedule_view(db, schedule, today) if schedule else None


@router.get("/{schedule_id}", response_model=sv.ScheduleView)
def get_schedule(schedule_id: uuid.UUID, user: AnyUser, db: DB, today: Today) -> sv.ScheduleView:
    return sv.schedule_view(db, visible_schedule(db, user, schedule_id), today)


@router.get("/{schedule_id}/suggestions", response_model=sv.SuggestionGroups)
def suggestions(schedule_id: uuid.UUID, user: AnyUser, db: DB) -> sv.SuggestionGroups:
    """Agent 2's electives in two groups: 'interest_match' then 'popular', with matched interest, fit, flags, reason."""
    return sv.suggestions(db, visible_schedule(db, user, schedule_id))


@router.post("/{schedule_id}/items", response_model=sv.ScheduleView)
def edit_items(schedule_id: uuid.UUID, body: ItemEdit, user: StudentUser, db: DB, today: Today) -> sv.ScheduleView:
    """Add, remove or swap a course in a draft, then re-validate it (the answer carries the live checks).
    An add or swap that creates a new blocking failure (time clash, prerequisite, unit limit...) is rolled back and
    answered 409 edit_blocked with the rule reasons: the draft stays as it was. Removing is never refused."""
    schedule = own_schedule(db, user, schedule_id)
    before = {sv.blocking_key(b) for b in sv.schedule_view(db, schedule, today).blocking}
    sv.edit_items(db, schedule, body.action, body.course_code, body.section_id, body.replace_course_code)
    view = sv.schedule_view(db, schedule, today)
    if body.action != "remove":
        new = [b for b in view.blocking if sv.blocking_key(b) not in before]
        if new:
            db.rollback()
            raise sv.EditBlocked(new)
    db.commit()
    return view


@router.post("/{schedule_id}/reset", response_model=sv.ScheduleView)
def reset(schedule_id: uuid.UUID, user: StudentUser, db: DB, today: Today) -> sv.ScheduleView:
    """Back to the draft exactly as the AI built it (draft only)."""
    schedule = lifecycle.reset(db, own_schedule(db, user, schedule_id), by_student=True)
    db.commit()
    return sv.schedule_view(db, schedule, today)


@router.delete("/{schedule_id}", response_model=sv.ScheduleView)
def discard(schedule_id: uuid.UUID, user: StudentUser, db: DB, today: Today) -> sv.ScheduleView:
    """Discard a draft (soft: status 'discarded', the row is kept). 409 if it is not a draft."""
    schedule = lifecycle.discard(db, own_schedule(db, user, schedule_id), by_student=True)
    db.commit()
    return sv.schedule_view(db, schedule, today)


@router.post("/{schedule_id}/withdraw", response_model=sv.ScheduleView)
def withdraw(schedule_id: uuid.UUID, user: StudentUser, db: DB, today: Today) -> sv.ScheduleView:
    """Take a submitted schedule back to a draft before the advisor decides (else 409)."""
    schedule = lifecycle.withdraw(db, own_schedule(db, user, schedule_id), by_student=True)
    db.commit()
    return sv.schedule_view(db, schedule, today)


@router.post("/{schedule_id}/resubmit", response_model=sv.ScheduleView, status_code=status.HTTP_201_CREATED)
def resubmit(schedule_id: uuid.UUID, user: StudentUser, db: DB, today: Today) -> sv.ScheduleView:
    """A rejected schedule becomes a new draft version with the same courses (it still has to be submitted)."""
    new = lifecycle.resubmit_from_rejected(db, own_schedule(db, user, schedule_id), by_student=True)
    db.commit()
    return sv.schedule_view(db, new, today)


@router.post("/{schedule_id}/submit", response_model=sv.ScheduleView)
def submit(schedule_id: uuid.UUID, user: StudentUser, db: DB, llm: LLM, today: Today) -> sv.ScheduleView:
    """Agent 4 (risk flags) then Agent 5 (7 checks). A blocking failure answers 409 compliance_blocked with the
    reasons and the draft stays a draft."""
    own_schedule(db, user, schedule_id)
    submit_for_review(db, user, schedule_id, llm=llm, today=today)
    return sv.schedule_view(db, db.get(Schedule, schedule_id), today)
