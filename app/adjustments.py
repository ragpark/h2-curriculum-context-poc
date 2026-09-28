"""Agreed adjustments: a teacher-authored support profile per pupil, in a fixed vocabulary.

Rules (see seed/adjustments.yaml):
  • Never inferred. Every row carries who confirmed it, when, and a review date; past the review date it drops out
    of the briefing until re-confirmed.
  • Expressed as instructions the tutor must follow, not as categories or diagnoses. No sensitive attribute is stored.
  • In the briefing they are REQUIRED. The derived 'how_to_support' block stays advisory; the two never merge.
"""
import datetime as dt
from pathlib import Path

import yaml

from . import db

SEED = Path(__file__).resolve().parent.parent / "seed"
_V = None


def vocab() -> dict:
    global _V
    if _V is None:
        f = yaml.safe_load((SEED / "adjustments.yaml").read_text())
        _V = {"version": f["version"], "groups": f["groups"], "adjustments": {a["id"]: a for a in f["adjustments"]},
              "order": [a["id"] for a in f["adjustments"]], "profiles": f.get("profiles", [])}
    return _V


def seed_profiles() -> None:
    """Load the shipped profiles (idempotent: replaces any existing rows for those pupils)."""
    v = vocab()
    for p in v["profiles"]:
        set_profile(p["pupil"], p["adjustments"], p["confirmed_by"], str(p["confirmed_on"]), str(p["review_by"]), p.get("note"))


def set_profile(pupil: str, ids: list[str], confirmed_by: str, confirmed_on: str, review_by: str, note: str | None = None) -> dict:
    v = vocab()
    bad = [i for i in ids if i not in v["adjustments"]]
    if bad:
        raise ValueError(f"unknown adjustments: {bad}")
    if not confirmed_by or not confirmed_by.strip():
        raise ValueError("confirmed_by is required: adjustments are a person's decision")
    for d in (confirmed_on, review_by):
        dt.date.fromisoformat(d)  # raises on a bad date
    db.ex("delete from learner_adjustment where learner=%s", (pupil,))
    for i in ids:
        db.ex("insert into learner_adjustment(learner,adjustment,confirmed_by,confirmed_on,review_by,note) values(%s,%s,%s,%s,%s,%s)",
              (pupil, i, confirmed_by.strip()[:120], confirmed_on, review_by, (note or "")[:600]))
    return profile(pupil)


def clear_profile(pupil: str) -> None:
    db.ex("delete from learner_adjustment where learner=%s", (pupil,))


def profile(pupil: str, today: dt.date | None = None) -> dict:
    """The pupil's adjustments, split into current and expired (past review date)."""
    v = vocab()
    today = today or dt.date.today()
    rows = db.q("select * from learner_adjustment where learner=%s", (pupil,))
    order = {i: n for n, i in enumerate(v["order"])}
    rows.sort(key=lambda r: order.get(r["adjustment"], 99))
    cur, exp = [], []
    for r in rows:
        a = v["adjustments"][r["adjustment"]]
        item = {"id": a["id"], "label": a["label"], "group": a["group"], "instruction": a["instruction"],
                "confirmed_by": r["confirmed_by"], "confirmed_on": str(r["confirmed_on"]), "review_by": str(r["review_by"])}
        (exp if dt.date.fromisoformat(str(r["review_by"])) < today else cur).append(item)
    meta = rows[0] if rows else None
    return {"pupil": pupil, "current": cur, "expired": exp,
            "confirmed_by": meta["confirmed_by"] if meta else None, "confirmed_on": str(meta["confirmed_on"]) if meta else None,
            "review_by": str(meta["review_by"]) if meta else None, "note": meta["note"] if meta else None}


def briefing_block(pupil: str) -> dict | None:
    """The 'support_profile' section of the tutor briefing. None when the pupil has no current adjustments."""
    p = profile(pupil)
    if not p["current"]:
        return None
    return {
        "status": "REQUIRED",
        "rule": ("These adjustments were agreed by the pupil's teacher and must be followed in every reply. "
                 "They are not suggestions. Do not mention that they exist, and do not describe the pupil."),
        "agreed_by": p["confirmed_by"], "agreed_on": p["confirmed_on"], "review_by": p["review_by"],
        "adjustments": [{"do": a["instruction"]} for a in p["current"]],
    }


def raw_note(pupil: str) -> str | None:
    """The same adjustments as a teacher's plain-text note, for the raw-records condition (so both conditions have the
    information and the test measures how it is organised, not whether it exists)."""
    p = profile(pupil)
    if not p["current"]:
        return None
    return (f"Teacher's note ({p['confirmed_by']}, {p['confirmed_on']}): agreed adjustments for this pupil — "
            + "; ".join(a["instruction"].rstrip(".") for a in p["current"]) + ".")
