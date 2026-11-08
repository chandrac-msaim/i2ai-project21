"""A. Program structure and rules."""
from decimal import Decimal
from typing import Any

from sqlalchemy import ARRAY, Boolean, ForeignKey, ForeignKeyConstraint, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class University(Base):
    __tablename__ = "universities"

    university_code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    short_name: Mapped[str] = mapped_column(Text)
    email_domain: Mapped[str | None] = mapped_column(Text, unique=True)


class Program(Base):
    __tablename__ = "programs"

    program_code: Mapped[str] = mapped_column(Text, primary_key=True)
    university_code: Mapped[str] = mapped_column(Text, ForeignKey("universities.university_code"))
    name: Mapped[str] = mapped_column(Text)
    degree: Mapped[str] = mapped_column(Text)
    college: Mapped[str] = mapped_column(Text)
    handbook_notes: Mapped[str | None] = mapped_column(Text)


class Track(Base):
    __tablename__ = "tracks"

    track_code: Mapped[str] = mapped_column(Text, primary_key=True)
    program_code: Mapped[str] = mapped_column(Text, ForeignKey("programs.program_code"))
    name: Mapped[str] = mapped_column(Text)
    duration_months: Mapped[int] = mapped_column(Integer)
    total_units: Mapped[int] = mapped_column(Integer)
    academic_terms: Mapped[int] = mapped_column(Integer)
    min_qpa: Mapped[Decimal] = mapped_column(Numeric(3, 2))
    typical_units_per_term: Mapped[int] = mapped_column(Integer)
    max_units_per_term: Mapped[int] = mapped_column(Integer)
    max_units_extended: Mapped[int] = mapped_column(Integer)  # 55..this only with QPA > extended_units_min_qpa
    extended_units_min_qpa: Mapped[Decimal] = mapped_column(Numeric(3, 2))
    internship_required: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class TrackTermPlan(Base):
    __tablename__ = "track_term_plan"

    track_code: Mapped[str] = mapped_column(Text, ForeignKey("tracks.track_code"), primary_key=True)
    term_seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[str] = mapped_column(Text)
    is_academic: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class CoreRequirement(Base):
    __tablename__ = "core_requirements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["track_code", "recommended_term_seq"],
            ["track_term_plan.track_code", "track_term_plan.term_seq"],
        ),
    )

    track_code: Mapped[str] = mapped_column(Text, ForeignKey("tracks.track_code"), primary_key=True)
    course_code: Mapped[str] = mapped_column(Text, ForeignKey("courses.course_code"), primary_key=True)
    recommended_term_seq: Mapped[int | None] = mapped_column(Integer)
    waivable: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    waiver_method: Mapped[str | None] = mapped_column(Text)
    waiver_adds_elective_units: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    catalog_prereqs_apply: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    prereq_override_note: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class ElectiveRequirement(Base):
    __tablename__ = "elective_requirements"

    track_code: Mapped[str] = mapped_column(Text, ForeignKey("tracks.track_code"), primary_key=True)
    min_units: Mapped[int] = mapped_column(Integer)
    max_units_outside_college: Mapped[int] = mapped_column(Integer)
    max_undergrad_units: Mapped[int] = mapped_column(Integer)
    undergrad_min_grade: Mapped[str] = mapped_column(Text)
    excluded_subject_areas: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    notes: Mapped[str | None] = mapped_column(Text)


class RuleSetting(Base):
    __tablename__ = "rule_settings"

    rule_code: Mapped[str] = mapped_column(Text, primary_key=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    severity: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
