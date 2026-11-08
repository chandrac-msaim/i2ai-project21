"""M4b: elective scoring (D16) as pure functions: no database."""
from datetime import date, time

import pytest

from app.agents.elective_scoring import Candidate, Settings, bucket, fit_and_flags, score, section_fit, select
from app.tools.catalog import MeetingOut, SectionOut

S = Settings(w_similarity=0.7, w_popularity=0.2, w_fit=0.1, min_similarity=0.4, top_n=4,
             one_per_interest_first=True, max_similarity_between_picks=0.85, fit_time_of_day=0.4,
             fit_modality=0.3, fit_no_clash=0.3, evening_from=time(17), morning_before=time(12), few_seats_below=5)


def section(code="95-001", start=(9, 30), end=(10, 50), day="M", seats=10, modality="remote", sid=None,
            tba=False) -> SectionOut:
    return SectionOut(
        section_id=sid or f"{code}-S27-A", course_code=code, term_code="S27", section_code="A", session_code="FULL",
        session_start=date(2027, 1, 11), session_end=date(2027, 5, 1), modality=modality, location=None,
        status="offered", capacity=None if seats is None else 30, enrolled=0, seats_left=seats, is_time_tba=tba,
        meetings=[] if tba else [MeetingOut(day=day, start=time(*start), end=time(*end))])


def test_time_of_day_buckets():
    assert [bucket(time(h), S) for h in (8, 11, 12, 16, 17, 20)] == [
        "morning", "morning", "afternoon", "afternoon", "evening", "evening"]


def test_section_fit_uses_time_modality_and_clash_weights():
    core = [section("95-100", (9, 30), (10, 50))]
    perfect = section(start=(18, 30), end=(19, 50))
    assert section_fit(perfect, core, "evening", "remote", S) == pytest.approx(1.0)
    assert section_fit(section(start=(18, 30), end=(19, 50), modality="in_person"), core, "evening", "remote",
                       S) == pytest.approx(0.7)  # modality weight lost
    assert section_fit(section(start=(10, 0), end=(11, 0)), core, "evening", "remote", S) == pytest.approx(0.3)
    assert section_fit(section(start=(10, 0), end=(11, 0)), [], None, None, S) == pytest.approx(1.0)  # no preference
    assert section_fit(section(tba=True), [], "evening", "remote", S) == pytest.approx(0.8)  # TBA = half the time part


def test_flags_time_clash_and_few_seats():
    core = [section("95-100", (9, 30), (10, 50))]
    clashing = [section(start=(10, 0), end=(11, 0), seats=2)]
    fit, flags = fit_and_flags(clashing, core, None, None, S)
    assert flags == ["time_clash", "few_seats"] and fit == pytest.approx(0.7)  # kept, with a lower fit
    # one clash-free section is enough to avoid the flag; the best section sets the fit
    _, flags = fit_and_flags(clashing + [section(sid="95-001-S27-B", start=(14, 0), end=(15, 0), seats=9)], core,
                             None, None, S)
    assert flags == []
    assert fit_and_flags([section(seats=None)], [], None, None, S)[1] == []  # no capacity limit = no few_seats
    assert fit_and_flags([], core, None, None, S) == (0.0, [])


def test_score_formula_and_popularity_only_formula():
    c = Candidate("95-001", [0.8], popularity=0.5, fit=1.0)
    assert score(c, 0.8, S) == pytest.approx(0.7 * 0.8 + 0.2 * 0.5 + 0.1 * 1.0)
    assert score(c, None, S) == pytest.approx((0.2 * 0.5 + 0.1 * 1.0) / 0.3)


def test_select_best_course_per_interest_first_then_fill_by_score():
    cands = [Candidate("A", [0.9, 0.1], 0.1), Candidate("B", [0.8, 0.1], 0.1), Candidate("C", [0.7, 0.1], 0.1),
             Candidate("D", [0.1, 0.5], 0.0), Candidate("E", [0.1, 0.45], 0.0)]
    picks = select(cands, 2, {}, S, top_n=3)
    assert {p.course_code for p in picks} == {"A", "D", "B"}  # interest 0 -> A, interest 1 -> D, then B by score
    assert {p.interest for p in picks} == {0, 1}
    assert [p.final_score for p in picks] == sorted((p.final_score for p in picks), reverse=True)


def test_select_covers_an_interest_below_the_threshold_but_puts_it_in_popular():
    cands = [Candidate("A", [0.9, 0.2], 0.1), Candidate("B", [0.8, 0.1], 0.1), Candidate("C", [0.7, 0.1], 0.1),
             Candidate("D", [0.1, 0.3], 0.2)]
    picks = {p.course_code: p for p in select(cands, 2, {}, S, top_n=2)}
    assert set(picks) == {"A", "D"}
    assert picks["A"].group_code == "interest_match" and picks["D"].group_code == "popular"
    assert picks["D"].interest == 1  # still the matched (closest) interest


def test_select_skips_near_duplicates():
    cands = [Candidate("A", [0.9], 0.0), Candidate("A2", [0.88], 0.0), Candidate("B", [0.6], 0.0),
             Candidate("C", [0.5], 0.0)]
    similar = {("A", "A2"): 0.95, ("A", "B"): 0.3, ("A", "C"): 0.3, ("A2", "B"): 0.3, ("B", "C"): 0.86}
    picks = [p.course_code for p in select(cands, 1, similar, S, top_n=4)]
    assert picks == ["A", "B"]  # A2 duplicates A (> 0.85); C duplicates B (0.86)
    near = {("A", "A2"): 0.85}  # exactly at the limit is allowed: only "above" is skipped
    assert [p.course_code for p in select(cands[:2], 1, near, S, top_n=4)] == ["A", "A2"]


def test_select_without_interests_is_popularity_and_fit_only_all_popular():
    cands = [Candidate("A", [], 0.9, fit=0.0), Candidate("B", [], 0.3, fit=1.0), Candidate("C", [], 0.1, fit=0.0)]
    picks = select(cands, 0, {}, S, top_n=3)
    assert {p.group_code for p in picks} == {"popular"}
    assert all(p.similarity is None and p.interest is None for p in picks)
    assert [p.course_code for p in picks] == ["A", "B", "C"]  # (0.2*0.9)/0.3=0.6, (0.06+0.1)/0.3=0.53, 0.07


def test_settings_require_every_rule():
    with pytest.raises(ValueError, match="ELECTIVE_DIVERSITY"):
        Settings.from_rule_settings({"ELECTIVE_SCORE_WEIGHTS": {"similarity": 0.7, "popularity": 0.2, "fit": 0.1},
                                     "ELECTIVE_MATCH_THRESHOLD": {"min_similarity": 0.4}, "ELECTIVE_FIT": {}})
