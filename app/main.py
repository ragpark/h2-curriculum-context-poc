import contextlib
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import align as A
from . import content as C
from . import context as X
from . import db
from . import evals as E
from . import graph as G
from . import learner as L
from . import llm
from . import seed as S
from . import tutor as T
from . import versioning as V
from .mcp_server import mcp

STATIC = Path(__file__).resolve().parent.parent / "static"


@contextlib.asynccontextmanager
async def lifespan(app):
    S.ensure()
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="H2 Curriculum Context Layer — POC", version="0.1", lifespan=lifespan,
              description="Shared curriculum context layer (H2) and how it improves learner data (H1) and teacher materials (H3).")
app.mount("/mcp", mcp.streamable_http_app())
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(KeyError)
async def _nf(_: Request, e: KeyError):
    return JSONResponse({"detail": f"Not found: {e}"}, status_code=404)


@app.exception_handler(ValueError)
async def _bad(_: Request, e: ValueError):
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    db.q1("select 1 as ok")
    return {"ok": True}


# ---------------------------------------------------------------- status
@app.get("/api/status")
def status():
    counts = {t: db.q1(f"select count(*) n from {t}")["n"] for t in
              ("node", "edge", "crosswalk", "content_unit", "alignment", "evidence", "learner_state", "learner_misconception")}
    counts["concepts"] = db.q1("select count(*) n from node where type='concept' and status='active'")["n"]
    return {"graph_version": G.get().version, "aligner_mode": A.default_mode(), "llm_available": llm.available(),
            "model": llm.model() if llm.available() else None, "counts": counts,
            "mcp_path": "/mcp/", "public_url": os.environ.get("RAILWAY_PUBLIC_DOMAIN")}


# ---------------------------------------------------------------- graph (H2)
@app.get("/api/graph")
def graph():
    return G.get().to_json()


@app.get("/api/graph/node")
def node(id: str):
    d = G.get().node_detail(id)
    if not d:
        raise HTTPException(404, "unknown node")
    d["usage"] = {
        "alignments": db.q1("select count(*) n from alignment where target=%s", (id,))["n"],
        "evidence": db.q1("select count(*) n from evidence where concepts ? %s", (id,))["n"],
    }
    return d


class AlignIn(BaseModel):
    text: str
    mode: str | None = None


@app.post("/api/align")
def align(body: AlignIn):
    return A.align(body.text, body.mode)


# ---------------------------------------------------------------- H3 materials
@app.get("/api/classes")
def classes():
    return db.q("select * from class order by id")


@app.get("/api/materials")
def materials(class_id: str | None = None):
    return C.materials(class_id)


class IngestIn(BaseModel):
    material_id: str | None = None
    class_id: str | None = None
    mode: str | None = None


@app.post("/api/materials/ingest")
def ingest(body: IngestIn):
    if body.material_id:
        return [C.ingest(body.material_id, body.mode)]
    return C.ingest_all(body.class_id, body.mode)


class MaterialIn(BaseModel):
    class_id: str
    week: int
    title: str
    body: str
    mode: str | None = None


@app.post("/api/materials")
def add_material(m: MaterialIn):
    mid = "mat:custom-" + str(db.q1("select count(*)+1 n from material where id like 'mat:custom-%%'")["n"])
    db.ex("insert into material(id,class,week,title,body) values(%s,%s,%s,%s,%s)", (mid, m.class_id, m.week, m.title, m.body))
    return C.ingest(mid, m.mode)


@app.get("/api/classes/{class_id}/coverage")
def coverage(class_id: str):
    return C.coverage(class_id)


# ---------------------------------------------------------------- H1 evidence
@app.get("/api/items")
def items():
    return db.q("select * from item order by id")


@app.get("/api/pupils")
def pupils():
    return db.q("select * from pupil order by id")


@app.get("/api/scenarios")
def scenarios():
    return S.scenarios()


class EvidenceIn(BaseModel):
    learner: str
    source: str = "Manual entry"
    item: str | None = None
    response: str | None = None
    activity: str | None = None
    score: float | None = None
    mode: str | None = None


@app.post("/api/evidence")
def evidence(e: EvidenceIn):
    r = L.record(e.learner, source=e.source, item=e.item, response=e.response, activity=e.activity, score=e.score, mode=e.mode)
    return {"recorded": r, "learner": L.view(e.learner)}


@app.post("/api/scenarios/{sid}/run")
def run_scenario(sid: str, mode: str | None = None):
    return T.run_scenario(sid, mode)


@app.get("/api/learners/{lid}")
def learner(lid: str):
    return L.view(lid)


@app.post("/api/learners/{lid}/clear")
def clear(lid: str):
    L.clear(lid)
    return L.view(lid)


# ---------------------------------------------------------------- context & tutor
class ContextIn(BaseModel):
    learner: str
    concept: str | None = None
    message: str | None = None
    mode: str | None = None


@app.post("/api/context")
def context(c: ContextIn, facets: bool = True):
    return X.assemble(c.learner, concept=c.concept, message=c.message, facets=facets, mode=c.mode)


@app.post("/api/tutor")
def tutor(c: ContextIn):
    return T.compare(c.learner, c.message or "", concept=c.concept, mode=c.mode)


# ---------------------------------------------------------------- evaluation & admin
@app.post("/api/eval/alignment")
def eval_alignment(mode: str | None = None, set: str = "tuning"):
    if set not in ("tuning", "heldout"):
        raise HTTPException(400, "set must be tuning or heldout")
    return E.eval_alignment(mode, set)


@app.post("/api/eval/tutor")
def eval_tutor_start():
    return E.start_tutor_suite()


@app.get("/api/eval/tutor/latest")
def eval_tutor_latest():
    return E.last_tutor_suite() or {}


@app.get("/api/eval/tutor/{jid}")
def eval_tutor_job(jid: str):
    return E.job_status(jid)


@app.get("/api/eval/tutor-suite")
def eval_tutor_suite_def():
    return E.suite()


@app.post("/api/demo/prepare")
def prepare():
    T.ensure_demo_state()
    return status()


@app.post("/api/admin/release")
def release():
    return V.release()


@app.post("/api/admin/reset")
def reset():
    S.reset()
    return status()
