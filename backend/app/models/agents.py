"""F. Agent outputs, audit, messages."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, Text, Uuid, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentRun(Base):
    """One row per agent run. `input` must never contain personal names."""

    __tablename__ = "agent_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    agent_name: Mapped[str] = mapped_column(Text)
    student_id: Mapped[str | None] = mapped_column(Text, ForeignKey("students.student_id"))
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("schedules.schedule_id"))
    status: Mapped[str] = mapped_column(Text)
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    tool_calls: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'"))
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    explanation: Mapped[str | None] = mapped_column(Text)
    llm_provider: Mapped[str | None] = mapped_column(Text)
    llm_model: Mapped[str | None] = mapped_column(Text)
    llm_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DegreeAudit(Base):
    __tablename__ = "degree_audits"

    audit_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    student_id: Mapped[str] = mapped_column(Text, ForeignKey("students.student_id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("agent_runs.run_id"))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class ElectiveSuggestion(Base):
    __tablename__ = "elective_suggestions"

    suggestion_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    student_id: Mapped[str] = mapped_column(Text, ForeignKey("students.student_id"))
    term_code: Mapped[str] = mapped_column(Text, ForeignKey("terms.term_code"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("agent_runs.run_id"))
    course_code: Mapped[str] = mapped_column(Text, ForeignKey("courses.course_code"))
    rank: Mapped[int] = mapped_column(Integer)
    similarity: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    popularity: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    fit: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    final_score: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    basis: Mapped[str] = mapped_column(Text)
    group_code: Mapped[str] = mapped_column(Text, server_default=text("'popular'"))
    matched_interest: Mapped[str | None] = mapped_column(Text)
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class RiskFlag(Base):
    __tablename__ = "risk_flags"

    flag_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    student_id: Mapped[str] = mapped_column(Text, ForeignKey("students.student_id"))
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("schedules.schedule_id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("agent_runs.run_id"))
    flag_code: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    rule_text: Mapped[str | None] = mapped_column(Text)
    suggested_action: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'open'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class ComplianceCheck(Base):
    __tablename__ = "compliance_checks"

    check_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schedules.schedule_id", ondelete="CASCADE")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("agent_runs.run_id"))
    stage: Mapped[str] = mapped_column(Text)
    check_code: Mapped[str] = mapped_column(Text)
    passed: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class AuditLog(Base):
    """Append-only (a DB trigger rejects UPDATE and DELETE). Insert only."""

    __tablename__ = "audit_log"

    log_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    actor_type: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Message(Base):
    __tablename__ = "messages"

    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    student_id: Mapped[str] = mapped_column(Text, ForeignKey("students.student_id"))
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("schedules.schedule_id"))
    sender_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.user_id"))
    body: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    __tablename__ = "notifications"

    notification_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    student_id: Mapped[str] = mapped_column(Text, ForeignKey("students.student_id"))
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("schedules.schedule_id"))
    template: Mapped[str] = mapped_column(Text)
    to_email: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
