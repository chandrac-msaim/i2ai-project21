"""Agent 5 - Compliance (the gate).

stage 'submit'   runs the 7 checks on the student's draft and blocks on any blocking failure. If it passes, the
                 draft is sent to the advisor (status 'submitted'). No LLM call.
stage 'approve'  runs after an advisor decided ('approve' | 'reject'). Approving re-runs the 7 checks live, so a
                 section that filled up since the student submitted blocks the approval and the schedule stays
                 'submitted'. Rejecting never needs seats. When allowed, the advisor's decision is applied, a
                 notifications row is created and the email is sent (Mailpit in development); one LLM call writes
                 the email's opening paragraph (fallback: template). The student's name is added in code after
                 the LLM call, never sent to the LLM.
Either stage records compliance_checks (one row per check code) and audit_log, blocked or not. The agent never
decides: the rule engine says pass/fail, and only an advisor (checked again in the lifecycle service and the
apply_decision tool) can approve or reject.
"""
import re
from decimal import Decimal
from email.utils import formataddr
from typing import Literal

from pydantic import BaseModel

from app.agents.base import Agent, RunContext, ungrounded_numbers
from app.config import get_settings
from app.rules.engine import CODES, CandidateSchedule, CheckResult, ScheduleItemIn
from app.services.decision_email import email_subject, opening_line, render_html
from app.services.schedule_lifecycle import InvalidTransition
from app.tools.schedules import CheckRecord, ContactOut, ScheduleOut

Stage = Literal["submit", "approve"]
EMAIL_SYSTEM = ("You write short, friendly emails from a university advising office directly to a student. "
                "Always write in the second person: 'you', 'your schedule', 'your advisor'. Never call the reader "
                "'the student'. Do not use any names. Say only what you are told.")
_THIRD_PERSON = re.compile(r"\bthe student\b|\bthe advisor\b", re.IGNORECASE)
EXPECTED_STATUS = {"submit": "draft", "approve": "submitted"}


class ComplianceOut(BaseModel):
    stage: str
    decision: str | None  # approve | reject (stage 'approve' only)
    allowed: bool
    blocked_reasons: list[str]
    checks: list[CheckRecord]
    advisor_flags: list[str]  # non-blocking: petition, special approval, conditional prerequisite, overload
    schedule_status: str  # after the run
    notification_id: int | None = None
    email_subject: str | None = None
    email_sent: bool | None = None
    email_error: str | None = None


class _Facts(BaseModel):
    schedule: ScheduleOut
    results: list[CheckResult]
    contact: ContactOut | None


class ComplianceAgent(Agent[ComplianceOut]):
    """run(schedule_id=..., stage='submit')  or  run(schedule_id=..., stage='approve', decision='approve'|'reject',
    advisor_id='ADV001', comment='...')"""

    name = "compliance"
    tools = frozenset({"get_schedule", "validate_schedule", "get_student_contact", "save_compliance_checks",
                       "apply_submission", "apply_decision", "create_notification", "send_email",
                       "mark_notification", "get_decision_email_facts"})
    max_attempts = 1

    def plan(self, ctx: RunContext) -> _Facts:
        stage = ctx.inputs["stage"]
        if stage not in EXPECTED_STATUS or (stage == "approve" and ctx.inputs.get("decision") not in
                                            ("approve", "reject")):
            raise ValueError("stage must be 'submit' or 'approve' (with decision 'approve' or 'reject')")
        schedule = self.call_tool("get_schedule", ctx.schedule_id)
        if schedule.status != EXPECTED_STATUS[stage]:
            raise InvalidTransition(f"Stage '{stage}' needs a {EXPECTED_STATUS[stage]} schedule "
                                    f"(this one is {schedule.status})")
        candidate = CandidateSchedule(student_id=schedule.student_id, term_code=schedule.term_code, items=[
            ScheduleItemIn(course_code=i.course_code, section_id=i.section_id, units=i.units)
            for i in schedule.items])
        results = self.call_tool("validate_schedule", candidate, ctx.today)  # live: reads current seats
        contact = self.call_tool("get_student_contact", schedule.student_id) if stage == "approve" else None
        return _Facts(schedule=schedule, results=results, contact=contact)

    def decide(self, ctx: RunContext, facts: _Facts) -> ComplianceOut:
        stage, decision = ctx.inputs["stage"], ctx.inputs.get("decision")
        blocking = [r for r in facts.results if r.blocking]
        rejecting = stage == "approve" and decision == "reject"
        allowed = rejecting or not blocking  # a rejection never needs the checks to pass
        return ComplianceOut(
            stage=stage, decision=decision, allowed=allowed,
            blocked_reasons=[] if allowed else [_reason(r) for r in blocking],
            checks=aggregate(facts.results),
            advisor_flags=sorted({_flag(r) for r in facts.results if r.flag or (not r.passed and not r.blocking)}),
            schedule_status=facts.schedule.status)

    def save(self, ctx: RunContext, facts: _Facts, decision: ComplianceOut) -> None:
        self.call_tool("save_compliance_checks", facts.schedule.schedule_id, ctx.run_id, decision.stage,
                       decision.checks, decision.allowed)
        if not decision.allowed:
            return  # the schedule stays as it is; the checks and the reasons are on record
        if decision.stage == "submit":
            decision.schedule_status = self.call_tool("apply_submission", facts.schedule.schedule_id).status
            return
        applied = self.call_tool("apply_decision", facts.schedule.schedule_id, decision.decision,
                                 ctx.inputs.get("advisor_id"), ctx.inputs.get("comment"))
        decision.schedule_status = applied.status
        self._notify(ctx, facts, decision)

    def explain(self, ctx: RunContext, facts: _Facts, decision: ComplianceOut) -> str:
        failed = [c.code for c in decision.checks if not c.passed]
        flags = f" Flags for the advisor: {', '.join(decision.advisor_flags)}." if decision.advisor_flags else ""
        if decision.stage == "submit":
            if decision.allowed:
                return f"All 7 checks passed; the schedule was sent to the advisor.{flags}"
            return f"Submission blocked by {', '.join(failed)}: {'; '.join(decision.blocked_reasons)}"
        if not decision.allowed:
            return (f"Approval blocked by the live re-check, schedule stays submitted: "
                    f"{'; '.join(decision.blocked_reasons)}")
        sent = ("email sent" if decision.email_sent else f"email not sent ({decision.email_error})")
        return f"The advisor {decision.decision}d the schedule; {sent}.{flags}"

    # ---------- email ----------

    def _notify(self, ctx: RunContext, facts: _Facts, decision: ComplianceOut) -> None:
        schedule, contact = facts.schedule, facts.contact
        approved = decision.decision == "approve"
        subject = email_subject(contact.university_name, schedule.term_label, approved)
        paragraph = self._paragraph(ctx, schedule, approved, ctx.inputs.get("comment"))
        body = compose_email(contact, schedule, approved, paragraph, ctx.inputs.get("comment"))
        decision.email_subject = subject
        decision.notification_id = self.call_tool(
            "create_notification", schedule.student_id, schedule.schedule_id,
            "schedule_approved" if approved else "schedule_rejected", contact.email, subject, body)
        settings = get_settings()
        email_facts = self.call_tool("get_decision_email_facts", schedule.schedule_id)
        html = render_html(email_facts, approved=approved, paragraph=paragraph, comment=ctx.inputs.get("comment"),
                           subject=subject, from_addr=settings.mail_from,
                           schedule_url=f"{settings.frontend_url.rstrip('/')}/schedule/{schedule.term_code}")
        cc = (formataddr((email_facts.advisor_name or "", email_facts.advisor_email))
              if email_facts.advisor_email else None)  # the advisor gets a copy: "Dr. Morgan Lee <address>"
        sent = self.call_tool("send_email", contact.email, subject, body, html=html, cc=cc)
        decision.email_sent, decision.email_error = sent.sent, sent.error
        self.call_tool("mark_notification", decision.notification_id, sent.sent, sent.error)

    def _paragraph(self, ctx: RunContext, schedule: ScheduleOut, approved: bool, comment: str | None) -> str:
        fallback = template_paragraph(schedule, approved)
        prompt = (f"Write the opening paragraph (at most 60 words, plain text, no greeting, no sign-off, no names) of "
                  f"an email to a student. The email tells them that their advisor "
                  f"{'approved' if approved else 'did not approve'} their {schedule.term_label} course schedule of "
                  f"{len(schedule.items)} courses and {_n(schedule.total_units)} units. "
                  + ("Say that registration continues as planned. " if approved else
                     "Say that they can change the schedule and send it again. ")
                  + (f"The advisor's comment will be added below your paragraph, so do not repeat it: {comment}. "
                     if comment else "")
                  + "Address the reader directly: write 'you', 'your schedule', 'your advisor'. "
                    "Never write 'the student'. Use only these facts.")
        text = second_person(self.ask_llm(prompt, fallback=fallback, system=EMAIL_SYSTEM, max_tokens=150))
        words = len(text.split())
        if words < 8 or words > 120 or ungrounded_numbers(text, prompt) or _THIRD_PERSON.search(text):
            ctx.tool_calls.append({"tool": "llm_guard", "rejected": True,
                                   "reason": "email paragraph too short, too long, third person or with invented "
                                             "numbers"})
            return fallback
        return text


# ---------- pure helpers ----------

def second_person(text: str) -> str:
    """The email is written to the student: "the student's schedule" -> "your schedule", "the advisor" -> "your
    advisor", "their" -> "your"; a greeting line the LLM added is dropped (the name is added in code)."""
    lines = [ln for ln in text.strip().splitlines()]
    while lines and re.match(r"^\s*(dear|hi|hello)\b", lines[0], re.IGNORECASE):
        lines.pop(0)
    text = "\n".join(lines).strip()

    def swap(pattern: str, new: str, value: str) -> str:
        return re.sub(pattern, lambda m: new.capitalize() if m.group(0)[0].isupper() else new, value,
                      flags=re.IGNORECASE)

    for pattern, new in ((r"\bthe student['\u2019]s\b", "your"), (r"\bthe advisor['\u2019]s\b", "your advisor's"),
                         (r"\bthe student\b", "you"), (r"\bthe advisor\b", "your advisor"),
                         (r"\btheirs\b", "yours"), (r"\btheir\b", "your")):
        text = swap(pattern, new, text)
    return text


def aggregate(results: list[CheckResult]) -> list[CheckRecord]:
    """One record per check code (all 7, in order). passed = every result of that check passed; a failed
    non-blocking result is a flag for the advisor and is marked as such in the detail."""
    records = []
    for code in CODES:
        mine = [r for r in results if r.code == code]
        details = []
        for r in mine:
            if r.passed:
                continue
            prefix = "" if r.blocking else "Flag for the advisor: "
            details.append(f"{prefix}{r.course_code + ': ' if r.course_code else ''}{r.detail}")
        records.append(CheckRecord(code=code, passed=all(r.passed for r in mine),
                                   detail="; ".join(details) or (mine[0].detail if mine else "No courses to check")))
    return records


def compose_email(contact: ContactOut, schedule: ScheduleOut, approved: bool, paragraph: str,
                  comment: str | None) -> str:
    courses = "\n".join(f"  - {i.course_code} {i.title} (section {i.section_code or '-'}, {_n(i.units)} units)"
                        for i in schedule.items)
    opening = opening_line(contact.university_name, contact.program_name, schedule.term_label, approved)
    parts = [f"Dear {contact.first_name}, {opening}", paragraph]
    if comment:
        parts.append(f"Advisor comment: {comment}")  # right after the opening, like the comment box in the HTML
    parts.append(f"Schedule for {schedule.term_label} ({_n(schedule.total_units)} units):\n{courses}")
    footer = ", ".join(x for x in (contact.university_name, contact.college, contact.program_title) if x)
    parts.append(f"Best regards,\n{contact.advisor_name or 'Your advisor'}\nGray DI Degree Pathway Advisor"
                 + (f"\n{footer}" if footer else ""))
    return "\n\n".join(parts)


def template_paragraph(schedule: ScheduleOut, approved: bool) -> str:
    if approved:
        return (f"Your advisor has approved your course schedule for {schedule.term_label}: "
                f"{len(schedule.items)} courses, {_n(schedule.total_units)} units. No further action is needed.")
    return (f"Your advisor has not approved your course schedule for {schedule.term_label}. Please read the comment "
            f"below, change the schedule and send it again.")


def _reason(r: CheckResult) -> str:
    return f"{r.code}{' ' + r.course_code if r.course_code else ''}: {r.detail}"


def _flag(r: CheckResult) -> str:
    return r.flag or r.code.lower()


def _n(value) -> str:
    return f"{Decimal(value).normalize():f}"
