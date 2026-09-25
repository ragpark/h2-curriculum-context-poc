"""H1 pipeline: evidence → (align if untagged) → append-only store → learner-model projection with graph propagation."""
from . import align as A
from . import db
from . import graph as G

SECURE, DEVELOPING = 0.7, 0.4
MC_ACTIVE = 0.5


def _norm(s):
    return (s or "").strip().lower().replace("−", "-").replace(" ", "")


def record(learner: str, *, source: str, item: str | None = None, response: str | None = None,
           activity: str | None = None, score: float | None = None, mode: str | None = None) -> dict:
    g = G.get()
    p = db.q1("select * from pupil where id=%s", (learner,))
    if not p:
        raise KeyError(learner)
    misconception, alignment = None, None
    if item:
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
        alignment = A.align(activity or "", mode)
        concepts = [c["id"] for c in alignment["concepts"]]
        conf = min([c["confidence"] for c in alignment["concepts"]] or [0.0])
        prov = alignment["provenance"]
        outcome = float(score if score is not None else 0.5)
    row = db.q1("""insert into evidence(learner,tenant,source,item,activity,response,outcome,concepts,concept_provenance,confidence,misconception,graph_version)
                   values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id, ts""",
                (learner, p["class"], source, item, activity, response, outcome, db.J(concepts), prov, conf, misconception, g.version))
    project(learner)
    return {"evidence_id": row["id"], "outcome": outcome, "concepts": [g.ref(c) for c in concepts],
            "concept_provenance": prov, "confidence": conf,
            "misconception": g.ref(misconception) if misconception else None, "alignment": alignment}


def project(learner: str) -> None:
    """Rebuild learner state by replaying the immutable evidence log through the current graph."""
    g = G.get()
    state: dict[str, dict] = {}
    mcs: dict[str, dict] = {}

    def st(c):
        return state.setdefault(c, {"m": 0.5, "n": 0, "ni": 0})

    for e in db.q("select * from evidence where learner=%s order by id", (learner,)):
        w = 1.0 if e["concept_provenance"] == "source" else 0.6 * e["confidence"]
        mc_id = g.resolve(e["misconception"]) if e["misconception"] else None
        for raw in e["concepts"]:
            c = g.resolve(raw)
            if mc_id and e["outcome"] < 0.5 and c not in g.affects.get(mc_id, []):
                continue  # graph-based credit assignment: blame the concept the misconception actually affects
            s = st(c)
            k = 0.4 * w / (1 + 0.12 * s["n"])
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
                      "graph_version": e["graph_version"], "ts": e["ts"].isoformat()} for e in ev],
    }


def state_map(learner: str) -> dict[str, dict]:
    return {s["concept"]: s for s in db.q("select * from learner_state where learner=%s", (learner,))}


def active_misconceptions(learner: str) -> list[str]:
    return [m["misconception"] for m in db.q("select * from learner_misconception where learner=%s and strength >= %s order by strength desc", (learner, MC_ACTIVE))]


def clear(learner: str):
    db.ex("delete from evidence where learner=%s", (learner,))
    project(learner)
