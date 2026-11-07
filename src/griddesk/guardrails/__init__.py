"""Guardrails: PII redaction, prompt-injection defenses and the pre-action policy check."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Defenses:
    """Which defenses are on. Each can be turned off to measure what it buys (attack success rate)."""

    in_tool_policy: bool = True  # tools refuse disallowed actions (griddesk.tools.common.enforce)
    action_guard: bool = True  # pre-action policy check in the runtime (guardrails.action_guard)
    spotlight: bool = True  # tool results wrapped and labeled as untrusted data
    injection_filter: bool = True  # instruction-like text masked or withheld from tool results

    @classmethod
    def none(cls) -> Defenses:
        return cls(False, False, False, False)

    def label(self) -> str:
        on = [k for k, v in self.__dict__.items() if v]
        return "+".join(on) if on else "none"
