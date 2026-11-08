"""Submit and decide (DESIGN section 4 flows), with the permission rules in the service, not only in the routes.

    submit_for_review(db, actor, schedule_id)  the schedule's own student: Agent 4 (risk flags), then Agent 5
                                               stage 'submit' (7 checks); a blocking failure raises ComplianceBlocked
                                               and the draft stays a draft
    decide(db, actor, schedule_id, decision)   ONLY an advisor: Agent 5 stage 'approve' (live re-check, decision,
                                               audit, notification, email); a blocking failure on approval raises
                                               ComplianceBlocked and the schedule stays submitted

`actor` is anything with role / student_id / advisor_id (app.auth.CurrentUser). No FastAPI imports here.
"""
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

from sqlalchemy.orm import Session

from app.agents import AgentError, ComplianceAgent, RiskAgent
from app.agents.compliance_agent import ComplianceOut
from app.agents.risk_agent import RiskFlagsOut
from app.llm import LLMClient
from app.models import Schedule, Student
from app.services.access import NotFound
from app.services.schedule_lifecycle import InvalidTransition, NotAdvisor, ScheduleError, ScheduleLocked


class Actor(Protocol):
    role: str
    student_id: str | None
    advisor_id: str | None


class ComplianceBlocked(ScheduleError):
    """The gate stopped the step. The schedule did not change; reasons and checks are recorded."""

    def __init__(self, stage: str, reasons: list[str], run_id: uuid.UUID) -> None:
        super().__init__(f"Blocked at {stage}: {'; '.join(reasons)}")
        self.stage, self.reasons, self.run_id = stage, reasons, run_id


@dataclass(frozen=True)
class ReviewResult:
    schedule_id: uuid.UUID
    status: str
    compliance: ComplianceOut
    compliance_run_id: uuid.UUID
    risk: RiskFlagsOut | None = None
    risk_run_id: uuid.UUID | None = None


def submit_for_review(db: Session, actor: Actor, schedule_id: uuid.UUID, llm: LLMClient | None = None,
                      today: date | None = None, commit: bool = True) -> ReviewResult:
    schedule = _load(db, schedule_id)
    if actor.role != "student" or actor.student_id != schedule.student_id:
        raise PermissionError("Only the student who owns the schedule can submit it")
    _require_status(schedule, "draft", "submitted")
    kwargs = {"llm": llm, "today": today, "commit": commit}
    risk = _run(RiskAgent(db, **kwargs), schedule_id=schedule_id)
    gate = _run(ComplianceAgent(db, **kwargs), schedule_id=schedule_id, stage="submit")
    if not gate.output.allowed:
        raise ComplianceBlocked("submit", gate.output.blocked_reasons, gate.run_id)
    return ReviewResult(schedule_id=schedule_id, status=gate.output.schedule_status, compliance=gate.output,
                        compliance_run_id=gate.run_id, risk=risk.output, risk_run_id=risk.run_id)


def decide(db: Session, actor: Actor, schedule_id: uuid.UUID, decision: Literal["approve", "reject"],
           comment: str | None = None, llm: LLMClient | None = None, today: date | None = None,
           commit: bool = True) -> ReviewResult:
    """Approve or reject a submitted schedule. Students, admins and anyone without an advisor id get NotAdvisor."""
    if actor.role != "advisor" or not actor.advisor_id:
        raise NotAdvisor("Only an advisor can approve or reject a schedule")
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be 'approve' or 'reject'")
    schedule = _load(db, schedule_id)
    student = db.get(Student, schedule.student_id)
    if student.advisor_id != actor.advisor_id:
        raise PermissionError("Only the student's own advisor can decide this schedule")
    _require_status(schedule, "submitted", "decided")
    gate = _run(ComplianceAgent(db, llm=llm, today=today, commit=commit), schedule_id=schedule_id, stage="approve",
                decision=decision, advisor_id=actor.advisor_id, comment=comment)
    if not gate.output.allowed:
        raise ComplianceBlocked("approve", gate.output.blocked_reasons, gate.run_id)
    return ReviewResult(schedule_id=schedule_id, status=gate.output.schedule_status, compliance=gate.output,
                        compliance_run_id=gate.run_id)


def _run(agent, **kwargs):
    """Run an agent; a lifecycle error inside it surfaces as itself, not as AgentError."""
    try:
        return agent.run(**kwargs)
    except AgentError as exc:
        if isinstance(exc.__cause__, (ScheduleError, NotAdvisor, PermissionError)):
            raise exc.__cause__ from exc
        raise


def _load(db: Session, schedule_id: uuid.UUID) -> Schedule:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise NotFound(f"Unknown schedule {schedule_id}")
    return schedule


def _require_status(schedule: Schedule, wanted: str, verb: str) -> None:
    if schedule.status == "approved":
        raise ScheduleLocked("Approved schedules are locked and cannot be changed")
    if schedule.status != wanted:
        raise InvalidTransition(f"Only a {wanted} schedule can be {verb} (this one is {schedule.status})")
