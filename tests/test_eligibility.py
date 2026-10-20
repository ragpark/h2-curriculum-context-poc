"""Eligibility and ranking of due_for_review items (retention.select_due), with no database."""
from app import retention as R

A, B, C, D, F, N = "cc:a", "cc:b", "cc:c", "cc:d", "cc:focus", "cc:next"


def rv(overdue, interval=3, due=True, lapses=0):
    return {"due": due, "overdue_days": overdue, "interval_days": interval, "lapses": lapses, "last_success": None}


def test_eligibility_rules():
    review = {A: rv(10), B: rv(10), C: rv(10), D: rv(10), F: rv(10), "cc:not-due": rv(-2, due=False)}
    status = {A: "secure", B: "gap", C: "developing", D: "secure", F: "secure", "cc:not-due": "secure"}
    taught = {A: 1, B: 1, C: 2, F: 4, "cc:not-due": 1}  # D is not taught
    chosen, _ = R.select_due(review, status, taught, F, None, {})
    assert set(chosen) == {A, C}  # B is a gap, D untaught, F is the focus, not-due is not due


def test_ranked_by_overdue_over_interval():
    review = {A: rv(30, 30), B: rv(6, 3), C: rv(10, 7)}  # ratios 1.0, 2.0, 1.43
    status = {k: "secure" for k in review}
    taught = {A: 1, B: 2, C: 3}
    chosen, _ = R.select_due(review, status, taught, F, None, {})
    assert chosen == [B, C, A]


def test_prerequisites_of_focus_or_next_step_are_boosted():
    review = {A: rv(9, 3), B: rv(10, 3), C: rv(10, 3)}  # A 3.0 → 4.5 boosted; B 3.33; C 3.33 → 5.0 boosted via next
    status = {k: "secure" for k in review}
    taught = {A: 1, B: 2, C: 3}
    prereqs = {F: [(A, 1.0)], N: [(C, 0.8)]}
    chosen, boost = R.select_due(review, status, taught, F, N, prereqs)
    assert chosen == [C, A, B] and boost == {A, C}


def test_ties_prefer_a_different_scheme_week_from_the_focus_then_the_earlier_week():
    review = {A: rv(6, 3), B: rv(6, 3), C: rv(6, 3)}
    status = {k: "secure" for k in review}
    taught = {A: 4, B: 2, C: 1, F: 4}  # A shares the focus's week
    chosen, _ = R.select_due(review, status, taught, F, None, {})
    assert chosen == [C, B, A]


def test_at_most_three_and_empty_when_none():
    review = {k: rv(5 + i) for i, k in enumerate(["cc:1", "cc:2", "cc:3", "cc:4", "cc:5"])}
    status = {k: "secure" for k in review}
    taught = {k: 1 for k in review}
    chosen, _ = R.select_due(review, status, taught, F, None, {})
    assert len(chosen) == 3 and chosen[0] == "cc:5"
    assert R.select_due({}, {}, {}, F, None, {})[0] == []


def test_why_is_about_the_topic_not_the_pupil():
    import datetime as dt
    now = dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc)
    r = {"lapses": 1, "last_success": now - dt.timedelta(days=21)}
    w = R.why(r, True, now)
    assert w == "slipped once before; last recalled correctly 3 weeks ago; a prerequisite of the current focus or next step"
    assert R.why({"lapses": 0, "last_success": None}, False, now) == "due for a recall check"
