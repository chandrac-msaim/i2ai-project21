"""Agent base: goal -> plan (tools) -> decide -> check (retry) -> save -> explain, fully logged.

Every run writes one agent_runs row (input without names, tool calls, decision, explanation, LLM info, timings)
and one audit_log entry (CLAUDE.md rule 3). Subclasses implement the steps; they reach data only through
call_tool (allow-listed names from app.tools) and the LLM only through ask_llm (names removed, at most once).

Step contract for subclasses:
    plan(ctx)                    -> facts      gather facts with self.call_tool(...)
    decide(ctx, facts)           -> OutT       ctx.problems holds the last check's problems on a retry
    check(ctx, facts, decision)  -> list[str]  problems; empty = accepted (use the rule engine here)
    save(ctx, facts, decision)   -> None       optional: persist outputs through tools
    explain(ctx, facts, decision)-> str        usually self.ask_llm(prompt, fallback=template text)
"""
import re
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from numbers import Number
from typing import Any, ClassVar

from pydantic import BaseModel
from pydantic_core import to_jsonable_python
from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm import LLMClient, LLMResult, Redactor
from app.models import AgentRun, AuditLog, Schedule
from app.tools import NO_DB_TOOLS, TOOLS
from app.tools.student import get_redaction_names

DEFAULT_SYSTEM = (
    "You are an academic advising assistant for a graduate program. Rules and advisors make every decision; "
    "you only explain decisions that were already made. Be brief, factual and plain. Refer to people only as "
    "'the student' or 'the advisor'."
)


class AgentError(RuntimeError):
    def __init__(self, agent: str, run_id: uuid.UUID, message: str) -> None:
        super().__init__(f"{agent} agent run {run_id} failed: {message}")
        self.run_id = run_id


class CheckFailed(RuntimeError):
    """decide() could not produce an accepted decision within max_attempts."""


@dataclass
class RunContext:
    run_id: uuid.UUID
    student_id: str | None
    schedule_id: uuid.UUID | None
    inputs: dict[str, Any]
    today: date
    attempt: int = 1
    problems: list[str] = field(default_factory=list)  # from the previous check, for a retry
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    llm: LLMResult | None = None
    redactor: Redactor = field(default_factory=Redactor)


@dataclass(frozen=True)
class AgentResult[OutT: BaseModel]:
    run_id: uuid.UUID
    status: str  # ok | retried
    output: OutT
    explanation: str
    attempts: int
    llm: LLMResult | None


class Agent[OutT: BaseModel](ABC):
    name: ClassVar[str]  # agent_runs.agent_name: requirements | electives | schedule | risk | compliance
    tools: ClassVar[frozenset[str]] = frozenset()  # allowed tool names from app.tools.TOOLS
    max_attempts: ClassVar[int] = 3
    system_prompt: ClassVar[str] = DEFAULT_SYSTEM

    def __init__(self, db: Session, llm: LLMClient | None = None, today: date | None = None,
                 commit: bool = True) -> None:
        self.db = db
        self.llm = llm or LLMClient()
        self.today = today or get_settings().today()
        self.commit = commit  # False in tests: the caller rolls everything back
        self._ctx: RunContext | None = None

    # ---------- steps (subclasses) ----------

    @abstractmethod
    def plan(self, ctx: RunContext) -> Any: ...

    @abstractmethod
    def decide(self, ctx: RunContext, facts: Any) -> OutT: ...

    def check(self, ctx: RunContext, facts: Any, decision: OutT) -> list[str]:
        return []

    def save(self, ctx: RunContext, facts: Any, decision: OutT) -> None:
        return None

    @abstractmethod
    def explain(self, ctx: RunContext, facts: Any, decision: OutT) -> str: ...

    # ---------- run ----------

    def run(self, student_id: str | None = None, schedule_id: uuid.UUID | None = None,
            **inputs: Any) -> AgentResult[OutT]:
        if student_id is None and schedule_id is not None and (schedule := self.db.get(Schedule, schedule_id)):
            student_id = schedule.student_id
        redactor = Redactor(get_redaction_names(self.db, student_id)) if student_id else Redactor()
        record = AgentRun(
            agent_name=self.name, student_id=student_id, schedule_id=schedule_id, status="running",
            input=redactor.scrub(_jsonable({"student_id": student_id, "schedule_id": schedule_id,
                                            "today": self.today, **inputs})),
        )
        self.db.add(record)
        self.db.flush()
        self._commit()
        ctx = RunContext(run_id=record.run_id, student_id=student_id, schedule_id=schedule_id,
                         inputs=inputs, today=self.today, redactor=redactor)
        self._ctx = ctx
        try:
            with self.db.begin_nested():  # a failed step rolls back its own writes, not the run row
                facts = self.plan(ctx)
                decision = self._decide_and_check(ctx, facts)
                self.save(ctx, facts, decision)
                explanation = self.explain(ctx, facts, decision)
        except Exception as exc:
            ctx.schedule_id = schedule_id  # anything created during the run was rolled back
            self._finish(record, ctx, "failed", error=f"{type(exc).__name__}: {exc}"[:1000])
            raise AgentError(self.name, record.run_id, str(exc)) from exc
        finally:
            self._ctx = None

        status = "retried" if ctx.attempt > 1 else "ok"
        self._finish(record, ctx, status, decision=decision, explanation=explanation)
        return AgentResult(run_id=record.run_id, status=status, output=decision, explanation=explanation,
                           attempts=ctx.attempt, llm=ctx.llm)

    def _decide_and_check(self, ctx: RunContext, facts: Any) -> OutT:
        for attempt in range(1, self.max_attempts + 1):
            ctx.attempt = attempt
            decision = self.decide(ctx, facts)
            problems = self.check(ctx, facts, decision)
            if not problems:
                return decision
            ctx.problems = problems
        raise CheckFailed(f"no accepted decision after {self.max_attempts} attempts: {'; '.join(ctx.problems)}")

    # ---------- helpers for subclasses ----------

    def call_tool(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Call an allow-listed tool and record it in tool_calls (arguments summarised, names removed)."""
        ctx = self._require_ctx()
        if name not in self.tools:
            raise PermissionError(f"tool {name!r} is not allowed for the {self.name} agent")
        func = TOOLS[name]
        start = time.perf_counter()
        entry: dict[str, Any] = {"tool": name, "args": ctx.redactor.scrub(_summary([*args, kwargs]))}
        try:
            result = func(*args, **kwargs) if name in NO_DB_TOOLS else func(self.db, *args, **kwargs)
        except Exception as exc:
            entry.update(ms=_ms(start), error=f"{type(exc).__name__}: {exc}"[:300])
            ctx.tool_calls.append(entry)
            raise
        entry.update(ms=_ms(start), result=_summary(result))
        ctx.tool_calls.append(entry)
        return result

    def ask_llm(self, prompt: str, fallback: str, system: str | None = None, max_tokens: int = 300) -> str:
        """The run's single LLM call. Names are removed from system and prompt first (CLAUDE.md rule 4)."""
        ctx = self._require_ctx()
        if ctx.llm is not None:
            raise RuntimeError(f"{self.name} agent: at most one LLM call per run (CLAUDE.md rule 5)")
        result = self.llm.complete(system=ctx.redactor.text(system or self.system_prompt),
                                   prompt=ctx.redactor.text(prompt), max_tokens=max_tokens,
                                   fallback=fallback)
        ctx.llm = result
        ctx.tool_calls.append({"tool": "llm", "provider": result.provider, "model": result.model,
                               "ms": result.ms, "used_fallback": result.used_fallback, "error": result.error})
        return result.text

    def guard_llm_text(self, text: str, source: str, fallback: str) -> str:
        """`text` if every number in it also appears in `source` (the facts sent to the LLM), else `fallback`.

        A small model sometimes invents totals; the LLM may only explain, so an invented number is rejected and
        the rejection is logged in tool_calls.
        """
        invented = ungrounded_numbers(text, source)
        if not invented:
            return text
        self._require_ctx().tool_calls.append({"tool": "llm_guard", "rejected": True,
                                               "reason": f"numbers not in the facts: {invented[:5]}"})
        return fallback

    # ---------- internals ----------

    def _finish(self, record: AgentRun, ctx: RunContext, status: str, decision: BaseModel | None = None,
                explanation: str | None = None, error: str | None = None) -> None:
        record.status = status
        record.schedule_id = ctx.schedule_id  # an agent may set it when it creates a schedule
        record.tool_calls = ctx.tool_calls
        record.decision = ctx.redactor.scrub(decision.model_dump(mode="json")) if decision is not None else None
        record.explanation = ctx.redactor.text(explanation) if explanation is not None else None
        record.error = error
        if ctx.llm is not None:
            record.llm_provider, record.llm_model, record.llm_ms = ctx.llm.provider, ctx.llm.model, ctx.llm.ms
        record.finished_at = datetime.now(timezone.utc)
        self.db.add(AuditLog(
            actor_type="agent", actor_id=self.name, action=f"agent_run_{status}", entity_type="agent_run",
            entity_id=str(record.run_id),
            details={"student_id": ctx.student_id, "schedule_id": _jsonable(ctx.schedule_id),
                     "attempts": ctx.attempt, "tool_calls": len(ctx.tool_calls),
                     "llm_provider": ctx.llm.provider if ctx.llm else None},
        ))
        self.db.flush()
        self._commit()

    def _commit(self) -> None:
        if self.commit:
            self.db.commit()

    def _require_ctx(self) -> RunContext:
        if self._ctx is None:
            raise RuntimeError("call_tool / ask_llm can only be used inside Agent.run()")
        return self._ctx


_NUMBER = re.compile(r"(?<![\w.\-])\d+(?:\.\d+)?(?![\w\-]|\.\d)")  # not inside course codes (95-841) or S27


def ungrounded_numbers(text: str, source: str) -> list[str]:
    """Numbers in `text` that do not appear in `source` (compared as values, so 48.0 == 48)."""
    known = {Decimal(n) for n in _NUMBER.findall(source)}
    return [n for n in _NUMBER.findall(text) if Decimal(n) not in known]


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _jsonable(value: Any) -> Any:
    return to_jsonable_python(value, fallback=str)


def _summary(value: Any) -> Any:
    """Small JSON-safe summary for tool_calls: vectors and long lists collapsed, models by type and size."""
    if isinstance(value, BaseModel):
        return type(value).__name__
    if isinstance(value, dict):
        return {str(k): _summary(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items = list(value)
        if items and all(isinstance(x, Number) for x in items) and len(items) > 8:
            return f"<vector {len(items)}>"
        if items and isinstance(items[0], BaseModel):
            return f"<{len(items)} {type(items[0]).__name__}>"
        if len(items) > 20:
            return [_summary(x) for x in items[:20]] + [f"... {len(items) - 20} more"]
        return [_summary(x) for x in items]
    if hasattr(value, "tolist"):  # numpy arrays
        return _summary(value.tolist())
    return _jsonable(value)
