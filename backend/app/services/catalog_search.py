"""Course search (pg_trgm on titles + code prefix) with the rule engine's verdict for the searching student,
and the course detail page. Read-only."""
from datetime import date
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.core_plan import usable
from app.agents.schedule_agent import days_times
from app.models import Course, CourseOffering, Instructor, SectionInstructor
from app.rules.repository import course_check_results
from app.tools.catalog import get_course_prerequisites, get_term_sections

MIN_QUERY = 2


class CourseNotFound(LookupError):
    pass


class RuleVerdict(BaseModel):
    eligible: bool  # no blocking failure: the student may add it (flags still need the advisor)
    blocking: list[str]
    flags: list[str]  # conditional | needs_petition | needs_approval | unit_overload
    notes: list[str]  # details of the flags


class CourseHit(BaseModel):
    course_code: str
    title: str
    units: Decimal | None
    level: str | None
    type_code: str | None
    offered_sections: int
    seats_left: int | None  # None = at least one section has no capacity limit
    similarity: float
    check: RuleVerdict | None  # None unless a student is searching


class SearchResult(BaseModel):
    q: str
    term_code: str
    results: list[CourseHit]


class SectionView(BaseModel):
    section_id: str
    section_code: str
    session_code: str
    days_times: str
    modality: str
    location: str | None
    status: str
    capacity: int | None
    seats_left: int | None
    instructors: list[str]


class PrerequisiteView(BaseModel):
    options: list[list[str]]  # OR of ANDs
    text: str | None
    catalog_applies: bool


class CourseDetail(BaseModel):
    course_code: str
    title: str
    description: str | None
    units: Decimal | None
    units_type: str
    units_min: Decimal | None
    units_max: Decimal | None
    level: str | None
    type_code: str | None
    typical_offering: str | None
    prerequisites: PrerequisiteView
    syllabus_url: str | None  # empty until Gray DI provides the data
    term_code: str | None
    offered: bool
    sections: list[SectionView]


_SEARCH = text("""
SELECT c.course_code, c.title, c.units_default, c.level, c.type_code,
       GREATEST(similarity(c.title, :q), CASE WHEN c.course_code ILIKE :prefix THEN 1 ELSE 0 END) AS sim
FROM courses c
JOIN course_offerings o ON o.course_code = c.course_code AND o.term_code = :term AND o.status = 'offered'
WHERE c.title % :q OR c.title ILIKE :like OR c.course_code ILIKE :prefix
ORDER BY (lower(c.course_code) = lower(:q)) DESC, sim DESC, c.course_code
LIMIT :limit
""")


def search_courses(db: Session, q: str, term_code: str, student_id: str | None, today: date,
                   limit: int = 20) -> SearchResult:
    q = " ".join(q.split())
    if len(q) < MIN_QUERY:
        raise ValueError(f"Type at least {MIN_QUERY} characters")
    like = "%" + q.replace("%", r"\%").replace("_", r"\_") + "%"
    rows = db.execute(_SEARCH, {"q": q, "like": like, "prefix": q.replace("%", "") + "%", "term": term_code,
                                "limit": limit}).all()
    codes = [r.course_code for r in rows]
    sections = get_term_sections(db, term_code, codes)
    verdicts = _verdicts(db, student_id, term_code, codes, today) if student_id else {}
    hits = []
    for r in rows:
        open_sections = [s for s in sections.get(r.course_code, []) if usable(s)]
        hits.append(CourseHit(
            course_code=r.course_code, title=r.title, units=r.units_default, level=r.level, type_code=r.type_code,
            offered_sections=len(open_sections),
            seats_left=None if any(s.seats_left is None for s in open_sections)
            else sum(s.seats_left or 0 for s in open_sections),
            similarity=round(float(r.sim), 3), check=verdicts.get(r.course_code)))
    return SearchResult(q=q, term_code=term_code, results=hits)


def _verdicts(db: Session, student_id: str, term_code: str, codes: list[str], today: date) -> dict[str, RuleVerdict]:
    out = {}
    for code, results in course_check_results(db, student_id, term_code, codes, today).items():
        blocking = [f"{r.code}: {r.detail}" for r in results if r.blocking]
        noted = [r for r in results if not r.blocking and (r.flag or not r.passed)]
        out[code] = RuleVerdict(eligible=not blocking, blocking=blocking,
                                flags=sorted({r.flag or r.code.lower() for r in noted}),
                                notes=[r.detail for r in noted])
    return out


def course_detail(db: Session, code: str, term_code: str | None) -> CourseDetail:
    course = db.get(Course, code)
    if course is None:
        raise CourseNotFound(f"Unknown course {code}")
    offering = db.get(CourseOffering, (code, term_code)) if term_code else None
    sections: list[SectionView] = []
    if term_code:
        found = get_term_sections(db, term_code, [code]).get(code, [])
        names: dict[str, list[str]] = {}
        for section_id, name in db.execute(
                select(SectionInstructor.section_id, Instructor.full_name)
                .join(Instructor, Instructor.instructor_id == SectionInstructor.instructor_id)
                .where(SectionInstructor.section_id.in_([s.section_id for s in found])).order_by(Instructor.full_name)):
            names.setdefault(section_id, []).append(name)
        sections = [SectionView(section_id=s.section_id, section_code=s.section_code, session_code=s.session_code,
                                days_times=days_times(s), modality=s.modality, location=s.location, status=s.status,
                                capacity=s.capacity, seats_left=s.seats_left,
                                instructors=names.get(s.section_id, [])) for s in found]
    prereq = get_course_prerequisites(db, code)
    return CourseDetail(
        course_code=course.course_code, title=course.title, description=course.description,
        units=course.units_default, units_type=course.units_type, units_min=course.units_min,
        units_max=course.units_max, level=course.level, type_code=course.type_code,
        typical_offering=course.typical_offering,
        prerequisites=PrerequisiteView(options=prereq.options, text=prereq.prereq_text,
                                       catalog_applies=prereq.catalog_prereqs_apply),
        syllabus_url=course.syllabus_url or (offering.syllabus_url if offering else None) or None,
        term_code=term_code, offered=bool(offering and offering.status == "offered"), sections=sections)
