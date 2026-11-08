"""C. People and login."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Advisor(Base):
    __tablename__ = "advisors"

    advisor_id: Mapped[str] = mapped_column(Text, primary_key=True)
    first_name: Mapped[str] = mapped_column(Text)
    last_name: Mapped[str] = mapped_column(Text)
    honorific: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    advisor_type: Mapped[str] = mapped_column(Text)
    scope_track: Mapped[str | None] = mapped_column(Text, ForeignKey("tracks.track_code"))
    university_code: Mapped[str] = mapped_column(Text, ForeignKey("universities.university_code"))


class Student(Base):
    __tablename__ = "students"

    student_id: Mapped[str] = mapped_column(Text, primary_key=True)
    first_name: Mapped[str] = mapped_column(Text)
    last_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, unique=True)
    track_code: Mapped[str] = mapped_column(Text, ForeignKey("tracks.track_code"))
    advisor_id: Mapped[str | None] = mapped_column(Text, ForeignKey("advisors.advisor_id"))
    entry_term: Mapped[str] = mapped_column(Text, ForeignKey("terms.term_code"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    undergrad_institution: Mapped[str | None] = mapped_column(Text)
    undergrad_major: Mapped[str | None] = mapped_column(Text)
    preferred_time_of_day: Mapped[str | None] = mapped_column(Text)
    preferred_modality: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    advisor: Mapped["Advisor | None"] = relationship()


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    student_id: Mapped[str | None] = mapped_column(Text, ForeignKey("students.student_id"), unique=True)
    advisor_id: Mapped[str | None] = mapped_column(Text, ForeignKey("advisors.advisor_id"), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    student: Mapped["Student | None"] = relationship()
    advisor: Mapped["Advisor | None"] = relationship()
