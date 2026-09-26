"""Evaluation harness.

1. Tagging accuracy on two sets:
   • tuning  — the 14 units the tagger was adjusted against (optimistic)
   • heldout — units written afterwards and never used to adjust anything (the honest number)
2. Hard tutor suite: 3 arms × 12 scenarios, each reply scored blind on five 0–3 criteria against
   HAND-WRITTEN ground truth (seed/tutor_suite.yaml), two independent judge passes averaged.
   Runs as a background job because it takes a few minutes.
"""
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from . import align as A
from . import content as C
from . import context as X
from . import db
from . import llm
from . import seed as S
from . import tutor as T

SEED = Path(__file__).resolve().parent.parent / "seed"


# ================================================================== tagging accuracy
def _units(which: str):
    if which in ("heldout", "heldout2"):
        d = yaml.safe_load((SEED / ("heldout_alignment.yaml" if which == "heldout" else "heldout_alignment_2.yaml")).read_text())
        return [(u["id"], u["title"], f"{u['title']}\n{u['text']}", u["gold"]) for u in d["units"]]
    mats = {m["id"]: m for m in db.q("select * from material")}
    out = []
    for uid, gold in S.gold().items():
        mid, idx = uid.split("#")
        head, body = C.split_units(mats[mid]["body"])[int(idx)]
        out.append((uid, head, f"{mats[mid]['title']}\n{head}\n{body}", gold))
    return out


def eval_alignment(mode: str | None = None, which: str = "tuning") -> dict:
    mode = mode or A.default_mode()
    units = _units(which)
    with ThreadPoolExecutor(6 if mode == "claude" else 1) as ex:
        results = list(ex.map(lambda u: A.align(u[2], mode), units))
    tot = {"concept": [0, 0, 0], "misconception": [0, 0, 0], "method": [0, 0], "representation": [0, 0], "offstrand": [0, 0]}
    rows = []
    for (uid, head, _, gold), res in zip(units, results):
        got_c = {c["id"] for c in res["concepts"]}
        got_m = {m["id"] for m in res["misconceptions"]}
        exp_c, ok_c = set(gold.get("concepts") or []), set(gold.get("acceptable_concepts") or [])
        exp_m = set(gold.get("misconceptions") or [])
        # precision: a tag is correct if expected OR acceptable; recall: against expected only
        tot["concept"][0] += len(got_c & (exp_c | ok_c)); tot["concept"][1] += len(got_c); tot["concept"][2] += len(exp_c)
        tot["misconception"][0] += len(got_m & exp_m); tot["misconception"][1] += len(got_m); tot["misconception"][2] += len(exp_m)
        gm = res["method"]["id"] if res["method"] else None
        gr = res["representation"]["id"] if res["representation"] else None
        tot["method"][0] += gm == gold.get("method"); tot["method"][1] += 1
        tot["representation"][0] += gr == gold.get("representation"); tot["representation"][1] += 1
        if not exp_c:
            tot["offstrand"][0] += not got_c; tot["offstrand"][1] += 1
        rows.append({"unit": uid, "heading": head,
                     "concepts": {"expected": sorted(exp_c), "acceptable": sorted(ok_c), "got": sorted(got_c)},
                     "misconceptions": {"expected": sorted(exp_m), "got": sorted(got_m)},
                     "method": {"expected": gold.get("method"), "got": gm},
                     "representation": {"expected": gold.get("representation"), "got": gr},
                     "rationale": res.get("rationale"), "fallback_reason": res.get("fallback_reason")})
    pr = lambda t: {"precision": round(t[0] / t[1], 2) if t[1] else None, "recall": round(t[0] / t[2], 2) if t[2] else None}
    summary = {"concepts": {"precision": round(tot["concept"][0] / tot["concept"][1], 2) if tot["concept"][1] else None,
                            "recall": round(sum(len(set(r["concepts"]["got"]) & set(r["concepts"]["expected"])) for r in rows) / max(1, tot["concept"][2]), 2)},
               "misconceptions": pr(tot["misconception"]),
               "method_accuracy": round(tot["method"][0] / tot["method"][1], 2),
               "representation_accuracy": round(tot["representation"][0] / tot["representation"][1], 2)}
    if tot["offstrand"][1]:
        summary["offstrand_correctly_untagged"] = f"{tot['offstrand'][0]}/{tot['offstrand'][1]}"
    return {"set": which, "mode": mode, "units": len(rows), "summary": summary, "rows": rows}


# ================================================================== hard tutor suite
ARMS = ("none", "raw", "h2")
ARM_LABELS = {"none": "No classroom context", "raw": "Raw classroom data", "h2": "Organised by the map"}
CRITERIA = ("diagnosis", "method", "scope", "next_step", "grounding")

RUBRIC = """Score the TUTOR REPLY on five criteria, each 0–3, using ONLY the ground truth below. Be strict: 3 is rare and must be earned.

diagnosis — does it address THIS pupil's actual need?
  0 addresses the wrong problem, or invents a problem the pupil does not have
  1 generic help that would suit any pupil
  2 relevant to the real need but only implicitly
  3 explicitly and accurately targets the real need (or correctly treats a one-off slip as a slip)
method — is it consistent with the class teacher's method?
  0 teaches mainly with a method the teacher avoids, or contradicts the teacher
  1 no discernible method, or mixes methods confusingly
  2 compatible with the teacher's method without naming or using it clearly
  3 clearly uses the teacher's own method (and representation where apt)
scope — does it respect what the class has and has not been taught?
  0 teaches not-yet-taught content as if it were known, or jumps ahead without comment
  1 drifts towards untaught content without flagging it
  2 stays within taught content
  3 stays within taught content AND handles any request for more explicitly (e.g. "that's coming next week")
next_step — is the step it proposes right for this pupil now?
  0 inappropriate (too far ahead, or skips a gap)
  1 generic ("try another one")
  2 sensible
  3 sensible and specifically sequenced to the pupil's position and gaps
grounding — are its claims about the pupil and class true?
  0 states something false about the pupil, the class or what the teacher did
  1 no claims about the class or pupil at all
  2 claims are consistent with the facts but vague
  3 accurately uses specific facts about the pupil or class (their error pattern, a real class resource or example)

GROUND TRUTH (written by the class teacher's team; the tutor did not necessarily have it)
Class {cls}: teacher {teacher}.
  Teacher's method: {method}
  Avoid: {avoid}
  Taught so far: {taught}
  NOT yet taught: {not_yet}
Pupil: {pupil}
  Actual need: {need}
  A good next step: {good_next}

PUPIL MESSAGE: {message}

TUTOR REPLY:
\"\"\"{reply}\"\"\"

Return ONLY JSON: {{"diagnosis":n,"method":n,"scope":n,"next_step":n,"grounding":n,"why":"one sentence citing the reply"}}"""

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


SUITES = {"dev": "tutor_suite.yaml", "heldout": "tutor_suite_heldout.yaml"}
SUITE_LABELS = {"dev": "Development scenarios (used to build the fix)", "heldout": "Fresh held-out scenarios (never used to build the fix)"}


def suite(name: str = "dev"):
    return yaml.safe_load((SEED / SUITES[name]).read_text())


def _context(arm: str, pupil: dict, message: str) -> dict:
    if arm == "none":
        return {"pupil": {"name": pupil["name"], "year": 10}}
    return X.assemble(pupil["id"], message=message, facets=(arm == "h2"))


def _judge(sc, cls, pupil, reply) -> dict:
    prompt = RUBRIC.format(cls=pupil["class"], teacher=cls["teacher"], method=cls["method"], avoid=cls["avoid"],
                           taught=cls["taught"], not_yet=cls["not_yet_taught"], pupil=pupil["name"],
                           need=sc["need"], good_next=sc["good_next"], message=sc["message"], reply=reply)
    out = A._parse_json(llm.complete(prompt, system="You are a strict, fair assessor of tutoring. Output JSON only.", max_tokens=300))
    return {c: max(0, min(3, int(out.get(c, 0)))) for c in CRITERIA} | {"why": out.get("why", "")}


def _run_one(sc, classes, pupils):
    pupil = pupils[sc["pupil"]]
    cls = classes[pupil["class"]]
    res = {"id": sc["id"], "category": sc["category"], "pupil": pupil["name"], "class": pupil["class"],
           "message": sc["message"], "need": sc["need"], "arms": {}}
    for arm in ARMS:
        try:
            ctx = _context(arm, pupil, sc["message"])
            reply = T.respond(ctx, sc["message"])
            judgements = [_judge(sc, cls, pupil, reply) for _ in range(2)]  # two independent passes
            scores = {c: round(sum(j[c] for j in judgements) / 2, 2) for c in CRITERIA}
            res["arms"][arm] = {"reply": reply, "scores": scores, "total": round(sum(scores.values()), 2),
                                "why": [j["why"] for j in judgements], "context_tokens": len(json.dumps(ctx, default=str)) // 4}
        except Exception as e:
            res["arms"][arm] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    return res


def _aggregate(results):
    ok = [r for r in results if all("scores" in r["arms"].get(a, {}) for a in ARMS)]
    agg = {a: {c: round(sum(r["arms"][a]["scores"][c] for r in ok) / max(1, len(ok)), 2) for c in CRITERIA} for a in ARMS}
    for a in ARMS:
        agg[a]["total"] = round(sum(agg[a][c] for c in CRITERIA), 2)
    cats = {}
    for r in ok:
        cats.setdefault(r["category"], []).append(r)
    by_cat = {c: {a: round(sum(r["arms"][a]["total"] for r in rs) / len(rs), 2) for a in ARMS} | {"n": len(rs)} for c, rs in cats.items()}
    wins = {"h2_vs_raw": {"win": 0, "tie": 0, "loss": 0}}
    for r in ok:
        d = r["arms"]["h2"]["total"] - r["arms"]["raw"]["total"]
        wins["h2_vs_raw"]["win" if d > 0.25 else "loss" if d < -0.25 else "tie"] += 1
    return {"by_arm": agg, "by_category": by_cat, "head_to_head": wins, "scored_scenarios": len(ok), "max_total": 3 * len(CRITERIA)}


def start_tutor_suite(name: str = "dev") -> dict:
    if not llm.available():
        raise ValueError("Set ANTHROPIC_API_KEY on the service to run the tutor suite.")
    with _lock:
        running = [j for j in _jobs.values() if j["status"] == "running"]
        if running:
            return running[0]
        jid = uuid.uuid4().hex[:8]
        if name not in SUITES:
            raise ValueError("unknown suite")
        job = {"id": jid, "status": "running", "done": 0, "total": 0, "started": time.time(), "model": llm.model(), "suite": name}
        _jobs[jid] = job

    def work():
        try:
            T.ensure_demo_state()
            s = suite(name)
            pupils = {p["id"]: p for p in db.q("select * from pupil")}
            job["total"] = len(s["scenarios"])
            results = []

            def one(sc):
                r = _run_one(sc, s["classes"], pupils)
                job["done"] += 1
                return r

            with ThreadPoolExecutor(4) as ex:
                results = list(ex.map(one, s["scenarios"]))
            out = {"suite": name, "suite_label": SUITE_LABELS[name], "model": llm.model(), "arms": ARM_LABELS, "criteria": CRITERIA, "summary": _aggregate(results),
                   "results": results, "finished": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                   "seconds": round(time.time() - job["started"])}
            db.meta_set(f"last_tutor_suite_{name}", json.dumps(out))
            job.update(status="done", result=out)
        except Exception as e:
            job.update(status="error", error=f"{type(e).__name__}: {str(e)[:300]}")

    threading.Thread(target=work, daemon=True).start()
    return job


def job_status(jid: str) -> dict:
    j = _jobs.get(jid)
    if not j:
        raise KeyError(jid)
    return {k: v for k, v in j.items() if k != "result"} | ({"result": j["result"]} if j.get("status") == "done" else {})


def last_tutor_suite(name: str = "dev") -> dict | None:
    raw = db.meta_get(f"last_tutor_suite_{name}")
    if raw:
        return with_before_fix(json.loads(raw), name)
    shipped = SEED / "results" / f"tutor_{name}_latest.json"  # results of the run made when this version was built
    out = json.loads(shipped.read_text()) if shipped.exists() else None
    return with_before_fix(out, name)


def with_before_fix(result: dict | None, name: str) -> dict | None:
    """Attach the pre-fix H2 scores on the same scenarios (recorded before the briefing fix) for comparison."""
    before = SEED / "results" / f"tutor_{name}_before_fix.json"
    if result and before.exists():
        b = json.loads(before.read_text())
        result = dict(result)
        result["before_fix"] = {"by_arm": b["summary"]["by_arm"], "by_category": b["summary"]["by_category"],
                                "per_scenario": {r["id"]: r["arms"]["h2"].get("total") for r in b["results"]}}
    return result
