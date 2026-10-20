"""A clock you can set: real time plus an offset in days, stored in `meta` (clock_offset_days, default 0).

Everything in the map that needs "now" goes through here, so a demo can be moved forward to show what a briefing
looks like weeks after the evidence was recorded. The offset is global: one clock for the whole prototype.
"""
import datetime as dt

from . import db

KEY = "clock_offset_days"


def offset_days() -> int:
    v = db.meta_get(KEY)
    return int(v) if v else 0


def _real_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def now() -> dt.datetime:
    return _real_now() + dt.timedelta(days=offset_days())


def today() -> dt.date:
    return now().date()


def ago(days: float) -> dt.datetime:
    """A timestamp `days` before now (for backdated synthetic evidence)."""
    return now() - dt.timedelta(days=days)


def state() -> dict:
    return {"now": now().isoformat(), "today": today().isoformat(), "offset_days": offset_days(),
            "real_today": _real_now().date().isoformat()}


def advance(days: int) -> dict:
    db.meta_set(KEY, offset_days() + int(days))
    return state()


def set_date(day: str) -> dict:
    target = dt.date.fromisoformat(day)
    db.meta_set(KEY, (target - _real_now().date()).days)
    return state()


def reset() -> dict:
    db.meta_set(KEY, 0)
    return state()
