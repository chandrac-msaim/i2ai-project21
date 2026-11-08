"""One place that turns domain errors into HTTP answers: {"detail": text, "code": machine-readable, ...}.

  400 term not plannable, an edit that cannot be built     403 NotAdvisor / PermissionError
  404 unknown schedule, course, interest                   409 wrong state, draft exists, compliance blocked, edit blocked by a rule
  422 bad input (empty interest, bad upload)               413 / 415 for uploads that are too big / wrong type
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.agents import AgentError
from app.services.access import NotFound
from app.services.catalog_search import CourseNotFound
from app.services.messaging import MessageInvalid
from app.services.schedule_lifecycle import (
    DraftExists,
    InvalidTransition,
    ItemError,
    ItemNotFound,
    NotAdvisor,
    ScheduleConflict,
    ScheduleError,
    ScheduleLocked,
)
from app.services.schedule_review import ComplianceBlocked
from app.services.schedule_views import EditBlocked, InvalidEdit
from app.services.student_portal import DocumentRejected, InterestDuplicate, InterestInvalid, InterestNotFound
from app.services.terms import TermNotPlannable


def _reply(http: int, code: str, exc: Exception, **extra) -> JSONResponse:
    return JSONResponse(status_code=http, content={"detail": str(exc), "code": code, **extra})


def to_response(exc: Exception) -> JSONResponse | None:
    """The HTTP answer for a known domain error, or None."""
    if isinstance(exc, ComplianceBlocked):
        return _reply(409, "compliance_blocked", exc, stage=exc.stage, reasons=exc.reasons, run_id=str(exc.run_id))
    if isinstance(exc, EditBlocked):
        return _reply(409, "edit_blocked", exc, reasons=exc.reasons)
    if isinstance(exc, DraftExists):
        return _reply(409, "draft_exists", exc, schedule_id=str(exc.schedule_id))
    if isinstance(exc, InterestDuplicate):
        return _reply(409, "duplicate_interest", exc, interest_id=exc.interest_id, status=exc.status)
    if isinstance(exc, ScheduleConflict):
        return _reply(409, "schedule_submitted", exc)
    if isinstance(exc, ScheduleLocked):
        return _reply(409, "schedule_locked", exc)
    if isinstance(exc, InvalidTransition):
        return _reply(409, "invalid_state", exc)
    if isinstance(exc, ItemError):
        return _reply(409, "item_conflict", exc)
    if isinstance(exc, ScheduleError):
        return _reply(409, "schedule_error", exc)
    if isinstance(exc, NotAdvisor):
        return _reply(403, "not_advisor", exc)
    if isinstance(exc, PermissionError):
        return _reply(403, "forbidden", exc)
    if isinstance(exc, TermNotPlannable):
        return _reply(400, "term_not_plannable", exc)
    if isinstance(exc, InvalidEdit):
        return _reply(400, "invalid_edit", exc)
    if isinstance(exc, DocumentRejected):
        return _reply(exc.status_code, "document_rejected", exc)
    if isinstance(exc, (InterestInvalid, MessageInvalid)):
        return _reply(422, "invalid_input", exc)
    if isinstance(exc, (ItemNotFound, InterestNotFound, CourseNotFound, NotFound)):
        return _reply(404, "not_found", exc)
    return None


def register(app: FastAPI) -> None:
    domain_errors = (ComplianceBlocked, DraftExists, InterestDuplicate, ScheduleError, NotAdvisor, PermissionError,
                     TermNotPlannable, InvalidEdit, EditBlocked, DocumentRejected, InterestInvalid, MessageInvalid, ItemNotFound,
                     InterestNotFound, CourseNotFound, NotFound)

    async def domain_handler(_: Request, exc: Exception) -> JSONResponse:
        return to_response(exc) or _reply(500, "error", exc)

    async def agent_handler(_: Request, exc: AgentError) -> JSONResponse:
        # an agent run failed: a domain error inside it (a lifecycle rule, a closed term) keeps its own answer
        answer = to_response(exc.__cause__) if exc.__cause__ is not None else None
        return answer or _reply(500, "agent_failed", exc, run_id=str(exc.run_id))

    for cls in domain_errors:
        app.add_exception_handler(cls, domain_handler)
    app.add_exception_handler(AgentError, agent_handler)
