"""Which university and program a person belongs to (M8b). The portal serves several universities, so no screen or
email hard-codes a name: it reads it from the logged-in student's (or advisor's) program. Read-only."""
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Advisor, Program, Student, Track, University


class UniversityInfo(BaseModel):
    university_code: str
    name: str  # "Carnegie Mellon University"
    short_name: str  # "CMU"


class ProgramInfo(BaseModel):
    program_code: str
    name: str  # "Artificial Intelligence Systems Management"
    degree: str  # "Master of Science"
    college: str  # "Heinz College"
    title: str  # "Master of Science in Artificial Intelligence Systems Management"
    university: UniversityInfo


def _university(row: University) -> UniversityInfo:
    return UniversityInfo(university_code=row.university_code, name=row.name, short_name=row.short_name)


def _program(program: Program, university: University) -> ProgramInfo:
    return ProgramInfo(program_code=program.program_code, name=program.name, degree=program.degree,
                       college=program.college, title=f"{program.degree} in {program.name}",
                       university=_university(university))


def program_of_track(db: Session, track_code: str) -> ProgramInfo:
    program, university = db.execute(
        select(Program, University).join(University, University.university_code == Program.university_code)
        .join(Track, Track.program_code == Program.program_code).where(Track.track_code == track_code)).one()
    return _program(program, university)


def program_of_student(db: Session, student_id: str) -> ProgramInfo:
    return program_of_track(db, db.get(Student, student_id).track_code)


def university_code_of_student(db: Session, student_id: str) -> str | None:
    return db.scalar(select(Program.university_code).join(Track, Track.program_code == Program.program_code)
                     .join(Student, Student.track_code == Track.track_code).where(Student.student_id == student_id))


def university_of_advisor(db: Session, advisor_id: str) -> UniversityInfo | None:
    advisor = db.get(Advisor, advisor_id)
    university = db.get(University, advisor.university_code) if advisor else None
    return _university(university) if university else None


def programs_of_advisor(db: Session, advisor_id: str) -> list[ProgramInfo]:
    """The programs of the advisor's own students (and of their scope track), within the advisor's university."""
    advisor = db.get(Advisor, advisor_id)
    if advisor is None:
        return []
    track_codes = set(db.scalars(select(Student.track_code).where(Student.advisor_id == advisor_id,
                                                                  Student.status == "active")))
    if advisor.scope_track:
        track_codes.add(advisor.scope_track)
    seen: dict[str, ProgramInfo] = {}
    for track in sorted(track_codes):
        info = program_of_track(db, track)
        if info.university.university_code == advisor.university_code:
            seen.setdefault(info.program_code, info)
    return list(seen.values())
