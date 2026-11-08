"""Agent 3 - Schedule builder.

Builds the draft for a term the student chose; the term must be plannable today (term_registration).
    1. Core: remaining core courses DUE this term (recommended_term_seq <= the student's place in
       track_term_plan) and OFFERED in the term, earliest first, prerequisites checked by the rule engine, while
       the total fits under the fill limit. Core from later terms is never pulled forward. A new student whose
       first term is not a fall term gets the first-term core that is offered. Core items are locked.
    2. Electives: Agent 2's ranked suggestions, in rank order, until the total reaches the fill limit
       (rule_settings SCHEDULE_FILL = tracks.max_units_per_term = 54), never above it, and no more than the
       elective units the student still needs. The draft may end under 54; the rationale says so.
    3. Sections: no time clash (backtracking for core, greedy for electives); prefer seats, modality, time.
    4. Validate with the rule engine; on a blocking failure drop / swap the course and retry
       (rule_settings.SCHEDULE_MAX_RETRIES).
Load (D17): one draft, always filled toward 54 units. No lighter load for a low QPA (that is only a risk flag).
One LLM call writes ai_explanation (fallback: template). Saves schedules + schedule_items through the
schedule lifecycle service; an open draft is replaced only with replace=True (the student confirmed).
"""
import uuid
from decimal import Decimal

from pydantic import BaseModel

from app.agents.base import Agent, RunContext
from app.agents.requirements_agent import CoreStatus, DegreeAuditOut
from app.agents.core_plan import CoreCandidate, clashes_with, pick_core, preference_key, usable
from app.rules.engine import CandidateSchedule, CheckResult, ScheduleItemIn
from app.services.terms import TermNotPlannable, plannable_status
from app.tools.catalog import SectionOut
from app.services.schedule_lifecycle import DraftExists, ScheduleItemDraft
from app.tools.records import SuggestionOut
from app.tools.student import ProgramRequirements, StudentHistory



class DraftItem(BaseModel):
    course_code: str
    title: str
    section_id: str
    section_code: str
    session_code: str
    days_times: str
    units: Decimal
    item_type: str  # core | elective
    is_locked: bool
    is_conditional: bool = False
    needs_approval: bool = False
    match_score: float | None = None
    reason: str


class ScheduleDraftOut(BaseModel):
    student_id: str
    term_code: str
    term_label: str = ""
    items: list[DraftItem]
    total_units: Decimal
    core_units: Decimal
    elective_units: Decimal
    target_units: Decimal  # the fill limit (SCHEDULE_FILL)
    max_units: int
    load_rationale: str
    skipped: dict[str, str]  # course_code -> why it is not in the draft
    checks: list[CheckResult] = []
    valid: bool = False
    schedule_id: uuid.UUID | None = None
    version: int | None = None
    ai_explanation: str = ""


class _ScheduleFacts(BaseModel):
    term_code: str
    term_label: str
    audit: DegreeAuditOut
    program: ProgramRequirements
    history: StudentHistory
    core: list[CoreStatus]  # remaining, offered, eligible; earliest recommended term first
    suggestions: list[SuggestionOut]
    sections: dict[str, list[SectionOut]]  # usable sections (offered, seats), best first
    titles: dict[str, str]
    skipped: dict[str, str]  # found while gathering facts
    dropped: dict[str, str] = {}  # removed after a rule-engine failure (filled by check on a retry)
    low_seats: int = 3
    fill_rule: dict = {}  # rule_settings SCHEDULE_FILL


class ScheduleAgent(Agent[ScheduleDraftOut]):
    """run(student_id=..., term_code='S27'). Texts name the term as "Spring 2027". Needs a current degree audit (Agent 1) and suggestions (Agent 2)."""

    name = "schedule"
    tools = frozenset({"get_term_registration", "get_current_audit", "get_elective_suggestions",
                       "get_student_history", "get_program_requirements", "get_rule_settings",
                       "get_active_courses", "get_term_sections", "check_course_eligibility", "validate_schedule",
                       "get_open_schedule", "save_schedule"})
    max_attempts = 5  # replaced by rule_settings.SCHEDULE_MAX_RETRIES in plan()

    # ---------- plan ----------

    def plan(self, ctx: RunContext) -> _ScheduleFacts:
        term = ctx.inputs["term_code"]
        term_info = plannable_status(self.call_tool("get_term_registration", ctx.today), term, ctx.today)  # refuses closed terms
        open_draft = self.call_tool("get_open_schedule", ctx.student_id, term)
        if open_draft is not None and open_draft.status == "draft" and not ctx.inputs.get("replace"):
            raise DraftExists(open_draft.schedule_id, term)  # the student has not confirmed replacing it
        audit_json = self.call_tool("get_current_audit", ctx.student_id)
        if audit_json is None:
            raise ValueError("No current degree audit: run the requirements agent first")
        audit = DegreeAuditOut.model_validate(audit_json)
        history = self.call_tool("get_student_history", ctx.student_id)
        program = self.call_tool("get_program_requirements", history.track_code)
        settings = self.call_tool("get_rule_settings")
        self.max_attempts = int(settings.get("SCHEDULE_MAX_RETRIES", {}).get("n", self.max_attempts))
        low_seats = int(settings.get("LOW_SEATS", {}).get("seats_left_below", 3))
        suggestions = self.call_tool("get_elective_suggestions", ctx.student_id, term)
        offered = {c.course_code: c for c in self.call_tool("get_active_courses", term)}

        skipped: dict[str, str] = {}
        remaining = sorted((c for c in audit.core if c.status == "remaining"),
                           key=lambda c: (c.recommended_term_seq or 99, c.course_code))
        for c in remaining:
            if c.course_code not in offered and (c.recommended_term_seq or 0) <= audit.plan_position:
                skipped[c.course_code] = f"Core course not offered in {term}; take it in a later term"
        for c in remaining:  # D17: never pull core forward from a later term of the plan
            if c.course_code in offered and (c.recommended_term_seq or 0) > audit.plan_position:
                skipped[c.course_code] = (f"Core course for a later term (recommended term {c.recommended_term_seq}); "
                                          f"not pulled forward")
        core = [c for c in remaining if c.course_code in offered
                and (c.recommended_term_seq or 0) <= audit.plan_position]
        blockers = self.call_tool("check_course_eligibility", ctx.student_id, term,
                                  [c.course_code for c in core], ctx.today)
        for code, problems in blockers.items():
            if problems:
                skipped[code] = f"Core course not possible yet: {_short(problems[0].detail)}"
        core = [c for c in core if not blockers.get(c.course_code)]

        found = self.call_tool("get_term_sections", term,
                               [c.course_code for c in core] + [s.course_code for s in suggestions])
        sections = {code: sorted((x for x in secs if usable(x)), key=lambda x: preference_key(x, history, low_seats))
                    for code, secs in found.items()}
        titles = {c.course_code: c.title for c in audit.core} | {s.course_code: s.title for s in suggestions}
        return _ScheduleFacts(term_code=term, term_label=term_info.term_label, audit=audit, program=program, history=history, core=core,
                              suggestions=suggestions, sections=sections, titles=titles, skipped=skipped,
                              low_seats=low_seats, fill_rule=dict(settings.get("SCHEDULE_FILL") or {}))

    # ---------- decide ----------

    def decide(self, ctx: RunContext, facts: _ScheduleFacts) -> ScheduleDraftOut:
        audit, program = facts.audit, facts.program
        target = _fill_limit(facts.fill_rule, program)  # 54: fill to it, never above
        skipped = dict(facts.skipped) | facts.dropped
        core = [c for c in facts.core if c.course_code not in facts.dropped]
        plan = pick_core([CoreCandidate(c.course_code, c.units, c.recommended_term_seq) for c in core],
                         facts.sections, target)
        skipped |= plan.skipped
        chosen = dict(plan.chosen)
        by_code = {c.course_code: c for c in core}
        items = [_item(facts, pick.course_code, chosen[pick.course_code], pick.units, "core",
                       reason=_core_reason(by_code[pick.course_code], facts))
                 for pick in plan.picks]
        core_units = plan.units

        elective_cap = min(target - core_units, audit.elective_units_left)
        elective_units = Decimal(0)
        taken = {i.course_code for i in items}
        for s in facts.suggestions:
            units = s.units_default or Decimal(0)
            if s.course_code in taken or s.course_code in facts.dropped or units <= 0:
                continue
            if elective_units + units > elective_cap:
                if elective_units < elective_cap:  # room left, but not for this course
                    skipped[s.course_code] = (f"Ranked elective of {_n(units)} units; only "
                                              f"{_n(elective_cap - elective_units)} units were left under the "
                                              f"{_n(target)}-unit limit")
                continue
            section = next((sec for sec in facts.sections.get(s.course_code, [])
                            if not clashes_with(sec, list(chosen.values()))), None)
            if section is None:
                skipped[s.course_code] = "Every section clashes with the courses already chosen"
                continue
            chosen[s.course_code] = section
            items.append(_item(facts, s.course_code, section, units, "elective", reason=s.reason or "",
                               match_score=s.final_score))
            elective_units += units

        total = core_units + elective_units
        return ScheduleDraftOut(
            student_id=audit.student_id, term_code=facts.term_code, term_label=facts.term_label, items=items, total_units=total,
            core_units=core_units, elective_units=elective_units, target_units=target,
            max_units=program.max_units_per_term,
            load_rationale=_load_rationale(facts, target, core_units, elective_units, elective_cap),
            skipped=skipped)

    # ---------- check ----------

    def check(self, ctx: RunContext, facts: _ScheduleFacts, decision: ScheduleDraftOut) -> list[str]:
        candidate = CandidateSchedule(student_id=decision.student_id, term_code=decision.term_code, items=[
            ScheduleItemIn(course_code=i.course_code, section_id=i.section_id, units=i.units)
            for i in decision.items])
        results: list[CheckResult] = self.call_tool("validate_schedule", candidate, ctx.today)
        decision.checks = results
        for item in decision.items:
            mine = [r for r in results if r.course_code == item.course_code]
            item.is_conditional = any(r.flag == "conditional" for r in mine)
            item.needs_approval = any(r.flag in ("needs_approval", "needs_petition") for r in mine)
        blocking = [r for r in results if r.blocking]
        decision.valid = not blocking
        for r in blocking:  # swap out what failed; the next attempt picks the next course / section
            code = r.course_code
            if code is None and r.code == "UNIT_LIMIT":
                code = next((i.course_code for i in reversed(decision.items) if i.item_type == "elective"), None)
            if code is None and r.code == "OFFERED":
                raise TermNotPlannable(r.detail)
            if code is not None:
                facts.dropped[code] = f"Removed after a rule check: {r.detail}"
        return [f"{r.code} {r.course_code or ''}: {r.detail}".strip() for r in blocking]

    # ---------- save + explain ----------

    def save(self, ctx: RunContext, facts: _ScheduleFacts, decision: ScheduleDraftOut) -> None:
        # The run's one LLM call happens here so the explanation is stored with the schedule.
        prompt, fallback = explanation_prompt(decision, facts), template_explanation(decision, facts)
        text = self.ask_llm(prompt, fallback=fallback, max_tokens=200)
        decision.ai_explanation = self.guard_llm_text(text, prompt, fallback)  # no invented numbers
        saved = self.call_tool("save_schedule", ctx.student_id, decision.term_code, ctx.run_id, [
            ScheduleItemDraft(course_code=i.course_code, section_id=i.section_id, units=i.units,
                              item_type=i.item_type, is_locked=i.is_locked, is_conditional=i.is_conditional,
                              needs_approval=i.needs_approval, match_score=i.match_score, reason=i.reason)
            for i in decision.items], decision.ai_explanation, decision.load_rationale,
            bool(ctx.inputs.get("replace")))
        decision.schedule_id, decision.version = saved.schedule_id, saved.version
        ctx.schedule_id = saved.schedule_id

    def explain(self, ctx: RunContext, facts: _ScheduleFacts, decision: ScheduleDraftOut) -> str:
        return decision.ai_explanation


# ---------- explanation text ----------

def template_explanation(d: ScheduleDraftOut, facts: _ScheduleFacts) -> str:
    core = [i for i in d.items if i.item_type == "core"]
    electives = [i for i in d.items if i.item_type == "elective"]
    parts = [f"The draft for {d.term_label or d.term_code} has {_n(d.total_units)} units (limit {d.max_units})."]
    if core:
        parts.append(f"{len(core)} core courses offered in {d.term_label or d.term_code} are locked, earliest recommended term "
                     f"first, with prerequisites checked: {', '.join(i.course_code for i in core)}.")
    if electives:
        basis = facts.suggestions[0].basis if facts.suggestions else "interests"
        how = "the student's confirmed interests" if basis == "interests" else "popularity with past students"
        parts.append(f"Electives were added by match score ({how}): "
                     f"{', '.join(f'{i.course_code} {i.title}' for i in electives)}.")
    parts.append(d.load_rationale)
    flags = [i.course_code for i in d.items if i.is_conditional or i.needs_approval]
    parts.append("The rule engine found no blocking problems" + (
        f"; flagged for the advisor: {', '.join(flags)}." if flags else "."))
    return " ".join(parts)


def explanation_prompt(d: ScheduleDraftOut, facts: _ScheduleFacts) -> str:
    lines = [f"- {i.course_code} {i.title}, {_n(i.units)} units, {i.item_type}"
             + (" (locked)" if i.is_locked else "") + (f": {i.reason}" if i.item_type == "elective" else "")
             for i in d.items]
    skipped = "; ".join(f"{c}: {why}" for c, why in list(d.skipped.items())[:5]) or "none"
    return (f"Explain in at most 4 short sentences how this draft schedule for {d.term_label or d.term_code} was built. "
            f"Use only these facts and do not invent any.\nCourses:\n" + "\n".join(lines) +
            f"\nTotal: {_n(d.total_units)} units ({_n(d.core_units)} core + {_n(d.elective_units)} elective), "
            f"limit {d.max_units}.\nLoad: {d.load_rationale}\n"
            f"Not included: {skipped}\nAll rule checks passed.")


def _fill_limit(rule: dict, program: ProgramRequirements) -> Decimal:
    """SCHEDULE_FILL fill_to (never above never_exceed); a 'tracks.<column>' pointer or a number."""
    def resolve(spec) -> Decimal:
        if isinstance(spec, str) and spec.startswith("tracks."):
            return Decimal(getattr(program, spec.removeprefix("tracks.")))
        return Decimal(str(spec))
    if "fill_to" not in rule:
        raise ValueError("rule_settings SCHEDULE_FILL is missing")
    limit = resolve(rule["fill_to"])
    return min(limit, resolve(rule["never_exceed"])) if "never_exceed" in rule else limit


def _load_rationale(facts: _ScheduleFacts, target: Decimal, core_units: Decimal, elective_units: Decimal,
                    elective_cap: Decimal) -> str:
    """"54 of 54 units: 42 core + 12 electives", then why the draft is below the limit, if it is."""
    total = core_units + elective_units
    text = f"{_n(total)} of {_n(target)} units: {_n(core_units)} core + {_n(elective_units)} electives."
    a = facts.audit
    if a.units_completed == 0 and a.units_in_progress == 0:
        text += (f" First term: only the term-1 core courses that are offered in {facts.term_label} are included; "
                 f"prerequisites are respected.")
    if total < target:
        if elective_cap < target - core_units:
            text += (f" No more required this term: all core due this term is included, and the student needs only "
                     f"{_n(a.elective_units_left)} more elective units.")
        else:
            text += f" No further eligible elective fits without a time clash or the {_n(target)}-unit limit."
    return text


def _core_reason(c: CoreStatus, facts: _ScheduleFacts) -> str:
    plan = f"{facts.program.track_code} core, recommended term {c.recommended_term_seq}"
    due = (c.recommended_term_seq or 0) <= facts.audit.plan_position
    return f"{plan}." if due else f"{plan}; offered now, so taken early."


# ---------- sections ----------

def _item(facts: _ScheduleFacts, code: str, section: SectionOut, units: Decimal, item_type: str, reason: str,
          match_score: float | None = None) -> DraftItem:
    return DraftItem(course_code=code, title=facts.titles.get(code, code), section_id=section.section_id,
                     section_code=section.section_code, session_code=section.session_code,
                     days_times=days_times(section), units=units, item_type=item_type,
                     is_locked=item_type == "core", match_score=match_score, reason=reason)


def days_times(s: SectionOut) -> str:
    if s.is_time_tba or not s.meetings:
        return "TBA"
    groups: dict[tuple, str] = {}
    for m in sorted(s.meetings, key=lambda m: "MTWRFSU".index(m.day)):
        groups[(m.start, m.end)] = groups.get((m.start, m.end), "") + m.day
    return "; ".join(f"{days} {start:%H:%M}-{end:%H:%M}" for (start, end), days in groups.items())


def _short(text: str, limit: int = 120) -> str:
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def _n(units: Decimal) -> str:
    return f"{Decimal(units).normalize():f}"
