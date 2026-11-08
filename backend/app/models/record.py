"""D. Student academic record."""
import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Numeric, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GradeScale(Base):
    __tablename__ = "grade_scale"

    grade: Mapped[str] = mapped_column(Text, primary_key=True)
    quality_points: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    counts_units: Mapped[bool] = mapped_column(Boolean)
    is_passing: Mapped[bool] = mapped_column(Boolean)


class Enrollment(Base):
    __tablename__ = "enrollments"

    enrollment_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    student_id: Mapped[str] = mapped_column(Text, ForeignKey("students.student_id"))
    course_code: Mapped[str] = mapped_column(Text, ForeignKey("courses.course_code"))
    term_code: Mapped[str] = mapped_column(Text, ForeignKey("terms.term_code"))
    section_id: Mapped[str | None] = mapped_column(Text, ForeignKey("sections.section_id"))
    units: Mapped[Decimal] = mapped_column(Numeric(4, 1))
    status: Mapped[str] = mapped_column(Text)
    grade: Mapped[str | None] = mapped_column(Text, ForeignKey("grade_scale.grade"))
    source: Mapped[str] = mapped_column(Text, server_default=text("'synthetic'"))


class CourseWaiver(Base):
    __tablename__ = "course_waivers"

    waiver_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    student_id: Mapped[str] = mapped_column(Text, ForeignKey("students.student_id"))
    course_code: Mapped[str] = mapped_column(Text, ForeignKey("courses.course_code"))
    method: Mapped[str] = mapped_column(Text, server_default=text("'exemption_exam'"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    decided_by: Mapped[str | None] = mapped_column(Text, ForeignKey("advisors.advisor_id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)


class StudentDocument(Base):
    __tablename__ = "student_documents"

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    student_id: Mapped[str] = mapped_column(Text, ForeignKey("students.student_id"))
    doc_type: Mapped[str] = mapped_column(Text)
    filename: Mapped[str | None] = mapped_column(Text)
    text_content: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class StudentInterest(Base):
    __tablename__ = "student_interests"

    interest_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    student_id: Mapped[str] = mapped_column(Text, ForeignKey("students.student_id"))
    label: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'suggested'"))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
