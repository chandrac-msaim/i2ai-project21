"""M4: Agents 1-3 (audit, electives + interest extractor, schedule) against the live dev database.

Agents run with commit=False and a FakeLLM; the db fixture rolls everything back afterwards.
"""
import re
from datetime import date

import httpx
import pytest
from sqlalchemy import func, select, text

from app.agents import (
    AgentError,
    ElectiveAgent,
    InterestExtractor,
    RequirementsAgent,
    ScheduleAgent,
    build_draft,
)
from app.services.schedule_lifecycle import DraftExists
from app.agents.base import ungrounded_numbers
from app.agents.elective_agent import parse_labels, parse_reasons
from app.agents.interest_keywords import keyword_labels
from app.config import get_settings
from app.llm import LLMClient
from app.models import (
    AgentRun,
    Course,
    DegreeAudit,
    ElectiveSuggestion,
    Schedule,
    ScheduleChange,
    Student,
    StudentInterest,
)
from app.rules.engine import CheckResult
from app.services.terms import TermNotPlannable
from app.tools import TOOLS
from tests.fakes import FakeLLM

PRIYA, DANIEL, ALEX = "S0056", "S0057", "S0126"
DEMO_DAY = date(2026, 11, 7)
TERM = "S27"


def _kw(llm=None) -> dict:
    return {"llm": llm or FakeLLM(), "today": DEMO_DAY, "commit": False}


def _fake_reasons(prompt: str) -> str:
    return "\n".join(f"**{code}**: Fake reason for {code}." for code in re.findall(r"^(\d{2}-\d{3}) ", prompt, re.M))


def _offered_in(db, term: str) -> set[str]:
    return set(db.scalars(text("SELECT DISTINCT course_code FROM sections WHERE term_code = :t AND status = 'offered'"),
                          {"t": term}))


# ---------- Agent 1: requirements ----------

def test_audit_priya(db):
    result = RequirementsAgent(db, **_kw()).run(student_id=PRIYA)
    a = result.output
    states = {s: sum(1 for c in a.core if c.status == s) for s in ("completed", "in_progress", "waived", "remaining")}
    assert states == {"completed": 4, "in_progress": 4, "waived": 0, "remaining": 11}
    assert a.units_completed == 24 and a.units_in_progress == 24 and a.units_remaining == 96
    assert a.elective_units_left == 18 and a.plan_position == 2
    assert a.next_term == TERM and a.projected_graduation_term == "M27"
    assert not a.below_min_qpa and "Projected graduation: M27" in result.explanation
    assert result.llm is None  # Agent 1 never waits on the LLM

    RequirementsAgent(db, **_kw()).run(student_id=PRIYA)  # a second audit replaces the current one
    audits = db.scalars(select(DegreeAudit).where(DegreeAudit.student_id == PRIYA)).all()
    assert sum(x.is_current for x in audits) == 1 and len(audits) >= 2


def test_audit_new_student_alex(db):
    a = RequirementsAgent(db, **_kw()).run(student_id=ALEX).output
    assert all(c.status == "remaining" for c in a.core) and a.units_remaining == 144
    assert a.plan_position == 1 and a.next_term == TERM and a.projected_graduation_term == "F27"


def test_audit_daniel_low_qpa(db):
    a = RequirementsAgent(db, **_kw()).run(student_id=DANIEL).output
    assert a.below_min_qpa and a.qpa < a.min_qpa


# ---------- Agent 2: electives ----------

def _core_codes(db, student: str) -> set[str]:
    track = db.get(Student, student).track_code
    return set(db.scalars(text("SELECT course_code FROM core_requirements WHERE track_code = :t"), {"t": track}))


def test_electives_priya_by_interests(db):
    llm = FakeLLM(_fake_reasons)
    result = ElectiveAgent(db, **_kw(llm)).run(student_id=PRIYA, term_code=TERM)
    out = result.output
    rules = {r: p for r, p in db.execute(text("SELECT rule_code, params FROM rule_settings")).tuples()}
    weights = rules["ELECTIVE_SCORE_WEIGHTS"]
    assert out.basis == "interests" and out.weights == {k: float(v) for k, v in weights.items()}
    assert set(out.weights) == {"similarity", "popularity", "fit"}
    assert out.interests == ["AI governance", "Financial services", "Model risk"]
    top_n = rules["ELECTIVE_DIVERSITY"]["top_n"]
    assert len(out.suggestions) == top_n == 8
    assert [s.rank for s in out.suggestions] == list(range(1, top_n + 1))
    scores = [s.final_score for s in out.suggestions]
    assert scores == sorted(scores, reverse=True)
    threshold = rules["ELECTIVE_MATCH_THRESHOLD"]["min_similarity"]
    for s in out.suggestions:
        assert s.final_score == pytest.approx(out.weights["similarity"] * s.similarity
                                              + out.weights["popularity"] * s.popularity
                                              + out.weights["fit"] * s.fit, abs=0.002)
        assert 0 <= s.fit <= 1 and s.matched_interest in out.interests
        assert s.group_code == ("interest_match" if s.similarity >= threshold else "popular")
        assert set(s.flags) <= {"time_clash", "few_seats"}
    assert {s.group_code for s in out.suggestions} == {"interest_match", "popular"}  # both groups appear

    # diversity: every interest is covered, and no two picks are near-duplicates
    assert {s.matched_interest for s in out.suggestions} == set(out.interests)
    codes = [s.course_code for s in out.suggestions]
    pairs = TOOLS["course_similarities"](db, codes)
    assert pairs and max(p.similarity for p in pairs) <= rules["ELECTIVE_DIVERSITY"]["max_similarity_between_picks"]

    codeset = set(codes)
    history = set(db.scalars(text("SELECT course_code FROM enrollments WHERE student_id = :s"), {"s": PRIYA}))
    assert not codeset & (_core_codes(db, PRIYA) | history)
    assert codeset <= _offered_in(db, TERM)
    assert all(db.get(Course, c).course_number >= 600 for c in codeset)
    assert not codeset & set(out.ineligible)

    # one LLM call: reasons for the top 5 come from it, the rest from the template
    assert len(llm.calls) == 1
    assert all(s.reason == f"Fake reason for {s.course_code}." for s in out.suggestions[:5])
    assert all(s.reason.startswith(("Matches your interest in", "Popular with")) for s in out.suggestions[5:])
    saved = {r.course_code: r for r in db.scalars(select(ElectiveSuggestion).where(
        ElectiveSuggestion.student_id == PRIYA, ElectiveSuggestion.term_code == TERM))}
    assert set(saved) == codeset and {r.run_id for r in saved.values()} == {result.run_id}
    for s in out.suggestions:  # fit, group_code, matched_interest and flags are stored
        row = saved[s.course_code]
        assert (float(row.fit), row.group_code, row.matched_interest, list(row.flags)) == (
            pytest.approx(s.fit, abs=0.001), s.group_code, s.matched_interest, s.flags)


def test_clashing_elective_is_flagged_not_removed(db):
    out = ElectiveAgent(db, **_kw()).run(student_id=PRIYA, term_code=TERM).output
    clashing = [s for s in out.suggestions if "time_clash" in s.flags]
    assert clashing, "Priya's S27 core should clash with at least one of her ranked electives"
    assert all("core course" in s.reason for s in clashing)
    # the same course really has no clash-free section against the core Agent 3 locks in
    draft = _build(db, PRIYA).schedule.output
    in_draft = {i.course_code for i in draft.items}
    assert not {s.course_code for s in clashing} & in_draft
    first = clashing[0]  # the best-ranked clashing course is tried first and skipped for its clash
    assert "clashes" in draft.skipped[first.course_code]


def test_electives_exclude_draft_courses(db):
    first = ElectiveAgent(db, **_kw()).run(student_id=PRIYA, term_code=TERM).output.suggestions[0].course_code
    again = ElectiveAgent(db, **_kw()).run(student_id=PRIYA, term_code=TERM, exclude=[first]).output
    assert first not in {s.course_code for s in again.suggestions} and first in again.excluded


def test_electives_popularity_without_confirmed_interests(db):
    no_interests = db.scalar(text(
        "SELECT s.student_id FROM students s WHERE s.status = 'active' AND s.entry_term = 'F26' AND NOT EXISTS "
        "(SELECT 1 FROM student_interests i WHERE i.student_id = s.student_id AND i.status = 'confirmed') "
        "ORDER BY 1 LIMIT 1"))
    for student in (ALEX, no_interests):
        out = ElectiveAgent(db, **_kw(LLMClient(provider="template"))).run(student_id=student, term_code=TERM).output
        assert out.basis == "popularity" and out.suggestions
        assert set(out.weights) == {"popularity", "fit"}
        w = out.weights
        assert all(s.similarity is None and s.matched_interest is None for s in out.suggestions)
        assert all(s.final_score == pytest.approx((w["popularity"] * s.popularity + w["fit"] * s.fit)
                                                  / (w["popularity"] + w["fit"]), abs=0.002)
                   for s in out.suggestions)
        assert {s.group_code for s in out.suggestions} == {"popular"}  # only 'popular', never 'interest_match'
        assert all(s.reason.startswith("Popular with") for s in out.suggestions)  # template fallback
        rows = db.execute(select(ElectiveSuggestion.basis, ElectiveSuggestion.group_code,
                                 ElectiveSuggestion.matched_interest)
                          .where(ElectiveSuggestion.student_id == student)).tuples().all()
        assert set(rows) == {("popularity", "popular", None)}


def test_electives_template_reason_uses_matched_interest(db):
    out = ElectiveAgent(db, **_kw(LLMClient(provider="template"))).run(student_id=DANIEL, term_code=TERM).output
    assert out.basis == "interests"
    for s in out.suggestions:
        if s.group_code == "interest_match":
            assert s.reason.startswith(f"Matches your interest in {s.matched_interest} (similarity {s.similarity:.2f})")
        else:  # below the threshold: popular, with the closest interest named
            assert s.reason.startswith("Popular with") and f"closest to your interest in {s.matched_interest}" in s.reason


def test_elective_settings_come_from_rule_settings(db):
    """Changing the weights in rule_settings changes the scores: nothing is hard-coded."""
    before = ElectiveAgent(db, **_kw()).run(student_id=PRIYA, term_code=TERM).output
    db.execute(text("UPDATE rule_settings SET params = '{\"similarity\": 0.5, \"popularity\": 0.4, \"fit\": 0.1}' "
                    "WHERE rule_code = 'ELECTIVE_SCORE_WEIGHTS'"))
    after = ElectiveAgent(db, **_kw()).run(student_id=PRIYA, term_code=TERM).output
    assert after.weights == {"similarity": 0.5, "popularity": 0.4, "fit": 0.1} != before.weights
    assert {s.course_code: s.final_score for s in after.suggestions} != {
        s.course_code: s.final_score for s in before.suggestions}
    db.execute(text("DELETE FROM rule_settings WHERE rule_code = 'ELECTIVE_FIT'"))
    with pytest.raises(AgentError, match="ELECTIVE_FIT"):
        ElectiveAgent(db, **_kw()).run(student_id=PRIYA, term_code=TERM)


def test_parse_reasons():
    text_ = "Here you go:\n1. **95-841**: Covers AI governance.\n94-847 - Responsible AI.\n99-999: unknown\n95-841: dup"
    assert parse_reasons(text_, {"95-841", "94-847"}) == {"95-841": "Covers AI governance.",
                                                          "94-847": "Responsible AI."}


# ---------- InterestExtractor ----------

def _interest_counts(db, student: str) -> dict[str, int]:
    return dict(db.execute(select(StudentInterest.status, func.count()).where(StudentInterest.student_id == student)
                           .group_by(StudentInterest.status)).tuples().all())


def test_interest_extractor_llm_labels_are_suggested_only(db):
    before = _interest_counts(db, ALEX)
    llm = FakeLLM("1. AI agents\n2. Backend APIs\n- Python development\n**Cloud computing**")
    result = InterestExtractor(db, **_kw(llm)).run(student_id=ALEX)
    out = result.output
    assert out.method == "llm" and len(llm.calls) == 1
    assert out.found == ["AI agents", "Backend APIs", "Python development", "Cloud computing"]
    assert out.skipped == ["AI agents"]  # Alex already has it (suggested)
    assert [(i.label, i.source) for i in out.new] == [("Backend APIs", "resume"), ("Python development", "resume"),
                                                      ("Cloud computing", "resume")]
    after = _interest_counts(db, ALEX)
    assert after.get("suggested", 0) == before.get("suggested", 0) + 3
    assert after.get("confirmed", 0) == before.get("confirmed", 0) == 0  # never confirmed
    _assert_no_student_name(db, ALEX, llm)


def test_interest_extractor_keyword_fallback(db):
    out = InterestExtractor(db, **_kw(LLMClient(provider="template"))).run(student_id=ALEX).output
    assert out.method == "keywords" and out.new
    assert ("Computer science", "undergrad") in [(i.label, i.source) for i in out.new]
    assert {i.label for i in out.new}.isdisjoint({"AI agents", "Software engineering", "LLM applications"})
    statuses = set(db.scalars(select(StudentInterest.status).where(
        StudentInterest.student_id == ALEX, StudentInterest.label.in_([i.label for i in out.new]))))
    assert statuses == {"suggested"}


def test_interest_extractor_skips_removed_labels(db):
    db.add(StudentInterest(student_id=ALEX, label="Computer science", source="undergrad", status="removed"))
    db.flush()
    out = InterestExtractor(db, **_kw(FakeLLM("computer science\nRobotics"))).run(student_id=ALEX).output
    assert out.skipped == ["Computer science"] and [i.label for i in out.new] == ["Robotics"]


def test_parse_labels_and_keywords():
    assert parse_labels("Interests:\n1) ai ethics\n2. AI Ethics\n- Data engineering.\n" + "x" * 50) == [
        "Ai ethics", "Data engineering"]
    assert parse_labels("Cloud, MLOps, Data engineering") == ["Cloud", "MLOps", "Data engineering"]
    assert keyword_labels("Built backend APIs in Python", limit=2) == ["Software engineering"]
    assert "Financial services" not in keyword_labels("capital letters")  # word-start match only


# ---------- Agent 3: schedule ----------

def _build(db, student: str, llm=None):
    return build_draft(db, student, TERM, **_kw(llm))


def test_priya_valid_s27_draft_with_all_core(db):
    built = _build(db, PRIYA)
    d = built.schedule.output
    assert d.valid and d.total_units == 48 <= d.max_units  # 30 core + the 18 elective units still needed; never above 54
    # core due by this term (recommended term <= her plan position) and offered in it; later-term core (the Capstone) waits
    due = built.audit.output.plan_position
    s27_core = {c.course_code for c in built.audit.output.core if c.status == "remaining"
                and (c.recommended_term_seq or 0) <= due} & _offered_in(db, TERM)
    core_items = {i.course_code for i in d.items if i.item_type == "core"}
    assert core_items == s27_core and len(s27_core) == 5  # the Capstone is a Summer course for AIM12
    assert all(i.is_locked for i in d.items if i.item_type == "core")
    assert any(i.item_type == "elective" and not i.is_locked for i in d.items)
    assert d.total_units == sum(i.units for i in d.items) == d.core_units + d.elective_units == 48
    assert all(r.passed for r in d.checks if r.code == "TIME_CONFLICT")

    schedule = db.get(Schedule, d.schedule_id)
    assert schedule.status == "draft" and schedule.term_code == TERM and schedule.version == d.version
    assert schedule.built_by_run_id == built.schedule.run_id
    assert schedule.ai_explanation and schedule.load_rationale.startswith("48 of 54 units: 30 core + 18 electives.")
    assert {i.course_code for i in schedule.items} == {i.course_code for i in d.items}
    assert all(i.added_by == "ai" for i in schedule.items)


def test_daniel_low_qpa_is_not_given_a_lighter_load(db):
    built = _build(db, DANIEL)
    d = built.schedule.output
    assert built.audit.output.below_min_qpa  # a QPA below 3.00 changes nothing in the draft
    assert d.valid and d.total_units == 48 and d.core_units == 30 and d.elective_units == 18
    assert "QPA" not in d.load_rationale and "ighter" not in d.load_rationale
    assert d.load_rationale.startswith("48 of 54 units: 30 core + 18 electives.")
    assert not hasattr(d, "light_load")
    assert db.get(Schedule, d.schedule_id).load_rationale == d.load_rationale


def test_alex_new_student_first_term(db):
    built = _build(db, ALEX)
    d = built.schedule.output
    assert d.valid and 0 < d.total_units <= 54
    assert {i.course_code for i in d.items} <= _offered_in(db, TERM)
    assert all(r.passed for r in d.checks if r.code == "PREREQ")
    assert {"94-879", "95-888", "95-891"} <= {i.course_code for i in d.items}  # term-1 core offered in spring
    assert built.electives.output.basis == "popularity"
    assert {s.group_code for s in built.electives.output.suggestions} == {"popular"}
    core = [i for i in d.items if i.item_type == "core"]
    seqs = [next(c.recommended_term_seq for c in built.audit.output.core if c.course_code == i.course_code)
            for i in core]
    assert seqs == sorted(seqs)  # earliest recommended term first
    assert d.load_rationale.startswith(f"{d.total_units.normalize():f} of 54 units") and "First term" in d.load_rationale


def test_alex_core_is_only_what_is_due_this_term_and_nothing_is_pulled_forward(db):
    """D17: core due this term, then electives, then stop at 54 or at the elective units still needed."""
    built = _build(db, ALEX)
    d, audit = built.schedule.output, built.audit.output
    seq = {c.course_code: c.recommended_term_seq for c in audit.core}
    due_in_plan = {c for c, n in seq.items() if n <= audit.plan_position}
    assert audit.plan_position == 1
    assert sum(c.units for c in audit.core if c.course_code in due_in_plan) == 48  # term-1 core per the plan
    core = {i.course_code for i in d.items if i.item_type == "core"}
    assert core == {"94-879", "95-888", "95-891"} == due_in_plan & _offered_in(db, TERM)  # offered ones only
    assert all(seq[c] <= audit.plan_position for c in core)  # nothing from term 2 or 3
    assert d.core_units == 24
    for later in ("94-739", "94-815", "94-844", "95-767", "95-864", "95-897"):
        assert "not pulled forward" in d.skipped[later]
    assert d.elective_units == 18 == audit.elective_units_left  # stops at the elective units still needed
    assert d.total_units == 42 < 54
    assert d.load_rationale.startswith("42 of 54 units: 24 core + 18 electives.")
    assert "No more required this term" in d.load_rationale
    assert db.get(Schedule, d.schedule_id).load_rationale == d.load_rationale


@pytest.mark.parametrize("student", [PRIYA, DANIEL, ALEX])
def test_draft_core_is_never_from_a_later_term(db, student):
    built = _build(db, student)
    position = built.audit.output.plan_position
    seq = {c.course_code: c.recommended_term_seq for c in built.audit.output.core}
    assert all(seq[i.course_code] <= position for i in built.schedule.output.items if i.item_type == "core")


@pytest.mark.parametrize("student", [PRIYA, DANIEL, ALEX])
def test_ai_draft_never_above_54_units_and_never_electives_from_core(db, student):
    built = _build(db, student)
    d = built.schedule.output
    assert d.total_units <= 54 and d.valid
    core = _core_codes(db, student)
    assert not {i.course_code for i in d.items if i.item_type == "elective"} & core
    assert not {s.course_code for s in built.electives.output.suggestions} & core
    rows = db.scalars(text("SELECT course_code FROM elective_suggestions WHERE student_id = :s"), {"s": student})
    assert not set(rows) & core
    rows = db.scalars(text("SELECT i.course_code FROM schedule_items i JOIN schedules s USING (schedule_id) "
                           "WHERE s.student_id = :s AND i.item_type = 'elective'"), {"s": student})
    assert not set(rows) & core


def test_agent_build_stores_the_draft_as_ai_baseline(db):
    d = _build(db, PRIYA).schedule.output
    baseline = db.get(Schedule, d.schedule_id).ai_baseline
    assert {b["course_code"] for b in baseline} == {i.course_code for i in d.items}
    by_code = {b["course_code"]: b for b in baseline}
    for item in d.items:
        b = by_code[item.course_code]
        assert (b["section_id"], b["item_type"], b["units"], b["is_locked"], b["is_conditional"],
                b["needs_approval"]) == (item.section_id, item.item_type, float(item.units), item.is_locked,
                                         item.is_conditional, item.needs_approval)
    rebuilt = build_draft(db, PRIYA, TERM, replace=True, **_kw()).schedule.output
    assert db.get(Schedule, rebuilt.schedule_id).ai_baseline is not None


def test_building_for_closed_term_is_refused(db):
    RequirementsAgent(db, **_kw()).run(student_id=PRIYA)
    with pytest.raises(AgentError) as err:
        ScheduleAgent(db, **_kw()).run(student_id=PRIYA, term_code="F26")
    assert isinstance(err.value.__cause__, TermNotPlannable) and "closed" in str(err.value)
    run = db.get(AgentRun, err.value.run_id)
    assert run.status == "failed" and "Fall 2026" in run.error and run.schedule_id is None
    with pytest.raises(TermNotPlannable):
        build_draft(db, PRIYA, "F26", **_kw())
    with pytest.raises(TermNotPlannable):  # not open yet
        build_draft(db, PRIYA, "M27", **_kw())


def test_rebuild_supersedes_previous_draft(db):
    first = _build(db, PRIYA).schedule.output
    second = build_draft(db, PRIYA, TERM, replace=True, **_kw()).schedule.output
    assert second.version == first.version + 1
    assert db.get(Schedule, first.schedule_id).status == "superseded"
    assert db.get(Schedule, second.schedule_id).status == "draft"
    assert db.get(Schedule, second.schedule_id).previous_schedule_id == first.schedule_id
    actions = {sid: db.scalars(select(ScheduleChange.action).where(ScheduleChange.schedule_id == sid)).all()
               for sid in (first.schedule_id, second.schedule_id)}
    assert actions == {first.schedule_id: ["build"], second.schedule_id: ["rebuild"]}


def test_build_with_an_open_draft_needs_confirmation(db):
    first = _build(db, PRIYA).schedule.output
    runs = db.scalar(select(func.count()).select_from(AgentRun))
    with pytest.raises(DraftExists):  # refused before any agent runs
        _build(db, PRIYA)
    assert db.scalar(select(func.count()).select_from(AgentRun)) == runs
    RequirementsAgent(db, **_kw()).run(student_id=PRIYA)
    with pytest.raises(AgentError) as err:  # Agent 3 on its own refuses too
        ScheduleAgent(db, **_kw()).run(student_id=PRIYA, term_code=TERM)
    assert isinstance(err.value.__cause__, DraftExists)
    assert db.get(Schedule, first.schedule_id).status == "draft"


def test_schedule_swaps_elective_after_rule_failure(db, monkeypatch):
    real = TOOLS["validate_schedule"]
    calls: list[list[str]] = []

    def fail_first_elective(session, schedule, today):
        results = real(session, schedule, today)
        calls.append([i.course_code for i in schedule.items])
        if len(calls) == 1:
            results.append(CheckResult(code="OFFERED", passed=False, severity="block",
                                       course_code=schedule.items[-1].course_code, detail="Section is full"))
        return results

    monkeypatch.setitem(TOOLS, "validate_schedule", fail_first_elective)
    built = _build(db, PRIYA)
    d = built.schedule.output
    assert built.schedule.status == "retried" and built.schedule.attempts == 2 and d.valid and d.total_units <= 54
    removed = calls[0][-1]
    assert removed not in {i.course_code for i in d.items}
    assert d.skipped[removed] == "Removed after a rule check: Section is full"
    assert any(i.item_type == "elective" for i in d.items)  # swapped in the next ranked elective


def test_every_agent_run_is_logged_with_tool_calls(db):
    llm = FakeLLM(lambda prompt: "Fake text.")
    built = _build(db, PRIYA, llm)
    expected = {
        built.audit.run_id: ("requirements", {"get_student_history", "get_program_requirements", "save_degree_audit"}),
        built.electives.run_id: ("electives", {"get_confirmed_interests", "search_course_content",
                                               "rank_courses_by_popularity", "check_course_eligibility",
                                               "get_term_sections", "course_similarities",
                                               "save_elective_suggestions", "llm"}),
        built.schedule.run_id: ("schedule", {"get_term_registration", "get_current_audit", "get_open_schedule",
                                             "get_elective_suggestions", "get_term_sections", "validate_schedule",
                                             "save_schedule", "llm"}),
    }
    for run_id, (name, tools) in expected.items():
        run = db.get(AgentRun, run_id)
        assert run.agent_name == name and run.status == "ok" and run.student_id == PRIYA
        called = [c["tool"] for c in run.tool_calls]
        assert tools <= set(called) and called.count("llm") <= 1
        assert run.decision and run.explanation
    schedule_run = db.get(AgentRun, built.schedule.run_id)
    assert schedule_run.schedule_id == built.schedule.output.schedule_id
    assert schedule_run.llm_provider == "fake"
    assert len(llm.calls) == 2  # electives + schedule; the audit makes none
    _assert_no_student_name(db, PRIYA, llm)
    for run_id in expected:
        _assert_text_has_no_name(db, PRIYA, str(db.get(AgentRun, run_id).input))


# ---------- LLM guard: the LLM may explain, never invent facts ----------

def test_ungrounded_numbers():
    facts = "Total: 48 units (42 core + 6 elective), limit 54. Courses 95-841, 94-739 in S27. QPA 2.56"
    assert ungrounded_numbers("48.0 units: 42 core and 6 elective under 54; 95-841 in S27; QPA 2.56.", facts) == []
    assert ungrounded_numbers("The core totals 36 units out of 42.", facts) == ["36"]


def test_schedule_explanation_with_invented_numbers_falls_back(db):
    llm = FakeLLM(lambda prompt: "Fake reason." if "one line" in prompt else "Core courses total 36 units.")
    built = _build(db, PRIYA, llm)
    d = built.schedule.output
    assert d.ai_explanation.startswith("The draft for Spring 2027 has 48 units")  # the template
    assert db.get(Schedule, d.schedule_id).ai_explanation == d.ai_explanation
    guard = [c for c in db.get(AgentRun, built.schedule.run_id).tool_calls if c["tool"] == "llm_guard"]
    assert guard and "36" in guard[0]["reason"]


def test_echoed_elective_reasons_fall_back_to_template(db):
    def echo(prompt: str) -> str:
        return "\n".join(f"{m[0]}: matched interest: {m[2]}" if i % 2 else f"{m[0]}: {m[1]} | matched interest: {m[2]}"
                         for i, m in enumerate(re.findall(r"^(\d{2}-\d{3}) (.*) \| matched interest: (.+)$",
                                                          prompt, re.M)))
    result = ElectiveAgent(db, **_kw(FakeLLM(echo))).run(student_id=DANIEL, term_code=TERM)
    assert all(s.reason.startswith(("Matches your interest in", "Popular with")) for s in result.output.suggestions)
    assert any(c["tool"] == "llm_guard" for c in db.get(AgentRun, result.run_id).tool_calls)


# ---------- helpers ----------

def _assert_no_student_name(db, student_id: str, llm: FakeLLM) -> None:
    for system, prompt in llm.calls:
        _assert_text_has_no_name(db, student_id, system + prompt)


def _assert_text_has_no_name(db, student_id: str, value: str) -> None:
    student = db.get(Student, student_id)
    for name in (student.first_name, student.last_name, student.email):
        assert name.lower() not in value.lower(), f"{name!r} leaked"


# ---------- integration: the real Ollama (pytest -m llm) ----------

def _ollama_up() -> bool:
    try:
        return httpx.get(f"{get_settings().ollama_url}/api/tags", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.mark.llm
@pytest.mark.skipif(not _ollama_up(), reason="Ollama is not reachable")
def test_interest_extractor_with_real_ollama(db):
    llm = LLMClient(provider="ollama", timeout_s=180)
    result = InterestExtractor(db, llm=llm, today=DEMO_DAY, commit=False).run(student_id=ALEX)
    assert result.llm is not None and result.llm.provider == "ollama", result.llm
    out = result.output
    assert out.method == "llm" and 1 <= len(out.found) <= 5
    assert all(len(label) <= 40 for label in out.found)
    assert _interest_counts(db, ALEX).get("confirmed", 0) == 0
