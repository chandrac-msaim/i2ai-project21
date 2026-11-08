"""Agent base: agent_runs + audit_log rows, tool allow-list, retries, one LLM call, no names sent to the LLM.

Agents run with commit=False; the db fixture rolls everything back afterwards.
"""
from datetime import date

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.agents import Agent, AgentError
from app.llm import LLMClient, LLMResult
from app.models import Advisor, AgentRun, AuditLog, Student

PRIYA = "S0056"


class FakeLLM(LLMClient):
    """Records every prompt it receives; never calls a real model."""

    def __init__(self) -> None:
        super().__init__(provider="fake")
        self.sent: list[str] = []

    def complete(self, system: str, prompt: str, max_tokens: int = 300, fallback: str = "") -> LLMResult:
        self.sent += [system, prompt]
        return LLMResult(text=f"Explained: {prompt[:40]}", provider="fake", model="fake-1", ms=1)


class Pick(BaseModel):
    courses: list[str]
    note: str


class DemoAgent(Agent[Pick]):
    """Reads Priya's history, picks two in-progress courses, explains with a prompt that (wrongly) has names."""

    name = "electives"
    tools = frozenset({"get_student_history"})

    def __init__(self, *args, names: str = "", reject_first: int = 0, extra_tool: str | None = None,
                 llm_calls: int = 1, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.names, self.reject_first, self.extra_tool, self.llm_calls = names, reject_first, extra_tool, llm_calls

    def plan(self, ctx):
        if self.extra_tool:
            self.call_tool(self.extra_tool, "S27")
        return self.call_tool("get_student_history", ctx.student_id)

    def decide(self, ctx, facts):
        return Pick(courses=facts.in_progress[:2], note=f"attempt {ctx.attempt} for {self.names}")

    def check(self, ctx, facts, decision):
        return ["try again"] if ctx.attempt <= self.reject_first else []

    def explain(self, ctx, facts, decision):
        text = ""
        for _ in range(self.llm_calls):
            text = self.ask_llm(f"Explain to {self.names} why {decision.courses} were chosen.",
                                fallback="Template explanation.")
        return text


@pytest.fixture
def names(db) -> str:
    student = db.get(Student, PRIYA)
    advisor = db.get(Advisor, student.advisor_id)
    return (f"{student.first_name} {student.last_name} ({student.email}), advisor {advisor.honorific or ''} "
            f"{advisor.first_name} {advisor.last_name} <{advisor.email}>")


def _assert_no_names(db, text: str) -> None:
    student = db.get(Student, PRIYA)
    advisor = db.get(Advisor, student.advisor_id)
    for name in (student.first_name, student.last_name, advisor.first_name, advisor.last_name,
                 student.email, advisor.email):
        assert name.lower() not in text.lower(), f"{name!r} leaked: {text!r}"


def test_run_records_agent_run_and_audit(db, names):
    llm = FakeLLM()
    result = DemoAgent(db, llm=llm, today=date(2026, 11, 7), commit=False, names=names).run(
        student_id=PRIYA, term_code="S27", note=names)
    assert result.status == "ok" and result.attempts == 1
    assert result.output.courses and result.explanation.startswith("Explained:")

    run = db.get(AgentRun, result.run_id)
    assert run.agent_name == "electives" and run.status == "ok" and run.student_id == PRIYA
    assert run.input["term_code"] == "S27" and run.input["today"] == "2026-11-07"
    assert [c["tool"] for c in run.tool_calls] == ["get_student_history", "llm"]
    assert run.tool_calls[0]["result"] == "StudentHistory" and run.tool_calls[0]["ms"] >= 0
    assert run.decision["courses"] == result.output.courses
    assert (run.llm_provider, run.llm_model, run.llm_ms) == ("fake", "fake-1", 1)
    assert run.finished_at is not None and run.error is None

    audit = db.scalars(select(AuditLog).where(AuditLog.entity_id == str(result.run_id))).one()
    assert (audit.actor_type, audit.actor_id, audit.action) == ("agent", "electives", "agent_run_ok")


def test_no_student_or_advisor_name_reaches_the_llm(db, names):
    llm = FakeLLM()
    result = DemoAgent(db, llm=llm, commit=False, names=names).run(student_id=PRIYA, note=names)
    assert len(llm.sent) == 2 and "the student" in llm.sent[1] and "the advisor" in llm.sent[1]
    for prompt in llm.sent:
        _assert_no_names(db, prompt)
    run = db.get(AgentRun, result.run_id)  # nor the stored input, decision or explanation
    _assert_no_names(db, str(run.input) + str(run.decision) + run.explanation)


def test_llm_down_uses_template(db):
    dead = LLMClient(provider="ollama", ollama_url="http://127.0.0.1:9", timeout_s=3)
    result = DemoAgent(db, llm=dead, commit=False).run(student_id=PRIYA)
    assert result.status == "ok" and result.explanation == "Template explanation."
    run = db.get(AgentRun, result.run_id)
    assert run.llm_provider == "template" and run.tool_calls[-1]["used_fallback"] is True


def test_check_retries_then_succeeds(db):
    result = DemoAgent(db, llm=FakeLLM(), commit=False, reject_first=2).run(student_id=PRIYA)
    assert result.status == "retried" and result.attempts == 3
    assert db.get(AgentRun, result.run_id).status == "retried"


def test_check_never_passes_records_failed(db):
    with pytest.raises(AgentError) as err:
        DemoAgent(db, llm=FakeLLM(), commit=False, reject_first=99).run(student_id=PRIYA)
    run = db.get(AgentRun, err.value.run_id)
    assert run.status == "failed" and "CheckFailed" in run.error and run.finished_at
    audit = db.scalars(select(AuditLog).where(AuditLog.entity_id == str(run.run_id))).one()
    assert audit.action == "agent_run_failed"


def test_tool_not_in_allow_list_fails_run(db):
    with pytest.raises(AgentError) as err:
        DemoAgent(db, llm=FakeLLM(), commit=False, extra_tool="get_active_courses").run(student_id=PRIYA)
    assert "PermissionError" in db.get(AgentRun, err.value.run_id).error


def test_second_llm_call_is_refused(db):
    llm = FakeLLM()
    with pytest.raises(AgentError) as err:
        DemoAgent(db, llm=llm, commit=False, llm_calls=2).run(student_id=PRIYA)
    assert len(llm.sent) == 2  # only the first call went out
    assert "at most one LLM call" in db.get(AgentRun, err.value.run_id).error


def test_helpers_need_a_run(db):
    with pytest.raises(RuntimeError):
        DemoAgent(db, llm=FakeLLM(), commit=False).call_tool("get_student_history", PRIYA)
