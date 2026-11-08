"""BUILD flow (DESIGN section 4): Agent 1 Requirements -> Agent 2 Electives -> Agent 3 Schedule.

Each agent writes its own agent_runs row. The term is checked first, so a closed term costs no agent runs
(Agent 3 refuses it again on its own).
"""
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.agents.base import AgentResult
from app.agents.elective_agent import ElectiveAgent, ElectiveSuggestionsOut
from app.agents.requirements_agent import DegreeAuditOut, RequirementsAgent
from app.agents.schedule_agent import ScheduleAgent, ScheduleDraftOut
from app.config import get_settings
from app.llm import LLMClient
from app.services.schedule_lifecycle import DraftExists, ScheduleConflict, open_schedule
from app.services.terms import require_plannable


@dataclass(frozen=True)
class BuildResult:
    audit: AgentResult[DegreeAuditOut]
    electives: AgentResult[ElectiveSuggestionsOut]
    schedule: AgentResult[ScheduleDraftOut]


def build_draft(db: Session, student_id: str, term_code: str, llm: LLMClient | None = None,
                today: date | None = None, commit: bool = True, replace: bool = False) -> BuildResult:
    """Audit, suggest electives and build the draft schedule for `term_code` (must be plannable today).

    An open draft is replaced only with replace=True (the student confirmed); otherwise DraftExists, raised
    before any agent runs. A submitted schedule is never replaced (ScheduleConflict).
    """
    today = today or get_settings().today()
    plannable = require_plannable(db, term_code, today)  # TermNotPlannable
    existing = open_schedule(db, student_id, term_code)
    if existing is not None and existing.status == "submitted":
        raise ScheduleConflict(f"A schedule for {plannable.term_label} is already with the advisor")
    if existing is not None and not replace:
        raise DraftExists(existing.schedule_id, term_code, plannable.term_label)
    llm = llm or LLMClient()
    kwargs = {"llm": llm, "today": today, "commit": commit}
    audit = RequirementsAgent(db, **kwargs).run(student_id=student_id)
    electives = ElectiveAgent(db, **kwargs).run(student_id=student_id, term_code=term_code)
    schedule = ScheduleAgent(db, **kwargs).run(student_id=student_id, term_code=term_code, replace=replace)
    return BuildResult(audit=audit, electives=electives, schedule=schedule)
