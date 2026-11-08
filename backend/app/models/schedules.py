"""E. Schedules for the planning term."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Schedule(Base):
    __tablename__ = "schedules"

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    student_id: Mapped[str] = mapped_column(Text, ForeignKey("students.student_id"))
    term_code: Mapped[str] = mapped_column(Text, ForeignKey("terms.term_code"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'draft'"))
    previous_schedule_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("schedules.schedule_id"))
    edited_by_student: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    ai_explanation: Mapped[str | None] = mapped_column(Text)
    load_rationale: Mapped[str | None] = mapped_column(Text)
    built_by_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("agent_runs.run_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(Text, ForeignKey("advisors.advisor_id"))
    advisor_comment: Mapped[str | None] = mapped_column(Text)
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ai_baseline: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)  # the draft as the AI built it

    items: Mapped[list["ScheduleItem"]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan", passive_deletes=True
    )


class ScheduleItem(Base):
    __tablename__ = "schedule_items"

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schedules.schedule_id", ondelete="CASCADE"), primary_key=True
    )
    course_code: Mapped[str] = mapped_column(Text, ForeignKey("courses.course_code"), primary_key=True)
    section_id: Mapped[str | None] = mapped_column(Text, ForeignKey("sections.section_id"))
    units: Mapped[Decimal] = mapped_column(Numeric(4, 1))
    item_type: Mapped[str] = mapped_column(Text)
    is_locked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    added_by: Mapped[str] = mapped_column(Text)
    is_conditional: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    needs_approval: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    match_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    reason: Mapped[str | None] = mapped_column(Text)

    schedule: Mapped["Schedule"] = relationship(back_populates="items")


class ScheduleChange(Base):
    __tablename__ = "schedule_changes"

    change_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schedules.schedule_id", ondelete="CASCADE")
    )
    action: Mapped[str] = mapped_column(Text)
    course_code: Mapped[str | None] = mapped_column(Text, ForeignKey("courses.course_code"))
    replaced_course_code: Mapped[str | None] = mapped_column(Text, ForeignKey("courses.course_code"))
    actor: Mapped[str] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
