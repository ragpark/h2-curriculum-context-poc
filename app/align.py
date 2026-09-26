"""Alignment service: tag any text (a teaching-material unit, an activity title, a pupil message) against the graph.

Two modes, same output contract:
  • heuristic — deterministic keyword/phrase scoring against graph node vocabularies (no API key needed)
  • claude    — graph-constrained LLM: candidates are retrieved from the graph first, Claude ranks/selects,
                and every returned ID is validated against the graph before it is accepted.
"""
import json
import math
import os
import re
from collections import Counter

from . import graph as G
from . import llm
from .textvec import normalise, phrase_in, tokens

FACETS = ("concept", "method", "representation", "misconception")


def default_mode() -> str:
    m = os.environ.get("ALIGNER_MODE", "auto")
    if m == "auto":
        return "claude" if llm.available() else "heuristic"
    return m


# ---------------------------------------------------------------- heuristic
def _scores(text: str, node_type: str, subject: str = "maths") -> list[tuple[str, float, list[str]]]:
    g = G.get()
    nodes = g.of_type(node_type, subject=subject)
    tn = normalise(text)
    tt = set(tokens(text))
    df = Counter(normalise(k).strip() for n in nodes for k in set(n.get("keywords") or []))
    out = []
    for n in nodes:
        score, hits = 0.0, []
        for k in n.get("keywords") or []:
            if phrase_in(k, tn, tt):
                kn = normalise(k).strip()
                w = (2.0 if (" " in kn or not kn.isalpha()) else 1.0) / df[kn]
                score += w; hits.append(k)
        if normalise(n["label"]) in tn:
            score += 2.0; hits.append(n["label"])
        if score > 0:
            out.append((n["id"], score, hits))
    out.sort(key=lambda x: -x[1])
    return out


def _conf(score: float) -> float:
    return round(1 - math.exp(-score / 1.5), 2)


def heuristic(text: str, subject: str = "maths") -> dict:
    g = G.get()
    cs = _scores(text, "concept", subject)
    top = cs[0][1] if cs else 0
    concepts = [g.ref(i, confidence=_conf(s), evidence=h) for i, s, h in cs if _conf(s) >= 0.45 and s >= 0.6 * top][:3]
    ms = _scores(text, "method", subject)
    method = g.ref(ms[0][0], confidence=_conf(ms[0][1]), evidence=ms[0][2]) if ms and _conf(ms[0][1]) >= 0.45 else None
    rs = _scores(text, "representation", subject)
    rep = g.ref(rs[0][0], confidence=_conf(rs[0][1]), evidence=rs[0][2]) if rs and _conf(rs[0][1]) >= 0.45 else None
    mc = [g.ref(i, confidence=_conf(s), evidence=h) for i, s, h in _scores(text, "misconception", subject) if _conf(s) >= 0.5][:3]
    return {"concepts": concepts, "method": method, "representation": rep, "misconceptions": mc,
            "provenance": "heuristic-v1", "mode": "heuristic", "graph_version": g.version_for(subject)}


# ---------------------------------------------------------------- claude
PROMPT = """You are the alignment service for a curriculum knowledge graph. Tag the TEXT below using ONLY the IDs listed.

Rules:
- concepts: {concept_rule}
- method: the ONE teaching method the text explicitly uses, or null if none is evident.
- representation: {rep_rule}
- misconceptions: ONLY misconceptions the text explicitly names, warns against, or shows as erroneous working. Do NOT tag a misconception just because the topic is prone to it. Empty list is the common case.
- confidence: 0–1 for each tag.
Return ONLY JSON: {{"concepts":[{{"id":"...","confidence":0.9}}],"method":{{"id":"...","confidence":0.8}}|null,"representation":{{...}}|null,"misconceptions":[{{"id":"...","confidence":0.7}}],"rationale":"one sentence"}}

CANDIDATE CONCEPTS (retrieved from the graph):
{concepts}

METHODS:
{methods}

REPRESENTATIONS:
{reps}

MISCONCEPTIONS:
{mcs}

TEXT:
\"\"\"{text}\"\"\""""


RULES = {
    "maths": {
        "concept_rule": "the concept(s) the text is mainly teaching, practising or assessing — usually ONE, primary first, at most 3. Do NOT tag a prerequisite the text merely uses along the way (e.g. negative numbers inside an expanding example), a concept only mentioned as a future step, or a neighbouring concept that is not the point of the text.",
        "rep_rule": "the ONE visual/concrete representation the text explicitly uses or asks pupils to draw, or null. A word such as \"balance\" used as a metaphor is not a representation unless scales are drawn or pictured.",
    },
    "english": {
        "concept_rule": "the topic(s) the text is mainly about — usually ONE or TWO, primary first, at most 3: the knowledge topic (a character, theme, context or language feature of the play) and/or the writing skill the task practises. Do NOT tag a topic only mentioned in passing, and do NOT tag anything for texts other than Macbeth (other poems, plays or creative writing) — return an empty list for those.",
        "rep_rule": "the ONE visual organiser the text explicitly uses or asks pupils to produce (e.g. a timeline or a quotation web), or null.",
    },
}


def claude(text: str, subject: str = "maths") -> dict:
    g = G.get()
    # 1) candidate retrieval from the graph (heuristic scores first, then the rest of the strand)
    ranked = [i for i, _, _ in _scores(text, "concept", subject)]
    rest = [n["id"] for n in g.of_type("concept", subject=subject) if n["id"] not in ranked]
    cands = ranked + rest if len(ranked) + len(rest) <= 60 else (ranked + rest)[:30]  # small graph: show the model every concept
    fmt = lambda ids: "\n".join(f"- {i}: {g.label(i)}" for i in ids)
    prompt = PROMPT.format(
        **RULES[subject],
        concepts=fmt(cands),
        methods=fmt([n["id"] for n in g.of_type("method", subject=subject)]),
        reps=fmt([n["id"] for n in g.of_type("representation", subject=subject)]),
        mcs="\n".join(f"- {n['id']}: {n['label']} — {n.get('description') or ''}" for n in g.of_type("misconception", subject=subject)),
        text=text[:4000],
    )
    raw = llm.complete(prompt, max_tokens=700, system="You output strict JSON only.")
    data = _parse_json(raw)

    # 2) validate every ID against the graph (the ontology constrains the model)
    def ok(item, t, allowed=None):
        if not isinstance(item, dict):
            return None
        i = item.get("id")
        if i in g.nodes and g.nodes[i]["type"] == t and g.nodes[i]["status"] == "active" and g.subject_of(i) == subject and (allowed is None or i in allowed):
            return g.ref(i, confidence=round(float(item.get("confidence", 0.7)), 2))
        return None

    rejected = []
    concepts = []
    for c in data.get("concepts") or []:
        r = ok(c, "concept", set(cands))
        (concepts.append(r) if r else rejected.append(c))
    mcs = []
    for m in data.get("misconceptions") or []:
        r = ok(m, "misconception")
        (mcs.append(r) if r else rejected.append(m))
    return {"concepts": concepts[:3], "method": ok(data.get("method"), "method"),
            "representation": ok(data.get("representation"), "representation"),
            "misconceptions": mcs[:3], "rationale": data.get("rationale"), "rejected_ids": rejected,
            "candidates_considered": len(cands), "provenance": f"claude:{llm.model()}", "mode": "claude",
            "graph_version": g.version_for(subject)}


def _parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise ValueError("aligner returned no JSON")
    return json.loads(m.group(0))


def align(text: str, mode: str | None = None, subject: str = "maths") -> dict:
    mode = mode or default_mode()
    if mode == "claude":
        try:
            r = claude(text, subject)
        except Exception as e:  # fall back but surface the reason
            r = heuristic(text, subject)
            r["fallback_reason"] = f"Claude aligner unavailable: {type(e).__name__}: {str(e)[:160]}"
    else:
        r = heuristic(text, subject)
    r.update(text_refs(text, subject))
    return r


# ---------------------------------------------------------------- text references (deterministic)
_STOP = set("a an the is this i and to my of your me it be we us he she that which but so well with what now all not o in on for as".split())
_ACTS = re.compile(r"\bacts?\s+(\d)(?:\s*(?:[–\-]|and|&|to)\s*(\d))?", re.I)


def _plain(s: str) -> str:
    s = (s or "").lower().replace("’", "'").replace("‘", "'")
    s = re.sub(r"(?<=\w)-(?=\w)", "", s)          # to-morrow -> tomorrow, fiend-like -> fiendlike
    s = re.sub(r"[^a-z0-9' ]+", " ", s).replace("'", "")
    return " " + re.sub(r"\s+", " ", s).strip() + " "


def _quote_keys(text: str) -> list[str]:
    """Search keys for a quotation: the opening words of each line (at least 3 words and 12 letters), or a whole short line."""
    keys = []
    for line in text.split("/"):
        w = _plain(line).split()
        if not w:
            continue
        if len(w) < 3:
            if len(" ".join(w)) >= 12:
                keys.append(" ".join(w))
            continue
        k = 3
        while k < len(w) and (len(" ".join(w[:k])) < 12 or all(x in _STOP for x in w[:k])):
            k += 1
        keys.append(" ".join(w[:k]))
        # pupils often quote from the middle of a line ("unsex me here"): any 3-word run with 2+ content words
        for i in range(1, len(w) - 2):
            run = w[i:i + 3]
            if sum(x not in _STOP for x in run) >= 2 and len(" ".join(run)) >= 12:
                keys.append(" ".join(run))
    return keys


def text_refs(text: str, subject: str = "maths") -> dict:
    """Which parts of the text (acts) and which key quotations does this text refer to?
    Deterministic in both modes: act numbers, key events and quotation matches — no model judgement."""
    g = G.get()
    secs = g.sections(subject)
    if not secs:
        return {}
    plain = _plain(text)
    by_order = {(n.get("extra") or {}).get("order"): n["id"] for n in secs}
    found = {}
    for m in _ACTS.finditer(text or ""):
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        for o in range(min(a, b), max(a, b) + 1):
            if o in by_order:
                found.setdefault(by_order[o], f"'Act {o}'")
    for n in secs:
        for k in n.get("keywords") or []:
            if f" {_plain(k).strip()} " in plain:
                found.setdefault(n["id"], f"'{k}'")
    quotes = []
    for qn in g.of_type("quotation", subject=subject):
        if any(f" {k} " in plain for k in _quote_keys(qn["label"])):
            q = g.quote(qn["id"])
            quotes.append(q)
            if q["section"]:
                found.setdefault(q["section"]["id"], f"quotation from {q['act']}")
    order = {n["id"]: (n.get("extra") or {}).get("order", 0) for n in secs}
    return {"sections": [g.ref(s, evidence=why) for s, why in sorted(found.items(), key=lambda x: order[x[0]])],
            "quotations": quotes}


def flatten(result: dict) -> list[dict]:
    """Alignment result -> rows for the alignment table."""
    rows = [{"target": c["id"], "facet": "concept", "confidence": c["confidence"]} for c in result["concepts"]]
    for f in ("method", "representation"):
        if result.get(f):
            rows.append({"target": result[f]["id"], "facet": f, "confidence": result[f]["confidence"]})
    rows += [{"target": m["id"], "facet": "misconception", "confidence": m["confidence"]} for m in result["misconceptions"]]
    rows += [{"target": s["id"], "facet": "section", "confidence": 1.0} for s in result.get("sections", [])]
    rows += [{"target": q["id"], "facet": "quotation", "confidence": 1.0} for q in result.get("quotations", [])]
    return rows
