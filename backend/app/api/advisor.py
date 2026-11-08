"""Advisor routes: caseload, review page, recheck, decision. Roles: advisor (own students); admin may read."""
import uuid
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import DB, LLM, AdvisorOrAdmin, AdvisorUser, Today
from app.services import advisor_portal as portal
from app.services.schedule_review import decide

router = APIRouter(prefix="/advisor", tags=["advisor"])


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=2000)


class DecisionResult(BaseModel):
    schedule_id: uuid.UUID
    status: str  # approved | rejected
    email_sent: bool | None
    email_subject: str | None
    email_error: str | None
    notification_id: int | None
    advisor_flags: list[str]  # non-blocking flags the checks raised


@router.get("/caseload", response_model=portal.Caseload)
def caseload(user: AdvisorOrAdmin, db: DB) -> portal.Caseload:
    """The advisor's students with progress, newest schedule, risk flags and unread messages."""
    return portal.caseload(db, user)


@router.get("/schedules/{schedule_id}", response_model=portal.AdvisorScheduleView)
def review(schedule_id: uuid.UUID, user: AdvisorOrAdmin, db: DB, today: Today) -> portal.AdvisorScheduleView:
    """Live checks, risk flags, the AI's explanation and run, every change (with the AI's original course for a
    swapped row), the message thread and the earlier versions."""
    result = portal.advisor_schedule(db, user, user.user_id, schedule_id, today)
    db.commit()  # reading the thread marks the student's messages as read
    return result


@router.post("/schedules/{schedule_id}/recheck", response_model=portal.Recheck)
def recheck(schedule_id: uuid.UUID, user: AdvisorUser, db: DB, llm: LLM, today: Today) -> portal.Recheck:
    """Read seats and rules again and refresh the risk flags; the schedule itself does not change."""
    result = portal.recheck(db, user, schedule_id, llm, today)
    db.commit()
    return result


class PreviewRequest(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/schedules/{schedule_id}/email-preview", response_model=portal.EmailPreview)
def email_preview(schedule_id: uuid.UUID, body: PreviewRequest, user: AdvisorUser, db: DB) -> portal.EmailPreview:
    """The decision email as the student would receive it (HTML). Nothing is sent, saved or logged as a decision."""
    return portal.email_preview(db, user, schedule_id, body.decision, body.comment)


@router.post("/schedules/{schedule_id}/decision", response_model=DecisionResult)
def decision(schedule_id: uuid.UUID, body: Decision, user: AdvisorUser, db: DB, llm: LLM,
             today: Today) -> DecisionResult:
    """Approve or reject. Approving re-checks seats live: a full section answers 409 compliance_blocked and the
    schedule stays submitted. Otherwise the decision is saved and the student gets an email."""
    result = decide(db, user, schedule_id, body.decision, body.comment, llm=llm, today=today)
    out = result.compliance
    return DecisionResult(schedule_id=schedule_id, status=result.status, email_sent=out.email_sent,
                          email_subject=out.email_subject, email_error=out.email_error,
                          notification_id=out.notification_id, advisor_flags=out.advisor_flags)
