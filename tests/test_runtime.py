from datetime import UTC, datetime

from griddesk.runtime import Clock, IdGenerator

START = datetime(2026, 10, 15, 15, tzinfo=UTC)


def test_deterministic_clock_ticks_one_second_per_call():
    c = Clock(START, deterministic=True)
    assert [c.now() for _ in range(3)] == [START.replace(second=s) for s in (1, 2, 3)]
    assert Clock(START, deterministic=True).now() == START.replace(second=1)


def test_live_clock_starts_at_simulated_time():
    c = Clock(START)
    assert START <= c.now() < START.replace(minute=1)
    assert c.today() == START.date()


def test_seeded_ids_repeat_and_differ_by_seed():
    a, b, other = IdGenerator("trial-1"), IdGenerator("trial-1"), IdGenerator("trial-2")
    seq_a = [a.new("PP") for _ in range(3)]
    assert seq_a == [b.new("PP") for _ in range(3)]
    assert len(set(seq_a)) == 3
    assert other.new("PP") != seq_a[0]


def test_unseeded_ids_are_random():
    g = IdGenerator()
    assert g.new("SO") != g.new("SO")
    assert g.new("SO").startswith("SO-")
