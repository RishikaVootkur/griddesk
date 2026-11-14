"""A live status file for a running eval suite.

`griddesk eval run` keeps `.griddesk/live/eval.json` up to date while it runs: trial counts, the
last few results, trials waiting on another trial's row locks, and model calls waiting out a
provider quota. The file is rewritten whole on every change (write then rename), so a reader never
sees half of it. Nothing is written unless `live.start()` was called, so library code can report
freely.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any

SCHEMA = 1
LIVE_STATUS = Path(".griddesk/live/eval.json")
RECENT = 8


class LiveStatus:
    def __init__(self) -> None:
        self.path: Path | None = None
        self._reset()

    def _reset(self) -> None:
        self.state: dict[str, Any] = {}
        self.recent: deque[dict[str, Any]] = deque(maxlen=RECENT)
        self.quota_until: dict[int, float] = {}
        self._next_quota_id = 0

    def start(self, path: Path, *, label: str, model: str, total: int) -> None:
        self.path = path
        self._reset()
        now = time.time()
        self.state = {
            "schema": SCHEMA,
            "pid": os.getpid(),
            "label": label,
            "model": model,
            "total": total,
            "passed": 0,
            "failed": 0,
            "errored": 0,
            "lock_retries": 0,
            "locked_now": 0,
            "quota_waits": 0,
            "started_at": now,
            "finished_at": None,
            "run_file": None,
        }
        self._write()

    def trial(self, task_id: str, trial: int, outcome: str, error: str = "") -> None:
        if self.path is None:
            return
        key = {"pass": "passed", "fail": "failed", "error": "errored"}[outcome]
        self.state[key] += 1
        self.recent.append({"task": task_id, "trial": trial, "outcome": outcome, "error": error[:120]})
        self._write()

    def lock_wait_begin(self) -> None:
        if self.path is None:
            return
        self.state["lock_retries"] += 1
        self.state["locked_now"] += 1
        self._write()

    def lock_wait_end(self) -> None:
        if self.path is None:
            return
        self.state["locked_now"] = max(0, self.state["locked_now"] - 1)
        self._write()

    def quota_wait_begin(self, seconds: float) -> int:
        """Record a call pausing for quota; pass the returned id to `quota_wait_end`."""
        self._next_quota_id += 1
        if self.path is None:
            return self._next_quota_id
        self.state["quota_waits"] += 1
        self.quota_until[self._next_quota_id] = time.time() + seconds
        self._write()
        return self._next_quota_id

    def quota_wait_end(self, wait_id: int) -> None:
        if self.path is None or self.quota_until.pop(wait_id, None) is None:
            return
        self._write()

    def finish(self, run_file: Path | None = None) -> None:
        if self.path is None:
            return
        self.state["finished_at"] = time.time()
        self.state["run_file"] = str(run_file) if run_file else None
        self.quota_until.clear()
        self.state["locked_now"] = 0
        self._write()
        self.path = None

    def _write(self) -> None:
        assert self.path is not None
        snapshot = {
            **self.state,
            "updated_at": time.time(),
            "quota_waiting_now": len(self.quota_until),
            "quota_until": max(self.quota_until.values(), default=None),
            "recent": list(self.recent),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(snapshot))
            tmp.replace(self.path)
        except OSError:
            pass  # a status file is a convenience; never fail a run over it


live = LiveStatus()
