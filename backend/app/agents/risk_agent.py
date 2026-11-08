"""Agent 4 - Risk flags.

Which flags exist is decided by plain rules (rule engine results + rule_settings thresholds); the LLM only
words them. For one schedule it raises
    LOW_QPA             cumulative QPA below the track minimum
    BEHIND_PACE         units completed or in progress below rule_settings BEHIND_PACE x academic terms taken
    CONDITIONAL_PREREQ  a prerequisite is still in progress (one flag per course)
    LOW_SEATS           a chosen section has fewer seats left than rule_settings LOW_SEATS
    NEEDS_APPROVAL      course type needs the advisor's special approval (one flag per course)
    UNDERGRAD_CAP       undergraduate course: petition, and the 12-unit cap (one flag per course)
    UNIT_OVERLOAD       55-60 units, allowed because the QPA is above 3.50: information for the advisor
One LLM call words each flag and suggests an advisor action ("F1: explanation || action"); a missing, malformed
or number-inventing answer falls back to the template text for that flag. No flags = no LLM call.
Saves risk_flags (earlier open flags of the schedule become 'resolved').
"""
import re
from decimal import Decimal

from pydantic import BaseModel

from app.agents.base import Agent, RunContext, ungrounded_numbers
from app.rules.engine import CandidateSchedule, CheckResult, ScheduleItemIn
from app.tools.schedules import RiskFlagIn, RuleDetail, ScheduleOut
from app.tools.student import ProgramRequirements, StudentHistory

FLAG_CODES = ("LOW_QPA", "BEHIND_PACE", "CONDITIONAL_PREREQ", "LOW_SEATS", "NEEDS_APPROVAL", "UNDERGRAD_CAP",
              "UNIT_OVERLOAD")
_FALLBACK_SEVERITY = {"UNIT_OVERLOAD": "low"}  # informational: it has no rule_settings row of its own
_RULE_FOR = {"UNIT_OVERLOAD": "UNIT_LIMIT"}  # whose description to show as "Rule: ..."
_SEVERITY = {"block": "high", "high": "high", "medium": "medium", "low": "low", "info": "low"}
_ACTION = {
    "LOW_QPA": "Check the student's academic standing and whether this course load is realistic before approving.",
    "BEHIND_PACE": "Review the student's plan for finishing on time and confirm the load for this term.",
    "CONDITIONAL_PREREQ": "Approve only if the prerequisite in progress is passed; check again when grades are posted.",
    "LOW_SEATS": "Decide soon: the section may fill up. Seats are checked again when you approve.",
    "NEEDS_APPROVAL": "Decide on the special approval for this course.",
    "UNDERGRAD_CAP": "Decide on the petition and check the undergraduate unit limit.",
    "UNIT_OVERLOAD": "Confirm the student wants more than the standard load; it is allowed because of the QPA.",
}
_LINE = re.compile(r"^\W*F(\d+)\W{0,3}[:\-–—]\s*(.+?)\s*\|\|\s*(.+)$")


class RiskFlagOut(BaseModel):
    flag_code: str
    severity: str  # high | medium | low
    course_code: str | None = None
    message: str
    rule_text: str | None = None
    suggested_action: str


class RiskFlagsOut(BaseModel):
    student_id: str
    schedule_id: str
    term_code: str
    term_label: str = ""
    total_units: Decimal
    flags: list[RiskFlagOut]


class _Facts(BaseModel):
    schedule: ScheduleOut
    history: StudentHistory
    program: ProgramRequirements
    rules: dict[str, RuleDetail]
    results: list[CheckResult]
    seats: dict[str, int | None]  # section_id -> seats left now (None = no limit)


class RiskAgent(Agent[RiskFlagsOut]):
    """run(schedule_id=...)"""

    name = "risk"
    tools = frozenset({"get_schedule", "get_student_history", "get_program_requirements", "get_rule_details",
                       "validate_schedule", "get_term_sections", "save_risk_flags"})
    max_attempts = 1

    def plan(self, ctx: RunContext) -> _Facts:
        schedule = self.call_tool("get_schedule", ctx.schedule_id)
        history = self.call_tool("get_student_history", schedule.student_id)
        program = self.call_tool("get_program_requirements", history.track_code)
        candidate = CandidateSchedule(student_id=schedule.student_id, term_code=schedule.term_code, items=[
            ScheduleItemIn(course_code=i.course_code, section_id=i.section_id, units=i.units)
            for i in schedule.items])
        results = self.call_tool("validate_schedule", candidate, ctx.today)
        found = self.call_tool("get_term_sections", schedule.term_code, [i.course_code for i in schedule.items])
        seats = {s.section_id: s.seats_left for secs in found.values() for s in secs}
        return _Facts(schedule=schedule, history=history, program=program,
                      rules=self.call_tool("get_rule_details"), results=results, seats=seats)

    def decide(self, ctx: RunContext, facts: _Facts) -> RiskFlagsOut:
        flags = compute_flags(facts.schedule, facts.history, facts.program, facts.rules, facts.results, facts.seats)
        return RiskFlagsOut(student_id=facts.schedule.student_id, schedule_id=str(facts.schedule.schedule_id),
                            term_code=facts.schedule.term_code, term_label=facts.schedule.term_label,
                            total_units=facts.schedule.total_units, flags=flags)

    def save(self, ctx: RunContext, facts: _Facts, decision: RiskFlagsOut) -> None:
        if decision.flags:  # the run's one LLM call words the flags and suggests the advisor's action
            prompt = flag_prompt(decision.flags)
            fallback = "\n".join(f"F{n}: {f.message} || {f.suggested_action}"
                                 for n, f in enumerate(decision.flags, start=1))
            text = self.ask_llm(prompt, fallback=fallback, max_tokens=min(80 * len(decision.flags), 500))
            rejected = apply_wording(decision.flags, text, prompt)
            if rejected:
                ctx.tool_calls.append({"tool": "llm_guard", "rejected": True,
                                       "reason": f"template text kept for flags {rejected}: missing, echoed or "
                                                 f"invented numbers"})
        self.call_tool("save_risk_flags", decision.student_id, facts.schedule.schedule_id, ctx.run_id, [
            RiskFlagIn(flag_code=f.flag_code, severity=f.severity, message=f.message, rule_text=f.rule_text,
                       suggested_action=f.suggested_action) for f in decision.flags])

    def explain(self, ctx: RunContext, facts: _Facts, decision: RiskFlagsOut) -> str:
        if not decision.flags:
            return f"No risk flags for the {decision.term_label} schedule ({_n(decision.total_units)} units)."
        counts = {c: sum(f.flag_code == c for f in decision.flags) for c in FLAG_CODES}
        listed = ", ".join(f"{c}" + (f" x{n}" if n > 1 else "") for c, n in counts.items() if n)
        return f"{len(decision.flags)} risk flags for the {decision.term_label} schedule: {listed}."


# ---------- the rules (pure; unit-testable without a database) ----------

def compute_flags(schedule: ScheduleOut, history: StudentHistory, program: ProgramRequirements,
                  rules: dict[str, RuleDetail], results: list[CheckResult],
                  seats: dict[str, int | None]) -> list[RiskFlagOut]:
    flags: list[RiskFlagOut] = []

    def add(code: str, fact: str, course: str | None = None) -> None:
        rule = rules.get(_RULE_FOR.get(code, code))
        own = rules.get(code)
        severity = _SEVERITY.get(own.severity, "low") if own and own.severity else _FALLBACK_SEVERITY.get(code, "low")
        flags.append(RiskFlagOut(flag_code=code, severity=severity, course_code=course, message=fact,
                                 rule_text=rule.description if rule else None, suggested_action=_ACTION[code]))

    if history.qpa is not None and history.qpa < program.min_qpa:
        add("LOW_QPA", f"Cumulative QPA {history.qpa:.2f} is below the {program.min_qpa:.2f} minimum.")

    pace = rules.get("BEHIND_PACE")
    terms_taken = len({e.term_seq for e in history.enrollments if e.status in ("completed", "in_progress")})
    if pace and terms_taken:
        minimum = Decimal(str(pace.params["min_units_per_term"]))
        have = history.units_completed + history.units_in_progress
        if have < minimum * terms_taken:
            add("BEHIND_PACE", f"{_n(have)} units completed or in progress after {terms_taken} "
                               f"term{'s' if terms_taken > 1 else ''}; the pace is at least {_n(minimum)} per term.")

    for r in results:
        if r.code == "PREREQ" and r.flag == "conditional":
            add("CONDITIONAL_PREREQ", f"{r.course_code}: {r.detail}", r.course_code)
        elif r.code == "APPROVAL" and r.flag == "needs_approval":
            add("NEEDS_APPROVAL", f"{r.course_code}: {r.detail}", r.course_code)
        elif r.code == "LEVEL" and r.flag == "needs_petition":
            add("UNDERGRAD_CAP", f"{r.course_code}: {r.detail}", r.course_code)
        elif r.code == "UNIT_LIMIT" and r.flag == "unit_overload":
            add("UNIT_OVERLOAD", r.detail)

    low = rules.get("LOW_SEATS")
    if low:
        below = int(low.params["seats_left_below"])
        for item in schedule.items:
            left = seats.get(item.section_id) if item.section_id else None
            if left is not None and left < below:
                add("LOW_SEATS", f"{item.course_code} section {item.section_code} has {left} "
                                 f"seat{'s' if left != 1 else ''} left (flag below {below}).", item.course_code)
    order = {c: i for i, c in enumerate(FLAG_CODES)}
    return sorted(flags, key=lambda f: ({"high": 0, "medium": 1, "low": 2}[f.severity], order[f.flag_code],
                                        f.course_code or ""))


def flag_prompt(flags: list[RiskFlagOut]) -> str:
    lines = [f"F{n} | {f.flag_code} | {f.severity} | {f.message}" for n, f in enumerate(flags, start=1)]
    return ("These risk flags were raised by rules for a student's draft schedule. For each flag write a plain "
            "one-sentence explanation for the student's advisor (at most 25 words) and one suggested advisor action "
            "(at most 20 words). Use only the facts given; do not invent numbers or names. "
            "Format exactly, one line per flag: F<number>: <explanation> || <action>\n" + "\n".join(lines))


def apply_wording(flags: list[RiskFlagOut], text: str, prompt: str) -> list[int]:
    """Put the LLM's explanation and action on each flag it worded well; returns the flag numbers kept as template."""
    wording: dict[int, tuple[str, str]] = {}
    for line in text.replace("*", "").splitlines():
        m = _LINE.match(line.strip())
        if m and int(m.group(1)) not in wording:
            wording[int(m.group(1))] = (m.group(2).strip(), m.group(3).strip())
    rejected = []
    for n, flag in enumerate(flags, start=1):
        message, action = wording.get(n, ("", ""))
        if (len(message.split()) < 3 or len(action.split()) < 3 or "|" in message + action
                or ungrounded_numbers(message + " " + action, prompt)):
            rejected.append(n)
            continue
        flag.message, flag.suggested_action = message[:400], action[:300]
    return rejected


def _n(value: Decimal) -> str:
    return f"{Decimal(value).normalize():f}"
