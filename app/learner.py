"""H1 pipeline: evidence → (align if untagged) → append-only store → learner-model projection with graph propagation."""
from . import align as A
from . import clock
from . import db
from . import behaviour as B
from . import graph as G
from . import retention as R
from . import seed as S

SECURE, DEVELOPING = 0.7, 0.4
MC_ACTIVE = 0.5
# Evidence a tutor tags itself (provenance 'tutor') is weighted between the alignment service and a teacher.
TUTOR_W, TUTOR_BASE = 0.8, 0.4


def _norm(s):
    return (s or "").strip().lower().replace("−", "-").replace(" ", "")


def record(learner: str, *, source: str, item: str | None = None, response: str | None = None,
           activity: str | None = None, score: float | None = None, mode: str | None = None,
           process: list[dict] | None = None, concepts: list[str] | None = None,
           misconception: str | None = None, days_ago: float | None = None, provenance: str | None = None) -> dict:
    """days_ago backdates the evidence (synthetic histories only): ts = clock.now() - days_ago.
    provenance='tutor' with concepts: the tutor, not a teacher, said which topics the answer covered."""
    g = G.get()
    ts = clock.ago(days_ago) if days_ago else clock.now()
    p = db.q1("select * from pupil where id=%s", (learner,))
    if not p:
        raise KeyError(learner)
    subject = S.subject_of_pupil(learner)
    alignment = None
    if misconception and (misconception not in g.nodes or g.nodes[misconception]["type"] != "misconception"):
        raise ValueError(f"unknown misconception {misconception}")
    if concepts:
        # Teacher-marked work: the teacher says which topics it covered, gives a mark and may record a misconception.
        bad = [c for c in concepts if c not in g.nodes or g.nodes[c]["type"] != "concept"]
        if bad:
            raise ValueError(f"unknown topics: {bad}")
        prov, conf = ("tutor" if provenance == "tutor" else "teacher"), 1.0
        outcome = float(score if score is not None else 0.5)
    elif item:
        misconception = None
        it = db.q1("select * from item where id=%s", (item,))
        if not it:
            raise KeyError(item)
        concepts, prov, conf = it["concepts"], "source", 1.0
        outcome = 1.0 if _norm(response) == _norm(it["answer"]) else 0.0
        if outcome == 0.0:
            for d in it["distractors"] or []:
                if _norm(d["response"]) == _norm(response):
                    misconception = d.get("misconception")
    else:
        # Activity-level markbook entry: no concept IDs supplied, so the alignment service interprets the title.
        alignment = A.align(activity or "", mode, subject)
        concepts = [c["id"] for c in alignment["concepts"]]
        conf = min([c["confidence"] for c in alignment["concepts"]] or [0.0])
        prov = alignment["provenance"]
        outcome = float(score if score is not None else 0.5)
    row = db.q1("""insert into evidence(learner,tenant,source,item,activity,response,outcome,concepts,concept_provenance,confidence,misconception,graph_version,ts)
                   values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id, ts""",
                (learner, p["class"], source, item, activity, response, outcome, db.J(concepts), prov, conf, misconception, g.version_for(subject), ts))
    # learning behaviour is interpreted against the pupil's knowledge BEFORE this answer, so derive it first
    beh = B.ingest(learner, row["id"], item, concepts, source, process, ts=ts)
    project(learner)
    return {"evidence_id": row["id"], "behaviour": beh, "outcome": outcome, "concepts": [g.ref(c) for c in concepts],
            "concept_provenance": prov, "confidence": conf,
            "misconception": g.ref(misconception) if misconception else None, "alignment": alignment}


def project(learner: str) -> None:
    """Rebuild learner state by replaying the immutable evidence log through the current graph."""
    g = G.get()
    state: dict[str, dict] = {}
    mcs: dict[str, dict] = {}

    def st(c):
        return state.setdefault(c, {"m": 0.5, "n": 0, "ni": 0})

    rows = db.q("select * from evidence where learner=%s order by id", (learner,))
    for e in rows:
        w = (1.0 if e["concept_provenance"] in ("source", "teacher") else
             TUTOR_W if e["concept_provenance"] == "tutor" else 0.6 * e["confidence"])
        # A teacher's mark on a piece of work carries more information than one right/wrong answer
        base = 0.6 if e["concept_provenance"] == "teacher" else TUTOR_BASE if e["concept_provenance"] == "tutor" else 0.4
        mc_id = g.resolve(e["misconception"]) if e["misconception"] else None
        for raw in e["concepts"]:
            c = g.resolve(raw)
            if mc_id and e["outcome"] < 0.5 and c not in g.affects.get(mc_id, []):
                continue  # graph-based credit assignment: blame the concept the misconception actually affects
            s = st(c)
            k = base * w / (1 + 0.12 * s["n"])
            s["m"] += k * (e["outcome"] - s["m"]); s["n"] += 1
            if e["outcome"] >= 0.5:
                # success is weak evidence that prerequisites are in place
                for pre, strength in g.prereqs.get(c, []):
                    ps = st(pre)
                    ps["m"] += 0.15 * strength * w * (1 - ps["m"]); ps["ni"] += 1
                # and weakens any misconception that affects this concept
                for mid in g.affected_by.get(c, []):
                    if mid in mcs:
                        mcs[mid]["strength"] *= 0.7
        if e["misconception"]:
            mid = g.resolve(e["misconception"])
            m = mcs.setdefault(mid, {"strength": 0.0, "count": 0})
            m["strength"] = min(1.0, m["strength"] + 0.3 * w); m["count"] += 1

    db.ex("delete from learner_state where learner=%s", (learner,))
    db.ex("delete from learner_misconception where learner=%s", (learner,))
    for c, s in state.items():
        db.ex("insert into learner_state values(%s,%s,%s,%s,%s,%s)", (learner, c, round(s["m"], 3), s["n"], s["ni"], status(s["m"], s["n"])))
    for mid, m in mcs.items():
        db.ex("insert into learner_misconception values(%s,%s,%s,%s)", (learner, mid, round(m["strength"], 3), m["count"]))
    # retention: a second projection over the same log, with the same credit-assignment rule
    rs = R.schedule([{"id": e["id"], "ts": e["ts"], "outcome": e["outcome"], "concepts": [g.resolve(c) for c in e["concepts"]],
                      "misconception": g.resolve(e["misconception"]) if e["misconception"] else None} for e in rows],
                    clock.now(), g.affects)
    db.ex("delete from review_state where learner=%s", (learner,))
    for c, s in rs.items():
        db.ex("insert into review_state(learner,concept,level,interval_days,last_success,last_seen,due_at,lapses,reviews) values(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (learner, c, s["level"], s["interval_days"], s["last_success"], s["last_seen"], s["due_at"], s["lapses"], s["reviews"]))


def reproject_all() -> int:
    """Rebuild every pupil's projections from the log (after a change to how they are derived, or a new projection)."""
    ids = [r["learner"] for r in db.q("select distinct learner from evidence")]
    for lid in ids:
        project(lid)
    return len(ids)


def status(m, n_direct):
    if n_direct == 0:
        return "inferred"
    return "secure" if m >= SECURE else "developing" if m >= DEVELOPING else "gap"


def view(learner: str) -> dict:
    g = G.get()
    p = db.q1("select * from pupil where id=%s", (learner,))
    states = db.q("select * from learner_state where learner=%s order by mastery desc", (learner,))
    mcs = db.q("select * from learner_misconception where learner=%s order by strength desc", (learner,))
    ev = db.q("select * from evidence where learner=%s order by id desc limit 60", (learner,))
    items = {i["id"]: i for i in db.q("select id, prompt, answer from item")}
    return {
        "pupil": p,
        "state": [{**g.ref(s["concept"]), "mastery": s["mastery"], "n_direct": s["n_direct"], "n_inferred": s["n_inferred"], "status": s["status"]} for s in states],
        "misconceptions": [{**g.ref(m["misconception"]), "strength": m["strength"], "count": m["count"], "active": m["strength"] >= MC_ACTIVE,
                            "affects": [g.ref(c) for c in g.affects.get(m["misconception"], [])]} for m in mcs],
        "evidence": [{"id": e["id"], "source": e["source"], "item": e["item"],
                      "prompt": items.get(e["item"], {}).get("prompt") if e["item"] else None,
                      "activity": e["activity"], "response": e["response"], "outcome": e["outcome"],
                      "concepts": [g.ref(c) for c in e["concepts"]], "concept_provenance": e["concept_provenance"],
                      "confidence": e["confidence"], "misconception": g.ref(e["misconception"]) if e["misconception"] else None,
                      "graph_version": e["graph_version"], "ts": e["ts"].isoformat(), "process": B.process_summary(e["id"])} for e in ev],
    }


def review_state(learner: str) -> dict[str, dict]:
    """The retention projection as stored, with 'due' and 'overdue_days' computed against the clock."""
    now = clock.now()
    out = {}
    for r in db.q("select * from review_state where learner=%s", (learner,)):
        r = dict(r)
        r["due"] = r["due_at"] is not None and r["due_at"] <= now
        r["overdue_days"] = round((now - r["due_at"]).total_seconds() / 86400, 1) if r["due_at"] else None
        out[r["concept"]] = r
    return out


def review_view(learner: str) -> dict:
    """Full review schedule for inspection: due topics first, most overdue at the top."""
    g = G.get()
    sm = state_map(learner)
    rows = []
    for c, r in review_state(learner).items():
        rows.append({**g.ref(c), "status": sm[c]["status"] if c in sm else "no evidence",
                     "level": r["level"], "interval_days": r["interval_days"],
                     "last_success": r["last_success"].isoformat() if r["last_success"] else None,
                     "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
                     "due_at": r["due_at"].isoformat() if r["due_at"] else None,
                     "due": r["due"], "overdue_days": r["overdue_days"], "lapses": r["lapses"], "reviews": r["reviews"]})
    rows.sort(key=lambda x: (not x["due"], -(x["overdue_days"] if x["overdue_days"] is not None else -1e9)))
    return {"learner": learner, "as_of": clock.today().isoformat(), "intervals": R.INTERVALS,
            "due": [x for x in rows if x["due"]], "not_due": [x for x in rows if not x["due"]]}


def state_map(learner: str) -> dict[str, dict]:
    return {s["concept"]: s for s in db.q("select * from learner_state where learner=%s", (learner,))}


def active_misconceptions(learner: str) -> list[str]:
    return [m["misconception"] for m in db.q("select * from learner_misconception where learner=%s and strength >= %s order by strength desc", (learner, MC_ACTIVE))]


def clear(learner: str):
    db.ex("delete from evidence where learner=%s", (learner,))
    B.clear(learner)
    project(learner)
