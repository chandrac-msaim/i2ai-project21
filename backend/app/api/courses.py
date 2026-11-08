"""Course routes: search (with the rule engine's verdict for the searching student) and the course page."""
from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import DB, AnyUser, Today, default_term
from app.services import catalog_search as cs

router = APIRouter(prefix="/courses", tags=["courses"])


@router.get("/search", response_model=cs.SearchResult)
def search(user: AnyUser, db: DB, today: Today, q: str = Query(min_length=cs.MIN_QUERY, max_length=100),
           term: str | None = None) -> cs.SearchResult:
    """Courses offered in the term (default: the first term open for planning) whose title or code matches.
    For a student every hit carries `check`: eligible (no blocking rule), blocking reasons and flags."""
    term_code = default_term(db, today, term)
    if term_code is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No term is open for planning today; pass ?term=")
    return cs.search_courses(db, q, term_code, user.student_id if user.role == "student" else None, today)


@router.get("/{course_code}", response_model=cs.CourseDetail)
def course(course_code: str, user: AnyUser, db: DB, today: Today, term: str | None = None) -> cs.CourseDetail:
    """Details, prerequisites, sections with days / times / seats / instructors. `syllabus_url` may be null."""
    return cs.course_detail(db, course_code, default_term(db, today, term))
