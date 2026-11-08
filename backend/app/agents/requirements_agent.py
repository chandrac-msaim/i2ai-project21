"""Agent 1 - Requirements (degree audit).

Core courses with status (completed / in progress / waived / remaining), elective units left, and a projected
graduation term. Pure arithmetic over the student's record: no LLM call, so the dashboard never waits on one;
the explanation is a template. Saves one current degree_audits row.
"""
from collections.abc import Sequence
from decimal import Decimal
from math import ceil
from typing import Any, Literal

from pydantic import BaseModel

from app.agents.base import Agent, RunContext
from app.services.terms import TermStatus
from app.tools.student import PlanTerm, ProgramRequirements, StudentHistory, WaiverOut

CoreState = Literal["completed", "in_progress", "waived", "remaining"]


class CoreStatus(BaseModel):
    course_code: str
    title: str
    units: Decimal
    recommended_term_seq: int | None
    status: CoreState


class DegreeAuditOut(BaseModel):
    student_id: str
    track_code: str
    entry_term: str
    qpa: Decimal | None
    min_qpa: Decimal
    below_min_qpa: bool
    total_units: int
    units_completed: Decimal
    units_in_progress: Decimal
    units_remaining: Decimal  # total - completed - in progress
    core: list[CoreStatus]
    core_units_remaining: Decimal
    elective_units_required: Decimal  # program minimum + units of waived core courses that move to electives
    elective_units_done: Decimal  # completed + in progress
    elective_units_left: Decimal
    plan_position: int  # track_term_plan.term_seq of the student's next term (1 = first term)
    next_term: str | None  # first term after the student's record (entry term for a new student)
    projected_graduation_term: str | None
    units_per_term: int  # pace used for the projection


class _Facts(BaseModel):
    history: StudentHistory
    program: ProgramRequirements
    waivers: list[WaiverOut]
    terms: list[TermStatus]


class RequirementsAgent(Agent[DegreeAuditOut]):
    name = "requirements"
    tools = frozenset({"get_student_history", "get_program_requirements", "get_course_waivers",
                       "get_term_registration", "save_degree_audit"})
    max_attempts = 1

    def plan(self, ctx: RunContext) -> _Facts:
        history = self.call_tool("get_student_history", ctx.student_id)
        return _Facts(history=history,
                      program=self.call_tool("get_program_requirements", history.track_code),
                      waivers=self.call_tool("get_course_waivers", ctx.student_id),
                      terms=self.call_tool("get_term_registration", ctx.today))

    def decide(self, ctx: RunContext, facts: _Facts) -> DegreeAuditOut:
        return audit(facts.history, facts.program, facts.waivers, facts.terms)

    def save(self, ctx: RunContext, facts: _Facts, decision: DegreeAuditOut) -> None:
        self.call_tool("save_degree_audit", ctx.student_id, ctx.run_id, decision.model_dump(mode="json"))

    def explain(self, ctx: RunContext, facts: _Facts, decision: DegreeAuditOut) -> str:
        return summary(decision)


def audit(history: StudentHistory, program: ProgramRequirements, waivers: Sequence[WaiverOut],
          terms: Sequence[TermStatus]) -> DegreeAuditOut:
    """The degree audit, from facts only (pure function, unit-testable without a database)."""
    waived = {w.course_code for w in waivers if w.status == "approved"}
    completed, in_progress = set(history.completed), set(history.in_progress)
    core_codes = {c.course_code for c in program.core}

    core: list[CoreStatus] = []
    for c in program.core:
        state: CoreState = ("completed" if c.course_code in completed else
                            "waived" if c.course_code in waived else
                            "in_progress" if c.course_code in in_progress else "remaining")
        core.append(CoreStatus(course_code=c.course_code, title=c.title, units=c.units_default or Decimal(0),
                               recommended_term_seq=c.recommended_term_seq, status=state))

    moved = sum((c.units_default or Decimal(0) for c in program.core
                 if c.course_code in waived and c.waiver_adds_elective_units), Decimal(0))
    elective_done = sum((e.units for e in history.enrollments if e.course_code not in core_codes and (
        e.status == "in_progress" or (e.status == "completed" and e.is_passing))), Decimal(0))
    elective_required = Decimal(program.elective_min_units) + moved
    remaining = max(Decimal(program.total_units) - history.units_completed - history.units_in_progress, Decimal(0))

    taken_seqs = sorted({e.term_seq for e in history.enrollments if e.status in ("completed", "in_progress")})
    position = plan_position(history)
    next_term, graduation = project(terms, history.entry_term, taken_seqs, position, program.term_plan,
                                    remaining, program.typical_units_per_term)
    return DegreeAuditOut(
        student_id=history.student_id, track_code=history.track_code, entry_term=history.entry_term,
        qpa=history.qpa, min_qpa=program.min_qpa,
        below_min_qpa=history.qpa is not None and history.qpa < program.min_qpa,
        total_units=program.total_units, units_completed=history.units_completed,
        units_in_progress=history.units_in_progress, units_remaining=remaining, core=core,
        core_units_remaining=sum((c.units for c in core if c.status == "remaining"), Decimal(0)),
        elective_units_required=elective_required, elective_units_done=elective_done,
        elective_units_left=max(elective_required - elective_done, Decimal(0)),
        plan_position=position, next_term=next_term, projected_graduation_term=graduation,
        units_per_term=program.typical_units_per_term,
    )


def plan_position(history: StudentHistory) -> int:
    """track_term_plan.term_seq of the student's next term: academic terms with courses on the record + 1."""
    return len({e.term_seq for e in history.enrollments if e.status in ("completed", "in_progress")}) + 1


def project(terms: Sequence[TermStatus], entry_term: str, taken_seqs: Sequence[int], position: int,
            term_plan: Sequence[PlanTerm], units_remaining: Decimal, per_term: int) -> tuple[str | None, str | None]:
    """(next term, graduation term): walk the calendar from the next term at `per_term` units per academic term.

    A non-academic plan term (AIM16 summer internship) passes without units. Nothing remaining = graduates in
    the last term already on the record.
    """
    by_seq = sorted(terms, key=lambda t: t.seq)
    entry_seq = next((t.seq for t in by_seq if t.term_code == entry_term), None)
    start = taken_seqs[-1] + 1 if taken_seqs else entry_seq
    upcoming = [t for t in by_seq if start is not None and t.seq >= start]
    next_term = upcoming[0].term_code if upcoming else None
    if units_remaining <= 0:
        last = next((t.term_code for t in by_seq if taken_seqs and t.seq == taken_seqs[-1]), None)
        return next_term, last
    plan = {p.term_seq: p for p in term_plan}
    needed = ceil(units_remaining / per_term)
    for term in upcoming:
        entry = plan.get(position)
        position += 1
        if entry is not None and not entry.is_academic:
            continue
        needed -= 1
        if needed == 0:
            return next_term, term.term_code
    return next_term, None  # beyond the terms in the calendar


def summary(a: DegreeAuditOut) -> str:
    counts = {s: sum(1 for c in a.core if c.status == s) for s in ("completed", "in_progress", "waived", "remaining")}
    parts = [
        f"The student has completed {_n(a.units_completed)} of {a.total_units} units"
        f" ({_n(a.units_in_progress)} in progress).",
        f"Core: {counts['completed']} completed, {counts['in_progress']} in progress, {counts['waived']} waived,"
        f" {counts['remaining']} remaining ({_n(a.core_units_remaining)} units).",
        f"Elective units left: {_n(a.elective_units_left)} of {_n(a.elective_units_required)}.",
    ]
    if a.projected_graduation_term:
        parts.append(f"Projected graduation: {a.projected_graduation_term} at {a.units_per_term} units per term.")
    if a.below_min_qpa:
        parts.append(f"QPA {a.qpa} is below the {a.min_qpa} minimum.")
    return " ".join(parts)


def _n(units: Decimal | Any) -> str:
    return f"{Decimal(units).normalize():f}"
