"""Retention projection: when is each topic due for recall?

A second projection over the evidence log, kept apart from learner_state (which never fades). Leitner-style:
each success after the due date moves a topic up one level and lengthens the interval; a failure drops it to
level 0. Only direct evidence on a topic counts — never the inferred credit a success gives its prerequisites.

schedule() is a pure function of the evidence rows and a clock reading, so it can be tested without a database.
"""
import datetime as dt

INTERVALS = [1, 3, 7, 14, 30, 60]  # days between reviews at level 0..5
MAX_LEVEL = len(INTERVALS) - 1
SUCCESS = 0.5  # outcome at or above this counts as a successful recall


def _blank():
    return {"level": 0, "interval_days": INTERVALS[0], "last_success": None, "last_seen": None, "due_at": None,
            "lapses": 0, "reviews": 0, "n": 0}


def schedule(events: list[dict], now: dt.datetime, affects: dict[str, list[str]] | None = None) -> dict[str, dict]:
    """events: evidence rows in time order, each {concepts: [...], outcome: float, ts: datetime, misconception: str|None}.
    affects: misconception -> concepts it affects (optional). When a failure carries a misconception that affects a
    different concept, that concept is not blamed — the same credit-assignment rule learner_state uses — so the two
    projections never disagree about whether a topic lapsed.
    Returns {concept: {level, interval_days, last_success, last_seen, due_at, lapses, reviews, n, due, overdue_days}}."""
    affects = affects or {}
    st: dict[str, dict] = {}
    for e in sorted(events, key=lambda e: (e["ts"], e.get("id", 0))):
        ts = e["ts"]
        ok = e["outcome"] >= SUCCESS
        mc = e.get("misconception")
        for c in e["concepts"]:
            if mc and not ok and c not in affects.get(mc, []):
                continue
            s = st.setdefault(c, _blank())
            s["n"] += 1
            s["last_seen"] = ts
            if ok:
                if s["last_success"] is None:
                    s["level"] = 1  # first success (first evidence, or after early failures)
                elif ts >= s["due_at"]:
                    s["level"] = min(MAX_LEVEL, s["level"] + 1)
                    s["reviews"] += 1
                # a success before the due date: last_seen and last_success move, the level does not
                s["last_success"] = ts
            else:
                if s["last_success"] is not None:
                    s["lapses"] += 1  # a lapse is a failure on something once held; a first failure held nothing
                s["level"] = 0
            s["interval_days"] = INTERVALS[s["level"]]
            s["due_at"] = (s["last_success"] + dt.timedelta(days=s["interval_days"])) if s["last_success"] else None
    for c, s in st.items():
        s["due"] = s["due_at"] is not None and s["due_at"] <= now
        s["overdue_days"] = (now - s["due_at"]).total_seconds() / 86400 if s["due_at"] else None
    return st
