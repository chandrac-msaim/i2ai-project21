"""LLM client fallbacks and name removal. Only the last test needs a running Ollama (skipped otherwise)."""
import sys

import httpx
import pytest

from app.config import get_settings
from app.llm import LLMClient, Redactor

DEAD_URL = "http://127.0.0.1:9"  # nothing listens here


def test_ollama_down_falls_back_to_template():
    llm = LLMClient(provider="ollama", ollama_url=DEAD_URL, timeout_s=3)
    result = llm.complete("system", "prompt", fallback="Template text.")
    assert result.text == "Template text."
    assert result.provider == "template" and result.model is None
    assert result.used_fallback and "ollama" in result.error
    assert result.ms >= 0


def test_template_provider_returns_fallback():
    result = LLMClient(provider="template").complete("s", "p", fallback="fixed")
    assert (result.text, result.provider, result.used_fallback, result.error) == ("fixed", "template", True, None)


def test_unknown_provider_falls_back():
    result = LLMClient(provider="gpt-9").complete("s", "p", fallback="fb")
    assert result.text == "fb" and result.used_fallback and "unknown" in result.error


def test_claude_without_key_falls_back_without_importing_sdk():
    settings = get_settings().model_copy(update={"anthropic_api_key": "  "})
    sys.modules.pop("anthropic", None)
    result = LLMClient(settings, provider="claude").complete("s", "p", fallback="fb")
    assert result.text == "fb" and result.provider == "template" and "ANTHROPIC_API_KEY" in result.error
    assert "anthropic" not in sys.modules


def test_redactor_replaces_names_parts_and_emails():
    r = Redactor([("the student", ["Priya", "Raman"]), ("the advisor", ["Morgan", "Lee"])])
    text = "Priya Raman (priya.raman@demo.graydi.local) met MORGAN lee; Raman agreed. Leeds stays."
    out = r.text(text)
    for name in ("Priya", "Raman", "Morgan", "Lee ", "lee;", "@"):
        assert name.lower() not in out.lower()
    assert out.startswith("the student ([email removed]) met the advisor; the student agreed.")
    assert "Leeds" in out
    assert r.scrub({"note": ["Priya"], "n": 1}) == {"note": ["the student"], "n": 1}


def _ollama_up() -> bool:
    try:
        return httpx.get(f"{get_settings().ollama_url}/api/tags", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.mark.llm
@pytest.mark.skipif(not _ollama_up(), reason="Ollama not running")
def test_ollama_live_answer():
    result = LLMClient(provider="ollama").complete("Answer with one word.", "Say: ready", max_tokens=5,
                                                  fallback="fb")
    assert result.provider == "ollama" and result.model == get_settings().ollama_model
    assert result.text and not result.used_fallback
