"""One LLM entry point: Ollama (default) | Claude (optional) | template fallback.

complete() never raises. If the provider is down, slow, misconfigured or returns nothing, the caller gets the
fallback text with provider='template', so rules and schedules keep working (CLAUDE.md rule 5).
Callers must strip personal names before calling (rule 4); app.agents.base does this for agents.
"""
import time
from dataclasses import dataclass

import httpx

from app.config import Settings, get_settings

TEMPLATE = "template"


@dataclass(frozen=True)
class LLMResult:
    text: str
    provider: str  # 'ollama' | 'claude' | 'template'
    model: str | None
    ms: int
    used_fallback: bool = False
    error: str | None = None


class LLMClient:
    def __init__(self, settings: Settings | None = None, provider: str | None = None,
                 ollama_url: str | None = None, timeout_s: float | None = None) -> None:
        s = settings or get_settings()
        self.provider = (provider or s.llm_provider).strip().lower()
        self.ollama_url = (ollama_url or s.ollama_url).rstrip("/")
        self.ollama_model = s.ollama_model
        self.claude_model = s.claude_model
        self.anthropic_api_key = (s.anthropic_api_key or "").strip() or None
        self.timeout_s = timeout_s if timeout_s is not None else s.llm_timeout_s

    @property
    def model(self) -> str | None:
        return {"ollama": self.ollama_model, "claude": self.claude_model}.get(self.provider)

    def complete(self, system: str, prompt: str, max_tokens: int = 300, fallback: str = "") -> LLMResult:
        """Text from the configured provider, or `fallback` (provider 'template') on any problem."""
        start = time.perf_counter()
        try:
            if self.provider == "ollama":
                text = self._ollama(system, prompt, max_tokens)
            elif self.provider == "claude":
                text = self._claude(system, prompt, max_tokens)
            elif self.provider == TEMPLATE:
                return LLMResult(fallback, TEMPLATE, None, _ms(start), used_fallback=True)
            else:
                raise ValueError(f"unknown LLM_PROVIDER {self.provider!r}")
            if not text.strip():
                raise ValueError("empty response")
            return LLMResult(text.strip(), self.provider, self.model, _ms(start))
        except Exception as exc:  # noqa: BLE001 - never raise to callers
            return LLMResult(fallback, TEMPLATE, None, _ms(start), used_fallback=True,
                             error=f"{self.provider}: {type(exc).__name__}: {exc}"[:500])

    def _ollama(self, system: str, prompt: str, max_tokens: int) -> str:
        response = httpx.post(
            f"{self.ollama_url}/api/chat",
            json={
                "model": self.ollama_model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_predict": max_tokens, "temperature": 0.2},
            },
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]

    def _claude(self, system: str, prompt: str, max_tokens: int) -> str:
        if not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        import anthropic  # lazy: optional dependency, only needed when LLM_PROVIDER=claude

        client = anthropic.Anthropic(api_key=self.anthropic_api_key, timeout=self.timeout_s, max_retries=0)
        message = client.messages.create(
            model=self.claude_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in message.content if block.type == "text")


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
