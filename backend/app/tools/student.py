"""Student and program tools: history, waivers, interests, degree requirements. Read-only.

Outputs carry student_id only, never names. get_redaction_names is the one exception: it exists so the
agent base can remove those names before any LLM call.
"""
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Advisor,
    CoreRequirement,
    Course,
    CourseWaiver,
    ElectiveRequirement,
    Enrollment,
    GradeScale,
    Student,
    StudentDocument,
    StudentInterest,
    Term,
    Track,
    TrackTermPlan,
)
from app.models.views import StudentProgress


class EnrollmentOut(BaseModel):
    course_code: str
    title: str
    term_code: str
    term_seq: int
    units: Decimal
    status: str  # completed | in_progress | ...
    grade: str | None
    is_passing: bool | None


class StudentHistory(BaseModel):
    student_id: str
    track_code: str
    entry_term: str
    status: str
    preferred_time_of_day: str | None
    preferred_modality: str | None
    qpa: Decimal | None
    units_completed: Decimal
    units_in_progress: Decimal
    completed: list[str]  # passed courses
    in_progress: list[str]
    enrollments: list[EnrollmentOut]


class WaiverOut(BaseModel):
    course_code: str
    method: str
    status: str  # pending | approved | rejected
    note: str | None


class CoreCourse(BaseModel):
    course_code: str
    title: str
    units_default: Decimal | None
    recommended_term_seq: int | None
    waivable: bool
    waiver_method: str | None
    waiver_adds_elective_units: bool = False
    catalog_prereqs_apply: bool
    notes: str | None


class PlanTerm(BaseModel):
    term_seq: int
    season: str  # Fall | Spring | Summer
    is_academic: bool


class ProgramRequirements(BaseModel):
    track_code: str
    program_code: str
    total_units: int
    min_qpa: Decimal
    typical_units_per_term: int
    max_units_per_term: int
    max_units_extended: int
    extended_units_min_qpa: Decimal
    core: list[CoreCourse]
    core_units: Decimal
    elective_min_units: int
    max_undergrad_units: int
    max_units_outside_college: int
    excluded_subject_areas: list[str]
    term_plan: list[PlanTerm] = []


class InterestOut(BaseModel):
    interest_id: int
    label: str
    source: str
    embedding: list[float] | None


class InterestLabel(BaseModel):
    label: str
    source: str
    status: str  # suggested | confirmed | removed


class DocumentOut(BaseModel):
    doc_type: str  # resume | undergrad_transcript
    text_content: str


def get_student_history(db: Session, student_id: str) -> StudentHistory:
    student = _student(db, student_id)
    progress = db.get(StudentProgress, student_id)
    rows = db.execute(
        select(Enrollment, Course.title, Term.seq, GradeScale.is_passing)
        .join(Course, Course.course_code == Enrollment.course_code)
        .join(Term, Term.term_code == Enrollment.term_code)
        .outerjoin(GradeScale, GradeScale.grade == Enrollment.grade)
        .where(Enrollment.student_id == student_id)
        .order_by(Term.seq, Enrollment.course_code)
    ).all()
    enrollments = [
        EnrollmentOut(course_code=e.course_code, title=title, term_code=e.term_code, term_seq=seq,
                      units=e.units, status=e.status, grade=e.grade, is_passing=passing)
        for e, title, seq, passing in rows
    ]
    return StudentHistory(
        student_id=student.student_id, track_code=student.track_code, entry_term=student.entry_term,
        status=student.status, preferred_time_of_day=student.preferred_time_of_day,
        preferred_modality=student.preferred_modality,
        qpa=progress.qpa if progress else None,
        units_completed=progress.units_completed if progress else Decimal(0),
        units_in_progress=progress.units_in_progress if progress else Decimal(0),
        completed=[e.course_code for e in enrollments if e.status == "completed" and e.is_passing],
        in_progress=[e.course_code for e in enrollments if e.status == "in_progress"],
        enrollments=enrollments,
    )


def get_course_waivers(db: Session, student_id: str) -> list[WaiverOut]:
    return [WaiverOut(course_code=w.course_code, method=w.method, status=w.status, note=w.note)
            for w in db.scalars(select(CourseWaiver).where(CourseWaiver.student_id == student_id)
                                .order_by(CourseWaiver.course_code))]


def get_program_requirements(db: Session, track_code: str) -> ProgramRequirements:
    track = db.get(Track, track_code)
    if track is None:
        raise ValueError(f"Unknown track {track_code}")
    electives = db.get(ElectiveRequirement, track_code)
    core = [
        CoreCourse(course_code=r.course_code, title=title, units_default=units,
                   recommended_term_seq=r.recommended_term_seq, waivable=r.waivable,
                   waiver_method=r.waiver_method, waiver_adds_elective_units=r.waiver_adds_elective_units,
                   catalog_prereqs_apply=r.catalog_prereqs_apply, notes=r.notes)
        for r, title, units in db.execute(
            select(CoreRequirement, Course.title, Course.units_default)
            .join(Course, Course.course_code == CoreRequirement.course_code)
            .where(CoreRequirement.track_code == track_code)
            .order_by(CoreRequirement.recommended_term_seq.nulls_last(), CoreRequirement.course_code))
    ]
    return ProgramRequirements(
        track_code=track.track_code, program_code=track.program_code, total_units=track.total_units,
        min_qpa=track.min_qpa, typical_units_per_term=track.typical_units_per_term,
        max_units_per_term=track.max_units_per_term, max_units_extended=track.max_units_extended,
        extended_units_min_qpa=track.extended_units_min_qpa, core=core,
        core_units=sum((c.units_default or Decimal(0) for c in core), Decimal(0)),
        elective_min_units=electives.min_units if electives else 0,
        max_undergrad_units=electives.max_undergrad_units if electives else 12,
        max_units_outside_college=electives.max_units_outside_college if electives else 0,
        excluded_subject_areas=list(electives.excluded_subject_areas) if electives else [],
        term_plan=[PlanTerm(term_seq=t.term_seq, season=t.season, is_academic=t.is_academic)
                   for t in db.scalars(select(TrackTermPlan).where(TrackTermPlan.track_code == track_code)
                                       .order_by(TrackTermPlan.term_seq))],
    )


def get_confirmed_interests(db: Session, student_id: str) -> list[InterestOut]:
    """Only confirmed interests are used for matching (DESIGN D13)."""
    return [InterestOut(interest_id=i.interest_id, label=i.label, source=i.source,
                        embedding=None if i.embedding is None else [float(x) for x in i.embedding])
            for i in db.scalars(select(StudentInterest).where(StudentInterest.student_id == student_id,
                                                              StudentInterest.status == "confirmed")
                                .order_by(StudentInterest.interest_id))]


def get_interest_labels(db: Session, student_id: str) -> list[InterestLabel]:
    """Every interest the student has, whatever its status (removed ones too), oldest first."""
    return [InterestLabel(label=i.label, source=i.source, status=i.status)
            for i in db.scalars(select(StudentInterest).where(StudentInterest.student_id == student_id)
                                .order_by(StudentInterest.interest_id))]


def get_student_documents(db: Session, student_id: str) -> list[DocumentOut]:
    """Resume / undergrad transcript text, newest first per type."""
    return [DocumentOut(doc_type=d.doc_type, text_content=d.text_content)
            for d in db.scalars(select(StudentDocument).where(StudentDocument.student_id == student_id)
                                .order_by(StudentDocument.doc_type, StudentDocument.uploaded_at.desc()))]


def get_redaction_names(db: Session, student_id: str) -> list[tuple[str, list[str]]]:
    """(role, name parts) for the student and their advisor, for app.llm.redact.Redactor."""
    student = _student(db, student_id)
    people = [("the student", [student.first_name, student.last_name])]
    if student.advisor_id and (advisor := db.get(Advisor, student.advisor_id)):
        people.append(("the advisor", [advisor.first_name, advisor.last_name]))
    return people


def _student(db: Session, student_id: str) -> Student:
    student = db.get(Student, student_id)
    if student is None:
        raise ValueError(f"Unknown student {student_id}")
    return student
