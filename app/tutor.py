"""Stub tutor + evaluation harness. Same tutor prompt for both arms; only the context differs."""
import json
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


# ------------------------------------------------------------------ demo state
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
