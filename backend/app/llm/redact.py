"""Remove personal names and email addresses before text reaches an LLM or agent_runs.input (CLAUDE.md rule 4)."""
import re
from collections.abc import Iterable
from typing import Any

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


class Redactor:
    """Replaces names with a role ('the student', 'the advisor'). Full names first, then single name parts."""

    def __init__(self, people: Iterable[tuple[str, Iterable[str]]] = ()) -> None:
        # people: (role, name parts), e.g. ("the student", ["Priya", "Raman"])
        pairs: list[tuple[str, str]] = []
        for role, parts in people:
            words = [p.strip() for p in parts if p and p.strip()]
            if len(words) > 1:
                pairs.append((" ".join(words), role))
            pairs.extend((w, role) for w in words if len(w) > 1)
        pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
        self._patterns = [(re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE), role)
                          for name, role in pairs]

    def text(self, value: str) -> str:
        value = EMAIL.sub("[email removed]", value)
        for pattern, role in self._patterns:
            value = pattern.sub(role, value)
        return value

    def scrub(self, value: Any) -> Any:
        """Redact every string inside nested dicts / lists (keys included)."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {self.scrub(k): self.scrub(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self.scrub(v) for v in value]
        return value
