"""Per-run context shared by tools: clock and id generation.

Inside an eval trial both are deterministic, which keeps tool outputs (and therefore the LLM
requests built from them) identical across replays. Live runs use real time and random ids.
"""

from __future__ import annotations

import hashlib
import itertools
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from time import monotonic

from griddesk.config import get_settings


class Clock:
    """Simulated utility time. Live clocks advance in real time from the simulated start."""

    def __init__(self, start: datetime | None = None, frozen: bool = False) -> None:
        self._start = start or get_settings().simulated_now
        self._frozen = frozen
        self._t0 = monotonic()

    def now(self) -> datetime:
        if self._frozen:
            return self._start
        return self._start + timedelta(seconds=monotonic() - self._t0)

    def today(self) -> date:
        return self.now().date()


@dataclass
class IdGenerator:
    """Make short prefixed ids. With a seed, the sequence is deterministic."""

    seed: str | None = None
    _counter: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    def new(self, prefix: str) -> str:
        if self.seed is None:
            return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"
        n = next(self._counter)
        digest = hashlib.sha256(f"{self.seed}:{prefix}:{n}".encode()).hexdigest()[:8].upper()
        return f"{prefix}-{digest}"
