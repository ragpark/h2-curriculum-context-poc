import datetime as dt

from app import retention as R

T0 = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.timezone.utc)
C = "cc:maths/alg/lin-eq-two-step"
PRE = "cc:maths/alg/lin-eq-one-step"


def ev(days, outcome, concepts=(C,), mc=None, i=None):
    return {"id": i or days, "ts": T0 + dt.timedelta(days=days), "outcome": outcome, "concepts": list(concepts), "misconception": mc}


def at(days):
    return T0 + dt.timedelta(days=days)


def test_first_success_is_level_1_due_after_first_interval():
    s = R.schedule([ev(0, 1.0)], at(0.5))[C]
    assert s["level"] == 1 and s["interval_days"] == R.INTERVALS[1]
    assert s["due_at"] == at(R.INTERVALS[1]) and s["due"] is False and s["lapses"] == 0


def test_first_failure_is_level_0_with_no_due_date_and_no_lapse():
    s = R.schedule([ev(0, 0.0)], at(5))[C]
    assert s["level"] == 0 and s["due_at"] is None and s["lapses"] == 0 and s["due"] is False


def test_success_after_due_moves_up_a_level_and_counts_a_review():
    s = R.schedule([ev(0, 1.0), ev(10, 1.0)], at(10))[C]  # due at day 3, recalled on day 10
    assert s["level"] == 2 and s["reviews"] == 1 and s["interval_days"] == R.INTERVALS[2]
    assert s["due_at"] == at(10 + R.INTERVALS[2])


def test_success_before_due_keeps_the_level_but_moves_last_success():
    s = R.schedule([ev(0, 1.0), ev(1, 1.0)], at(1))[C]  # due at day 3, recalled on day 1
    assert s["level"] == 1 and s["reviews"] == 0
    assert s["last_success"] == at(1) and s["last_seen"] == at(1) and s["due_at"] == at(1 + R.INTERVALS[1])


def test_failure_after_success_is_a_lapse_and_resets_to_level_0():
    s = R.schedule([ev(0, 1.0), ev(10, 1.0), ev(20, 0.0)], at(20))[C]
    assert s["level"] == 0 and s["lapses"] == 1 and s["interval_days"] == R.INTERVALS[0]
    assert s["due_at"] == at(10 + R.INTERVALS[0]) and s["due"] is True  # immediately due again


def test_interval_is_capped_at_the_top_level():
    evs, day = [ev(0, 1.0)], 0
    for n in range(1, 12):  # recall well after each due date, eleven times
        day += 100
        evs.append(ev(day, 1.0))
    s = R.schedule(evs, at(day))[C]
    assert s["level"] == R.MAX_LEVEL and s["interval_days"] == R.INTERVALS[-1] and s["reviews"] == 11


def test_only_direct_evidence_counts_never_inferred_prerequisite_credit():
    # a success on C gives learner_state inferred credit on its prerequisite PRE; retention must not
    st = R.schedule([ev(0, 1.0)], at(0))
    assert C in st and PRE not in st


def test_failure_blamed_on_a_misconception_elsewhere_is_not_a_lapse_here():
    affects = {"mc:sign": ["cc:maths/alg/lin-eq-both-sides"]}
    st = R.schedule([ev(0, 1.0), ev(5, 0.0, mc="mc:sign")], at(5), affects)
    assert st[C]["lapses"] == 0 and st[C]["level"] == 1  # same credit assignment as learner_state


def test_failure_with_a_misconception_that_affects_this_topic_does_count():
    affects = {"mc:order": [C]}
    st = R.schedule([ev(0, 1.0), ev(5, 0.0, mc="mc:order")], at(5), affects)
    assert st[C]["lapses"] == 1 and st[C]["level"] == 0


def test_events_are_replayed_in_time_order_not_insertion_order():
    st = R.schedule([ev(10, 0.0, i=1), ev(0, 1.0, i=2)], at(10))
    assert st[C]["lapses"] == 1


def test_overdue_days_and_due_flag():
    s = R.schedule([ev(0, 1.0)], at(3 + 7))[C]
    assert s["due"] is True and abs(s["overdue_days"] - 7) < 1e-6


def test_relative_time_phrases():
    assert R.relative(at(0), at(0)) == "today"
    assert R.relative(at(0), at(1)) == "yesterday"
    assert R.relative(at(0), at(5)) == "5 days ago"
    assert R.relative(at(0), at(21)) == "3 weeks ago"
