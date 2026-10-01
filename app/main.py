import contextlib
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import adjustments as ADJ
from . import align as A
from . import behaviour as B
from . import case as K
from . import content as C
from . import context as X
from . import db
from . import evals as E
from . import graph as G
from . import learner as L
from . import llm
from . import scheme as SW
from . import seed as S
from . import tutor as T
from . import tutors as TU
from . import validate as VAL
from . import versioning as V
from .mcp_server import mcp

STATIC = Path(__file__).resolve().parent.parent / "static"


@contextlib.asynccontextmanager
async def lifespan(app):
    S.ensure()
    E.run_on_start(os.environ.get("RUN_EVALS_ON_START", ""))
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Curriculum Map for AI Tutors — POC", version="0.2", lifespan=lifespan,
              description="A shared curriculum map (H2), with pupil progress (H1) and teachers' materials (H3) tagged against it, assembled into a briefing for AI tutors.")
app.mount("/mcp", mcp.streamable_http_app())


@app.middleware("http")
async def _mcp_no_redirect(request: Request, call_next):
    # Clients connect to ".../mcp"; serve it directly instead of a 307 redirect to ".../mcp/",
    # which some MCP clients do not follow for POST requests.
    if request.scope["path"] == "/mcp":
        request.scope["path"] = "/mcp/"
        request.scope["raw_path"] = b"/mcp/"
    return await call_next(request)
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
    return {"graph_version": G.get().version, "graph_versions": {k: G.get().version_for(k) for k in S.SUBJECTS}, "aligner_mode": A.default_mode(), "llm_available": llm.available(),
            "model": llm.model() if llm.available() else None, "counts": counts,
            "mcp_path": "/mcp/", "public_url": os.environ.get("RAILWAY_PUBLIC_DOMAIN")}


# ---------------------------------------------------------------- subjects & graph (H2)
@app.get("/api/subjects")
def subjects():
    g = G.get()
    return [{"id": k, "label": v["label"], "layout": v["layout"], "graph_version": g.version_for(k),
             "concepts": len(g.of_type("concept", subject=k)),
             "classes": [c["id"] for c in db.q("select id from class where subject=%s order by id", (k,))]}
            for k, v in S.SUBJECTS.items()]


@app.get("/api/graph")
def graph(subject: str = "maths"):
    if subject not in S.SUBJECTS:
        raise HTTPException(400, "unknown subject")
    return G.get().to_json(subject)


@app.get("/api/graph/node")
def node(id: str):
    id = K.resolve_alias(id)  # accepts the readable ID, a CASE UUID or a CASE URI
    d = G.get().node_detail(id)
    if not d:
        raise HTTPException(404, "unknown node")
    d["usage"] = {
        "alignments": db.q1("select count(*) n from alignment where target=%s", (id,))["n"],
        "evidence": db.q1("select count(*) n from evidence where concepts ? %s", (id,))["n"],
    }
    return d


# ---------------------------------------------------------------- map authoring: validator and templates
AUTHORING = Path(__file__).resolve().parent.parent / "seed" / "authoring"


class ValidateIn(BaseModel):
    yaml_text: str


@app.post("/api/validate")
def validate_map(body: ValidateIn):
    if len(body.yaml_text) > 400_000:
        raise HTTPException(413, "map file too large (400 KB limit)")
    return VAL.validate(body.yaml_text)


@app.get("/api/authoring/{name}")
def authoring_file(name: str):
    files = {"template": "template.yaml", "example": "example_fractions.yaml"}
    if name not in files:
        raise HTTPException(404, "unknown file")
    return PlainTextResponse((AUTHORING / files[name]).read_text(), media_type="text/yaml; charset=utf-8",
                             headers={"content-disposition": f'attachment; filename="curriculum-map-{name}.yaml"'})


@app.get("/api/validate/shipped")
def validate_shipped():
    """The two live maps run through the same validator (they must pass)."""
    out = {}
    for sub, meta in S.SUBJECTS.items():
        r = VAL.validate((Path(__file__).resolve().parent.parent / "seed" / meta["graph"]).read_text())
        out[sub] = {"ok": r["ok"], "counts": r["counts"], "warnings": r["warnings"]}
    return out


# ---------------------------------------------------------------- CASE v1.1 (read-only export)
@app.get(K.API + "/CFDocuments")
def case_documents():
    return {"CFDocuments": K.documents()}


@app.get(K.API + "/CFDocuments/{sid}")
def case_document(sid: str):
    d = next((x for x in K.documents() if x["identifier"] == sid), None)
    if not d:
        raise HTTPException(404, "unknown CFDocument")
    return d


@app.get(K.API + "/CFPackages/{sid}")
def case_package(sid: str):
    return K.package(sid)


@app.get(K.API + "/CFItems/{sid}")
def case_item(sid: str):
    return K.find_item(sid)[0]


@app.get(K.API + "/CFItemAssociations/{sid}")
def case_item_associations(sid: str):
    return K.item_associations(sid)


@app.get(K.API + "/CFAssociations/{sid}")
def case_association(sid: str):
    for s_ in S.SUBJECTS:
        for a in K.associations(s_):
            if a["identifier"] == sid:
                return a
    raise HTTPException(404, "unknown CFAssociation")


class AlignIn(BaseModel):
    text: str
    mode: str | None = None
    subject: str = "maths"


@app.post("/api/align")
def align(body: AlignIn):
    return A.align(body.text, body.mode, body.subject)


# ---------------------------------------------------------------- H3 materials
@app.get("/api/classes")
def classes(subject: str | None = None):
    return db.q("select * from class where (%s::text is null or subject=%s) order by id", (subject, subject))


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


class SchemePreviewIn(BaseModel):
    csv_text: str
    class_id: str | None = None
    mode: str | None = None


@app.post("/api/scheme/preview")
def scheme_preview(body: SchemePreviewIn):
    if len(body.csv_text) > 200_000:
        raise HTTPException(413, "file too large (200 KB limit)")
    return SW.preview(body.csv_text, body.class_id, body.mode)


class SchemeAcceptIn(BaseModel):
    weeks: list[dict]
    source: str | None = None


@app.put("/api/classes/{class_id}/scheme")
def scheme_accept(class_id: str, body: SchemeAcceptIn):
    if not db.q1("select 1 from class where id=%s", (class_id,)):
        raise KeyError(class_id)
    return SW.accept(class_id, body.weeks, body.source)


@app.get("/api/classes/{class_id}/scheme")
def scheme_get(class_id: str):
    return SW.get(class_id)


@app.delete("/api/classes/{class_id}/scheme")
def scheme_clear(class_id: str):
    SW.clear(class_id)
    return SW.get(class_id)


@app.get("/api/schemes/exemplar/{name}")
def scheme_exemplar(name: str):
    files = {"10X": "sow_10X_maths_linear_equations.csv", "10Y": "sow_10Y_maths_linear_equations.csv",
             "11E": "sow_11E_english_macbeth.csv", "11F": "sow_11F_english_macbeth.csv"}
    if name not in files:
        raise HTTPException(404, "unknown exemplar")
    p = Path(__file__).resolve().parent.parent / "seed" / "schemes" / files[name]
    return PlainTextResponse(p.read_text(), media_type="text/csv; charset=utf-8",
                             headers={"content-disposition": f'attachment; filename="{files[name]}"'})


@app.get("/api/classes/{class_id}/coverage")
def coverage(class_id: str):
    return C.coverage(class_id)


# ---------------------------------------------------------------- H1 evidence
@app.get("/api/items")
def items():
    return db.q("select * from item order by id")


@app.get("/api/pupils")
def pupils(subject: str | None = None):
    return db.q("""select p.*, c.subject from pupil p join class c on c.id=p.class
                   where (%s::text is null or c.subject=%s) order by p.id""", (subject, subject))


@app.get("/api/scenarios")
def scenarios(subject: str | None = None):
    return S.scenarios(subject)


class EvidenceIn(BaseModel):
    learner: str
    source: str = "Manual entry"
    item: str | None = None
    response: str | None = None
    activity: str | None = None
    score: float | None = None
    mode: str | None = None
    process: list[dict] | None = None
    concepts: list[str] | None = None
    misconception: str | None = None


@app.post("/api/evidence")
def evidence(e: EvidenceIn):
    r = L.record(e.learner, source=e.source, item=e.item, response=e.response, activity=e.activity, score=e.score, mode=e.mode,
                 process=e.process, concepts=e.concepts, misconception=e.misconception)
    return {"recorded": r, "learner": L.view(e.learner)}


@app.post("/api/scenarios/{sid}/run")
def run_scenario(sid: str, mode: str | None = None):
    return T.run_scenario(sid, mode)


@app.get("/api/learners/{lid}")
def learner(lid: str):
    return L.view(lid)


@app.get("/api/behaviour/framework")
def behaviour_framework():
    return B.framework()


@app.get("/api/learners/{lid}/behaviour")
def learner_behaviour(lid: str):
    return B.view(lid) | {"briefing": B.briefing(lid), "shared": B.shared(lid)}


class ConfirmIn(BaseModel):
    confirmed: bool = True


@app.post("/api/learners/{lid}/behaviour/{construct:path}/confirm")
def confirm_pattern(lid: str, construct: str, body: ConfirmIn):
    B.set_confirmed(lid, construct, body.confirmed)
    return B.view(lid) | {"briefing": B.briefing(lid), "shared": B.shared(lid)}


@app.post("/api/learners/{lid}/clear")
def clear(lid: str):
    L.clear(lid)
    return L.view(lid)


# ---------------------------------------------------------------- agreed adjustments (teacher-authored)
class ProfileIn(BaseModel):
    adjustments: list[str]
    confirmed_by: str
    confirmed_on: str
    review_by: str
    note: str | None = None


@app.get("/api/adjustments/vocabulary")
def adjustments_vocab():
    v = ADJ.vocab()
    return {"version": v["version"], "groups": v["groups"], "adjustments": [v["adjustments"][i] for i in v["order"]]}


@app.get("/api/learners/{lid}/adjustments")
def learner_adjustments(lid: str):
    return ADJ.profile(lid)


@app.put("/api/learners/{lid}/adjustments")
def set_learner_adjustments(lid: str, body: ProfileIn):
    if not db.q1("select 1 from pupil where id=%s", (lid,)):
        raise KeyError(lid)
    return ADJ.set_profile(lid, body.adjustments, body.confirmed_by, body.confirmed_on, body.review_by, body.note)


@app.delete("/api/learners/{lid}/adjustments")
def clear_learner_adjustments(lid: str):
    ADJ.clear_profile(lid)
    return ADJ.profile(lid)


# ---------------------------------------------------------------- context & tutor
class ContextIn(BaseModel):
    learner: str
    concept: str | None = None
    message: str | None = None
    mode: str | None = None


@app.post("/api/context")
def context(c: ContextIn, facets: bool = True):
    return X.assemble(c.learner, concept=K.resolve_alias(c.concept) if c.concept else None, message=c.message, facets=facets, mode=c.mode)


@app.post("/api/tutor")
def tutor(c: ContextIn):
    return T.compare(c.learner, c.message or "", concept=c.concept, mode=c.mode)


# ---------------------------------------------------------------- evaluation & admin
@app.post("/api/eval/alignment")
def eval_alignment(mode: str | None = None, set: str = "tuning"):
    if set not in ("tuning", "heldout", "heldout2", "english"):
        raise HTTPException(400, "set must be tuning, heldout, heldout2 or english")
    return E.eval_alignment(mode, set)


@app.get("/api/eval/alignment/latest")
def eval_alignment_latest(set: str = "english", mode: str = "claude"):
    return E.last_alignment(set, mode) or {}


# ---------------------------------------------------------------- independent tutors (level 2 integration)
class TutorIn(BaseModel):
    label: str
    kind: str = "webhook"
    url: str | None = None
    secret: str | None = None
    notes: str | None = None


@app.get("/api/tutors")
def tutors_list():
    return TU.list_tutors()


@app.post("/api/tutors")
def tutors_register(t: TutorIn):
    return TU.register(t.label, t.kind, t.url, t.secret, t.notes)


@app.delete("/api/tutors/{tid}")
def tutors_remove(tid: str):
    if tid == "builtin":
        raise HTTPException(400, "the built-in tutor cannot be removed")
    TU.remove(tid)
    return {"removed": tid}


@app.post("/api/tutors/{tid}/ping")
def tutors_ping(tid: str):
    return TU.ping(TU.get(tid))


@app.get("/api/eval/tutor/export")
def eval_tutor_export(suite: str = "heldout"):
    return E.export_suite(suite)


class RepliesIn(BaseModel):
    suite: str | None = None
    replies: list[dict]


@app.post("/api/eval/tutor/import")
def eval_tutor_import(body: RepliesIn, suite: str = "heldout", tutor: str = "builtin"):
    if TU.get(tutor)["kind"] != "offline":
        raise HTTPException(400, "replies can only be uploaded for an offline tutor")
    replies = E.parse_replies(suite, body.model_dump())
    return E.start_tutor_suite(suite, tutor, replies)


@app.get("/api/eval/tutor/compare")
def eval_tutor_compare(suite: str = "heldout"):
    return E.compare_tutors(suite)


@app.post("/api/eval/tutor")
def eval_tutor_start(suite: str = "heldout", tutor: str = "builtin"):
    return E.start_tutor_suite(suite, tutor)


@app.get("/api/eval/tutor/latest")
def eval_tutor_latest(suite: str = "heldout", tutor: str = "builtin"):
    return E.last_tutor_suite(suite, tutor) or {}


@app.get("/api/eval/tutor/{jid}")
def eval_tutor_job(jid: str):
    return E.job_status(jid)


@app.get("/api/eval/tutor-suite")
def eval_tutor_suite_def(suite: str = "heldout"):
    return E.suite(suite)


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
