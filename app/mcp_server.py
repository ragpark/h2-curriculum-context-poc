"""MCP surface: lets any MCP-capable AI tutor use H2 without a bespoke integration."""
from mcp.server.fastmcp import FastMCP

from . import align as A
from . import content as C
from . import context as X
from . import graph as G
from . import learner as L

mcp = FastMCP("h2-curriculum-context", stateless_http=True, json_response=True)
mcp.settings.streamable_http_path = "/"


@mcp.tool()
def get_learning_context(learner_id: str, message: str = "", concept_id: str = "") -> dict:
    """Assemble the H2 context pack for a tutor turn: focus concept, learner state and misconceptions,
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
def record_evidence(learner_id: str, source: str, item_id: str = "", response: str = "", activity: str = "", score: float = -1) -> dict:
    """Record a piece of learning evidence. Either item_id + response (aligned item) or activity + score 0–1
    (untagged activity; the alignment service interprets it). Updates the learner model."""
    return L.record(learner_id, source=source, item=item_id or None, response=response or None,
                    activity=activity or None, score=None if score < 0 else score)
