"""M5: Agent 4 (risk flags), Agent 5 (compliance gate), submit / decide service, approval email in Mailpit.

Agents run with commit=False and a FakeLLM; the db fixture rolls the database back. Emails are really sent to
Mailpit (http://localhost:8025) and the test removes the messages it created.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import os
import httpx
import pytest
from sqlalchemy import select, text

from app.agents import AgentError, ComplianceAgent, RiskAgent, build_draft
from app.agents.compliance_agent import aggregate
from app.agents.risk_agent import apply_wording, compute_flags
from app.models import (
    AgentRun,
    AuditLog,
    ComplianceCheck,
    Notification,
    RiskFlag,
    Schedule,
    ScheduleChange,
    ScheduleItem,
    Student,
)
from app.rules.engine import CheckResult
from app.services import schedule_lifecycle as lifecycle
from app.services.schedule_lifecycle import (
    InvalidTransition,
    NotAdvisor,
    ScheduleItemDraft,
    ScheduleLocked,
)
from app.services.schedule_review import ComplianceBlocked, decide, submit_for_review
from app.tools import TOOLS
from app.tools.email import EmailResult
from app.llm import LLMClient
from tests.fakes import FakeLLM

PRIYA, DANIEL, ALEX = "S0056", "S0057", "S0126"
DEMO_DAY = date(2026, 11, 7)
TERM = "S27"
MAILPIT = os.environ.get("MAILPIT_API_URL", "http://localhost:8025/api/v1")  # set it when Mailpit runs on another port


@dataclass
class Who:
    role: str
    student_id: str | None = None
    advisor_id: str | None = None


def kw(llm=None) -> dict:
    return {"llm": llm or FakeLLM(), "today": DEMO_DAY, "commit": False}


def as_student(student_id: str) -> Who:
    return Who("student", student_id=student_id)


def advisor_of(db, student_id: str) -> Who:
    advisor_id = db.get(Student, student_id).advisor_id
    assert advisor_id, "demo students have an advisor"
    return Who("advisor", advisor_id=advisor_id)


def draft(db, student_id: str):
    return build_draft(db, student_id, TERM, **kw()).schedule.output.schedule_id


def flags_by_code(out) -> dict[str, list]:
    found: dict[str, list] = {}
    for f in out.flags:
        found.setdefault(f.flag_code, []).append(f)
    return found


def add_item(db, schedule_id, course: str, section: str, units: int = 6, item_type: str = "elective") -> None:
    db.add(ScheduleItem(schedule_id=schedule_id, course_code=course, section_id=section, units=Decimal(units),
                        item_type=item_type, is_locked=False, added_by="student"))
    db.flush()


def free_course(db, schedule_id, candidates=("95-719", "95-819", "95-881", "94-813")) -> tuple[str, str]:
    taken = set(db.scalars(select(ScheduleItem.course_code).where(ScheduleItem.schedule_id == schedule_id)))
    code = next(c for c in candidates if c not in taken)
    return code, db.scalar(text("SELECT section_id FROM sections WHERE course_code = :c AND term_code = :t "
                                "AND status = 'offered' ORDER BY section_id LIMIT 1"), {"c": code, "t": TERM})


# ---------- Mailpit ----------

def _mailpit_up() -> bool:
    try:
        return httpx.get(f"{MAILPIT}/info", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


needs_mailpit = pytest.mark.skipif(not _mailpit_up(), reason="Mailpit is not reachable on localhost:8025")


class Mailbox:
    def __init__(self) -> None:
        self.created: set[str] = set()

    def messages(self, to: str) -> list[dict]:
        return httpx.get(f"{MAILPIT}/search", params={"query": f"to:{to}", "limit": 200}).json()["messages"]

    def snapshot(self, to: str) -> set[str]:
        return {m["ID"] for m in self.messages(to)}

    def new_since(self, to: str, before: set[str]) -> list[dict]:
        new = [m for m in self.messages(to) if m["ID"] not in before]
        self.created |= {m["ID"] for m in new}
        return new

    def text(self, message_id: str) -> str:
        return httpx.get(f"{MAILPIT}/message/{message_id}").json()["Text"]


@pytest.fixture
def mailbox():
    box = Mailbox()
    yield box
    if box.created:
        httpx.request("DELETE", f"{MAILPIT}/messages", json={"IDs": sorted(box.created)})


@pytest.fixture(autouse=True)
def _no_real_email(request, monkeypatch):
    """Only tests that ask for `mailbox` send real email (and clean it up); the others use a fake sender."""
    if "mailbox" not in request.fixturenames:
        monkeypatch.setitem(TOOLS, "send_email", lambda to, subject, body, **_: EmailResult(sent=True))


# ---------- Agent 4: risk flags ----------

def test_daniel_gets_low_qpa(db):
    llm = FakeLLM(lambda p: "\n".join(f"F{n}: Fake explanation of flag {n} for review. || Fake action to take now."
                                      for n in range(1, 9)))
    schedule_id = draft(db, DANIEL)
    result = RiskAgent(db, **kw(llm)).run(schedule_id=schedule_id)
    low = flags_by_code(result.output)["LOW_QPA"]
    assert len(low) == 1 and low[0].severity == "high"
    import re
    assert re.search(r"QPA \d\.\d\d is below the 3\.00 minimum", low[0].message) or low[0].message.startswith("Fake explanation")
    assert low[0].rule_text and "QPA" in low[0].rule_text  # rule_settings description, shown as "Rule: ..."
    assert result.output.flags[0].flag_code == "LOW_QPA"  # highest severity first

    rows = db.scalars(select(RiskFlag).where(RiskFlag.schedule_id == schedule_id)).all()
    assert {r.flag_code for r in rows} == {f.flag_code for f in result.output.flags}
    assert all(r.status == "open" and r.run_id == result.run_id and r.student_id == DANIEL for r in rows)
    assert all(r.suggested_action for r in rows)

    run = db.get(AgentRun, result.run_id)
    assert run.agent_name == "risk" and run.status == "ok" and run.schedule_id == schedule_id
    called = [c["tool"] for c in run.tool_calls]
    assert {"get_schedule", "validate_schedule", "get_rule_details", "save_risk_flags", "llm"} <= set(called)
    assert called.count("llm") == 1 and len(llm.calls) == 1  # one LLM call for all the flags
    student = db.get(Student, DANIEL)
    for system, prompt in llm.calls:
        assert student.first_name.lower() not in (system + prompt).lower()


def test_priya_has_no_low_qpa_and_a_clean_schedule_costs_no_llm_call(db):
    schedule_id = draft(db, PRIYA)
    llm = FakeLLM("F1: should not be asked || nothing")
    result = RiskAgent(db, **kw(llm)).run(schedule_id=schedule_id)
    assert "LOW_QPA" not in flags_by_code(result.output)
    if not result.output.flags:
        assert llm.calls == [] and result.llm is None and "No risk flags" in result.explanation
        assert db.scalars(select(RiskFlag).where(RiskFlag.schedule_id == schedule_id)).all() == []


def test_a_second_run_resolves_the_first_runs_flags(db):
    schedule_id = draft(db, DANIEL)
    RiskAgent(db, **kw()).run(schedule_id=schedule_id)
    RiskAgent(db, **kw()).run(schedule_id=schedule_id)
    statuses = [r.status for r in db.scalars(select(RiskFlag).where(RiskFlag.flag_code == "LOW_QPA",
                                                                    RiskFlag.schedule_id == schedule_id)
                                            .order_by(RiskFlag.flag_id))]
    assert statuses == ["resolved", "open"]


def test_low_seats_flag_uses_the_live_seat_count(db):
    schedule_id = draft(db, PRIYA)
    item = next(i for i in db.get(Schedule, schedule_id).items if db.scalar(
        text("SELECT capacity FROM sections WHERE section_id = :s"), {"s": i.section_id}) is not None)
    db.execute(text("UPDATE sections SET enrolled = capacity - 1 WHERE section_id = :s"), {"s": item.section_id})
    out = RiskAgent(db, **kw(FakeLLM())).run(schedule_id=schedule_id).output
    low = [f for f in out.flags if f.flag_code == "LOW_SEATS"]
    assert [f.course_code for f in low] == [item.course_code] and low[0].severity == "low"
    assert "1 seat left" in low[0].message


def test_unit_overload_flag_is_informational(db):
    schedule_id = draft(db, PRIYA)  # 48 units; Priya's QPA is above 3.50
    for _ in range(2):  # 48 + 12 = 60 units
        course, section = free_course(db, schedule_id)
        add_item(db, schedule_id, course, section, 6)
    out = RiskAgent(db, **kw(FakeLLM())).run(schedule_id=schedule_id).output
    over = flags_by_code(out)["UNIT_OVERLOAD"]
    assert len(over) == 1 and over[0].severity == "low" and "60 units" in over[0].message
    assert over[0].rule_text and "55-60" in over[0].rule_text  # the UNIT_LIMIT rule's description
    # allowed, so the compliance gate lets it through and the advisor only sees the flag
    gate = ComplianceAgent(db, **kw()).run(schedule_id=schedule_id, stage="submit").output
    assert gate.allowed and "unit_overload" in gate.advisor_flags


def test_undergrad_cap_and_needs_approval_flags(db):
    saved = lifecycle.build(db, ALEX, TERM, [ScheduleItemDraft(course_code="94-400", section_id="94-400-S27-A",
                                                               units=Decimal(6), item_type="elective",
                                                               is_locked=False)])
    out = RiskAgent(db, **kw(FakeLLM())).run(schedule_id=saved.schedule_id).output
    found = flags_by_code(out)
    assert found["UNDERGRAD_CAP"][0].course_code == "94-400" and found["UNDERGRAD_CAP"][0].severity == "medium"
    assert found["NEEDS_APPROVAL"][0].course_code == "94-400" and found["NEEDS_APPROVAL"][0].severity == "low"


def test_compute_flags_conditional_prereq_and_behind_pace(db):
    schedule_id = draft(db, PRIYA)
    schedule = TOOLS["get_schedule"](db, schedule_id)
    history = TOOLS["get_student_history"](db, PRIYA)
    program = TOOLS["get_program_requirements"](db, history.track_code)
    rules = TOOLS["get_rule_details"](db)
    conditional = CheckResult(code="PREREQ", passed=True, severity="low", course_code="95-800", flag="conditional",
                              detail="Prerequisite in progress: 95-891")
    flags = compute_flags(schedule, history, program, rules, [conditional], {})
    cond = [f for f in flags if f.flag_code == "CONDITIONAL_PREREQ"]
    assert len(cond) == 1 and cond[0].course_code == "95-800" and cond[0].severity == "low"

    slow = history.model_copy(update={"units_completed": Decimal(6), "units_in_progress": Decimal(12)})
    pace = [f for f in compute_flags(schedule, slow, program, rules, [], {}) if f.flag_code == "BEHIND_PACE"]
    assert len(pace) == 1 and pace[0].severity == "medium" and "18 units" in pace[0].message
    assert not [f for f in compute_flags(schedule, history, program, rules, [], {}) if f.flag_code == "BEHIND_PACE"]
    fresh = history.model_copy(update={"units_completed": Decimal(0), "units_in_progress": Decimal(0),
                                       "enrollments": []})
    assert compute_flags(schedule, fresh, program, rules, [], {}) == []  # a new student is never "behind"


def test_flag_wording_comes_from_the_llm_but_invented_numbers_are_rejected(db):
    schedule_id = draft(db, DANIEL)
    flags = RiskAgent(db, **kw()).run(schedule_id=schedule_id).output.flags
    n = len(flags)
    prompt = "F1 | LOW_QPA | high | Cumulative QPA 2.56 is below the 3.00 minimum."
    good = "F1: The QPA of 2.56 is under the minimum of 3.00. || Check the student's support options first."
    bad = "F2: Ninety-nine students failed last year. 77 courses affected. || Cancel 42 schedules immediately."
    flags2 = [f.model_copy() for f in flags]
    rejected = apply_wording(flags2, good + "\n" + bad, prompt + " 54")
    assert 1 not in rejected and flags2[0].message.startswith("The QPA of 2.56")
    assert flags2[0].suggested_action == "Check the student's support options first."
    if n > 1:
        assert 2 in rejected and flags2[1].message == flags[1].message  # template text kept
    llm = FakeLLM("F1: Roughly 12345 students are affected. || Do 99 things.")
    result = RiskAgent(db, **kw(llm)).run(schedule_id=schedule_id)
    assert not any("12345" in f.message for f in result.output.flags)
    run = db.get(AgentRun, result.run_id)
    assert any(c["tool"] == "llm_guard" for c in run.tool_calls)


# ---------- Agent 5: submit ----------

def test_submit_for_priya_passes(db):
    schedule_id = draft(db, PRIYA)
    llm = FakeLLM()
    result = submit_for_review(db, as_student(PRIYA), schedule_id, **kw(llm))
    assert result.status == "submitted" and result.compliance.allowed and result.compliance.blocked_reasons == []

    schedule = db.get(Schedule, schedule_id)
    assert schedule.status == "submitted" and schedule.submitted_at is not None
    checks = db.scalars(select(ComplianceCheck).where(ComplianceCheck.schedule_id == schedule_id)).all()
    assert len(checks) == 7 and {c.stage for c in checks} == {"submit"}
    assert {c.check_code for c in checks} == {"UNIT_LIMIT", "PREREQ", "OFFERED", "LEVEL", "APPROVAL",
                                              "TIME_CONFLICT", "PRIVACY"}
    assert all(c.passed for c in checks if c.check_code in ("UNIT_LIMIT", "OFFERED", "TIME_CONFLICT", "PRIVACY"))
    assert all(c.run_id == result.compliance_run_id for c in checks)
    assert "submit" in db.scalars(select(ScheduleChange.action).where(ScheduleChange.schedule_id == schedule_id)).all()
    audit = set(db.scalars(select(AuditLog.action).where(AuditLog.entity_id == str(schedule_id))))
    assert {"schedule_submit", "compliance_submit_passed", "risk_flags_saved"} <= audit

    # both agents are logged; the gate makes no LLM call at 'submit'
    risk_run, gate_run = db.get(AgentRun, result.risk_run_id), db.get(AgentRun, result.compliance_run_id)
    assert (risk_run.agent_name, gate_run.agent_name) == ("risk", "compliance")
    assert gate_run.status == "ok" and gate_run.llm_provider is None
    assert {"get_schedule", "validate_schedule", "save_compliance_checks", "apply_submission"} <= {
        c["tool"] for c in gate_run.tool_calls}


def test_submit_is_blocked_when_a_check_fails_and_the_draft_stays_a_draft(db):
    schedule_id = draft(db, PRIYA)
    for _ in range(3):  # 48 + 18 = 66 units: UNIT_LIMIT blocks even with QPA > 3.50
        course, section = free_course(db, schedule_id)
        add_item(db, schedule_id, course, section, 6)
    with pytest.raises(ComplianceBlocked) as err:
        submit_for_review(db, as_student(PRIYA), schedule_id, **kw())
    assert err.value.stage == "submit" and any(r.startswith("UNIT_LIMIT") for r in err.value.reasons)
    schedule = db.get(Schedule, schedule_id)
    assert schedule.status == "draft" and schedule.submitted_at is None
    unit = db.scalar(select(ComplianceCheck).where(ComplianceCheck.schedule_id == schedule_id,
                                                   ComplianceCheck.check_code == "UNIT_LIMIT"))
    assert not unit.passed and "66 units" in unit.detail  # recorded even though blocked
    assert "compliance_submit_blocked" in set(db.scalars(select(AuditLog.action).where(
        AuditLog.entity_id == str(schedule_id))))
    assert db.get(AgentRun, err.value.run_id).status == "ok"  # a block is a decision, not a crash


def test_only_the_owner_can_submit_and_only_a_draft(db):
    schedule_id = draft(db, PRIYA)
    for who in (as_student(DANIEL), advisor_of(db, PRIYA), Who("admin")):
        with pytest.raises(PermissionError):
            submit_for_review(db, who, schedule_id, **kw())
    assert db.get(Schedule, schedule_id).status == "draft"
    submit_for_review(db, as_student(PRIYA), schedule_id, **kw())
    with pytest.raises(InvalidTransition):  # already submitted
        submit_for_review(db, as_student(PRIYA), schedule_id, **kw())


def test_submit_withdraw_and_submit_again(db):
    schedule_id = draft(db, PRIYA)
    submit_for_review(db, as_student(PRIYA), schedule_id, **kw())
    lifecycle.withdraw(db, db.get(Schedule, schedule_id))
    assert submit_for_review(db, as_student(PRIYA), schedule_id, **kw()).status == "submitted"


# ---------- who may decide ----------

def test_only_an_advisor_can_approve_or_reject(db):
    schedule_id = draft(db, PRIYA)
    submit_for_review(db, as_student(PRIYA), schedule_id, **kw())
    for who in (as_student(PRIYA), Who("admin"), Who("advisor", advisor_id=None)):
        for decision in ("approve", "reject"):
            with pytest.raises(NotAdvisor):
                decide(db, who, schedule_id, decision, **kw())
    schedule = db.get(Schedule, schedule_id)
    assert schedule.status == "submitted" and schedule.decided_by is None
    # the same rule is enforced below the service: the lifecycle step and the agent's tool
    for bad in (None, "", "ADV999", PRIYA):
        with pytest.raises(NotAdvisor):
            lifecycle.approve(db, schedule, bad)
        with pytest.raises(NotAdvisor):
            TOOLS["apply_decision"](db, schedule_id, "reject", bad, "no")
    assert schedule.status == "submitted"
    runs = db.scalars(select(AgentRun).where(AgentRun.schedule_id == schedule_id,
                                             AgentRun.agent_name == "compliance")).all()
    assert len(runs) == 1  # only the 'submit' run: nobody got as far as the gate


def test_a_run_without_an_advisor_id_fails_and_changes_nothing(db):
    schedule_id = draft(db, PRIYA)
    submit_for_review(db, as_student(PRIYA), schedule_id, **kw())
    with pytest.raises(AgentError) as err:
        ComplianceAgent(db, **kw()).run(schedule_id=schedule_id, stage="approve", decision="approve")
    assert isinstance(err.value.__cause__, NotAdvisor)
    assert db.get(Schedule, schedule_id).status == "submitted"
    assert db.get(AgentRun, err.value.run_id).status == "failed"
    assert db.scalars(select(Notification).where(Notification.schedule_id == schedule_id)).all() == []


# ---------- Agent 5: approve + email ----------

@needs_mailpit
def test_approve_sends_an_email_visible_in_mailpit(db, mailbox):
    schedule_id = draft(db, PRIYA)
    submit_for_review(db, as_student(PRIYA), schedule_id, **kw())
    student, advisor = db.get(Student, PRIYA), advisor_of(db, PRIYA)
    before = mailbox.snapshot(student.email)
    llm = FakeLLM(lambda p: "Your advisor approved your schedule for the term. Registration continues as planned, "
                            "so nothing else is needed from you right now.")
    result = decide(db, advisor, schedule_id, "approve", "Looks good, enjoy the term.", **kw(llm))

    assert result.status == "approved" and result.compliance.email_sent is True
    schedule = db.get(Schedule, schedule_id)
    assert (schedule.status, schedule.decided_by, schedule.advisor_comment) == (
        "approved", advisor.advisor_id, "Looks good, enjoy the term.") and schedule.decided_at

    mails = mailbox.new_since(student.email, before)  # the approval email is in Mailpit
    assert len(mails) == 1 and mails[0]["Subject"] == "Carnegie Mellon University: Your Spring 2027 schedule was approved"
    body = mailbox.text(mails[0]["ID"])
    assert body.startswith(f"Dear {student.first_name},") and "Registration continues as planned" in body
    assert all(i.course_code in body for i in schedule.items)
    assert "Advisor comment: Looks good, enjoy the term." in body and "Gray DI Degree Pathway Advisor" in body

    note = db.scalar(select(Notification).where(Notification.schedule_id == schedule_id))
    assert (note.template, note.status, note.to_email, note.error) == ("schedule_approved", "sent", student.email,
                                                                       None)
    assert note.sent_at is not None and note.body.strip() == body.replace("\r\n", "\n").strip()  # what was sent
    approve_checks = db.scalars(select(ComplianceCheck).where(ComplianceCheck.schedule_id == schedule_id,
                                                              ComplianceCheck.stage == "approve")).all()
    assert len(approve_checks) == 7 and all(c.passed for c in approve_checks if c.check_code == "OFFERED")
    audit = set(db.scalars(select(AuditLog.action).where(AuditLog.entity_id == str(schedule_id))))
    assert {"schedule_approve", "compliance_approve_passed"} <= audit

    run = db.get(AgentRun, result.compliance_run_id)
    assert run.llm_provider == "fake" and [c["tool"] for c in run.tool_calls].count("llm") == 1
    assert {"validate_schedule", "apply_decision", "create_notification", "send_email", "mark_notification"} <= {
        c["tool"] for c in run.tool_calls}
    assert len(llm.calls) == 1  # one LLM call, and no name was sent to it
    prompt = llm.calls[0][0] + llm.calls[0][1]
    adv = db.get(Student, PRIYA).advisor
    for name in (student.first_name, student.last_name, student.email, adv.first_name, adv.last_name):
        assert name.lower() not in prompt.lower()
        assert name.lower() not in (str(run.decision) + str(run.input) + (run.explanation or "")).lower()


@needs_mailpit
def test_reject_sends_a_rejection_email_and_needs_no_seats(db, mailbox):
    schedule_id = draft(db, PRIYA)
    submit_for_review(db, as_student(PRIYA), schedule_id, **kw())
    student = db.get(Student, PRIYA)
    db.execute(text("UPDATE sections SET enrolled = capacity WHERE section_id IN "
                    "(SELECT section_id FROM schedule_items WHERE schedule_id = :s) AND capacity IS NOT NULL"),
               {"s": schedule_id})  # every section is full now: a rejection does not care
    before = mailbox.snapshot(student.email)
    result = decide(db, advisor_of(db, PRIYA), schedule_id, "reject", "Please add a second elective.", **kw())
    assert result.status == "rejected" and result.compliance.email_sent
    mails = mailbox.new_since(student.email, before)
    assert len(mails) == 1 and mails[0]["Subject"] == "Carnegie Mellon University: Your Spring 2027 schedule needs changes"
    body = mailbox.text(mails[0]["ID"])
    assert "Please add a second elective." in body and "send it again" in body
    note = db.scalar(select(Notification).where(Notification.schedule_id == schedule_id))
    assert note.template == "schedule_rejected" and note.status == "sent"


@needs_mailpit
def test_approve_fails_cleanly_if_a_section_became_full(db, mailbox):
    schedule_id = draft(db, PRIYA)
    submit_for_review(db, as_student(PRIYA), schedule_id, **kw())
    schedule = db.get(Schedule, schedule_id)
    full = next(i for i in schedule.items if db.scalar(
        text("SELECT capacity FROM sections WHERE section_id = :s"), {"s": i.section_id}) is not None)
    db.execute(text("UPDATE sections SET enrolled = capacity WHERE section_id = :s"), {"s": full.section_id})
    student = db.get(Student, PRIYA)
    before = mailbox.snapshot(student.email)
    llm = FakeLLM()

    with pytest.raises(ComplianceBlocked) as err:
        decide(db, advisor_of(db, PRIYA), schedule_id, "approve", "ok", **kw(llm))
    assert err.value.stage == "approve"
    assert any(r.startswith(f"OFFERED {full.course_code}") and "is full" in r for r in err.value.reasons)

    assert schedule.status == "submitted" and schedule.decided_by is None and schedule.decided_at is None
    offered = db.scalar(select(ComplianceCheck).where(
        ComplianceCheck.schedule_id == schedule_id, ComplianceCheck.stage == "approve",
        ComplianceCheck.check_code == "OFFERED"))
    assert not offered.passed and "is full" in offered.detail  # recorded
    assert "compliance_approve_blocked" in set(db.scalars(select(AuditLog.action).where(
        AuditLog.entity_id == str(schedule_id))))
    assert db.scalars(select(Notification).where(Notification.schedule_id == schedule_id)).all() == []
    assert mailbox.new_since(student.email, before) == [] and llm.calls == []  # no email, no LLM call
    run = db.get(AgentRun, err.value.run_id)
    assert run.status == "ok" and not run.decision["allowed"] and "Approval blocked" in run.explanation
    assert not any(c["tool"] in ("apply_decision", "send_email") for c in run.tool_calls)

    # after the seat is back, the advisor can approve the same schedule
    db.execute(text("UPDATE sections SET enrolled = 0 WHERE section_id = :s"), {"s": full.section_id})
    assert decide(db, advisor_of(db, PRIYA), schedule_id, "approve", None, **kw()).status == "approved"
    mailbox.new_since(student.email, before)


def _approval_mail(db, mailbox, llm) -> str:
    schedule_id = draft(db, PRIYA)
    submit_for_review(db, as_student(PRIYA), schedule_id, **kw())
    student = db.get(Student, PRIYA)
    before = mailbox.snapshot(student.email)
    decide(db, advisor_of(db, PRIYA), schedule_id, "approve", "See you in class.", **kw(llm))
    (mail,) = mailbox.new_since(student.email, before)
    return mailbox.text(mail["ID"])


def _assert_second_person(body: str) -> None:
    assert "Dear Priya," in body and "your" in body.lower()
    assert "the student" not in body.lower() and "the advisor" not in body.lower().replace("your advisor", "")
    assert "student's" not in body.lower()


@needs_mailpit
def test_email_is_written_to_you_not_about_the_student(db, mailbox):
    body = _approval_mail(db, mailbox, LLMClient(provider="template"))  # the fallback template
    _assert_second_person(body)
    assert "Your advisor has approved your course schedule for Spring 2027" in body


@needs_mailpit
def test_a_third_person_llm_paragraph_is_rewritten_before_it_is_sent(db, mailbox):
    llm = FakeLLM("Dear the student,\nThe student's schedule was approved by the advisor, so their registration "
                  "continues as planned and nothing else is needed from the student.")
    body = _approval_mail(db, mailbox, llm)
    _assert_second_person(body)
    assert "Your schedule was approved by your advisor, so your registration continues as planned" in body
    assert "Dear Priya," in body and "Dear the student" not in body
    system, prompt = llm.calls[0]
    assert "second person" in system and "Never write 'the student'" in prompt
    assert "Priya" not in system + prompt  # the first name is added after the call


def test_a_decided_schedule_cannot_be_decided_again(db):
    schedule_id = draft(db, PRIYA)
    submit_for_review(db, as_student(PRIYA), schedule_id, **kw())
    advisor = advisor_of(db, PRIYA)
    decide(db, advisor, schedule_id, "approve", None, **kw())
    for decision in ("approve", "reject"):
        with pytest.raises(ScheduleLocked):
            decide(db, advisor, schedule_id, decision, None, **kw())
    with pytest.raises(ScheduleLocked):
        lifecycle.discard(db, db.get(Schedule, schedule_id))


def test_the_decision_stands_when_the_email_cannot_be_sent(db, monkeypatch):
    monkeypatch.setitem(TOOLS, "send_email", lambda to, subject, body, **_: EmailResult(sent=False,
                                                                                         error="SMTPConnectError: no"))
    schedule_id = draft(db, PRIYA)
    submit_for_review(db, as_student(PRIYA), schedule_id, **kw())
    result = decide(db, advisor_of(db, PRIYA), schedule_id, "approve", None, **kw())
    assert result.status == "approved" and result.compliance.email_sent is False
    note = db.scalar(select(Notification).where(Notification.schedule_id == schedule_id))
    assert note.status == "failed" and "SMTPConnectError" in note.error and note.sent_at is None
    assert "notification_failed" in set(db.scalars(select(AuditLog.action).where(
        AuditLog.entity_id == str(note.notification_id))))


def test_invented_numbers_in_the_email_paragraph_fall_back_to_the_template(db, monkeypatch):
    monkeypatch.setitem(TOOLS, "send_email", lambda to, subject, body, **_: EmailResult(sent=True))
    schedule_id = draft(db, PRIYA)
    submit_for_review(db, as_student(PRIYA), schedule_id, **kw())
    llm = FakeLLM("Congratulations, your schedule of 99 courses and 1234 units has been approved by the advisor.")
    result = decide(db, advisor_of(db, PRIYA), schedule_id, "approve", None, **kw(llm))
    body = db.scalar(select(Notification.body).where(Notification.schedule_id == schedule_id))
    assert "99 courses" not in body and "Your advisor has approved your course schedule for Spring 2027" in body
    run = db.get(AgentRun, result.compliance_run_id)
    assert any(c["tool"] == "llm_guard" for c in run.tool_calls)


def test_aggregate_gives_one_record_per_check_in_order():
    results = [CheckResult(code="UNIT_LIMIT", passed=True, severity="info", detail="54 units"),
               CheckResult(code="LEVEL", passed=False, severity="medium", course_code="94-413", flag="needs_petition",
                           detail="Undergraduate course"),
               CheckResult(code="OFFERED", passed=False, severity="block", course_code="95-800", detail="is full")]
    records = {r.code: r for r in aggregate(results)}
    assert list(records) == ["UNIT_LIMIT", "PREREQ", "OFFERED", "LEVEL", "APPROVAL", "TIME_CONFLICT", "PRIVACY"]
    assert records["PREREQ"].passed and records["PREREQ"].detail == "No courses to check"
    assert not records["LEVEL"].passed and records["LEVEL"].detail.startswith("Flag for the advisor: 94-413")
    assert not records["OFFERED"].passed and not records["OFFERED"].detail.startswith("Flag")
