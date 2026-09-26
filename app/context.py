"""Context Assembly: the one call a tutor makes. Joins graph + learner state (H1) + class content (H3).

facets=True  → the H2 context pack (everything keyed on shared concept IDs)
facets=False → the baseline a tutor gets WITHOUT H2: raw activity log + text-similarity retrieval
"""
import json
import re

from . import align as A
from . import behaviour as B
from . import content as C
from . import db
from . import graph as G
from . import learner as L
from . import llm
from . import seed as S


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
            proc = B.process_summary(e["id"])
            log.append(f"{e['source']}: \"{items[e['item']]['prompt']}\" → \"{e['response']}\" ({'correct' if e['outcome'] >= 0.5 else 'incorrect'})" + (f" [{proc}]" if proc else ""))
        else:
            note = f" — teacher's comment: \"{G.get().label(e['misconception'])}\"" if e["misconception"] else ""
            log.append(f"{e['source']}: \"{e['activity']}\" — {round(e['outcome'] * 100)}%{note}")
    query = message or (G.get().label(concept) if concept else "")
    # (the raw arm gets the same materials search and activity log in every subject)
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

READ_PROMPT_EN = """A pupil sent this message to an English Literature tutor while studying Macbeth. Classify it using ONLY the concept IDs listed.

intent — one of: specific (asks about a particular question, quotation, character, theme or topic), check_answer (asks whether their answer or paragraph is right or good),
practice (wants a question to do), revision (wants to revise / prepare for a test), next (asks what to do next or how to improve),
harder (wants harder or new work), start (does not know where to begin), stuck (stuck, no details),
explain_method (wants something explained a particular way), other.
concept — the topic the message is mainly about: a character, theme, context or language feature of the play, or a writing skill
  (e.g. "how do I use quotations better?" is the quotation skill), or null if the message names no topic.

CONCEPTS:
{concepts}

MESSAGE: \"\"\"{message}\"\"\"

Return ONLY JSON: {{"intent":"...","concept":"<id or null>","confidence":0.0}}"""

_EQ = re.compile(r"[0-9a-zA-Z()\s+\-−×÷/*]{3,}=[^?.!]{1,}")


def read_message(message: str, mode: str | None = None, subject: str = "maths") -> dict:
    g = G.get()
    if (mode or A.default_mode()) == "claude" and llm.available():
        try:
            tmpl = READ_PROMPT if subject == "maths" else READ_PROMPT_EN
            prompt = tmpl.format(concepts="\n".join(f"- {n['id']}: {n['label']}" for n in g.of_type("concept", subject=subject)), message=message)
            d = A._parse_json(llm.complete(prompt, system="You output strict JSON only.", max_tokens=200))
            c = d.get("concept")
            ok = c in g.nodes and g.nodes[c]["type"] == "concept" and g.nodes[c]["status"] == "active" and g.subject_of(c) == subject
            return {"intent": d.get("intent") if d.get("intent") in INTENTS else "other",
                    "concept": c if ok else None, "confidence": float(d.get("confidence") or 0.6) if ok else 0.0,
                    "provenance": f"claude:{llm.model()}"}
        except Exception as e:  # fall through to the deterministic reader
            fallback = f"{type(e).__name__}"
        else:
            fallback = None
    t = message.lower()
    intent = ("check_answer" if subject != "maths" and re.search(r"check my|is (this|my) .*(good|right|ok)", t) else
              "check_answer" if re.search(r"\b(is|are)\b.*\b(right|correct)\b", t) else
              "revision" if re.search(r"revis|test|exam", t) else
              "harder" if re.search(r"harder|something new|challenge|boring|skip", t) else
              "next" if re.search(r"what('?s| is)? next|finished", t) else
              "practice" if re.search(r"practi[cs]e|quiz|question to", t) else
              "start" if re.search(r"where (should|do) i start|don'?t (really )?get algebra", t) else
              "stuck" if re.search(r"stuck|homework", t) else
              "explain_method" if re.search(r"way my teacher|explain", t) else "specific")
    m = _EQ.search(message) if subject == "maths" else None
    al = A.heuristic(m.group(0) if m else message, subject)
    top = al["concepts"][0] if al["concepts"] else None
    return {"intent": intent, "concept": top["id"] if top and top["confidence"] >= 0.5 else None,
            "confidence": top["confidence"] if top else 0.0, "provenance": "heuristic-v1"}


def _status(sm, cid):
    s = sm.get(cid)
    return s["status"] if s else "no evidence"


def _web(subject) -> bool:
    return bool(S.SUBJECTS.get(subject, {}).get("cross_cutting"))


def scope(g, cls, cov) -> tuple[dict, dict]:
    """What is in scope for this class: {concept: week} taught and planned.

    Ladder subjects (maths): a topic is taught once a dated, taught lesson is tagged with it.
    Web subjects (English): that rule fails, because skills are practised in every lesson but are rarely a lesson's
    main tag, and characters and themes recur across the whole play. So:
      • cross-cutting strands (writing skills) are always in scope;
      • any other topic is in scope if a taught lesson names it, or unless every key quotation that evidences it
        sits in a part of the play the class has not reached yet (then it is planned for when the class gets there)."""
    taught = {c["id"]: c["week"] for c in cov["taught"]}
    planned = {c["id"]: c["week"] for c in cov["planned"]}
    subject = cls["subject"]
    if not _web(subject):
        return taught, planned
    cross = set(S.SUBJECTS[subject].get("cross_cutting") or [])
    studied = {x["id"] for x in cov.get("sections_studied", [])}
    coming = {x["id"]: x.get("week") for x in cov.get("sections_coming", [])}
    t, p = {}, {}
    for n in g.of_type("concept", subject=subject):
        c = n["id"]
        if n.get("strand") in cross:
            t[c] = taught.get(c, 1)
            continue
        secs = {g.section_of.get(q) for q in g.evidenced_by.get(c, [])} - {None}
        if c not in taught and secs and not secs & studied:  # a taught lesson naming the topic always wins
            p[c] = min([coming.get(x) or cls["current_week"] + 1 for x in secs])
        else:
            t[c] = taught.get(c, cls["current_week"])
    return t, p


def learning_edge(g, sm, active_mc, taught: dict, subject: str = "maths") -> str | None:
    """Earliest concept in the taught sequence that this pupil has not secured (or that an active misconception affects).

    In a web-shaped subject there is no prerequisite order among most topics, so the strongest signal is the pupil's
    repeated error pattern: topics an active misconception affects come first, then gaps, then developing topics."""
    affected = {c for m in active_mc for c in g.affects.get(m, [])}
    if _web(subject):
        rank = {"gap": 0, "developing": 1}
        cands = [c for c in taught if c in affected or _status(sm, c) in rank]
        if not cands:
            return None
        return min(cands, key=lambda c: (c not in affected, rank.get(_status(sm, c), 2), taught[c]))
    layers = g.layers(subject)
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
    subject = cls["subject"]
    taught, _ = scope(g, cls, cov)
    read = read_message(message or "", mode, subject) if message else {"intent": "other", "concept": None, "confidence": 0.0}
    edge = learning_edge(g, sm, active, taught, subject)
    if read["concept"] and read["intent"] in ("specific", "check_answer", "explain_method", "stuck", "practice") and read["confidence"] >= 0.5:
        return read["concept"], {"how": "the maths named in the pupil's message" if subject == "maths" else "the topic named in the pupil's message", "intent": read["intent"], "confidence": read["confidence"], "provenance": read["provenance"]}, read, edge
    if read["concept"] and read["intent"] in ("harder", "next", "revision") and read["concept"] not in taught:
        # the pupil asked for a specific topic that is beyond what has been taught
        return read["concept"], {"how": "topic the pupil asked about (not yet taught)", "intent": read["intent"], "provenance": read["provenance"]}, read, edge
    if edge:
        return edge, {"how": "the pupil's learning edge: earliest taught topic not yet secure", "intent": read["intent"]}, read, edge
    gaps = prerequisite_gaps(g, sm, taught)
    if read["intent"] == "revision" and gaps:
        return gaps[0], {"how": "a weak prerequisite of the taught topics (everything taught is secure)", "intent": read["intent"]}, read, edge
    if taught:
        latest = max(taught, key=lambda c: (taught[c], g.layers(subject).get(c, 0)))
        return latest, {"how": "latest taught topic (everything taught is secure)", "intent": read["intent"]}, read, edge
    return None, {"how": "none"}, read, edge


def next_step(g, sm, focus, focus_secure, taught: dict, planned: dict, subject: str = "maths", edge: str | None = None,
              coming_sections: list | None = None):
    """Readiness-aware: consolidate if not secure; otherwise the next topic in the class's scheme.
    Web-shaped subjects: when nothing follows on the ladder, use this topic to practise the pupil's learning edge,
    or connect it to a related topic the class has studied."""
    if coming_sections:
        s0 = coming_sections[0]
        return {"action": "preview_requested", "part_of_text": s0["label"], "week": s0.get("week"),
                "why": (f"the class has not reached {s0['label']} yet" + (f" (planned for week {s0['week']})" if s0.get("week") else "")
                        + ": pupils often read ahead, so answer briefly, say it is coming up in class, and link it back to what they have studied"),
                "link_back_to": g.ref(edge or focus)}
    if focus not in taught:
        weak = [a["id"] for a in g.ancestors(focus, depth=2) if _status(sm, a["id"]) in ("gap", "developing")]
        return {"action": "preview_requested", "concept": g.ref(focus), "week": planned.get(focus),
                "why": ("not yet taught to the class" + (f" (planned for week {planned[focus]})" if focus in planned else "")
                        + ": acknowledge that, and keep any preview short and clearly flagged"),
                "check_first": [g.ref(w) for w in weak]}
    if not focus_secure:
        after = [d for d, _ in g.dependents.get(focus, []) if d in taught or d in planned]
        if not after and _web(subject):
            after = [r for r in g.related.get(focus, []) if r in taught]
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
    if _web(subject):
        if edge and edge != focus:
            return {"action": "practise_on_this_topic", "concept": g.ref(edge), "topic": g.ref(focus),
                    "why": f"the pupil knows this topic; use it to practise their learning edge ({g.label(edge)})"}
        rel = [r for r in g.related.get(focus, []) if r in taught]
        rel.sort(key=lambda r: ({"gap": 0, "developing": 1}.get(_status(sm, r), 2), taught[r]))
        if rel:
            return {"action": "connect", "concept": g.ref(rel[0]), "why": "connect this topic to a related one the class has studied"}
    return {"action": "stretch", "concept": g.ref(focus), "why": "harder questions on the same topic; nothing further in the class's scheme yet"}


def _with_h2(p, cls, concept, message, mode):
    g = G.get()
    subject = cls["subject"]
    if concept:
        focus, how, read, edge = g.resolve(concept), {"how": "requested by caller"}, None, None
    else:
        focus, how, read, edge = plan_focus(p, cls, message, mode)
    if not focus:
        return {"mode": "with_h2", "error": "Could not resolve a focus concept — pass a concept or a message."}
    sm = L.state_map(p["id"])
    active = L.active_misconceptions(p["id"])
    cross = set(S.SUBJECTS.get(subject, {}).get("cross_cutting") or [])
    # a misconception about a cross-cutting skill (e.g. retelling instead of analysing) is relevant to every topic
    relevant_mc = [m for m in active if m in g.affected_by.get(focus, []) or any(g.strand(c) in cross for c in g.affects.get(m, []))]
    other_mc = [m for m in active if m not in relevant_mc]
    cov = C.coverage(cls["id"])
    taught, planned = scope(g, cls, cov)
    lessons = {c["id"]: c["week"] for c in cov["taught"]}  # topics named in taught lessons (what the class actually did)

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
    layers = g.layers(subject)
    if concept:
        edge = learning_edge(g, sm, active, taught, subject)
    refs = A.text_refs(message or "", subject) if message else {}
    studied_ids = {x["id"] for x in cov.get("sections_studied", [])}
    coming = {x["id"]: x for x in cov.get("sections_coming", [])}
    mentioned_coming = [coming[x["id"]] for x in refs.get("sections", []) if x["id"] in coming]

    if relevant_mc:
        diagnosis = f"Matches this pupil's repeated error pattern: {g.label(relevant_mc[0])}. Address it explicitly."
    elif subject == "maths" and read and read.get("intent") == "check_answer" and focus_status == "secure":
        diagnosis = "The pupil is secure on this topic, so a wrong answer here is most likely a one-off slip: check by substituting back rather than re-teaching."
    elif focus_status in ("gap", "developing"):
        diagnosis = "Not yet secure on this topic, with no specific misconception identified."
    elif focus not in taught:
        diagnosis = "This topic has not been taught to the class yet."
    else:
        diagnosis = "No known difficulty on this topic."

    pack = {
        "mode": "with_h2",
        "graph_version": g.version_for(subject),
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
            "taught_so_far": [f"Wk{w} {g.label(c)}" for c, w in sorted((taught if subject == "maths" else lessons).items(), key=lambda x: x[1])],
            "not_yet_taught": [f"Wk{w} {g.label(c)}" for c, w in sorted(planned.items(), key=lambda x: x[1])],
            "preferred_method": pref["method"], "preferred_representation": pref["representation"],
        },
        "materials": mats,
        "how_to_support": B.briefing(p["id"]),
        "guidance": {
            "diagnosis": diagnosis,
            "teach_with": {"method": pref["method"], "representation": pref["representation"]},
            "avoid_methods": avoid,
            "check_prerequisites_first": gaps,
            "next_step": None,  # filled below
            "scope_rule": ("Stay within 'taught_so_far'. If the pupil asks for something in 'not_yet_taught', say it is coming later and only give a clearly flagged preview."),
        },
    }
    if subject != "maths":
        pack = {"mode": pack.pop("mode"), "subject": S.SUBJECTS[subject]["label"], **pack}
    if "sections_studied" in cov:
        # Literature: scope is about how far through the text the class has read, not only which topics
        pack["class"]["text_studied_so_far"] = [x["label"] for x in cov["sections_studied"]]
        pack["class"]["text_coming_up"] = [f"{x['label']}" + (f" (week {x['week']})" if x.get("week") else "") for x in cov["sections_coming"]]
        pack["request"]["mentions"] = {"parts_of_text": [x["label"] for x in refs.get("sections", [])],
                                       "quotations": [x["text"] for x in refs.get("quotations", [])]}
        pack["learner"]["related_topics"] = [{"topic": g.label(r), "status": ls(r)["status"]} for r in g.related.get(focus, [])][:6]
        quotes = [g.quote(q) for q in g.evidenced_by.get(focus, [])]
        pack["key_quotations"] = [{"text": q["text"], "speaker": q["speaker"], "act": q["act"]}
                                  for q in quotes if q["section"] and q["section"]["id"] in studied_ids][:4]
        pack["guidance"]["scope_rule"] = (
            f"The class has studied {', '.join(pack['class']['text_studied_so_far']) or 'none of the play yet'}"
            + (f"; still to come: {', '.join(pack['class']['text_coming_up'])}" if pack['class']['text_coming_up'] else "")
            + ". Writing skills are practised throughout the course, and any character, theme or context topic can be discussed using the parts studied."
            + " Pupils may have read ahead: you can discuss later parts of the play, but say they are coming up in class and link back to what has been studied. Prefer quotations from the parts studied.")
    pack["guidance"]["next_step"] = next_step(g, sm, focus, focus_secure, taught, planned, subject, edge, mentioned_coming)
    pack["approx_tokens"] = len(json.dumps(pack)) // 4
    return pack
