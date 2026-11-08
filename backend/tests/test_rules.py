"""Rule engine (M2): the 7 checks on real DB data (S27 catalog, demo students) and small hand-made cases."""
from dataclasses import replace
from datetime import date, time
from decimal import Decimal

import pytest

from app.rules.engine import (
    CODES,
    CandidateSchedule,
    CheckResult,
    CourseInfo,
    Meeting,
    RuleContext,
    ScheduleItemIn,
    SectionInfo,
    check_prereq,
    check_time_conflict,
    check_unit_limit,
    clash,
    is_valid,
    validate,
)
from app.rules.repository import load_context, validate_schedule

NOV_7 = date(2026, 11, 7)
JUL_15 = date(2026, 7, 15)
PRIYA, ALEX = "S0056", "S0126"  # AIM12; Alex is new (entry S27, no history)


def items(*pairs) -> list[ScheduleItemIn]:
    out = []
    for p in pairs:
        code, section, *units = p
        out.append(ScheduleItemIn(course_code=code, section_id=section, units=units[0] if units else None))
    return out


def sched(student: str, term: str, *pairs) -> CandidateSchedule:
    return CandidateSchedule(student_id=student, term_code=term, items=items(*pairs))


def run(db, student, term, *pairs, today=NOV_7) -> list[CheckResult]:
    return validate_schedule(db, sched(student, term, *pairs), today)


ANY = object()


def of(results: list[CheckResult], code: str, course=ANY) -> list[CheckResult]:
    """Results for `code`; course=None selects the schedule-level result."""
    return [r for r in results if r.code == code and (course is ANY or r.course_code == course)]


def one(results, code, course=ANY) -> CheckResult:
    found = of(results, code, course)
    assert len(found) == 1, found
    return found[0]


# ---------- hand-made context ----------

def ctx(**kw) -> RuleContext:
    base = dict(student_id="S9999", track_code="AIM12", term_code="S27", term_registration="open",
                max_units_per_term=Decimal(54), max_undergrad_units=Decimal(12),
                max_units_extended=Decimal(60), extended_units_min_qpa=Decimal("3.50"))
    return RuleContext(**(base | kw))


def course(code: str, units: int = 12, **kw) -> CourseInfo:
    return CourseInfo(course_code=code, title=code, course_number=int(code[3:]),
                      units_default=Decimal(units), **kw)


def section(sid: str, session: str, *meetings: Meeting, tba: bool = False) -> SectionInfo:
    dates = {"FULL": (date(2027, 1, 11), date(2027, 5, 1)),
             "M3": (date(2027, 1, 11), date(2027, 3, 2)),
             "M4": (date(2027, 3, 16), date(2027, 5, 1))}[session]
    return SectionInfo(section_id=sid, course_code=sid[:6], term_code="S27", session_code=session,
                       session_start=dates[0], session_end=dates[1], status="offered", seats_left=10,
                       is_time_tba=tba, meetings=meetings)


TUE_930 = Meeting("T", time(9, 30), time(10, 50))


# ---------- validate / is_valid ----------

def test_validate_returns_all_7_codes(db):
    results = run(db, PRIYA, "S27", ("95-719", "95-719-S27-A3"))
    assert {r.code for r in results} == set(CODES)
    assert is_valid(results)


def test_empty_schedule_is_valid(db):
    results = run(db, PRIYA, "S27")
    assert {r.code for r in results} == set(CODES) and is_valid(results)


# ---------- UNIT_LIMIT ----------

def test_unit_limit_54_passes():
    c = ctx(courses={f"95-7{i}0": course(f"95-7{i}0", 6) for i in range(9)})
    s = CandidateSchedule(student_id="S9999", term_code="S27", items=items(*[(k, None) for k in c.courses]))
    r = one(check_unit_limit(s, c), "UNIT_LIMIT")
    assert r.passed and "54 units" in r.detail


def _units_schedule(total: int, **kw):
    n, rest = divmod(total, 12)
    sizes = [12] * n + ([rest] if rest else [])
    c = ctx(courses={f"95-7{i}0": course(f"95-7{i}0", u) for i, u in enumerate(sizes)}, **kw)
    s = CandidateSchedule(student_id="S9999", term_code="S27", items=items(*[(k, None) for k in c.courses]))
    return s, c


def test_unit_limit_60_without_qpa_fails_with_reason():
    r = one(check_unit_limit(*_units_schedule(60)), "UNIT_LIMIT")
    assert r.blocking and "Above 54 units needs a QPA above 3.50" in r.detail


@pytest.mark.parametrize("qpa, ok", [("3.51", True), ("3.50", False), ("2.56", False), (None, False)])
def test_unit_limit_55_to_60_needs_qpa_above_3_50(qpa, ok):
    for total in (55, 60):
        r = one(check_unit_limit(*_units_schedule(total, qpa=Decimal(qpa) if qpa else None)), "UNIT_LIMIT")
        assert r.passed is ok and r.blocking is (not ok)
        assert (r.flag == "unit_overload") is ok  # the advisor sees the overload


def test_unit_limit_above_60_always_fails():
    r = one(check_unit_limit(*_units_schedule(61, qpa=Decimal("4.00"))), "UNIT_LIMIT")
    assert r.blocking and "never more than 60" in r.detail


def test_unit_limit_55_to_60_on_real_students(db):
    # explicit units on one variable-unit course (as test below); Priya QPA 3.78, Daniel 2.56, Alex has none
    def unit_limit(student, units):
        return one(run(db, student, "S27", ("94-902", "94-902-S27-G", Decimal(units))), "UNIT_LIMIT")
    for units in (55, 60):
        assert unit_limit(PRIYA, units).passed and unit_limit(PRIYA, units).flag == "unit_overload"
        assert unit_limit("S0057", units).blocking and "QPA above 3.50" in unit_limit("S0057", units).detail
        assert unit_limit(ALEX, units).blocking
    assert all(unit_limit(s, 61).blocking for s in (PRIYA, "S0057", ALEX))


def test_unit_limit_uses_explicit_units_for_variable_courses(db):
    results = run(db, PRIYA, "S27", ("94-902", "94-902-S27-G", Decimal(61)))
    assert one(results, "UNIT_LIMIT").blocking


# ---------- PREREQ ----------

def test_95_864_aim12_program_sequence_override(db):
    # Catalog says 95-828 or an ML course; AIM12 follows the program sequence instead (A11)
    r = one(run(db, PRIYA, "S27", ("95-864", "95-864-S27-A3")), "PREREQ")
    assert r.passed and r.flag is None and "Program sequence" in r.detail


def test_95_864_without_override_would_fail(db):
    s = sched(ALEX, "S27", ("95-864", "95-864-S27-A3"))
    c = load_context(db, s, NOV_7)
    c.courses["95-864"] = replace(c.courses["95-864"], catalog_prereqs_apply=True)
    assert one(check_prereq(s, c), "PREREQ").blocking


@pytest.mark.parametrize(("completed", "in_progress", "outcome"), [
    ({"95-712"}, set(), "pass"),                     # option 1
    ({"17-681", "17-683"}, set(), "pass"),           # option 2 (both needed)
    ({"95-807"}, set(), "pass"),                     # option 3
    ({"17-681"}, set(), "fail"),                     # half of option 2 is not enough
    (set(), set(), "fail"),
    ({"17-681"}, {"17-683"}, "conditional"),         # option 2 with one part in progress
    (set(), {"95-712"}, "conditional"),
])
def test_95_706_prerequisite_options(db, completed, in_progress, outcome):
    s = sched(ALEX, "S27", ("95-706", "95-706-S27-Z3"))
    c = load_context(db, s, NOV_7)
    assert c.courses["95-706"].prereq_options == (
        frozenset({"95-712"}), frozenset({"17-681", "17-683"}), frozenset({"95-807"}))
    c.completed, c.in_progress = completed, in_progress
    r = one(check_prereq(s, c), "PREREQ")
    if outcome == "pass":
        assert r.passed and r.flag is None
    elif outcome == "fail":
        assert r.blocking and "(95-712) or (17-681 and 17-683) or (95-807)" in r.detail
    else:
        assert r.passed and r.flag == "conditional" and r.severity == "low"


def test_priya_completed_prereq_passes(db):
    r = one(run(db, PRIYA, "S27", ("95-760", "95-760-S27-A3")), "PREREQ")  # needs 95-796 (completed F26)
    assert r.passed and "95-796" in r.detail


def test_in_progress_now_is_conditional_for_a_later_term(db):
    s27 = load_context(db, sched(PRIYA, "S27"), NOV_7)
    f26 = load_context(db, sched(PRIYA, "F26"), NOV_7)
    assert "95-891" in s27.in_progress and "95-891" not in s27.completed
    assert "95-891" not in f26.in_progress  # same term: not a prerequisite yet
    assert "95-796" in s27.completed


def test_alex_no_history_course_without_prereqs_passes(db):
    results = run(db, ALEX, "S27", ("95-719", "95-719-S27-A3"))
    r = one(results, "PREREQ")
    assert r.passed and r.detail == "No prerequisites"
    assert is_valid(results)


def test_alex_no_history_course_with_prereqs_fails(db):
    results = run(db, ALEX, "S27", ("95-760", "95-760-S27-A3"))
    assert one(results, "PREREQ").blocking
    assert not is_valid(results)


# ---------- OFFERED ----------

def test_offered_open_section_with_seats(db):
    r = one(run(db, PRIYA, "S27", ("95-719", "95-719-S27-A3")), "OFFERED", "95-719")
    assert r.passed


def test_full_section_fails_offered(db):
    r = one(run(db, PRIYA, "S27", ("95-759", "95-759-S27-A3")), "OFFERED", "95-759")
    assert r.blocking and "full" in r.detail


def test_planning_f26_on_nov_7_fails_registration_closed(db):
    results = run(db, PRIYA, "F26", ("95-719", "95-719-F26-A2"), today=NOV_7)
    r = one(results, "OFFERED")
    assert r.blocking and "Registration for Fall 2026 is closed" in r.detail
    assert not is_valid(results)


def test_planning_f26_on_jul_15_is_open(db):
    r = one(run(db, PRIYA, "F26", ("95-719", "95-719-F26-A2"), today=JUL_15), "OFFERED", "95-719")
    assert r.passed


def test_planning_m27_on_nov_7_not_open_yet(db):
    r = one(run(db, PRIYA, "M27", ("95-719", None)), "OFFERED")
    assert r.blocking and "not open yet" in r.detail


def test_wrong_section_or_unoffered_course_fails(db):
    results = run(db, PRIYA, "S27", ("95-719", "95-864-S27-A3"), ("95-868", None))
    assert "not a Spring 2027 section of 95-719" in one(results, "OFFERED", "95-719").detail
    assert "Not offered in Spring 2027" in one(results, "OFFERED", "95-868").detail


def test_cancelled_section_fails():
    s = CandidateSchedule(student_id="S9999", term_code="S27", items=items(("95-719", "95-719-S27-A3")))
    c = ctx(offered_courses={"95-719"},
            sections={"95-719-S27-A3": replace(section("95-719-S27-A3", "M3"), status="cancelled")})
    r = one(validate(s, c), "OFFERED", "95-719")
    assert r.blocking and "cancelled" in r.detail


# ---------- LEVEL ----------

def test_graduate_course_passes_level(db):
    assert one(run(db, PRIYA, "S27", ("95-719", "95-719-S27-A3")), "LEVEL", "95-719").passed


def test_undergraduate_course_flagged_for_petition(db):
    results = run(db, PRIYA, "S27", ("94-413", "94-413-S27-A3"))
    r = one(results, "LEVEL", "94-413")
    assert not r.passed and r.flag == "needs_petition" and r.severity == "medium"
    assert one(results, "LEVEL", None).passed  # 6 undergrad units <= 12
    assert is_valid(results)  # a petition is a flag, not a block


def test_undergraduate_units_over_12_blocks(db):
    results = run(db, PRIYA, "S27", ("94-408", "94-408-S27-A"), ("94-413", "94-413-S27-A3"))
    cap = one(results, "LEVEL", None)
    assert cap.blocking and "18 undergraduate units" in cap.detail
    assert not is_valid(results)


def test_undergraduate_cap_counts_earlier_terms():
    s = CandidateSchedule(student_id="S9999", term_code="S27", items=items(("94-413", None)))
    c = ctx(courses={"94-413": course("94-413", 6)}, prior_undergrad_units=Decimal(12))
    assert one(validate(s, c), "LEVEL", None).blocking


# ---------- APPROVAL ----------

def test_regular_course_needs_no_approval(db):
    assert one(run(db, PRIYA, "S27", ("95-719", "95-719-S27-A3")), "APPROVAL", "95-719").passed


def test_independent_study_flagged_for_approval(db):
    results = run(db, PRIYA, "S27", ("94-902", "94-902-S27-G", Decimal(6)))
    r = one(results, "APPROVAL", "94-902")
    assert not r.passed and r.flag == "needs_approval" and r.severity == "info"
    assert not r.blocking and is_valid(results)


# ---------- TIME_CONFLICT ----------

def test_mini3_vs_mini4_same_slot_no_clash(db):
    # 95-451 A3 (Mini 3) and 95-723 A4 (Mini 4) both meet Friday 11:00-12:20
    results = run(db, PRIYA, "S27", ("95-451", "95-451-S27-A3"), ("95-723", "95-723-S27-A4"))
    assert one(results, "TIME_CONFLICT").passed


def test_full_semester_vs_mini3_real_clash(db):
    # 95-422 A (FULL) and 95-864 A3 (Mini 3) both meet Monday 09:30-10:50
    results = run(db, PRIYA, "S27", ("95-422", "95-422-S27-A"), ("95-864", "95-864-S27-A3"))
    r = one(results, "TIME_CONFLICT")
    assert r.blocking and "95-422-S27-A" in r.detail and "95-864-S27-A3" in r.detail and "M 09:30" in r.detail
    assert not is_valid(results)


def test_tba_never_clashes():
    a = section("95-700-S27-A", "FULL", TUE_930)
    b = section("95-701-S27-A", "FULL", TUE_930)
    assert clash(a, b) == TUE_930
    assert clash(a, replace(b, is_time_tba=True)) is None


def test_same_session_different_times_no_clash():
    a = section("95-700-S27-A3", "M3", TUE_930)
    b = section("95-701-S27-A3", "M3", Meeting("T", time(10, 50), time(12, 0)))  # back to back
    s = CandidateSchedule(student_id="S9999", term_code="S27",
                          items=items(("95-700", a.section_id), ("95-701", b.section_id)))
    c = ctx(sections={a.section_id: a, b.section_id: b})
    assert one(check_time_conflict(s, c), "TIME_CONFLICT").passed


# ---------- PRIVACY (placeholder: always passes) ----------

def test_privacy_records_name_removal(db):
    r = one(run(db, ALEX, "S27", ("95-719", "95-719-S27-A3")), "PRIVACY")
    assert r.passed and "S0126" in r.detail and "Alex" not in r.detail


# ---------- repository ----------

def test_null_capacity_means_no_seat_limit(db):
    from sqlalchemy import select

    from app.models import Section

    sid = db.scalar(select(Section.section_id).where(Section.capacity.is_(None)).limit(1))
    if sid is None:
        pytest.skip("no section without capacity")
    s = sched(PRIYA, "S27", (sid[:6], sid))
    assert load_context(db, s, NOV_7).sections[sid].seats_left is None


def test_unknown_student_raises(db):
    with pytest.raises(ValueError):
        load_context(db, sched("S0000", "S27"), NOV_7)
