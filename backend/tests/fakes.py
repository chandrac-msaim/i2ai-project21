"""A fake LLMClient for unit tests: fast, deterministic, records what it was sent."""
from collections.abc import Callable

from app.llm import LLMClient, LLMResult


class FakeLLM(LLMClient):
    """reply: fixed text, or a function of the prompt; None = answer with the caller's fallback text."""

    def __init__(self, reply: str | Callable[[str], str] | None = None) -> None:
        super().__init__(provider="fake")
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str, max_tokens: int = 300, fallback: str = "") -> LLMResult:
        self.calls.append((system, prompt))
        text = self.reply(prompt) if callable(self.reply) else self.reply if self.reply is not None else fallback
        return LLMResult(text=text, provider="fake", model="fake-1", ms=1)
