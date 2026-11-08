"""Who may see or touch which student's data. Used by the API routes and by services; no FastAPI imports.

student -> only their own data; advisor -> the students they advise; admin -> everything (read), but an admin
never decides a schedule (see schedule_review.decide).
"""
from typing import Protocol

from sqlalchemy.orm import Session

from app.models import Advisor, Student
from app.services.program_info import university_code_of_student


class NotFound(LookupError):
    """An id that does not exist (the API answers 404)."""


class Actor(Protocol):
    role: str
    student_id: str | None
    advisor_id: str | None


def can_access_student(db: Session, actor: Actor, student_id: str) -> bool:
    if actor.role == "admin":
        return True
    if actor.role == "student":
        return actor.student_id == student_id
    if actor.role == "advisor" and actor.advisor_id:
        student = db.get(Student, student_id)
        if student is None or student.advisor_id != actor.advisor_id:
            return False
        advisor = db.get(Advisor, actor.advisor_id)
        return advisor is not None and advisor.university_code == university_code_of_student(db, student_id)
    return False


def assert_access(db: Session, actor: Actor, student_id: str) -> None:
    if not can_access_student(db, actor, student_id):
        raise PermissionError("You do not have access to this student's data")
