import datetime as dt

from app import clock


class FakeMeta:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = str(v)


def _patch(monkeypatch):
    m = FakeMeta()
    monkeypatch.setattr(clock.db, "meta_get", m.get)
    monkeypatch.setattr(clock.db, "meta_set", m.set)
    fixed = dt.datetime(2026, 10, 20, 12, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(clock, "_real_now", lambda: fixed)
    return m, fixed


def test_default_offset_is_zero(monkeypatch):
    _, fixed = _patch(monkeypatch)
    assert clock.offset_days() == 0 and clock.now() == fixed and clock.today() == fixed.date()


def test_advance_accumulates(monkeypatch):
    _, fixed = _patch(monkeypatch)
    clock.advance(7); clock.advance(14)
    assert clock.offset_days() == 21 and clock.today() == (fixed + dt.timedelta(days=21)).date()


def test_set_date_and_reset(monkeypatch):
    _, fixed = _patch(monkeypatch)
    st = clock.set_date("2026-12-25")
    assert st["offset_days"] == 66 and clock.today() == dt.date(2026, 12, 25)
    clock.set_date("2026-10-01")
    assert clock.offset_days() == -19
    assert clock.reset()["offset_days"] == 0 and clock.today() == fixed.date()


def test_ago_is_relative_to_the_shifted_clock(monkeypatch):
    _, fixed = _patch(monkeypatch)
    clock.advance(10)
    assert clock.ago(3) == fixed + dt.timedelta(days=7)
