"""Advisor-facing read models: caseload, schedule review page, recheck. Flushes, never commits.

An advisor sees the students they advise; an admin sees everyone (and can read, not decide).
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents import RiskAgent
from app.agents.compliance_agent import aggregate, compose_email, template_paragraph
from app.config import get_settings
from app.llm import LLMClient
from app.models import (
    Advisor,
    AgentRun,
    ComplianceCheck,
    Course,
    Message,
    RiskFlag,
    Schedule,
    ScheduleChange,
    ScheduleItem,
    Program,
    Student,
    StudentProgress,
    Track,
    User,
)
from app.rules.engine import CandidateSchedule, ScheduleItemIn
from app.rules.repository import validate_schedule
from app.services import schedule_views as sv
from app.services.decision_email import email_subject, render_html
from app.services.program_info import (
    ProgramInfo,
    UniversityInfo,
    program_of_student,
    program_of_track,
    programs_of_advisor,
    university_of_advisor,
)
from app.services.terms import term_labels
from app.services.access import Actor, NotFound, assert_access
from app.services.messaging import MessageView, thread
from app.services.schedule_lifecycle import InvalidTransition
from app.tools.schedules import get_decision_email_facts, get_schedule, get_student_contact

_OPEN_STATUSES = ("draft", "submitted", "approved", "rejected")


class ScheduleBrief(BaseModel):
    schedule_id: uuid.UUID
    term_code: str
    term_label: str
    version: int
    status: str
    total_units: Decimal
    submitted_at: datetime | None


class CaseloadRow(BaseModel):
    student_id: str
    first_name: str
    last_name: str
    track_code: str
    entry_term: str
    units_completed: Decimal
    units_in_progress: Decimal
    qpa: Decimal | None
    min_qpa: Decimal
    total_units: int  # the program's units (e.g. 144)
    university_name: str  # shown with every student row
    program_title: str
    below_min_qpa: bool
    open_flags: int
    flag_codes: list[str]
    top_severity: str | None  # high | medium | low
    schedule: ScheduleBrief | None  # the student's newest schedule that is not discarded or superseded
    unread_messages: int  # from the student, not yet read


class CaseloadMetrics(BaseModel):
    students: int
    with_risk_flags: int
    awaiting_review: int
    unread_messages: int


class Caseload(BaseModel):
    advisor_name: str | None
    advisor_title: str | None  # advisors.title
    university: UniversityInfo | None  # the advisor sees only students of this university
    programs: list[ProgramInfo]
    metrics: CaseloadMetrics
    rows: list[CaseloadRow]


def caseload(db: Session, actor: Actor) -> Caseload:
    query = select(Student).order_by(Student.last_name, Student.first_name)
    advisor = None
    if actor.role == "advisor":
        advisor = db.get(Advisor, actor.advisor_id)
        query = (query.join(Track, Track.track_code == Student.track_code).join(Program, Program.program_code == Track.program_code)
                 .where(Student.advisor_id == actor.advisor_id, Program.university_code == advisor.university_code))
    students = db.scalars(query.where(Student.status == "active")).all()
    ids = [s.student_id for s in students]
    progress = {p.student_id: p for p in db.scalars(select(StudentProgress).where(StudentProgress.student_id.in_(ids)))}
    tracks = {c: (m, u) for c, m, u in db.execute(select(Track.track_code, Track.min_qpa, Track.total_units)).all()}
    min_qpa = {c: m for c, (m, _) in tracks.items()}
    latest: dict[str, Schedule] = {}
    for sched in db.scalars(select(Schedule).where(Schedule.student_id.in_(ids), Schedule.status.in_(_OPEN_STATUSES))
                            .order_by(Schedule.created_at.desc(), Schedule.version.desc())):
        latest.setdefault(sched.student_id, sched)
    sched_ids = [s.schedule_id for s in latest.values()]
    units = dict(db.execute(select(ScheduleItem.schedule_id, func.sum(ScheduleItem.units))
                            .where(ScheduleItem.schedule_id.in_(sched_ids)).group_by(ScheduleItem.schedule_id)).all())
    flags: dict[uuid.UUID, list[RiskFlag]] = {}
    for flag in db.scalars(select(RiskFlag).where(RiskFlag.schedule_id.in_(sched_ids), RiskFlag.status == "open")):
        flags.setdefault(flag.schedule_id, []).append(flag)
    unread = dict(db.execute(select(Message.student_id, func.count()).join(User, User.user_id == Message.sender_user_id)
                             .where(Message.student_id.in_(ids), Message.read_at.is_(None), User.role == "student")
                             .group_by(Message.student_id)).all())
    rank = {"high": 0, "medium": 1, "low": 2}
    labels = term_labels(db)
    program_by_track = {t: program_of_track(db, t) for t in {s.track_code for s in students}}
    rows = []
    for s in students:
        sched = latest.get(s.student_id)
        mine = flags.get(sched.schedule_id, []) if sched else []
        p = progress.get(s.student_id)
        qpa = p.qpa if p else None
        info = program_by_track[s.track_code]
        rows.append(CaseloadRow(
            student_id=s.student_id, first_name=s.first_name, last_name=s.last_name, track_code=s.track_code,
            entry_term=s.entry_term, units_completed=p.units_completed if p else Decimal(0),
            units_in_progress=p.units_in_progress if p else Decimal(0), qpa=qpa, min_qpa=min_qpa[s.track_code],
            total_units=tracks[s.track_code][1], university_name=info.university.name, program_title=info.title,
            below_min_qpa=qpa is not None and qpa < min_qpa[s.track_code], open_flags=len(mine),
            flag_codes=sorted({f.flag_code for f in mine}),
            top_severity=min((f.severity for f in mine), key=rank.get, default=None),
            schedule=ScheduleBrief(schedule_id=sched.schedule_id, term_code=sched.term_code,
                                   term_label=labels.get(sched.term_code, sched.term_code), version=sched.version,
                                   status=sched.status, total_units=Decimal(units.get(sched.schedule_id) or 0),
                                   submitted_at=sched.submitted_at) if sched else None,
            unread_messages=unread.get(s.student_id, 0)))
    metrics = CaseloadMetrics(students=len(rows), with_risk_flags=sum(r.open_flags > 0 for r in rows),
                              awaiting_review=sum(bool(r.schedule and r.schedule.status == "submitted") for r in rows),
                              unread_messages=sum(r.unread_messages for r in rows))
    return Caseload(advisor_name=f"{advisor.first_name} {advisor.last_name}" if advisor else None,
                    advisor_title=advisor.title if advisor else None,
                    university=university_of_advisor(db, advisor.advisor_id) if advisor else None,
                    programs=programs_of_advisor(db, advisor.advisor_id) if advisor else [], metrics=metrics, rows=rows)


# ---------- the review page ----------

class StudentBrief(BaseModel):
    student_id: str
    first_name: str
    last_name: str
    email: str
    track_code: str
    entry_term: str
    qpa: Decimal | None
    min_qpa: Decimal
    below_min_qpa: bool
    units_completed: Decimal
    units_in_progress: Decimal
    advisor_name: str | None
    university_name: str  # header of the review page
    program: ProgramInfo


class ChangeView(BaseModel):
    action: str
    course_code: str | None
    course_title: str | None
    replaced_course_code: str | None  # on a swap: the course that left
    replaced_course_title: str | None
    actor: str
    changed_at: datetime


class RecordedChecks(BaseModel):
    stage: str  # submit | approve
    checked_at: datetime
    checks: list[sv.CheckView]


class AiRunView(BaseModel):
    run_id: uuid.UUID
    agent_name: str
    status: str
    llm_provider: str | None
    llm_model: str | None
    llm_ms: int | None
    tools: list[str]  # the tools the agent called, in order
    started_at: datetime


class RemovedCourse(BaseModel):
    course_code: str
    title: str


class AdvisorScheduleView(BaseModel):
    student: StudentBrief
    schedule: sv.ScheduleView  # items carry in_ai_draft and ai_original_course; checks are live
    recorded_checks: RecordedChecks | None  # what the compliance gate recorded last
    changes: list[ChangeView]
    ai_explanation: str | None
    load_rationale: str | None
    ai_run: AiRunView | None
    removed_from_ai_draft: list[RemovedCourse]  # in the AI's draft, not in the student's schedule now
    messages: list[MessageView]
    versions: list[sv.HistoryItem]


def advisor_schedule(db: Session, actor: Actor, user_id: uuid.UUID, schedule_id: uuid.UUID,
                     today: date) -> AdvisorScheduleView:
    schedule = _schedule(db, schedule_id)
    assert_access(db, actor, schedule.student_id)
    view = sv.schedule_view(db, schedule, today)
    titles = dict(db.execute(select(Course.course_code, Course.title)).all())
    changes = [ChangeView(action=c.action, course_code=c.course_code, course_title=titles.get(c.course_code),
                          replaced_course_code=c.replaced_course_code,
                          replaced_course_title=titles.get(c.replaced_course_code), actor=c.actor,
                          changed_at=c.changed_at)
               for c in db.scalars(select(ScheduleChange).where(ScheduleChange.schedule_id == schedule_id)
                                   .order_by(ScheduleChange.change_id))]
    now = {i.course_code for i in view.items}
    removed = [RemovedCourse(course_code=b["course_code"], title=titles.get(b["course_code"], b["course_code"]))
               for b in (schedule.ai_baseline or []) if b["course_code"] not in now]
    return AdvisorScheduleView(
        student=_student_brief(db, schedule.student_id), schedule=view, recorded_checks=_recorded(db, schedule_id),
        changes=changes, ai_explanation=schedule.ai_explanation, load_rationale=schedule.load_rationale,
        ai_run=_ai_run(db, schedule), removed_from_ai_draft=removed,
        messages=thread(db, user_id, actor, schedule.student_id, mark_read=True).messages,
        versions=sv.history(db, schedule.student_id, schedule.term_code))


def _student_brief(db: Session, student_id: str) -> StudentBrief:
    s = db.get(Student, student_id)
    p = db.get(StudentProgress, student_id)
    advisor = db.get(Advisor, s.advisor_id) if s.advisor_id else None
    min_qpa = db.get(Track, s.track_code).min_qpa
    program = program_of_student(db, student_id)
    qpa = p.qpa if p else None
    return StudentBrief(student_id=s.student_id, first_name=s.first_name, last_name=s.last_name, email=s.email,
                        track_code=s.track_code, entry_term=s.entry_term, qpa=qpa, min_qpa=min_qpa,
                        below_min_qpa=qpa is not None and qpa < min_qpa,
                        units_completed=p.units_completed if p else Decimal(0),
                        units_in_progress=p.units_in_progress if p else Decimal(0),
                        advisor_name=f"{advisor.first_name} {advisor.last_name}" if advisor else None,
                        university_name=program.university.name, program=program)


def _recorded(db: Session, schedule_id: uuid.UUID) -> RecordedChecks | None:
    last = db.scalar(select(ComplianceCheck).where(ComplianceCheck.schedule_id == schedule_id)
                     .order_by(ComplianceCheck.checked_at.desc(), ComplianceCheck.check_id.desc()).limit(1))
    if last is None:
        return None
    rows = db.scalars(select(ComplianceCheck).where(ComplianceCheck.schedule_id == schedule_id,
                                                    ComplianceCheck.run_id == last.run_id)
                      .order_by(ComplianceCheck.check_id)).all()
    return RecordedChecks(stage=last.stage, checked_at=last.checked_at, checks=[
        sv.CheckView(code=c.check_code, passed=c.passed, detail=c.detail or "",
                     advisor_flag=not c.passed and (c.detail or "").startswith("Flag for the advisor"))
        for c in rows])


def _ai_run(db: Session, schedule: Schedule) -> AiRunView | None:
    run = db.get(AgentRun, schedule.built_by_run_id) if schedule.built_by_run_id else None
    if run is None:
        return None
    return AiRunView(run_id=run.run_id, agent_name=run.agent_name, status=run.status, llm_provider=run.llm_provider,
                     llm_model=run.llm_model, llm_ms=run.llm_ms, tools=[c.get("tool", "?") for c in run.tool_calls],
                     started_at=run.started_at)


# ---------- recheck ----------

class Recheck(BaseModel):
    schedule_id: uuid.UUID
    status: str
    checks: list[sv.CheckView]  # live: seats and sections are read again now
    blocking: list[str]
    can_approve: bool
    flags: list[sv.FlagView]  # refreshed by Agent 4
    risk_run_id: uuid.UUID


def recheck(db: Session, actor: Actor, schedule_id: uuid.UUID, llm: LLMClient | None, today: date) -> Recheck:
    """Re-read seats and rules for a submitted schedule and refresh its risk flags. Changes nothing else."""
    if actor.role != "advisor":
        raise PermissionError("Only an advisor can recheck a schedule")
    schedule = _schedule(db, schedule_id)
    assert_access(db, actor, schedule.student_id)
    if schedule.status != "submitted":
        raise InvalidTransition(f"Only a submitted schedule can be rechecked (this one is {schedule.status})")
    run = RiskAgent(db, llm=llm, today=today).run(schedule_id=schedule_id)
    results = validate_schedule(db, CandidateSchedule(
        student_id=schedule.student_id, term_code=schedule.term_code,
        items=[ScheduleItemIn(course_code=i.course_code, section_id=i.section_id, units=i.units)
               for i in schedule.items]), today)
    blocking = [f"{r.code}{' ' + r.course_code if r.course_code else ''}: {r.detail}" for r in results if r.blocking]
    checks = [sv.CheckView(code=c.code, passed=c.passed, detail=c.detail,
                           advisor_flag=not c.passed and not any(r.blocking for r in results if r.code == c.code))
              for c in aggregate(results)]
    flags = [sv.FlagView(flag_id=f.flag_id, flag_code=f.flag_code, severity=f.severity, message=f.message,
                         rule_text=f.rule_text, suggested_action=f.suggested_action, status=f.status)
             for f in db.scalars(select(RiskFlag).where(RiskFlag.schedule_id == schedule_id,
                                                        RiskFlag.status == "open").order_by(RiskFlag.flag_id))]
    return Recheck(schedule_id=schedule_id, status=schedule.status, checks=checks, blocking=blocking,
                   can_approve=not blocking, flags=flags, risk_run_id=run.run_id)


def _schedule(db: Session, schedule_id: uuid.UUID) -> Schedule:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise NotFound(f"Unknown schedule {schedule_id}")
    return schedule


# ---------- email preview ----------

class EmailPreview(BaseModel):
    to: str
    cc: str | None
    from_addr: str
    subject: str
    html: str
    text: str  # the plain-text part that goes with the HTML
    note: str


def email_preview(db: Session, actor: Actor, schedule_id: uuid.UUID, decision: str, comment: str | None) -> EmailPreview:
    """The email the student would get for this decision, with the template opening (no LLM call, nothing is sent
    or saved). The real email may word its first paragraph differently."""
    if actor.role != "advisor":
        raise PermissionError("Only an advisor can preview the decision email")
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be 'approve' or 'reject'")
    schedule = _schedule(db, schedule_id)
    assert_access(db, actor, schedule.student_id)
    approved = decision == "approve"
    facts = get_decision_email_facts(db, schedule_id)
    settings = get_settings()
    subject = email_subject(facts.university_name, facts.term_label, approved)
    schedule_out = get_schedule(db, schedule_id)
    paragraph = template_paragraph(schedule_out, approved)
    text = compose_email(get_student_contact(db, schedule.student_id), schedule_out, approved, paragraph, comment)
    html = render_html(facts, approved=approved, paragraph=paragraph,
                       comment=comment, subject=subject, from_addr=settings.mail_from,
                       schedule_url=f"{settings.frontend_url.rstrip('/')}/schedule/{schedule.term_code}")
    cc = f"{facts.advisor_name} <{facts.advisor_email}>" if facts.advisor_email else facts.advisor_name
    return EmailPreview(to=f"{facts.student_name} <{facts.student_email}>", cc=cc,
                        from_addr=settings.mail_from, subject=subject, html=html, text=text,
                        note="Preview only. Nothing is sent until you approve or reject.")
