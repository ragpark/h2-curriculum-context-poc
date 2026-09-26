"""Learning-behaviour data: how a pupil learns, not what they know.

observed events (per answer)  ->  indicators (per episode, by explicit rules)  ->  construct patterns (per pupil)

Design choices:
  • Every episode is tied to a topic on the curriculum map, and the rules read the pupil's knowledge state and the
    class's scheme: a hint before trying is only counted against help-seeking on a topic the pupil already knows or
    that the class was taught earlier — on a brand-new topic, early help is reasonable.
  • Emotional signals ('affect') are session-only: discarded at ingestion, never stored.
  • Patterns need at least two indicators, weight recent episodes more, and are described as recent patterns, never traits.
  • Only teacher-confirmed patterns are shared beyond the tutoring tool.
"""
from collections import defaultdict
from pathlib import Path

import yaml

from . import db
from . import graph as G

SEED = Path(__file__).resolve().parent.parent / "seed"
RECENCY = 0.85  # weight multiplier per episode back in time: older episodes fade


def framework() -> dict:
    return yaml.safe_load((SEED / "behaviours.yaml").read_text())


_F = None


def fw():
    global _F
    if _F is None:
        f = framework()
        _F = {"constructs": {c["id"]: c for c in f["constructs"]}, "indicators": {i["id"]: i for i in f["indicators"]},
              "events": f["events"], "retention": f["retention"], "version": f["version"]}
    return _F


def _familiar(learner: str, concepts: list[str]) -> bool:
    """Would we expect the pupil to try this themselves? Secure on it already, or taught to the class before this week."""
    g = G.get()
    p = db.q1("select class from pupil where id=%s", (learner,))
    cls = db.q1("select current_week from class where id=%s", (p["class"],))
    for c in concepts:
        c = g.resolve(c)
        st = db.q1("select status from learner_state where learner=%s and concept=%s", (learner, c))
        if st and st["status"] == "secure":
            return True
        wk = db.q1("""select min(u.week) w from alignment a join content_unit u on u.id=a.subject
                      where u.class=%s and a.facet='concept' and a.target=%s and a.confidence >= 0.5""", (p["class"], c))
        if wk and wk["w"] is not None and wk["w"] < cls["current_week"]:
            return True
    return False


def derive(learner: str, events: list[dict], concepts: list[str]) -> list[tuple[str, str]]:
    """Episode rules. Returns [(indicator_id, human-readable detail)]."""
    ev = sorted(events, key=lambda e: e.get("t", 0))
    attempts = [e for e in ev if e["type"] == "attempt"]
    first_t = attempts[0]["t"] if attempts else float("inf")
    hints = [e for e in ev if e["type"] == "hint_requested"]
    out = []
    early = [h for h in hints if h["t"] < first_t]
    if early:
        if _familiar(learner, concepts):
            out.append(("bi:help-without-trying", f"hint after {early[0]['t']:.0f}s, before any attempt, on a familiar topic"))
    if any(h["t"] > first_t for h in hints):
        out.append(("bi:tried-before-help", "asked for a hint after attempting"))
    if len(hints) >= 3:
        out.append(("bi:hint-overuse", f"{len(hints)} hints on one question"))
    wrong = [a for a in attempts if not a.get("correct")]
    if any(not a.get("correct") and any(b["t"] > a["t"] for b in attempts) for a in attempts):
        out.append(("bi:persisted", f"{len(attempts)} attempts"))
    if attempts and not attempts[-1].get("correct") and any(e["type"] == "abandoned" for e in ev):
        out.append(("bi:gave-up", "stopped after a wrong answer"))
    answers = [str(a.get("answer", "")).strip() for a in wrong]
    if len(answers) != len(set(answers)):
        out.append(("bi:repeated-method", f"gave '{max(set(answers), key=answers.count)}' more than once"))
    if wrong and attempts[-1].get("correct") and str(attempts[-1].get("answer")) not in answers:
        out.append(("bi:changed-approach", "different approach after an error, then correct"))
    if any(e["type"] == "answer_revised" for e in ev) and attempts and attempts[-1].get("correct"):
        out.append(("bi:self-corrected", "revised own answer to a correct one"))
    if any(e["type"] == "checked" for e in ev):
        out.append(("bi:checked-answer", "checked the answer"))
    if any(e["type"] == "plan_stated" and e["t"] < first_t for e in ev):
        out.append(("bi:planned", "described an approach first"))
    conf = [e for e in ev if e["type"] == "confidence"]
    if conf and attempts:
        r, ok = conf[-1].get("rating", 2), attempts[-1].get("correct")
        if r >= 3 and not ok:
            out.append(("bi:overconfident", f"confidence {r}/4 on a wrong answer"))
        elif r <= 2 and ok:
            out.append(("bi:underconfident", f"confidence {r}/4 on a correct answer"))
        else:
            out.append(("bi:calibrated", f"confidence {r}/4 matched the outcome"))
    return out


def ingest(learner: str, episode: int, item: str | None, concepts: list[str], source: str, process: list[dict] | None) -> dict:
    """Store observable events (except session-only affect), then derive this episode's indicators."""
    if not process:
        return {"stored": 0, "discarded_session_only": 0, "indicators": []}
    kept = [e for e in process if e.get("type") != "affect"]
    discarded = len(process) - len(kept)
    for e in kept:
        payload = {k: v for k, v in e.items() if k not in ("type", "t")}
        db.ex("insert into behaviour_event(learner,episode,item,concepts,type,t,payload,source) values(%s,%s,%s,%s,%s,%s,%s,%s)",
              (learner, episode, item, db.J(concepts), e["type"], e.get("t", 0), db.J(payload), source))
    inds = derive(learner, [{"type": e["type"], "t": e.get("t", 0), **e} for e in kept], concepts)
    f = fw()
    for iid, detail in inds:
        i = f["indicators"][iid]
        db.ex("insert into learner_indicator(learner,episode,indicator,construct,polarity,concept,detail) values(%s,%s,%s,%s,%s,%s,%s)",
              (learner, episode, iid, i["construct"], i["polarity"], concepts[0] if concepts else None, detail))
    project(learner)
    return {"stored": len(kept), "discarded_session_only": discarded,
            "indicators": [{"id": iid, "label": f["indicators"][iid]["label"], "detail": d,
                            "construct": f["constructs"][f["indicators"][iid]["construct"]]["label"],
                            "polarity": f["indicators"][iid]["polarity"]} for iid, d in inds]}


def project(learner: str) -> None:
    """Recency-weighted construct patterns. Keeps any teacher confirmation already given."""
    f = fw()
    confirmed = {r["construct"] for r in db.q("select construct from learner_construct where learner=%s and teacher_confirmed", (learner,))}
    rows = db.q("select * from learner_indicator where learner=%s order by episode desc, id desc", (learner,))
    episodes = []
    for r in rows:
        if r["episode"] not in episodes:
            episodes.append(r["episode"])
    by = defaultdict(list)
    for r in rows:
        by[r["construct"]].append(r)
    db.ex("delete from learner_construct where learner=%s", (learner,))
    for cid, rs in by.items():
        wpos = sum(RECENCY ** episodes.index(r["episode"]) for r in rs if r["polarity"] > 0)
        wneg = sum(RECENCY ** episodes.index(r["episode"]) for r in rs if r["polarity"] < 0)
        npos, nneg = sum(r["polarity"] > 0 for r in rs), sum(r["polarity"] < 0 for r in rs)
        score = (wpos - wneg) / (wpos + wneg) if (wpos + wneg) else 0
        neg_share = wneg / (wpos + wneg) if (wpos + wneg) else 0
        if nneg >= 2 and neg_share >= 0.5:
            status = "support"
        elif npos >= 2 and neg_share <= 0.2:
            status = "strength"
        elif len(rs) >= 2:
            status = "mixed"
        else:
            status = "too little evidence"
        counts = defaultdict(int)
        for r in rs:
            counts[r["indicator"]] += 1
        top = sorted(counts.items(), key=lambda x: (-(f["indicators"][x[0]]["polarity"] < 0) if status == "support" else 0, -x[1]))
        summary = "; ".join(f"{f['indicators'][i]['label'].lower()} ({n}×)" for i, n in top[:2])
        db.ex("insert into learner_construct values(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (learner, cid, round(score, 2), len(rs), npos, nneg, status, summary, cid in confirmed))


def view(learner: str) -> dict:
    f = fw()
    cons = db.q("select * from learner_construct where learner=%s", (learner,))
    order = {"support": 0, "strength": 1, "mixed": 2, "too little evidence": 3}
    cons.sort(key=lambda c: (order.get(c["status"], 9), -c["n"]))
    inds = db.q("select * from learner_indicator where learner=%s order by episode desc, id", (learner,))
    g = G.get()
    return {
        "patterns": [{"construct": c["construct"], "label": f["constructs"][c["construct"]]["label"],
                      "description": f["constructs"][c["construct"]]["description"], "status": c["status"],
                      "score": c["score"], "n": c["n"], "summary": c["summary"],
                      "support": f["constructs"][c["construct"]]["support"], "teacher_confirmed": c["teacher_confirmed"]} for c in cons],
        "indicators": [{"episode": i["episode"], "label": f["indicators"][i["indicator"]]["label"], "polarity": i["polarity"],
                        "construct": f["constructs"][i["construct"]]["label"], "topic": g.label(i["concept"]) if i["concept"] else None,
                        "detail": i["detail"]} for i in inds],
        "retention": f["retention"],
    }


def process_summary(episode: int) -> str:
    """Raw, uninterpreted event log for one answer (what a tutor gets without the framework)."""
    evs = db.q("select type, t, payload from behaviour_event where episode=%s order by t, id", (episode,))
    bits = []
    for e in evs:
        p = e["payload"] or {}
        if e["type"] == "attempt":
            bits.append(f"attempt \"{p.get('answer')}\" at {e['t']:.0f}s ({'right' if p.get('correct') else 'wrong'})")
        elif e["type"] == "hint_requested":
            bits.append(f"hint at {e['t']:.0f}s")
        elif e["type"] == "confidence":
            bits.append(f"confidence {p.get('rating')}/4")
        elif e["type"] == "abandoned":
            bits.append(f"stopped at {e['t']:.0f}s")
        else:
            bits.append(f"{e['type'].replace('_', ' ')} at {e['t']:.0f}s")
    return "; ".join(bits)


def briefing(learner: str) -> dict:
    """The 'how to support' section of the tutor briefing: patterns to support and strengths, never labels."""
    v = view(learner)
    sup = [p for p in v["patterns"] if p["status"] == "support"]
    strong = [p for p in v["patterns"] if p["status"] == "strength"]
    n_eps = db.q1("select count(distinct episode) n from learner_indicator where learner=%s", (learner,))["n"]
    return {
        "based_on": f"{n_eps} recent questions",
        "patterns_to_support": [{"area": p["label"], "what_we_saw": p["summary"], "how_to_help": p["support"]} for p in sup],
        "strengths": [{"area": p["label"], "what_we_saw": p["summary"]} for p in strong],
        "rule": "These are recent patterns, not fixed traits. Adapt how you help; never describe the pupil with a label.",
    }


def set_confirmed(learner: str, construct: str, value: bool) -> None:
    db.ex("update learner_construct set teacher_confirmed=%s where learner=%s and construct=%s", (value, learner, construct))


def shared(learner: str) -> list[dict]:
    """What may leave the tutoring tool: teacher-confirmed patterns only."""
    return [p for p in view(learner)["patterns"] if p["teacher_confirmed"]]


def clear(learner: str) -> None:
    db.ex("delete from behaviour_event where learner=%s", (learner,))
    db.ex("delete from learner_indicator where learner=%s", (learner,))
    db.ex("delete from learner_construct where learner=%s", (learner,))
