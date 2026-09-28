"""Evaluation harness.

1. Tagging accuracy on two sets:
   • tuning  — the "practice" set: the 14 units the tagger was adjusted against (optimistic)
   • heldout — the "unseen" sets: written and locked before a change, never used to adjust anything (the honest number)
   (internal keys are kept for stored results and URLs; the UI says practice / unseen)
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
from . import tutors as TU

SEED = Path(__file__).resolve().parent.parent / "seed"


# ================================================================== tagging accuracy
ALIGN_SETS = {"heldout": "heldout_alignment.yaml", "heldout2": "heldout_alignment_2.yaml", "english": "heldout_alignment_english.yaml"}


def _units(which: str):
    if which in ALIGN_SETS:
        d = yaml.safe_load((SEED / ALIGN_SETS[which]).read_text())
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
    subject = "english" if which == "english" else "maths"
    with ThreadPoolExecutor(6 if mode == "claude" else 1) as ex:
        results = list(ex.map(lambda u: A.align(u[2], mode, subject), units))
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
    out = {"set": which, "subject": subject, "mode": mode, "units": len(rows), "summary": summary, "rows": rows,
           "finished": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
    db.meta_set(f"last_alignment_{which}_{mode}", json.dumps(out))
    return out


def last_alignment(which: str, mode: str) -> dict | None:
    raw = db.meta_get(f"last_alignment_{which}_{mode}")
    return json.loads(raw) if raw else None


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

{extra_rubric}GROUND TRUTH (written by the class teacher's team; the tutor did not necessarily have it)
Class {cls}: teacher {teacher}.
  Teacher's method: {method}
  Avoid: {avoid}
  Taught so far: {taught}
  NOT yet taught: {not_yet}
Pupil: {pupil}
  Actual need: {need}
  A good next step: {good_next}{extra_truth}

PUPIL MESSAGE: {message}

TUTOR REPLY:
\"\"\"{reply}\"\"\"

Return ONLY JSON: {json_line}"""

ADJUSTMENTS_RUBRIC = """adjustments — does it honour the pupil's AGREED ADJUSTMENTS (required by their teacher), and avoid inventing adjustments for a pupil who has none?
  0 breaks an agreed adjustment (e.g. gives the method before an attempt when told not to; consolidates when told to stretch), or invents restrictions for a pupil with none
  1 ignores the adjustments: a generic reply that happens not to break them
  2 follows most of the adjustments, with a lapse
  3 follows every agreed adjustment exactly, without mentioning them or describing the pupil (for a pupil with none: a normal reply)

"""

SUPPORT_RUBRIC = """support — does it respond to HOW this pupil learns (their learning behaviour), not just what they know?
  0 works against it (e.g. hands a hint straight away to a pupil who asks before trying; sets more of the same to a pupil stuck repeating a method)
  1 generic encouragement only
  2 somewhat adapted to the pupil's learning behaviour
  3 clearly and appropriately adapted to this pupil's learning behaviour, without labelling the pupil

"""

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


SUITES = {"dev": "tutor_suite.yaml", "heldout": "tutor_suite_heldout.yaml", "behaviour": "tutor_suite_behaviour.yaml",
          "english": "tutor_suite_english.yaml", "english_heldout": "tutor_suite_english_heldout.yaml",
          "adjustments": "tutor_suite_adjustments.yaml"}
SUITE_LABELS = {"dev": "Development scenarios (used to build the fix)", "heldout": "Unseen scenarios (written and locked before the fix)",
                "behaviour": "Learning behaviour: same answers, different behaviour",
                "english": "English (Macbeth): first scenarios (used to diagnose the scope fix)",
                "english_heldout": "English (Macbeth): unseen scenarios (written and locked before the scope fix)",
                "adjustments": "Agreed adjustments: same answers, with and without a teacher's required adjustments"}


def suite(name: str = "dev"):
    return yaml.safe_load((SEED / SUITES[name]).read_text())


def _context(arm: str, pupil: dict, message: str) -> dict:
    if arm == "none":
        return {"pupil": {"name": pupil["name"], "year": S.SUBJECTS[S.subject_of_pupil(pupil["id"])]["year"]}}
    return X.assemble(pupil["id"], message=message, facets=(arm == "h2"))


def _judge(sc, cls, pupil, reply, crit, pinfo=None) -> dict:
    extra_rubric = (SUPPORT_RUBRIC if "support" in crit else "") + (ADJUSTMENTS_RUBRIC if "adjustments" in crit else "")
    extra_truth = ""
    if pinfo and "behaviour" in pinfo:
        extra_truth += f"\n  Learning behaviour: {pinfo['behaviour']}\n  Good support: {pinfo['good_support']}"
    if pinfo and "adjustments" in pinfo:
        extra_truth += f"\n  Agreed adjustments: {pinfo['adjustments']}\n  What honouring them looks like here: {pinfo['good_adjustments']}"
    json_line = "{" + ",".join(f'"{c}":n' for c in crit) + ',"why":"one sentence citing the reply"}'
    prompt = RUBRIC.format(cls=pupil["class"], teacher=cls["teacher"], method=cls["method"], avoid=cls["avoid"],
                           taught=cls["taught"], not_yet=cls["not_yet_taught"], pupil=pupil["name"],
                           need=sc["need"], good_next=sc["good_next"], message=sc["message"], reply=reply,
                           extra_rubric=extra_rubric, extra_truth=extra_truth, json_line=json_line)
    out = A._parse_json(llm.complete(prompt, system="You are a strict, fair assessor of tutoring. Output JSON only.", max_tokens=300))
    return {c: max(0, min(3, int(out.get(c, 0)))) for c in crit} | {"why": out.get("why", "")}


def _run_one(sc, classes, pupils, crit=CRITERIA, pinfo=None, tutor=TU.BUILTIN, replies=None):
    """One scenario, three arms. `replies` (offline tutors): {arm: reply} already produced elsewhere."""
    pupil = pupils[sc["pupil"]]
    cls = classes[pupil["class"]]
    res = {"id": sc["id"], "category": sc["category"], "pupil": pupil["name"], "class": pupil["class"],
           "message": sc["message"], "need": sc["need"], "arms": {}}
    for arm in ARMS:
        try:
            ctx = _context(arm, pupil, sc["message"])
            if replies is not None:
                reply = (replies.get(arm) or "").strip()
                if not reply:
                    raise ValueError("no reply supplied for this arm")
            else:
                reply = TU.respond(tutor, ctx, sc["message"], S.subject_of_pupil(pupil["id"]), arm=arm, pupil_id=pupil["id"],
                                   turn_id=f"{sc['id']}:{arm}")
            judgements = [_judge(sc, cls, pupil, reply, crit, (pinfo or {}).get(sc["pupil"])) for _ in range(2)]  # two independent passes
            scores = {c: round(sum(j[c] for j in judgements) / 2, 2) for c in crit}
            res["arms"][arm] = {"reply": reply, "scores": scores, "total": round(sum(scores.values()), 2),
                                "why": [j["why"] for j in judgements], "context_tokens": len(json.dumps(ctx, default=str)) // 4}
        except Exception as e:
            res["arms"][arm] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    return res


def _aggregate(results, crit=CRITERIA):
    ok = [r for r in results if all("scores" in r["arms"].get(a, {}) for a in ARMS)]
    agg = {a: {c: round(sum(r["arms"][a]["scores"][c] for r in ok) / max(1, len(ok)), 2) for c in crit} for a in ARMS}
    for a in ARMS:
        agg[a]["total"] = round(sum(agg[a][c] for c in crit), 2)
    cats = {}
    for r in ok:
        cats.setdefault(r["category"], []).append(r)
    by_cat = {c: {a: round(sum(r["arms"][a]["total"] for r in rs) / len(rs), 2) for a in ARMS} | {"n": len(rs)} for c, rs in cats.items()}
    wins = {"h2_vs_raw": {"win": 0, "tie": 0, "loss": 0}}
    for r in ok:
        d = r["arms"]["h2"]["total"] - r["arms"]["raw"]["total"]
        wins["h2_vs_raw"]["win" if d > 0.25 else "loss" if d < -0.25 else "tie"] += 1
    return {"by_arm": agg, "by_category": by_cat, "head_to_head": wins, "scored_scenarios": len(ok), "max_total": 3 * len(crit)}


def _result_key(name: str, tutor_id: str) -> str:
    return f"last_tutor_suite_{name}" if tutor_id == "builtin" else f"last_tutor_suite_{name}:{tutor_id}"


def start_tutor_suite(name: str = "dev", tutor_id: str = "builtin", replies: dict | None = None) -> dict:
    """Run a suite against a tutor. For an offline tutor, `replies` is {scenario_id: {arm: reply}} from an upload;
    the judge still needs the model, so the key is required in every case."""
    tutor = TU.get(tutor_id)
    if tutor["kind"] == "offline" and replies is None:
        raise ValueError("This tutor is offline: export the suite, run it, then upload the replies file.")
    if not llm.available():
        raise ValueError("Set ANTHROPIC_API_KEY on the service to run the tutor suite.")
    with _lock:
        running = [j for j in _jobs.values() if j["status"] == "running"]
        if running:
            return running[0]
        jid = uuid.uuid4().hex[:8]
        if name not in SUITES:
            raise ValueError("unknown suite")
        job = {"id": jid, "status": "running", "done": 0, "total": 0, "started": time.time(), "model": llm.model(),
               "suite": name, "tutor": tutor_id}
        _jobs[jid] = job

    def work():
        try:
            T.ensure_demo_state()
            s = suite(name)
            pupils = {p["id"]: p for p in db.q("select * from pupil")}
            job["total"] = len(s["scenarios"])
            results = []

            crit = CRITERIA + tuple(s.get("extra_criteria", []))

            def one(sc):
                r = _run_one(sc, s["classes"], pupils, crit, s.get("pupils"), tutor,
                             None if replies is None else replies.get(sc["id"], {}))
                job["done"] += 1
                return r

            with ThreadPoolExecutor(4) as ex:
                results = list(ex.map(one, s["scenarios"]))
            out = {"suite": name, "suite_label": SUITE_LABELS[name], "model": llm.model(), "arms": ARM_LABELS, "criteria": crit,
                   "tutor": TU.public(tutor) if tutor["kind"] != "builtin" else tutor, "summary": _aggregate(results, crit),
                   "results": results, "finished": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                   "seconds": round(time.time() - job["started"])}
            db.meta_set(_result_key(name, tutor_id), json.dumps(out))
            job.update(status="done", result=out)
        except Exception as e:
            job.update(status="error", error=f"{type(e).__name__}: {str(e)[:300]}")

    threading.Thread(target=work, daemon=True).start()
    return job


# ------------------------------------------------------------------ offline tutors: export turns, import replies
def export_suite(name: str) -> dict:
    """Every scenario × arm with the exact context the built-in tutor would receive. A tutor run elsewhere answers
    each turn and returns {"suite": name, "replies": [{"turn_id": "...", "reply": "..."}]}."""
    if name not in SUITES:
        raise ValueError("unknown suite")
    T.ensure_demo_state()
    s = suite(name)
    pupils = {p["id"]: p for p in db.q("select * from pupil")}
    turns = []
    for sc in s["scenarios"]:
        pupil = pupils[sc["pupil"]]
        subject = S.subject_of_pupil(pupil["id"])
        for arm in ARMS:
            turns.append({"turn_id": f"{sc['id']}:{arm}", "scenario_id": sc["id"], "arm": arm, "arm_label": ARM_LABELS[arm],
                          "subject": subject, "pupil": pupil["id"], "message": sc["message"],
                          "context": _context(arm, pupil, sc["message"])})
    return {"suite": name, "suite_label": SUITE_LABELS[name], "arms": ARM_LABELS, "graph_versions": {k: G_version(k) for k in S.SUBJECTS},
            "instructions": ("Answer every turn as your tutor would, using as much or as little of `context` as you like. "
                             "Return {\"suite\": ..., \"replies\": [{\"turn_id\": ..., \"reply\": ...}]} and upload it. "
                             "The three arms for one scenario are the same pupil message with different context; treat them independently."),
            "turns": turns}


def G_version(subject):
    from . import graph as G
    return G.get().version_for(subject)


def parse_replies(name: str, body: dict) -> dict:
    """Validate an uploaded replies file -> {scenario_id: {arm: reply}}. Missing turns are reported, not silently skipped."""
    if not isinstance(body, dict) or not isinstance(body.get("replies"), list):
        raise ValueError('upload must be JSON with a "replies" list')
    if body.get("suite") and body["suite"] != name:
        raise ValueError(f"file is for suite '{body['suite']}', not '{name}'")
    s = suite(name)
    expected = {f"{sc['id']}:{arm}" for sc in s["scenarios"] for arm in ARMS}
    out: dict = {}
    seen = set()
    for r in body["replies"]:
        tid = str(r.get("turn_id", ""))
        if tid not in expected:
            raise ValueError(f"unknown turn_id '{tid[:60]}'")
        sid, arm = tid.rsplit(":", 1)
        out.setdefault(sid, {})[arm] = str(r.get("reply", ""))[:TU.MAX_REPLY]
        seen.add(tid)
    missing = sorted(expected - seen)
    if missing:
        raise ValueError(f"{len(missing)} of {len(expected)} turns have no reply, e.g. {missing[0]}")
    return out


def job_status(jid: str) -> dict:
    j = _jobs.get(jid)
    if not j:
        raise KeyError(jid)
    return {k: v for k, v in j.items() if k != "result"} | ({"result": j["result"]} if j.get("status") == "done" else {})


def last_tutor_suite(name: str = "dev", tutor_id: str = "builtin") -> dict | None:
    raw = db.meta_get(_result_key(name, tutor_id))
    if raw:
        return with_before_fix(json.loads(raw), name)
    if tutor_id != "builtin":
        return None
    shipped = SEED / "results" / f"tutor_{name}_latest.json"  # results of the run made when this version was built
    out = json.loads(shipped.read_text()) if shipped.exists() else None
    return with_before_fix(out, name)


def compare_tutors(name: str) -> dict:
    """Latest result per tutor on one suite, side by side (by-arm totals and head-to-head)."""
    rows = []
    for t in TU.list_tutors():
        r = last_tutor_suite(name, t["id"])
        if r and r.get("summary"):
            sm = r["summary"]
            rows.append({"tutor": t["id"], "label": t["label"], "kind": t["kind"], "finished": r.get("finished"),
                         "by_arm": {a: sm["by_arm"][a]["total"] for a in ARMS}, "head_to_head": sm["head_to_head"]["h2_vs_raw"],
                         "max_total": sm["max_total"], "scored": sm["scored_scenarios"]})
    return {"suite": name, "suite_label": SUITE_LABELS.get(name), "tutors": rows}


def with_before_fix(result: dict | None, name: str) -> dict | None:
    """Attach the pre-fix H2 scores on the same scenarios (recorded before the briefing fix) for comparison."""
    before = SEED / "results" / f"tutor_{name}_before_fix.json"
    if result and before.exists():
        b = json.loads(before.read_text())
        result = dict(result)
        result["before_fix"] = {"by_arm": b["summary"]["by_arm"], "by_category": b["summary"]["by_category"],
                                "per_scenario": {r["id"]: r["arms"]["h2"].get("total") for r in b["results"]}}
    return result


# ================================================================== start-up runs (for a service whose key can't be read elsewhere)
def run_on_start(spec: str) -> None:
    """RUN_EVALS_ON_START="align:english:claude,align:english:heuristic,tutor:english" runs each once per distinct spec,
    in the background, and prints a one-line JSON summary of each result to the service log."""
    import sys
    if not spec or db.meta_get("startup_evals_done") == spec:
        return

    def work():
        import psycopg
        try:
            # Railway overlaps old and new containers during a deploy: only one may do start-up work at a time.
            # The lock is tied to this connection, so it is released if this container is shut down.
            lock = psycopg.connect(db.DATABASE_URL, autocommit=True)
            lock.execute("select pg_advisory_lock(424242)")
            if db.meta_get("startup_evals_done") == spec:
                return
            T.ensure_demo_state()
            for part in [x.strip() for x in spec.split(",") if x.strip()]:
                kind, *args = part.split(":")
                if kind == "align":
                    r = eval_alignment(args[1] if len(args) > 1 else None, args[0])
                    print("EVAL_RESULT " + json.dumps({"align": part, "summary": r["summary"],
                          "misses": [x for x in r["rows"] if set(x["concepts"]["got"]) - set(x["concepts"]["expected"]) - set(x["concepts"]["acceptable"])
                                     or set(x["concepts"]["expected"]) - set(x["concepts"]["got"]) or x["misconceptions"]["got"] != x["misconceptions"]["expected"]
                                     or x["method"]["got"] != x["method"]["expected"] or x["representation"]["got"] != x["representation"]["expected"]]}), flush=True, file=sys.stdout)
                elif kind == "tutor":
                    job = start_tutor_suite(args[0])
                    while _jobs[job["id"]]["status"] == "running":
                        time.sleep(5)
                    j = _jobs[job["id"]]
                    if j["status"] == "done":
                        res = j["result"]
                        print("EVAL_RESULT " + json.dumps({"tutor": args[0], "summary": res["summary"]}), flush=True)
                        for r in res["results"]:
                            print("EVAL_ROW " + json.dumps({"id": r["id"], "cat": r["category"],
                                  **{a: (r["arms"][a].get("scores"), r["arms"][a].get("total")) for a in ARMS},
                                  "h2_why": r["arms"]["h2"].get("why", [""])[0], "raw_why": r["arms"]["raw"].get("why", [""])[0]}), flush=True)
                    else:
                        print("EVAL_ERROR " + json.dumps(j.get("error")), flush=True)
            db.meta_set("startup_evals_done", spec)
        except Exception as e:
            print(f"EVAL_ERROR {type(e).__name__}: {e}", flush=True)

    threading.Thread(target=work, daemon=True).start()
