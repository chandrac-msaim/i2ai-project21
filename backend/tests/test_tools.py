"""Tools return the expected shapes for Priya (S0056, AIM12). Runs against the live dev database."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.services.terms import plannable_terms
from app.tools import TOOLS
from app.tools.catalog import ActiveCourse, Prerequisites, SectionOut, get_active_courses, get_course_prerequisites, get_sections
from app.tools.popularity import rank_courses_by_popularity
from app.tools.student import (
    get_confirmed_interests,
    get_course_waivers,
    get_program_requirements,
    get_redaction_names,
    get_student_history,
)
from app.tools.vector_search import _reset_embedding_model, embed_text, embed_texts, get_embedding_model, search_course_content

PRIYA = "S0056"
DEMO_DAY = date(2026, 11, 7)


@pytest.fixture
def term(db) -> str:
    """The planning term is decided by date, never hard-coded: S27 on 2026-11-07."""
    terms = plannable_terms(db, DEMO_DAY)
    assert [t.term_code for t in terms] == ["S27"]
    return terms[0].term_code


def test_student_history(db):
    h = get_student_history(db, PRIYA)
    assert h.student_id == PRIYA and h.track_code == "AIM12" and h.entry_term == "F26"
    assert h.completed and h.in_progress and not set(h.completed) & set(h.in_progress)
    assert len(h.enrollments) == len(h.completed) + len(h.in_progress)
    assert isinstance(h.qpa, Decimal) and h.qpa >= Decimal("3.0")
    assert "first_name" not in h.model_dump() and "Priya" not in h.model_dump_json()


def test_course_waivers(db):
    assert get_course_waivers(db, PRIYA) == []


def test_program_requirements(db):
    r = get_program_requirements(db, "AIM12")
    assert r.total_units == 144 and r.max_units_per_term == 54 and r.min_qpa == Decimal("3.00")
    assert r.core_units == 126 and r.elective_min_units == 18 and r.max_undergrad_units == 12
    seqs = [c.recommended_term_seq for c in r.core if c.recommended_term_seq is not None]
    assert seqs == sorted(seqs)


def test_active_courses_and_sections(db, term):
    courses = get_active_courses(db, term)
    assert courses and all(isinstance(c, ActiveCourse) for c in courses)
    assert all(c.offered_sections > 0 for c in courses)
    assert {c.level for c in courses} <= {"graduate", "undergraduate"}
    course = next(c for c in courses if c.level == "graduate")
    sections = get_sections(db, course.course_code, term)
    assert sections and all(isinstance(s, SectionOut) and s.term_code == term for s in sections)
    assert all(s.session_start <= s.session_end for s in sections)


def test_course_prerequisites(db):
    p = get_course_prerequisites(db, "94-719")
    assert isinstance(p, Prerequisites)
    assert p.options == [["90-723"], ["95-719"]]  # OR of two single-course options
    with pytest.raises(ValueError):
        get_course_prerequisites(db, "00-000")


def test_rank_by_popularity(db):
    ranked = rank_courses_by_popularity(db, "AIM12", limit=5)
    assert len(ranked) == 5 and ranked[0].score == 1.0
    assert [r.popularity for r in ranked] == sorted((r.popularity for r in ranked), reverse=True)
    assert all(0 <= r.score <= 1 for r in ranked)
    only = rank_courses_by_popularity(db, "AIM12", course_codes=["95-841", "95-879"])
    assert {r.course_code for r in only} == {"95-841", "95-879"}


def test_confirmed_interests_and_redaction_names(db):
    interests = get_confirmed_interests(db, PRIYA)
    assert [i.label for i in interests] == ["AI governance", "Financial services", "Model risk"]
    assert all(len(i.embedding) == 384 for i in interests)
    people = get_redaction_names(db, PRIYA)
    assert people[0] == ("the student", ["Priya", "Raman"]) and people[1][0] == "the advisor"


def test_embedding_model_is_cached():
    assert get_embedding_model() is get_embedding_model()
    v = embed_text("AI governance in banks")
    assert len(v) == 384 and abs(sum(x * x for x in v) - 1) < 1e-3
    assert embed_texts([]) == []


def test_search_course_content_only_returns_eligible(db, term):
    history = get_student_history(db, PRIYA)
    vectors = [i.embedding for i in get_confirmed_interests(db, PRIYA)]
    exclude = history.completed + history.in_progress + ["95-841"]
    matches = search_course_content(db, vectors, term, exclude=exclude, limit=10)
    assert len(matches) == 10
    assert [m.similarity for m in matches] == sorted((m.similarity for m in matches), reverse=True)
    assert all(0 <= m.best_interest < len(vectors) for m in matches)
    codes = [m.course_code for m in matches]
    assert not set(codes) & set(exclude)
    eligible = set(db.scalars(text("""
        SELECT c.course_code FROM courses c
        WHERE c.level = 'graduate' AND EXISTS (
            SELECT 1 FROM sections s JOIN course_offerings o USING (course_code, term_code)
            WHERE s.course_code = c.course_code AND s.term_code = :t AND o.status = 'offered'
              AND s.status = 'offered' AND (s.capacity IS NULL OR s.seats_left > 0))"""), {"t": term}))
    assert set(codes) <= eligible


def test_search_finds_governance_courses(db, term):
    matches = search_course_content(db, [embed_text("AI governance in banks")], term, limit=5)
    assert matches and all(m.course_number >= 600 for m in matches)
    assert any("Governance" in m.title for m in matches[:3])
    assert search_course_content(db, [], term) == []


def test_registry_lists_every_tool():
    assert {"get_active_courses", "get_sections", "get_course_prerequisites", "get_student_history",
            "get_course_waivers", "get_program_requirements", "rank_courses_by_popularity",
            "search_course_content", "send_email"} <= set(TOOLS)


def test_embedding_model_is_loaded_once_even_with_concurrent_first_requests(monkeypatch):
    """Routes run in a thread pool: requests that arrive before the first load ends must share one load, not load one each."""
    import sys
    import threading
    import time
    import types

    loads: list[int] = []

    class FakeModel:
        def __init__(self, name):
            loads.append(1)
            time.sleep(0.2)  # a slow load, so all threads reach the loader while it is still running

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=FakeModel))
    original = get_embedding_model()  # keep the real model (if any) to put back afterwards
    _reset_embedding_model()
    try:
        got: list[object] = []
        threads = [threading.Thread(target=lambda: got.append(get_embedding_model())) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(loads) == 1 and len({id(m) for m in got}) == 1 and isinstance(got[0], FakeModel)
        assert get_embedding_model() is got[0] and len(loads) == 1  # later calls reuse it too
    finally:
        _reset_embedding_model()
        import app.tools.vector_search as vs
        vs._model = original
