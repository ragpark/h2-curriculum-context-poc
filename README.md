# H2 Curriculum Context Layer — proof of concept

A working test of hypothesis **H2** (a shared curriculum context layer) and how it improves **H1** (learner experiential data) and **H3** (teachers' instructional materials) for AI tutoring.

One strand only: KS3–4 algebra, linear equations. All pupils, classes and teachers are synthetic.

## What it demonstrates

| Page | What you see |
|---|---|
| **Overview** | The architecture with live counts per layer, and a guided walkthrough |
| **Reference graph (H2)** | 23 concepts laid out by prerequisite depth; misconceptions, methods and representations as first-class nodes; crosswalks; overlays for class coverage or pupil mastery; a simulated graph release (2026.2 → 2026.3) showing migration of alignments, immutable evidence and re-projection of learner state |
| **Teacher materials (H3)** | Materials split into units and tagged with concepts, method, representation and misconceptions; the class's taught-so-far set and preferred method derived from them |
| **Learner evidence (H1)** | Item evidence (tagged at source) and untagged markbook entries (aligned); append-only log; learner model with graph propagation and misconception-aware credit assignment |
| **Tutor context** | The same pupil turn, side by side: the context a tutor gets **without H2** (raw log + text search) vs **with H2** (joined on concept IDs), plus live Claude tutor replies from each |
| **Evaluate** | Alignment precision/recall against a hand-aligned gold set, and a blind LLM-judged tutor A/B test |
| **Connect** | MCP endpoint (`/mcp/`) and REST (`/docs`) |

## Architecture

- **Reference graph**: `node`, `edge`, `crosswalk` tables, loaded from `seed/graph.yaml`
- **Alignment service** (`app/align.py`): heuristic (deterministic) or Claude (graph-constrained; candidates retrieved from the graph, returned IDs validated against it)
- **Content index** (`app/content.py`): units + hashed embeddings + alignment facets (hybrid retrieval)
- **Evidence store** (`app/learner.py`): append-only; learner state is a projection rebuilt from it
- **Context assembly** (`app/context.py`): the one call a tutor makes; `facets=false` gives the no-H2 baseline
- **MCP** (`app/mcp_server.py`): `get_learning_context`, `get_prerequisites`, `find_teacher_materials`, `align_text`, `record_evidence`

## Configuration

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection string (Railway reference: `${{Postgres.DATABASE_URL}}`) |
| `ANTHROPIC_API_KEY` | Enables the Claude aligner, live tutor replies and the tutor A/B evaluation |
| `ANTHROPIC_MODEL` | Optional; defaults to `claude-sonnet-5` |
| `ALIGNER_MODE` | `auto` (default: Claude if a key is set), `heuristic` or `claude` |

The schema is created and seeded automatically on first start. **Reset** in the UI restores the seed state.

## Run locally

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:pass@localhost:5432/h2
uvicorn app.main:app --reload
```

## Limitations (deliberate, for a POC)

- The embedding is a hashed bag of words, standing in for a real embedding model.
- The learner model is a simple per-concept exponential update with prerequisite propagation, not a calibrated knowledge-tracing model.
- The crosswalks to DfE GCSE subject content references and the "shared-layer" IDs are illustrative.
- There is no authentication. Do not load real pupil data.
