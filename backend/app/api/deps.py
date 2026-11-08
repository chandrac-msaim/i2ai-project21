"""Shared FastAPI dependencies for the routers."""
import uuid
from datetime import date
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_role
from app.db import get_db
from app.llm import LLMClient
from app.models import Schedule
from app.services.access import can_access_student
from app.services.terms import get_today, plannable_terms

DB = Annotated[Session, Depends(get_db)]
Today = Annotated[date, Depends(get_today)]
AnyUser = Annotated[CurrentUser, Depends(get_current_user)]
StudentUser = Annotated[CurrentUser, Depends(require_role("student"))]
AdvisorUser = Annotated[CurrentUser, Depends(require_role("advisor"))]
AdvisorOrAdmin = Annotated[CurrentUser, Depends(require_role("advisor", "admin"))]
StudentOrAdvisorUser = Annotated[CurrentUser, Depends(require_role("student", "advisor"))]


def get_llm() -> LLMClient:
    """One LLM client per request (tests override this with a fake)."""
    return LLMClient()


LLM = Annotated[LLMClient, Depends(get_llm)]


def own_schedule(db: Session, user: CurrentUser, schedule_id: uuid.UUID) -> Schedule:
    """The schedule if it belongs to the logged-in student (404 if unknown, 403 if someone else's)."""
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown schedule {schedule_id}")
    if schedule.student_id != user.student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This is not your schedule")
    return schedule


def visible_schedule(db: Session, user: CurrentUser, schedule_id: uuid.UUID) -> Schedule:
    """The schedule if the user may see it: the student, their advisor, or an admin."""
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown schedule {schedule_id}")
    if not can_access_student(db, user, schedule.student_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this schedule")
    return schedule


def default_term(db: Session, today: date, term: str | None) -> str | None:
    """The requested term, else the first term that can be planned today (None if there is none)."""
    if term:
        return term
    open_terms = plannable_terms(db, today)
    return open_terms[0].term_code if open_terms else None
