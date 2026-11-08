"""Read-only mappings for the view v_student_progress and the materialized view course_popularity.

They use their own metadata, so they are never part of Base.metadata.tables and nothing can try to create them.
"""
from decimal import Decimal

from sqlalchemy import MetaData, Numeric, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ViewBase(DeclarativeBase):
    metadata = MetaData(schema="advisor")


class StudentProgress(ViewBase):
    __tablename__ = "v_student_progress"

    student_id: Mapped[str] = mapped_column(Text, primary_key=True)
    track_code: Mapped[str] = mapped_column(Text)
    units_completed: Mapped[Decimal] = mapped_column(Numeric)
    units_in_progress: Mapped[Decimal] = mapped_column(Numeric)
    qpa: Mapped[Decimal | None] = mapped_column(Numeric)


class CoursePopularity(ViewBase):
    __tablename__ = "course_popularity"

    track_code: Mapped[str] = mapped_column(Text, primary_key=True)
    course_code: Mapped[str] = mapped_column(Text, primary_key=True)
    track_share: Mapped[Decimal] = mapped_column(Numeric)
    demand: Mapped[Decimal] = mapped_column(Numeric)
    popularity: Mapped[Decimal] = mapped_column(Numeric)
