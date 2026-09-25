"""Stub tutor + evaluation harness. Same tutor prompt for both arms; only the context differs."""
import json
import random
from concurrent.futures import ThreadPoolExecutor

from . import align as A
from . import content as C
from . import context as X
from . import db
from . import graph as G
from . import learner as L
from . import llm
from . import seed as S

TUTOR_SYSTEM = """You are an AI maths tutor working one-to-one with a Year 10 pupil in an English secondary school.
You are given a CONTEXT object assembled by the school's platform. Use it to personalise your reply.
Reply in under 140 words, British English, one small step at a time, and end with a single question for the pupil.
Do not mention the context object, IDs or the platform."""


def respond(ctx: dict, message: str) -> str:
    prompt = f"CONTEXT:\n{json.dumps(ctx, indent=1, default=str)}\n\nPUPIL: {message}"
    return llm.complete(prompt, system=TUTOR_SYSTEM, max_tokens=400, temperature=0.3)


def compare(learner: str, message: str, concept: str | None = None, mode: str | None = None) -> dict:
    with_h2 = X.assemble(learner, concept=concept, message=message, facets=True, mode=mode)
    without = X.assemble(learner, concept=concept, message=message, facets=False, mode=mode)
    out = {"with_h2": {"context": with_h2}, "without_h2": {"context": without}, "llm": llm.available(), "model": llm.model() if llm.available() else None}
    if llm.available():
        with ThreadPoolExecutor(2) as ex:
            fa = ex.submit(respond, with_h2, message)
            fb = ex.submit(respond, without, message)
            for key, fut in (("with_h2", fa), ("without_h2", fb)):
                try:
                    out[key]["response"] = fut.result()
                except Exception as e:
                    out[key]["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return out


# ------------------------------------------------------------------ evaluation
def eval_alignment(mode: str | None = None) -> dict:
    gold = S.gold()
    mats = {m["id"]: m for m in db.q("select * from material")}
    rows, tot = [], {"concept": [0, 0, 0], "misconception": [0, 0, 0], "method": [0, 0], "representation": [0, 0]}
    for uid, exp in gold.items():
        mid, idx = uid.split("#")
        units = C.split_units(mats[mid]["body"])
        head, body = units[int(idx)]
        res = A.align(f"{mats[mid]['title']}\n{head}\n{body}", mode)
        got_c = {c["id"] for c in res["concepts"]}
        got_m = {m["id"] for m in res["misconceptions"]}
        exp_c, exp_m = set(exp["concepts"]), set(exp["misconceptions"])
        for key, got, want in (("concept", got_c, exp_c), ("misconception", got_m, exp_m)):
            tot[key][0] += len(got & want); tot[key][1] += len(got); tot[key][2] += len(want)
        gm = res["method"]["id"] if res["method"] else None
        gr = res["representation"]["id"] if res["representation"] else None
        tot["method"][0] += gm == exp["method"]; tot["method"][1] += 1
        tot["representation"][0] += gr == exp["representation"]; tot["representation"][1] += 1
        rows.append({"unit": uid, "heading": head,
                     "concepts": {"expected": sorted(exp_c), "got": sorted(got_c)},
                     "misconceptions": {"expected": sorted(exp_m), "got": sorted(got_m)},
                     "method": {"expected": exp["method"], "got": gm},
                     "representation": {"expected": exp["representation"], "got": gr},
                     "fallback_reason": res.get("fallback_reason")})
    pr = lambda t: {"precision": round(t[0] / t[1], 2) if t[1] else None, "recall": round(t[0] / t[2], 2) if t[2] else None}
    return {"mode": rows and (mode or A.default_mode()), "units": len(rows),
            "summary": {"concepts": pr(tot["concept"]), "misconceptions": pr(tot["misconception"]),
                        "method_accuracy": round(tot["method"][0] / tot["method"][1], 2),
                        "representation_accuracy": round(tot["representation"][0] / tot["representation"][1], 2)},
            "rows": rows}


TUTOR_SCENARIOS = [
    {"learner": "pupil:amara", "message": "I got 5x + 3 = 2x + 12 wrong again, I got x = 5. Can you help?"},
    {"learner": "pupil:amara", "message": "How do I solve 7x − 2 = 3x + 10?"},
    {"learner": "pupil:ben", "message": "Can you help me with 3(x + 2) = 21?"},
    {"learner": "pupil:ben", "message": "I don't get how to expand −2(x − 3)."},
    {"learner": "pupil:amara", "message": "What should I practise next?"},
]

JUDGE = """You are assessing two AI-tutor replies to the same pupil message. Score each reply 0–2 on each criterion,
using the GROUND TRUTH about this pupil and class (which the tutors may or may not have had).

Criteria:
1. targets_misconception — addresses the pupil's actual error pattern ({mc}).
2. method_consistency — teaches with the class teacher's method ({method}); penalise methods the teacher avoids ({avoid}).
3. within_scope — stays within what the class has been taught; does not jump to untaught content ({untaught}).
4. next_step — the step it proposes is sensible given the pupil's prerequisites and progression.

PUPIL MESSAGE: {msg}

REPLY A:
{a}

REPLY B:
{b}

Return ONLY JSON: {{"A":{{"targets_misconception":0,"method_consistency":0,"within_scope":0,"next_step":0}},"B":{{...}},"notes":"one sentence"}}"""


def eval_tutor() -> dict:
    if not llm.available():
        return {"error": "Set ANTHROPIC_API_KEY on the service to run the tutor A/B evaluation."}
    ensure_demo_state()
    g = G.get()
    results = []

    def run(sc):
        r = compare(sc["learner"], sc["message"])
        ctx = r["with_h2"]["context"]
        gd = ctx.get("guidance", {})
        cov = C.coverage(ctx["class"]["id"])
        untaught = ", ".join(c["label"] for c in cov["planned"]) or "none"
        pair = [("with_h2", r["with_h2"].get("response", "")), ("without_h2", r["without_h2"].get("response", ""))]
        random.shuffle(pair)
        prompt = JUDGE.format(
            mc=", ".join(m["label"] for m in ctx["learner"]["active_misconceptions"]) or "none detected",
            method=(gd.get("reteach_with", {}).get("method") or {}).get("label", "unknown"),
            avoid=", ".join(m["label"] for m in gd.get("avoid_methods", [])) or "none",
            untaught=untaught, msg=sc["message"], a=pair[0][1], b=pair[1][1])
        raw = llm.complete(prompt, system="You output strict JSON only.", max_tokens=400, temperature=0)
        j = A._parse_json(raw)
        scores = {pair[0][0]: j.get("A", {}), pair[1][0]: j.get("B", {})}
        return {**sc, "focus": ctx["focus"]["label"], "scores": scores, "notes": j.get("notes"),
                "responses": {k: r[k].get("response") for k in ("with_h2", "without_h2")}}

    with ThreadPoolExecutor(3) as ex:
        for res in ex.map(lambda s: _safe(run, s), TUTOR_SCENARIOS):
            results.append(res)
    crit = ["targets_misconception", "method_consistency", "within_scope", "next_step"]
    agg = {arm: {c: round(sum(r["scores"][arm].get(c, 0) for r in results if "scores" in r) / max(1, sum("scores" in r for r in results)), 2) for c in crit}
           for arm in ("with_h2", "without_h2")}
    for arm in agg:
        agg[arm]["total_of_8"] = round(sum(agg[arm][c] for c in crit), 2)
    return {"model": llm.model(), "summary": agg, "results": results, "criteria": crit}


def _safe(fn, sc):
    try:
        return fn(sc)
    except Exception as e:
        return {**sc, "error": f"{type(e).__name__}: {str(e)[:200]}"}


def ensure_demo_state():
    """Make sure materials are ingested and scenario evidence exists, so the demo works from any tab."""
    if db.q1("select count(*) n from content_unit")["n"] == 0:
        C.ingest_all()
    for sc in S.scenarios():
        if db.q1("select count(*) n from evidence where learner=%s", (sc["pupil"],))["n"] == 0:
            run_scenario(sc["id"])


def run_scenario(sid: str, mode: str | None = None) -> dict:
    sc = next(s for s in S.scenarios() if s["id"] == sid)
    L.clear(sc["pupil"])
    out = []
    for e in sc["events"]:
        out.append(L.record(sc["pupil"], source=e["source"], item=e.get("item"), response=e.get("response"),
                            activity=e.get("activity"), score=e.get("score"), mode=mode))
    return {"scenario": sc, "recorded": out, "learner": L.view(sc["pupil"])}
