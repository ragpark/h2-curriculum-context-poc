"""MCP surface: lets any MCP-capable AI tutor use H2 without a bespoke integration."""
from mcp.server.fastmcp import FastMCP

from . import align as A
from . import content as C
from . import context as X
from . import graph as G
from . import learner as L

mcp = FastMCP("curriculum-map", stateless_http=True, json_response=True)
mcp.settings.streamable_http_path = "/"


@mcp.tool()
def get_learning_context(learner_id: str, message: str = "", concept_id: str = "") -> dict:
    """Assemble the tutor briefing for one pupil question: focus topic, the pupil's progress and misconceptions,
    the class teacher's method and representation, relevant teacher materials, and next-step guidance.
    learner_id e.g. 'pupil:amara'. Provide the pupil's message and/or a concept ID."""
    return X.assemble(learner_id, concept=concept_id or None, message=message or None, facets=True)


@mcp.tool()
def get_prerequisites(concept_id: str) -> dict:
    """Prerequisite chain, misconceptions, methods and crosswalks for a curriculum concept ID
    (e.g. 'cc:maths/alg/lin-eq-both-sides')."""
    d = G.get().node_detail(G.get().resolve(concept_id))
    return d or {"error": f"unknown concept {concept_id}"}


@mcp.tool()
def find_teacher_materials(class_id: str, concept_id: str, query: str = "", method_id: str = "") -> list:
    """Retrieve the class teacher's own materials aligned to a concept, boosted by teaching method."""
    return C.search(class_id, query or G.get().label(concept_id), concept=G.get().resolve(concept_id), method=method_id or None, k=5)


@mcp.tool()
def align_text(text: str) -> dict:
    """Tag free text against the curriculum graph (concepts, method, representation, misconceptions)."""
    return A.align(text)


@mcp.tool()
def record_evidence(learner_id: str, source: str, item_id: str = "", response: str = "", activity: str = "", score: float = -1,
                    process: list[dict] | None = None) -> dict:
    """Record a pupil's answer. Either item_id + response (a question tagged to the map) or activity + score 0–1
    (untagged; the tagging service interprets it). Optional 'process': events observed while answering, e.g.
    [{"type":"hint_requested","t":3},{"type":"attempt","t":40,"answer":"x = 4","correct":true},{"type":"confidence","t":42,"rating":2}].
    Types: attempt, hint_requested, answer_revised, checked, plan_stated, confidence (1-4), abandoned, affect (session only, never stored).
    Updates the pupil's progress and learning-behaviour patterns."""
    return L.record(learner_id, source=source, item=item_id or None, response=response or None,
                    activity=activity or None, score=None if score < 0 else score, process=process)
