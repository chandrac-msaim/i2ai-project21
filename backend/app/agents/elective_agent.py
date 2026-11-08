"""Agent 2 - Electives (interest match, D16), and the InterestExtractor that suggests interests from documents.

ElectiveAgent: eligible graduate electives offered in the term with seats, scored
    score = w_similarity x similarity + w_popularity x popularity + w_fit x fit
with every weight, the match threshold, the diversity limits and the fit weights read from rule_settings
(app.agents.elective_scoring). Similarity is computed per confirmed interest (the best interest is the matched
interest); fit = preferred time of day + preferred modality + no clash with the locked core sections.
group_code 'interest_match' when similarity >= ELECTIVE_MATCH_THRESHOLD, else 'popular'. Diversity: the best course
for each interest first, then fill by score skipping near-duplicates. Flags: time_clash (kept, not removed),
few_seats. No confirmed interests: popularity + fit only, everything 'popular'.
Excluded: the track's core courses, courses completed / in progress / waived, courses already in the draft,
courses needing special approval (the student can still add those by hand). Eligibility (prerequisites, offering,
level) comes from the rule engine. One LLM call writes a one-line reason for the top courses; fallback: template.

InterestExtractor: one LLM call reads the resume and undergrad text and returns up to 5 short labels, saved
as status 'suggested' (never 'confirmed'). Fallback: keyword match (app.agents.interest_keywords).
"""
import re
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.agents import elective_scoring as scoring
from app.agents.base import Agent, RunContext, ungrounded_numbers
from app.agents.core_plan import CoreCandidate, pick_core, preference_key, usable
from app.agents.interest_keywords import keyword_labels
from app.agents.requirements_agent import plan_position
from app.tools.catalog import ActiveCourse, SectionOut
from app.tools.popularity import PopularityOut
from app.tools.records import SuggestionIn
from app.tools.student import DocumentOut, InterestLabel, InterestOut, ProgramRequirements, StudentHistory

_REASON_LINE = re.compile(r"(\d{2}-\d{3})\W{0,4}\s*[:\-–—]\s*(.+)")
_ALL = 1000  # search limit: similarity for every candidate course


# ---------- ElectiveAgent ----------

class Suggestion(BaseModel):
    rank: int
    course_code: str
    title: str
    units: Decimal | None
    similarity: float | None
    popularity: float
    fit: float = 0.0
    final_score: float
    group_code: str = "popular"  # interest_match | popular
    matched_interest: str | None = None
    flags: list[str] = []  # time_clash | few_seats
    reason: str = ""


class ElectiveSuggestionsOut(BaseModel):
    student_id: str
    term_code: str
    term_label: str = ""
    basis: str  # interests | popularity
    weights: dict[str, float]
    interests: list[str]
    excluded: list[str]
    ineligible: dict[str, str]  # course_code -> first blocking rule detail
    suggestions: list[Suggestion]


class _ElectiveFacts(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    history: StudentHistory
    program: ProgramRequirements
    interests: list[InterestOut]
    settings: scoring.Settings
    excluded: set[str]
    candidates: dict[str, ActiveCourse]
    interest_sims: dict[str, list[float]]  # course_code -> similarity per interest
    popularity: dict[str, PopularityOut]
    sections: dict[str, list[SectionOut]]  # usable sections of each candidate
    core_sections: list[SectionOut]  # the locked core's sections (the same selection Agent 3 makes)
    similar: dict[tuple[str, str], float]  # course pair (sorted) -> embedding similarity
    ineligible: dict[str, str]


class ElectiveAgent(Agent[ElectiveSuggestionsOut]):
    """run(student_id=..., term_code='S27', exclude=[draft courses], limit=None (default top_n), explain_top=5)"""

    name = "electives"
    tools = frozenset({"get_student_history", "get_program_requirements", "get_course_waivers",
                       "get_confirmed_interests", "get_active_courses", "get_term_sections",
                       "search_course_content", "course_similarities", "rank_courses_by_popularity",
                       "check_course_eligibility", "get_rule_settings", "save_elective_suggestions",
                       "get_term_label"})
    max_attempts = 1

    def plan(self, ctx: RunContext) -> _ElectiveFacts:
        term = ctx.inputs["term_code"]
        history = self.call_tool("get_student_history", ctx.student_id)
        program = self.call_tool("get_program_requirements", history.track_code)
        waived = {w.course_code for w in self.call_tool("get_course_waivers", ctx.student_id)
                  if w.status == "approved"}
        interests = [i for i in self.call_tool("get_confirmed_interests", ctx.student_id) if i.embedding]
        rules = self.call_tool("get_rule_settings")
        settings = scoring.Settings.from_rule_settings(rules)

        core_codes = {c.course_code for c in program.core}
        taken = set(history.completed) | set(history.in_progress) | waived
        excluded = core_codes | taken | set(ctx.inputs.get("exclude") or ())
        active = {c.course_code: c for c in self.call_tool("get_active_courses", term)}
        candidates = {code: c for code, c in active.items()
                      if c.level == "graduate" and not c.needs_approval and code not in excluded
                      and (c.seats_left is None or c.seats_left > 0) and (c.units_default or 0) > 0}

        # the locked core (remaining, offered in the term, eligible), to flag electives that clash with it
        position = plan_position(history)
        core_pool = [c for c in program.core if c.course_code not in taken and c.course_code in active
                     and (c.recommended_term_seq or 0) <= position]  # due this term only, as in Agent 3
        pool = sorted(candidates)
        blockers = self.call_tool("check_course_eligibility", ctx.student_id, term,
                                  pool + [c.course_code for c in core_pool], ctx.today)
        ineligible = {code: problems[0].detail for code, problems in blockers.items()
                      if problems and code in candidates}
        core_ok = [c for c in core_pool if not blockers.get(c.course_code)]
        pool = [code for code in pool if code not in ineligible]
        found = self.call_tool("get_term_sections", term, pool + [c.course_code for c in core_ok])
        low_seats = int(rules.get("LOW_SEATS", {}).get("seats_left_below", 3))
        sections = {code: sorted((x for x in secs if usable(x)), key=lambda x: preference_key(x, history, low_seats))
                    for code, secs in found.items()}
        core_plan = pick_core([CoreCandidate(c.course_code, c.units_default or Decimal(0), c.recommended_term_seq)
                               for c in core_ok], sections, Decimal(program.max_units_per_term))

        sims = {code: [0.0] * len(interests) for code in pool}
        for idx, interest in enumerate(interests):  # one search per interest: similarity of every candidate
            for m in self.call_tool("search_course_content", [interest.embedding], term,
                                    exclude=sorted(excluded), limit=_ALL):
                if m.course_code in sims:
                    sims[m.course_code][idx] = m.similarity
        popularity = {p.course_code: p for p in self.call_tool("rank_courses_by_popularity", history.track_code,
                                                               course_codes=pool)}
        similar: dict[tuple[str, str], float] = {}
        if len(pool) > 1:
            similar = {(p.course_a, p.course_b): p.similarity for p in self.call_tool("course_similarities", pool)}
        return _ElectiveFacts(
            history=history, program=program, interests=interests, settings=settings, excluded=excluded,
            candidates=candidates, interest_sims=sims, popularity=popularity,
            sections={code: sections.get(code, []) for code in pool}, similar=similar, ineligible=ineligible,
            core_sections=list(core_plan.chosen.values()))

    def decide(self, ctx: RunContext, facts: _ElectiveFacts) -> ElectiveSuggestionsOut:
        s, h = facts.settings, facts.history
        basis = "interests" if facts.interests else "popularity"
        cands = []
        for code, sims in facts.interest_sims.items():
            fit, flags = scoring.fit_and_flags(facts.sections.get(code, []), facts.core_sections,
                                               h.preferred_time_of_day, h.preferred_modality, s)
            pop = facts.popularity[code].score if code in facts.popularity else 0.0
            cands.append(scoring.Candidate(course_code=code, sims=sims, popularity=pop, fit=fit, flags=flags))
        top_n = int(ctx.inputs.get("limit") or s.top_n)
        picks = scoring.select(cands, len(facts.interests), facts.similar, s, top_n)
        suggestions = []
        for rank, p in enumerate(picks, start=1):
            course = facts.candidates[p.course_code]
            suggestion = Suggestion(
                rank=rank, course_code=p.course_code, title=course.title, units=course.units_default,
                similarity=p.similarity, popularity=round(p.popularity, 3), fit=round(p.fit, 3),
                final_score=round(p.final_score, 3), group_code=p.group_code, flags=list(p.flags),
                matched_interest=facts.interests[p.interest].label if p.interest is not None else None)
            suggestion.reason = template_reason(suggestion, h.track_code)
            suggestions.append(suggestion)
        weights = ({"similarity": s.w_similarity, "popularity": s.w_popularity, "fit": s.w_fit}
                   if basis == "interests" else {"popularity": s.w_popularity, "fit": s.w_fit})
        return ElectiveSuggestionsOut(
            student_id=h.student_id, term_code=ctx.inputs["term_code"],
            term_label=self.call_tool("get_term_label", ctx.inputs["term_code"]), basis=basis, weights=weights,
            interests=[i.label for i in facts.interests], excluded=sorted(facts.excluded),
            ineligible=facts.ineligible, suggestions=suggestions)

    def save(self, ctx: RunContext, facts: _ElectiveFacts, decision: ElectiveSuggestionsOut) -> None:
        # The run's one LLM call happens here so the reasons are stored with the suggestions.
        top = decision.suggestions[:int(ctx.inputs.get("explain_top") or 5)]
        if top:
            fallback = "\n".join(f"{s.course_code}: {s.reason}" for s in top)
            prompt = reason_prompt(top, decision)
            text = self.ask_llm(prompt, fallback=fallback, max_tokens=60 * len(top))
            reasons = parse_reasons(text, {s.course_code for s in top})
            kept = {c: r for c, r in reasons.items() if usable_reason(r, prompt)}
            if len(kept) < len(reasons):  # the template reason stays for the rejected ones
                ctx.tool_calls.append({"tool": "llm_guard", "rejected": True,
                                       "reason": f"echoed or invented reasons: {sorted(set(reasons) - set(kept))}"})
            for code, reason in kept.items():
                next(s for s in top if s.course_code == code).reason = reason
        self.call_tool("save_elective_suggestions", ctx.student_id, decision.term_code, ctx.run_id, [
            SuggestionIn(course_code=s.course_code, rank=s.rank, similarity=s.similarity, popularity=s.popularity,
                         fit=s.fit, final_score=s.final_score, basis=decision.basis,
                         matched_interest=s.matched_interest, group_code=s.group_code, flags=s.flags,
                         reason=s.reason)
            for s in decision.suggestions])

    def explain(self, ctx: RunContext, facts: _ElectiveFacts, decision: ElectiveSuggestionsOut) -> str:
        if not decision.suggestions:
            return f"No eligible electives with seats were found for {decision.term_label}."
        score = " + ".join(f"{k} x {v}" for k, v in sorted(decision.weights.items()))
        how = (f"matched to the confirmed interests ({', '.join(decision.interests)}), score = {score}"
               if decision.basis == "interests" else
               f"ranked by popularity and fit because the student has no confirmed interests, score = {score}")
        groups = {g: sum(s.group_code == g for s in decision.suggestions) for g in ("interest_match", "popular")}
        flagged = sum(bool(s.flags) for s in decision.suggestions)
        return (f"{len(decision.suggestions)} eligible electives for {decision.term_label}, {how}. "
                f"{groups['interest_match']} match an interest, {groups['popular']} are popular picks"
                f"{f', {flagged} flagged' if flagged else ''}. "
                f"{len(decision.ineligible)} candidates were left out by the rule engine. "
                f"Top: {decision.suggestions[0].course_code} {decision.suggestions[0].title}.")


def template_reason(s: Suggestion, track_code: str) -> str:
    if s.group_code == "interest_match":
        text = f"Matches your interest in {s.matched_interest} (similarity {s.similarity:.2f})."
    elif s.matched_interest:
        text = (f"Popular with {track_code} students (popularity {s.popularity:.2f}); "
                f"closest to your interest in {s.matched_interest} (similarity {s.similarity:.2f}).")
    else:
        text = f"Popular with {track_code} students (popularity {s.popularity:.2f})."
    if "time_clash" in s.flags:
        text += " Clashes with a core course, so check the sections."
    if "few_seats" in s.flags:
        text += " Few seats left."
    return text


def reason_prompt(top: list[Suggestion], decision: ElectiveSuggestionsOut) -> str:
    lines = [f"{s.course_code} {s.title}" + (f" | matched interest: {s.matched_interest}"
                                              if s.group_code == "interest_match"
                                              else f" | popularity {s.popularity:.2f}") for s in top]
    context = (f"The student's interests: {', '.join(decision.interests)}." if decision.interests
               else "The student has not confirmed any interests; these are popular courses.")
    return (f"{context}\nFor each course write one line, at most 20 words, on why it suits the student. "
            f"Use only these facts. Format exactly: <course code>: <reason>\n" + "\n".join(lines))


def usable_reason(reason: str, prompt: str) -> bool:
    """False for a reason that only repeats the prompt ("matched interest: X") or invents numbers."""
    if re.search(r"matched interest|popularity \d|\|", reason, re.IGNORECASE) or len(reason.split()) < 3:
        return False
    return not ungrounded_numbers(reason, prompt)


def parse_reasons(text: str, codes: set[str]) -> dict[str, str]:
    """'95-xxx: reason' lines -> {code: reason}, only for known codes; markdown and quotes stripped."""
    reasons: dict[str, str] = {}
    for line in text.splitlines():
        m = _REASON_LINE.search(line.replace("*", ""))
        if m and m.group(1) in codes and m.group(1) not in reasons:
            reason = m.group(2).strip().strip('"').strip()
            if reason:
                reasons[m.group(1)] = reason[:240]
    return reasons


# ---------- InterestExtractor ----------

class SuggestedInterest(BaseModel):
    label: str
    source: str  # resume | undergrad


class InterestSuggestionsOut(BaseModel):
    student_id: str
    method: str  # llm | keywords | none
    found: list[str]  # every label read from the documents
    new: list[SuggestedInterest]  # saved as status 'suggested'
    skipped: list[str]  # the student already has these (any status, removed included)


class _InterestFacts(BaseModel):
    documents: list[DocumentOut]
    existing: list[InterestLabel]


class InterestExtractor(Agent[InterestSuggestionsOut]):
    """run(student_id=...). Part of Agent 2, so runs are logged as agent 'electives'."""

    name = "electives"
    tools = frozenset({"get_student_documents", "get_interest_labels", "save_suggested_interests"})
    max_attempts = 1  # decide() makes the LLM call, so it must not be retried
    max_labels = 5

    def plan(self, ctx: RunContext) -> _InterestFacts:
        return _InterestFacts(documents=self.call_tool("get_student_documents", ctx.student_id),
                              existing=self.call_tool("get_interest_labels", ctx.student_id))

    def decide(self, ctx: RunContext, facts: _InterestFacts) -> InterestSuggestionsOut:
        resume = _doc(facts.documents, "resume")
        undergrad = _doc(facts.documents, "undergrad_transcript")
        if not (resume or undergrad):
            return InterestSuggestionsOut(student_id=ctx.student_id, method="none", found=[], new=[], skipped=[])
        have = {i.label.lower() for i in facts.existing}
        fallback = "\n".join(keyword_labels(f"{resume}\n{undergrad}", skip=have, limit=self.max_labels))
        text = self.ask_llm(self._prompt(resume, undergrad, facts.existing), fallback=fallback, max_tokens=80)
        found = parse_labels(text, self.max_labels)
        default_source = "resume" if resume else "undergrad"
        new = [SuggestedInterest(label=label, source=label_source(label, resume, undergrad, default_source))
               for label in found if label.lower() not in have]
        return InterestSuggestionsOut(
            student_id=ctx.student_id, method="keywords" if ctx.llm and ctx.llm.used_fallback else "llm",
            found=found, new=new, skipped=[label for label in found if label.lower() in have])

    def save(self, ctx: RunContext, facts: _InterestFacts, decision: InterestSuggestionsOut) -> None:
        if decision.new:
            self.call_tool("save_suggested_interests", ctx.student_id, [(i.label, i.source) for i in decision.new])

    def explain(self, ctx: RunContext, facts: _InterestFacts, decision: InterestSuggestionsOut) -> str:
        if decision.method == "none":
            return "No resume or undergraduate text on file, so no interests were suggested."
        how = "the LLM" if decision.method == "llm" else "keyword matching (LLM unavailable)"
        new = ", ".join(i.label for i in decision.new) or "none new"
        return (f"Read the resume and undergraduate text with {how}. Suggested for the student to confirm: {new}."
                + (f" Already on file: {', '.join(decision.skipped)}." if decision.skipped else ""))

    def _prompt(self, resume: str, undergrad: str, existing: list[InterestLabel]) -> str:
        avoid = f"\nAlready listed, do not repeat: {', '.join(i.label for i in existing)}." if existing else ""
        return (f"Resume:\n{resume[:1500] or '(none)'}\n\nUndergraduate degree:\n{undergrad[:500] or '(none)'}\n\n"
                f"List up to {self.max_labels} short academic interests (2-4 words each) for choosing graduate "
                f"electives, one per line, no numbering, nothing else.{avoid}")


def parse_labels(text: str, limit: int = 5) -> list[str]:
    """LLM lines (or one comma-separated line) -> short, de-duplicated labels."""
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) == 1 and "," in lines[0]:
        lines = lines[0].split(",")
    labels: list[str] = []
    for line in lines:
        label = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).replace("*", "").strip().strip("\"'").strip()
        label = label.rstrip(".;")
        if not label or label.endswith(":") or len(label) > 40 or len(label.split()) > 5:
            continue
        label = label[0].upper() + label[1:]
        if label.lower() not in {x.lower() for x in labels}:
            labels.append(label)
    return labels[:limit]


def label_source(label: str, resume: str, undergrad: str, default: str) -> str:
    """'undergrad' only when every word of the label is found in the undergrad text and not all of them are in
    the resume (so "Computer science" -> undergrad, a loosely related "Cloud computing" does not); otherwise
    'resume' when there is a resume, else `default`."""
    stems = [w.lower()[:5] for w in re.findall(r"\w+", label) if len(w) >= 4]
    all_in_resume = bool(stems) and all(s in resume.lower() for s in stems)
    all_in_undergrad = bool(stems) and all(s in undergrad.lower() for s in stems)
    if all_in_undergrad and not all_in_resume:
        return "undergrad"
    return "resume" if resume else default


def _doc(documents: list[DocumentOut], doc_type: str) -> str:
    return next((d.text_content for d in documents if d.doc_type == doc_type), "")
