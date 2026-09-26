"""Context Assembly: the one call a tutor makes. Joins graph + learner state (H1) + class content (H3).

facets=True  → the H2 context pack (everything keyed on shared concept IDs)
facets=False → the baseline a tutor gets WITHOUT H2: raw activity log + text-similarity retrieval
"""
import json
import re

from . import align as A
from . import content as C
from . import db
from . import graph as G
from . import learner as L
from . import llm


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
        "note": "No shared map: the topic, misconceptions, earlier topics and the teacher's method must be worked out from raw text.",
    }
    pack["approx_tokens"] = len(json.dumps(pack)) // 4
    return pack


# ------------------------------------------------------------------ message reading & focus planning
INTENTS = ("specific", "check_answer", "practice", "revision", "next", "harder", "start", "stuck", "explain_method", "other")

READ_PROMPT = """A pupil sent this message to a maths tutor. Classify it using ONLY the concept IDs listed.

intent — one of: specific (asks about a particular question or topic), check_answer (asks whether an answer is right),
practice (wants a question to do), revision (wants to revise / prepare for a test), next (asks what to do next),
harder (wants harder or new work), start (does not know where to begin), stuck (stuck, no details),
explain_method (wants something explained a particular way), other.
concept — the concept the maths in the message is about, or null if the message names no maths.
  For an answer check such as "is x = 4 right for 4x + 5 = x − 7", the concept is the type of equation, NOT substitution.
  For an equation like 3(x + 2) = 21 it is equations with brackets; for 5x + 3 = 2x + 12 it is unknowns on both sides.

CONCEPTS:
{concepts}

MESSAGE: \"\"\"{message}\"\"\"

Return ONLY JSON: {{"intent":"...","concept":"<id or null>","confidence":0.0}}"""

_EQ = re.compile(r"[0-9a-zA-Z()\s+\-−×÷/*]{3,}=[^?.!]{1,}")


def read_message(message: str, mode: str | None = None) -> dict:
    g = G.get()
    if (mode or A.default_mode()) == "claude" and llm.available():
        try:
            prompt = READ_PROMPT.format(concepts="\n".join(f"- {n['id']}: {n['label']}" for n in g.of_type("concept")), message=message)
            d = A._parse_json(llm.complete(prompt, system="You output strict JSON only.", max_tokens=200))
            c = d.get("concept")
            ok = c in g.nodes and g.nodes[c]["type"] == "concept" and g.nodes[c]["status"] == "active"
            return {"intent": d.get("intent") if d.get("intent") in INTENTS else "other",
                    "concept": c if ok else None, "confidence": float(d.get("confidence") or 0.6) if ok else 0.0,
                    "provenance": f"claude:{llm.model()}"}
        except Exception as e:  # fall through to the deterministic reader
            fallback = f"{type(e).__name__}"
        else:
            fallback = None
    t = message.lower()
    intent = ("check_answer" if re.search(r"\b(is|are)\b.*\b(right|correct)\b", t) else
              "revision" if re.search(r"revis|test|exam", t) else
              "harder" if re.search(r"harder|something new|challenge|boring|skip", t) else
              "next" if re.search(r"what('?s| is)? next|finished", t) else
              "practice" if re.search(r"practi[cs]e|quiz|question to", t) else
              "start" if re.search(r"where (should|do) i start|don'?t (really )?get algebra", t) else
              "stuck" if re.search(r"stuck|homework", t) else
              "explain_method" if re.search(r"way my teacher|explain", t) else "specific")
    m = _EQ.search(message)
    al = A.heuristic(m.group(0) if m else message)
    top = al["concepts"][0] if al["concepts"] else None
    return {"intent": intent, "concept": top["id"] if top and top["confidence"] >= 0.5 else None,
            "confidence": top["confidence"] if top else 0.0, "provenance": "heuristic-v1"}


def _status(sm, cid):
    s = sm.get(cid)
    return s["status"] if s else "no evidence"


def learning_edge(g, sm, active_mc, taught: dict) -> str | None:
    """Earliest concept in the taught sequence that this pupil has not secured (or that an active misconception affects)."""
    layers = g.layers()
    affected = {c for m in active_mc for c in g.affects.get(m, [])}
    order = sorted(taught, key=lambda c: (layers.get(c, 0), taught[c]))
    for c in order:
        if _status(sm, c) in ("gap", "developing") or c in affected:
            return c
    return None


def prerequisite_gaps(g, sm, concepts) -> list[str]:
    out = []
    for c in concepts:
        for p, _ in g.prereqs.get(c, []):
            if _status(sm, p) == "gap" and p not in out:
                out.append(p)
    return out


def plan_focus(p, cls, message, mode):
    """Decide what the tutor should focus on for this turn, and why."""
    g = G.get()
    sm = L.state_map(p["id"])
    active = L.active_misconceptions(p["id"])
    cov = C.coverage(cls["id"])
    taught = {c["id"]: c["week"] for c in cov["taught"]}
    read = read_message(message or "", mode) if message else {"intent": "other", "concept": None, "confidence": 0.0}
    edge = learning_edge(g, sm, active, taught)
    if read["concept"] and read["intent"] in ("specific", "check_answer", "explain_method", "stuck", "practice") and read["confidence"] >= 0.5:
        return read["concept"], {"how": "the maths named in the pupil's message", "intent": read["intent"], "confidence": read["confidence"], "provenance": read["provenance"]}, read, edge
    if read["concept"] and read["intent"] in ("harder", "next", "revision") and read["concept"] not in taught:
        # the pupil asked for a specific topic that is beyond what has been taught
        return read["concept"], {"how": "topic the pupil asked about (not yet taught)", "intent": read["intent"], "provenance": read["provenance"]}, read, edge
    if edge:
        return edge, {"how": "the pupil's learning edge: earliest taught topic not yet secure", "intent": read["intent"]}, read, edge
    gaps = prerequisite_gaps(g, sm, taught)
    if read["intent"] == "revision" and gaps:
        return gaps[0], {"how": "a weak prerequisite of the taught topics (everything taught is secure)", "intent": read["intent"]}, read, edge
    if taught:
        latest = max(taught, key=lambda c: (taught[c], g.layers().get(c, 0)))
        return latest, {"how": "latest taught topic (everything taught is secure)", "intent": read["intent"]}, read, edge
    return None, {"how": "none"}, read, edge


def next_step(g, sm, focus, focus_secure, taught: dict, planned: dict):
    """Readiness-aware: consolidate if not secure; otherwise the next topic in the class's scheme."""
    if focus not in taught:
        weak = [a["id"] for a in g.ancestors(focus, depth=2) if _status(sm, a["id"]) in ("gap", "developing")]
        return {"action": "preview_requested", "concept": g.ref(focus), "week": planned.get(focus),
                "why": ("not yet taught to the class" + (f" (planned for week {planned[focus]})" if focus in planned else "")
                        + ": acknowledge that, and keep any preview short and clearly flagged"),
                "check_first": [g.ref(w) for w in weak]}
    if not focus_secure:
        after = [d for d, _ in g.dependents.get(focus, []) if d in taught or d in planned]
        return {"action": "consolidate", "concept": g.ref(focus),
                "why": "the pupil has not secured this yet, so do not move on",
                "after_that": g.ref(sorted(after, key=lambda d: taught.get(d, planned.get(d, 99)))[0]) if after else None}
    cands = [d for d, _ in g.dependents.get(focus, []) if d in taught or d in planned]
    cands.sort(key=lambda d: (d not in taught, taught.get(d, planned.get(d, 99))))
    for d in cands:
        missing = [p for p, _ in g.prereqs.get(d, []) if _status(sm, p) in ("gap",)]
        if d in taught:
            return {"action": "move_on", "concept": g.ref(d), "why": "taught to the class and the pupil is ready", "blocked_by": [g.ref(m) for m in missing]}
        return {"action": "preview", "concept": g.ref(d), "week": planned[d],
                "why": f"not yet taught to the class (planned for week {planned[d]}); only as a clearly flagged preview",
                "check_first": [g.ref(m) for m in missing]}
    return {"action": "stretch", "concept": g.ref(focus), "why": "harder questions on the same topic; nothing further in the class's scheme yet"}


def _with_h2(p, cls, concept, message, mode):
    g = G.get()
    if concept:
        focus, how, read, edge = g.resolve(concept), {"how": "requested by caller"}, None, None
    else:
        focus, how, read, edge = plan_focus(p, cls, message, mode)
    if not focus:
        return {"mode": "with_h2", "error": "Could not resolve a focus concept — pass a concept or a message."}
    sm = L.state_map(p["id"])
    active = L.active_misconceptions(p["id"])
    relevant_mc = [m for m in active if m in g.affected_by.get(focus, [])]
    other_mc = [m for m in active if m not in relevant_mc]
    cov = C.coverage(cls["id"])
    taught = {c["id"]: c["week"] for c in cov["taught"]}
    planned = {c["id"]: c["week"] for c in cov["planned"]}

    def ls(cid):
        s = sm.get(cid)
        return {"status": s["status"], "mastery": s["mastery"]} if s else {"status": "no evidence", "mastery": None}

    focus_status = ls(focus)["status"]
    focus_secure = focus_status in ("secure", "inferred") and not relevant_mc
    prereqs = [{**g.ref(pid, strength=w), **ls(pid)} for pid, w in sorted(g.prereqs.get(focus, []), key=lambda x: -x[1])]
    gaps = [x for x in prereqs if x["status"] in ("gap", "developing")]
    pref = C.preferred_method(cls["id"], focus)
    method_id = pref["method"]["id"] if pref["method"] else None
    avoid = [g.ref(m) for m in g.taught_by.get(focus, []) if m != method_id] if method_id else []
    mats = C.search(cls["id"], message or g.label(focus), concept=focus, method=method_id, misconceptions=relevant_mc, k=3)
    layers = g.layers()

    if relevant_mc:
        diagnosis = f"Matches this pupil's repeated error pattern: {g.label(relevant_mc[0])}. Address it explicitly."
    elif read and read.get("intent") == "check_answer" and focus_status == "secure":
        diagnosis = "The pupil is secure on this topic, so a wrong answer here is most likely a one-off slip: check by substituting back rather than re-teaching."
    elif focus_status in ("gap", "developing"):
        diagnosis = "Not yet secure on this topic, with no specific misconception identified."
    elif focus not in taught:
        diagnosis = "This topic has not been taught to the class yet."
    else:
        diagnosis = "No known difficulty on this topic."

    pack = {
        "mode": "with_h2",
        "graph_version": g.version,
        "request": {"intent": (read or {}).get("intent"), "message_topic": g.label(read["concept"]) if read and read.get("concept") else None},
        "focus": {**g.ref(focus), "chosen_because": how, "crosswalk": g.crosswalk.get(focus, [])},
        "learner": {
            "name": p["name"], **ls(focus),
            "active_misconceptions": [{**g.ref(m), "description": g.nodes[m].get("description")} for m in relevant_mc],
            "other_active_misconceptions": [g.ref(m) for m in other_mc],
            "prerequisites": prereqs,
            "overview": [{"topic": g.label(c), "status": s["status"]} for c, s in sorted(sm.items(), key=lambda x: layers.get(x[0], 0)) if s["status"] != "inferred"],
            "learning_edge": g.label(edge) if edge else "none — secure on everything taught so far",
        },
        "class": {
            "id": cls["id"], "teacher": cls["teacher"], "current_week": cls["current_week"],
            "focus_taught": focus in taught, "focus_week": taught.get(focus) or planned.get(focus),
            "taught_so_far": [f"Wk{w} {g.label(c)}" for c, w in sorted(taught.items(), key=lambda x: x[1])],
            "not_yet_taught": [f"Wk{w} {g.label(c)}" for c, w in sorted(planned.items(), key=lambda x: x[1])],
            "preferred_method": pref["method"], "preferred_representation": pref["representation"],
        },
        "materials": mats,
        "guidance": {
            "diagnosis": diagnosis,
            "teach_with": {"method": pref["method"], "representation": pref["representation"]},
            "avoid_methods": avoid,
            "check_prerequisites_first": gaps,
            "next_step": next_step(g, sm, focus, focus_secure, taught, planned),
            "scope_rule": ("Stay within 'taught_so_far'. If the pupil asks for something in 'not_yet_taught', say it is coming later and only give a clearly flagged preview."),
        },
    }
    pack["approx_tokens"] = len(json.dumps(pack)) // 4
    return pack
