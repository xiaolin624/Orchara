"""Deterministic stub model for the P01 probe.

No provider is called. The stub replays a fixed script so the probe can prove
the *control plane* (allowlist, step budget, call accounting) without claiming
anything about model quality.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Action:
    """One scripted model decision."""

    kind: str  # "tool" | "final"
    name: str | None = None
    arguments: dict[str, Any] | None = None
    text: str | None = None

    @staticmethod
    def tool(name: str, arguments: dict[str, Any]) -> "Action":
        return Action(kind="tool", name=name, arguments=arguments)

    @staticmethod
    def final(text: str) -> "Action":
        return Action(kind="final", text=text)


@dataclass
class ScriptedModel:
    """Replays ``script`` in order, recording how many decisions it produced."""

    script: list[Action]
    model_id: str = "stub-scripted-v1"
    calls: int = 0
    _cursor: int = 0

    def next_action(self, step: int, observations: list[Any]) -> Action | None:
        """Return the next scripted action, or None when the script is exhausted."""
        self.calls += 1
        if self._cursor >= len(self.script):
            return None
        action = self.script[self._cursor]
        self._cursor += 1
        return action

    @property
    def exhausted(self) -> bool:
        return self._cursor >= len(self.script)

    def as_dict(self) -> dict[str, Any]:
        return {"model_id": self.model_id, "script_len": len(self.script), "calls": self.calls}
