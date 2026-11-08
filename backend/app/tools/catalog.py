"""Catalog tools: courses offered in a term, their sections, their prerequisites. Read-only."""
from collections import defaultdict
from datetime import date, time
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CoreRequirement, Course, CourseOffering, CoursePrerequisite, CourseType, Section, TermSession


class ActiveCourse(BaseModel):
    course_code: str
    title: str
    course_number: int
    level: str
    units_default: Decimal | None
    type_code: str | None
    needs_approval: bool
    offered_sections: int
    seats_left: int | None  # sum over offered sections; None = at least one section has no capacity limit


class MeetingOut(BaseModel):
    day: str  # M T W R F S U
    start: time
    end: time


class SectionOut(BaseModel):
    section_id: str
    course_code: str
    term_code: str
    section_code: str
    session_code: str
    session_start: date
    session_end: date
    modality: str
    location: str | None
    status: str  # offered | cancelled
    capacity: int | None
    enrolled: int
    seats_left: int | None  # None = no capacity limit
    is_time_tba: bool
    meetings: list[MeetingOut]


class Prerequisites(BaseModel):
    course_code: str
    options: list[list[str]]  # OR of ANDs: satisfied when every code of any one option is done
    prereq_text: str | None
    catalog_prereqs_apply: bool = True  # False when the track's core plan overrides the catalog
    override_note: str | None = None


def get_active_courses(db: Session, term_code: str) -> list[ActiveCourse]:
    """Courses offered in `term_code` that have at least one offered (not cancelled) section."""
    unlimited = func.bool_or(Section.capacity.is_(None))
    rows = db.execute(
        select(Course, CourseType.needs_approval, func.count(Section.section_id),
               func.sum(Section.seats_left), unlimited)
        .join(CourseOffering, CourseOffering.course_code == Course.course_code)
        .join(Section, (Section.course_code == Course.course_code) & (Section.term_code == term_code))
        .outerjoin(CourseType, CourseType.type_code == Course.type_code)
        .where(CourseOffering.term_code == term_code, CourseOffering.status == "offered",
               Section.status == "offered")
        .group_by(Course.course_code, CourseType.needs_approval)
        .order_by(Course.course_code)
    ).all()
    return [
        ActiveCourse(
            course_code=c.course_code, title=c.title, course_number=c.course_number, level=c.level,
            units_default=c.units_default, type_code=c.type_code, needs_approval=bool(needs_approval),
            offered_sections=n, seats_left=None if has_unlimited else int(seats or 0),
        )
        for c, needs_approval, n, seats, has_unlimited in rows
    ]


def get_sections(db: Session, course_code: str, term_code: str) -> list[SectionOut]:
    """All sections of a course in a term (cancelled ones included, with status), with meetings."""
    return get_term_sections(db, term_code, [course_code]).get(course_code, [])


def get_term_label(db: Session, term_code: str) -> str:
    """"Spring 2027" for S27: the name of a term in any text a person reads (and in LLM facts)."""
    from app.services.terms import label_of
    return label_of(db, term_code)


def get_term_sections(db: Session, term_code: str, course_codes: list[str]) -> dict[str, list[SectionOut]]:
    """Sections of many courses in one query: course_code -> sections (cancelled ones included), with meetings."""
    rows = db.execute(
        select(Section, TermSession.start_date, TermSession.end_date)
        .join(TermSession, (TermSession.term_code == Section.term_code)
              & (TermSession.session_code == Section.session_code))
        .where(Section.course_code.in_(course_codes), Section.term_code == term_code)
        .order_by(Section.course_code, Section.section_code)
    ).all()
    out: dict[str, list[SectionOut]] = {}
    for s, start, end in rows:
        out.setdefault(s.course_code, []).append(SectionOut(
            section_id=s.section_id, course_code=s.course_code, term_code=s.term_code,
            section_code=s.section_code, session_code=s.session_code, session_start=start, session_end=end,
            modality=s.modality, location=s.location, status=s.status, capacity=s.capacity,
            enrolled=s.enrolled, seats_left=None if s.capacity is None else s.seats_left,
            is_time_tba=s.is_time_tba,
            meetings=[MeetingOut(day=m.day_of_week, start=m.start_time, end=m.end_time) for m in s.meetings]))
    return out


def get_course_prerequisites(db: Session, course_code: str, track_code: str | None = None) -> Prerequisites:
    """Prerequisite options of a course; with `track_code`, also whether the core plan overrides them."""
    course = db.get(Course, course_code)
    if course is None:
        raise ValueError(f"Unknown course {course_code}")
    options: dict[int, list[str]] = defaultdict(list)
    for p in db.scalars(select(CoursePrerequisite).where(CoursePrerequisite.course_code == course_code)
                        .order_by(CoursePrerequisite.option_no, CoursePrerequisite.required_course_code)):
        options[p.option_no].append(p.required_course_code)
    core = db.get(CoreRequirement, (track_code, course_code)) if track_code else None
    return Prerequisites(
        course_code=course_code,
        options=[codes for _, codes in sorted(options.items())],
        prereq_text=course.prereq_text,
        catalog_prereqs_apply=core.catalog_prereqs_apply if core else True,
        override_note=core.prereq_override_note if core else None,
    )
