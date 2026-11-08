"""Tools for Agents 4 and 5: read a schedule, rule details, contact data; save risk flags, compliance checks and
notifications; apply a submission or an advisor's decision.

The only write tools besides app.tools.records (CLAUDE.md rule 2). State changes go through
app.services.schedule_lifecycle, which enforces who may do what; every one writes audit_log (rule 3).
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import (
    Advisor,
    AuditLog,
    ComplianceCheck,
    Course,
    Notification,
    RiskFlag,
    RuleSetting,
    Schedule,
    ScheduleItem,
    Section,
    Student,
    Term,
)
from app.services import schedule_lifecycle
from app.services.program_info import program_of_student
from app.services.terms import label_of, term_label
from app.tools.catalog import SectionOut, get_term_sections
from app.tools.records import get_current_audit


class ScheduleItemOut(BaseModel):
    course_code: str
    title: str
    section_id: str | None
    section_code: str | None
    units: Decimal
    item_type: str
    is_locked: bool
    is_conditional: bool
    needs_approval: bool


class ScheduleOut(BaseModel):
    schedule_id: uuid.UUID
    student_id: str
    term_code: str
    term_label: str  # "Spring 2027": for any text a person reads
    version: int
    status: str
    advisor_comment: str | None
    items: list[ScheduleItemOut]
    total_units: Decimal


class RuleDetail(BaseModel):
    params: dict[str, Any]
    severity: str | None
    description: str


class ContactOut(BaseModel):
    """Who to write to. Names stay here: they go into the email in code, never into an LLM prompt."""
    student_id: str
    first_name: str
    email: str
    advisor_id: str | None
    advisor_name: str | None  # "Dr. Last"
    university_name: str = ""  # for the email subject, opening line and footer
    college: str = ""
    program_title: str = ""
    program_name: str = ""  # "Artificial Intelligence Systems Management"


class RiskFlagIn(BaseModel):
    flag_code: str
    severity: str  # high | medium | low
    message: str
    rule_text: str | None = None
    suggested_action: str | None = None


class CheckRecord(BaseModel):
    code: str
    passed: bool
    detail: str


class DecisionOut(BaseModel):
    schedule_id: uuid.UUID
    status: str


# ---------- reads ----------

def get_schedule(db: Session, schedule_id: uuid.UUID) -> ScheduleOut:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise ValueError(f"Unknown schedule {schedule_id}")
    rows = db.execute(
        select(ScheduleItem, Course.title, Section.section_code)
        .join(Course, Course.course_code == ScheduleItem.course_code)
        .outerjoin(Section, Section.section_id == ScheduleItem.section_id)
        .where(ScheduleItem.schedule_id == schedule_id)
        .order_by(ScheduleItem.item_type.desc(), ScheduleItem.course_code)
    ).all()
    items = [ScheduleItemOut(course_code=i.course_code, title=title, section_id=i.section_id, section_code=code,
                             units=i.units, item_type=i.item_type, is_locked=i.is_locked,
                             is_conditional=i.is_conditional, needs_approval=i.needs_approval)
             for i, title, code in rows]
    return ScheduleOut(schedule_id=schedule.schedule_id, student_id=schedule.student_id,
                       term_code=schedule.term_code, term_label=label_of(db, schedule.term_code), version=schedule.version, status=schedule.status,
                       advisor_comment=schedule.advisor_comment, items=items,
                       total_units=sum((i.units for i in items), Decimal(0)))


def get_rule_details(db: Session) -> dict[str, RuleDetail]:
    """rule_code -> params, severity and description for every active rule setting."""
    return {r.rule_code: RuleDetail(params=dict(r.params), severity=r.severity, description=r.description)
            for r in db.scalars(select(RuleSetting).where(RuleSetting.active))}


def get_student_contact(db: Session, student_id: str) -> ContactOut:
    student = db.get(Student, student_id)
    if student is None:
        raise ValueError(f"Unknown student {student_id}")
    advisor = db.get(Advisor, student.advisor_id) if student.advisor_id else None
    name = f"{advisor.honorific + ' ' if advisor and advisor.honorific else ''}{advisor.last_name}" if advisor else None
    program = program_of_student(db, student_id)
    return ContactOut(student_id=student_id, first_name=student.first_name, email=student.email,
                      advisor_id=student.advisor_id, advisor_name=name, university_name=program.university.name,
                      college=program.college, program_title=program.title, program_name=program.name)


# ---------- Agent 4 ----------

def save_risk_flags(db: Session, student_id: str, schedule_id: uuid.UUID, run_id: uuid.UUID,
                    flags: list[RiskFlagIn]) -> int:
    """Replace the schedule's open flags: earlier open ones become 'resolved' (history kept), new ones are added."""
    db.execute(update(RiskFlag).where(RiskFlag.schedule_id == schedule_id, RiskFlag.status == "open")
               .values(status="resolved"))
    db.add_all(RiskFlag(student_id=student_id, schedule_id=schedule_id, run_id=run_id, flag_code=f.flag_code,
                        severity=f.severity, message=f.message, rule_text=f.rule_text,
                        suggested_action=f.suggested_action) for f in flags)
    db.add(AuditLog(actor_type="agent", actor_id="risk", action="risk_flags_saved", entity_type="schedule",
                    entity_id=str(schedule_id),
                    details={"student_id": student_id, "flags": sorted({f.flag_code for f in flags})}))
    db.flush()
    return len(flags)


# ---------- Agent 5 ----------

def save_compliance_checks(db: Session, schedule_id: uuid.UUID, run_id: uuid.UUID, stage: str,
                           checks: list[CheckRecord], allowed: bool) -> int:
    db.add_all(ComplianceCheck(schedule_id=schedule_id, run_id=run_id, stage=stage, check_code=c.code,
                               passed=c.passed, detail=c.detail) for c in checks)
    db.add(AuditLog(actor_type="agent", actor_id="compliance",
                    action=f"compliance_{stage}_{'passed' if allowed else 'blocked'}", entity_type="schedule",
                    entity_id=str(schedule_id),
                    details={"failed": [c.code for c in checks if not c.passed], "allowed": allowed}))
    db.flush()
    return len(checks)


def apply_submission(db: Session, schedule_id: uuid.UUID) -> DecisionOut:
    """The gate passed: the student's draft goes to the advisor."""
    schedule = schedule_lifecycle.submit(db, _schedule(db, schedule_id), by_student=True)
    return DecisionOut(schedule_id=schedule.schedule_id, status=schedule.status)


def apply_decision(db: Session, schedule_id: uuid.UUID, decision: str, advisor_id: str | None,
                   comment: str | None) -> DecisionOut:
    """Apply an advisor's decision ('approve' | 'reject'). NotAdvisor unless advisor_id is a real advisor."""
    step = {"approve": schedule_lifecycle.approve, "reject": schedule_lifecycle.reject}.get(decision)
    if step is None:
        raise ValueError(f"Unknown decision {decision!r}")
    schedule = step(db, _schedule(db, schedule_id), advisor_id, comment)
    return DecisionOut(schedule_id=schedule.schedule_id, status=schedule.status)


def create_notification(db: Session, student_id: str, schedule_id: uuid.UUID, template: str, to_email: str,
                        subject: str, body: str) -> int:
    note = Notification(student_id=student_id, schedule_id=schedule_id, template=template, to_email=to_email,
                        subject=subject, body=body, status="queued")
    db.add(note)
    db.flush()
    return note.notification_id


def mark_notification(db: Session, notification_id: int, sent: bool, error: str | None = None) -> None:
    note = db.get(Notification, notification_id)
    note.status = "sent" if sent else "failed"
    note.sent_at = datetime.now(timezone.utc) if sent else None
    note.error = error
    db.add(AuditLog(actor_type="agent", actor_id="compliance",
                    action="notification_sent" if sent else "notification_failed", entity_type="notification",
                    entity_id=str(notification_id), details={"schedule_id": str(note.schedule_id), "error": error}))
    db.flush()


def _schedule(db: Session, schedule_id: uuid.UUID) -> Schedule:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise ValueError(f"Unknown schedule {schedule_id}")
    return schedule


# ---------- Agent 5 / email preview: the facts the decision email shows ----------

class EmailRow(BaseModel):
    course_code: str
    title: str
    when: str  # "Mon, Wed 9:30"
    units: Decimal


class DecisionEmailFacts(BaseModel):
    """Everything the HTML email shows besides the advisor's comment. Names stay in code, never in an LLM prompt."""
    student_first: str
    student_name: str
    student_email: str
    advisor_name: str | None  # "Dr. Morgan Lee"
    advisor_short: str | None  # "Dr. Lee"
    advisor_email: str | None
    term_label: str  # "Spring 2027"
    university_name: str
    college: str
    program_title: str
    program_name: str
    registration_closes: date | None
    projected_graduation: str | None
    rows: list[EmailRow]
    total_units: Decimal


_DAY = {"M": "Mon", "T": "Tue", "W": "Wed", "R": "Thu", "F": "Fri", "S": "Sat", "U": "Sun"}


def _when(section: SectionOut | None) -> str:
    if section is None or section.is_time_tba or not section.meetings:
        return "Time to be announced"
    meetings = sorted(section.meetings, key=lambda m: "MTWRFSU".index(m.day))
    start = meetings[0].start
    days = ", ".join(dict.fromkeys(_DAY[m.day] for m in meetings))
    return f"{days} {start.hour}:{start.minute:02d}"


def get_decision_email_facts(db: Session, schedule_id: uuid.UUID) -> DecisionEmailFacts:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise ValueError(f"Unknown schedule {schedule_id}")
    student = db.get(Student, schedule.student_id)
    advisor = db.get(Advisor, student.advisor_id) if student.advisor_id else None
    term = db.get(Term, schedule.term_code)
    program = program_of_student(db, schedule.student_id)
    audit = get_current_audit(db, schedule.student_id) or {}
    rows = db.execute(
        select(ScheduleItem, Course.title).join(Course, Course.course_code == ScheduleItem.course_code)
        .where(ScheduleItem.schedule_id == schedule_id).order_by(ScheduleItem.item_type.desc(), ScheduleItem.course_code)
    ).all()
    sections = {s.section_id: s for secs in get_term_sections(
        db, schedule.term_code, [i.course_code for i, _ in rows]).values() for s in secs}
    honorific = f"{advisor.honorific} " if advisor and advisor.honorific else ""
    return DecisionEmailFacts(
        student_first=student.first_name, student_name=f"{student.first_name} {student.last_name}",
        student_email=student.email,
        advisor_name=f"{honorific}{advisor.first_name} {advisor.last_name}" if advisor else None,
        advisor_short=f"{honorific}{advisor.last_name}" if advisor else None,
        advisor_email=getattr(advisor, "email", None) if advisor else None,
        term_label=term_label(term) if term else schedule.term_code,
        university_name=program.university.name, college=program.college, program_title=program.title,
        program_name=program.name,
        registration_closes=term.registration_closes if term else None,
        projected_graduation=(label_of(db, audit["projected_graduation_term"])
                              if audit.get("projected_graduation_term") else None),
        rows=[EmailRow(course_code=i.course_code, title=title,
                       when=_when(sections.get(i.section_id) if i.section_id else None), units=i.units)
              for i, title in rows],
        total_units=sum((i.units for i, _ in rows), Decimal(0)))
