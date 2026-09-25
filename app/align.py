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
def _scores(text: str, node_type: str) -> list[tuple[str, float, list[str]]]:
    g = G.get()
    nodes = g.of_type(node_type)
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


def heuristic(text: str) -> dict:
    g = G.get()
    cs = _scores(text, "concept")
    top = cs[0][1] if cs else 0
    concepts = [g.ref(i, confidence=_conf(s), evidence=h) for i, s, h in cs if _conf(s) >= 0.45 and s >= 0.6 * top][:3]
    ms = _scores(text, "method")
    method = g.ref(ms[0][0], confidence=_conf(ms[0][1]), evidence=ms[0][2]) if ms and _conf(ms[0][1]) >= 0.45 else None
    rs = _scores(text, "representation")
    rep = g.ref(rs[0][0], confidence=_conf(rs[0][1]), evidence=rs[0][2]) if rs and _conf(rs[0][1]) >= 0.45 else None
    mc = [g.ref(i, confidence=_conf(s), evidence=h) for i, s, h in _scores(text, "misconception") if _conf(s) >= 0.5][:3]
    return {"concepts": concepts, "method": method, "representation": rep, "misconceptions": mc,
            "provenance": "heuristic-v1", "mode": "heuristic", "graph_version": g.version}


# ---------------------------------------------------------------- claude
PROMPT = """You are the alignment service for a curriculum knowledge graph. Tag the TEXT below using ONLY the IDs listed.

Rules:
- concepts: 1–3 concept IDs the text actually teaches, practises or assesses (primary first). Do not tag concepts merely mentioned as a future step.
- method: the ONE teaching method the text uses, or null if none is evident.
- representation: the ONE visual/concrete representation used, or null.
- misconceptions: misconception IDs the text explicitly addresses, warns about, or reveals. Empty list if none.
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


def claude(text: str) -> dict:
    g = G.get()
    # 1) candidate retrieval from the graph (heuristic scores first, then the rest of the strand)
    ranked = [i for i, _, _ in _scores(text, "concept")]
    rest = [n["id"] for n in g.of_type("concept") if n["id"] not in ranked]
    cands = (ranked + rest)[:14]
    fmt = lambda ids: "\n".join(f"- {i}: {g.label(i)}" for i in ids)
    prompt = PROMPT.format(
        concepts=fmt(cands),
        methods=fmt([n["id"] for n in g.of_type("method")]),
        reps=fmt([n["id"] for n in g.of_type("representation")]),
        mcs="\n".join(f"- {n['id']}: {n['label']} — {n.get('description') or ''}" for n in g.of_type("misconception")),
        text=text[:4000],
    )
    raw = llm.complete(prompt, max_tokens=700, system="You output strict JSON only.")
    data = _parse_json(raw)

    # 2) validate every ID against the graph (the ontology constrains the model)
    def ok(item, t, allowed=None):
        if not isinstance(item, dict):
            return None
        i = item.get("id")
        if i in g.nodes and g.nodes[i]["type"] == t and g.nodes[i]["status"] == "active" and (allowed is None or i in allowed):
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
            "graph_version": g.version}


def _parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise ValueError("aligner returned no JSON")
    return json.loads(m.group(0))


def align(text: str, mode: str | None = None) -> dict:
    mode = mode or default_mode()
    if mode == "claude":
        try:
            return claude(text)
        except Exception as e:  # fall back but surface the reason
            r = heuristic(text)
            r["fallback_reason"] = f"Claude aligner unavailable: {type(e).__name__}: {str(e)[:160]}"
            return r
    return heuristic(text)


def flatten(result: dict) -> list[dict]:
    """Alignment result -> rows for the alignment table."""
    rows = [{"target": c["id"], "facet": "concept", "confidence": c["confidence"]} for c in result["concepts"]]
    for f in ("method", "representation"):
        if result.get(f):
            rows.append({"target": result[f]["id"], "facet": f, "confidence": result[f]["confidence"]})
    rows += [{"target": m["id"], "facet": "misconception", "confidence": m["confidence"]} for m in result["misconceptions"]]
    return rows
