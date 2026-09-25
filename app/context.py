"""Context Assembly: the one call a tutor makes. Joins graph + learner state (H1) + class content (H3).

facets=True  → the H2 context pack (everything keyed on shared concept IDs)
facets=False → the baseline a tutor gets WITHOUT H2: raw activity log + text-similarity retrieval
"""
import json

from . import align as A
from . import content as C
from . import db
from . import graph as G
from . import learner as L


def _focus(learner: str, concept: str | None, message: str | None, mode: str | None):
    g = G.get()
    if concept:
        return g.resolve(concept), {"how": "requested by caller"}
    if message:
        al = A.align(message, mode)
        if al["concepts"]:
            top = al["concepts"][0]
            return top["id"], {"how": "aligned from pupil message", "confidence": top["confidence"], "provenance": al["provenance"]}
    last = db.q1("select concepts from evidence where learner=%s and jsonb_array_length(concepts) > 0 order by id desc limit 1", (learner,))
    if last and last["concepts"]:
        return g.resolve(last["concepts"][0]), {"how": "most recent evidence"}
    return None, {"how": "none"}


def assemble(learner: str, *, concept: str | None = None, message: str | None = None,
             facets: bool = True, mode: str | None = None) -> dict:
    p = db.q1("select * from pupil where id=%s", (learner,))
    if not p:
        raise KeyError(learner)
    cls = db.q1("select * from class where id=%s", (p["class"],))
    return (_with_h2 if facets else _without_h2)(p, cls, concept, message, mode)


def _without_h2(p, cls, concept, message, mode):
    items = {i["id"]: i for i in db.q("select id, prompt from item")}
    log = []
    for e in db.q("select * from evidence where learner=%s order by id desc limit 8", (p["id"],)):
        if e["item"]:
            log.append(f"{e['source']}: \"{items[e['item']]['prompt']}\" → \"{e['response']}\" ({'correct' if e['outcome'] >= 0.5 else 'incorrect'})")
        else:
            log.append(f"{e['source']}: \"{e['activity']}\" — {round(e['outcome'] * 100)}%")
    query = message or (G.get().label(concept) if concept else "")
    pack = {
        "mode": "without_h2",
        "pupil": {"name": p["name"], "class": p["class"]},
        "recent_activity": log,
        "materials": C.search(cls["id"], query, k=3),
        "note": "No shared concept IDs: topic, misconceptions, prerequisites and the teacher's method must be inferred from raw text.",
    }
    pack["approx_tokens"] = len(json.dumps(pack)) // 4
    return pack


def _with_h2(p, cls, concept, message, mode):
    g = G.get()
    focus, how = _focus(p["id"], concept, message, mode)
    if not focus:
        return {"mode": "with_h2", "error": "Could not resolve a focus concept — pass a concept or a message."}
    sm = L.state_map(p["id"])
    active = L.active_misconceptions(p["id"])
    relevant_mc = [m for m in active if m in g.affected_by.get(focus, [])]
    other_mc = [m for m in active if m not in relevant_mc]
    cov = C.coverage(cls["id"])
    taught = {c["id"]: c["week"] for c in cov["taught"]}

    def ls(cid):
        s = sm.get(cid)
        return {"status": s["status"], "mastery": s["mastery"]} if s else {"status": "no evidence", "mastery": None}

    prereqs = [{**g.ref(pid, strength=w), **ls(pid)} for pid, w in sorted(g.prereqs.get(focus, []), key=lambda x: -x[1])]
    gaps = [x for x in prereqs if x["status"] in ("gap", "no evidence")]
    nexts = []
    for nid, w in sorted(g.dependents.get(focus, []), key=lambda x: -x[1]):
        nexts.append({**g.ref(nid), "taught_to_class": nid in taught, **ls(nid)})
    next_step = nexts[0] if nexts else None
    pref = C.preferred_method(cls["id"], focus)
    method_id = pref["method"]["id"] if pref["method"] else None
    avoid = [g.ref(m) for m in g.taught_by.get(focus, []) if m != method_id] if method_id else []
    mats = C.search(cls["id"], message or g.label(focus), concept=focus, method=method_id, misconceptions=relevant_mc, k=3)

    pack = {
        "mode": "with_h2",
        "graph_version": g.version,
        "focus": {**g.ref(focus), "resolved": how, "crosswalk": g.crosswalk.get(focus, [])},
        "learner": {
            "name": p["name"], **ls(focus),
            "active_misconceptions": [{**g.ref(m), "description": g.nodes[m].get("description")} for m in relevant_mc],
            "other_active_misconceptions": [g.ref(m) for m in other_mc],
            "prerequisites": prereqs,
        },
        "class": {
            "id": cls["id"], "teacher": cls["teacher"], "current_week": cls["current_week"],
            "focus_taught": focus in taught, "focus_taught_week": taught.get(focus),
            "preferred_method": pref["method"], "preferred_representation": pref["representation"], "preference_scope": pref["scope"],
        },
        "materials": mats,
        "guidance": {
            "target_misconception": g.ref(relevant_mc[0]) if relevant_mc else None,
            "reteach_with": {"method": pref["method"], "representation": pref["representation"]},
            "avoid_methods": avoid,
            "check_prerequisites": gaps,
            "next_step": next_step,
            "next_step_note": (None if not next_step else
                               ("taught to the class" if next_step["taught_to_class"] else "NOT yet taught to the class — preview only, or stay on the focus concept")),
        },
    }
    pack["approx_tokens"] = len(json.dumps(pack)) // 4
    return pack
