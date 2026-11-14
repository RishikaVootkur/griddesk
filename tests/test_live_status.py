import json
from pathlib import Path

from griddesk.observability.live import LiveStatus


def read(p: Path) -> dict:
    return json.loads(p.read_text())


def test_disabled_writes_nothing(tmp_path: Path) -> None:
    s = LiveStatus()
    s.trial("t1", 0, "pass")
    s.lock_wait_begin()
    s.quota_wait_end(s.quota_wait_begin(5))
    s.finish()
    assert list(tmp_path.iterdir()) == []


def test_tracks_trials_locks_quota(tmp_path: Path) -> None:
    p = tmp_path / "live" / "eval.json"
    s = LiveStatus()
    s.start(p, label="core", model="groq:x", total=3)
    s.trial("t1", 0, "pass")
    s.trial("t2", 0, "error", "TrialConflict: blocked")
    s.lock_wait_begin()
    w = s.quota_wait_begin(60)
    snap = read(p)
    assert (snap["passed"], snap["errored"], snap["total"]) == (1, 1, 3)
    assert snap["locked_now"] == 1 and snap["lock_retries"] == 1
    assert snap["quota_waiting_now"] == 1 and snap["quota_until"] > snap["updated_at"]
    assert snap["recent"][-1]["task"] == "t2"
    s.lock_wait_end()
    s.quota_wait_end(w)
    snap = read(p)
    assert snap["locked_now"] == 0 and snap["quota_waiting_now"] == 0 and snap["quota_until"] is None
    s.finish()
    assert read(p)["finished_at"] is not None
    assert s.path is None
