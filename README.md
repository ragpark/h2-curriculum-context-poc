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
- **MCP** (`app/mcp_server.py`): `get_learning_context`, `get_prerequisites`, `find_teacher_materials`, `align_text`, `record_evidence`, `get_review_schedule`
- **Retention** (`app/retention.py`): a second projection over the evidence log, `review_state`, saying when each topic is due for recall; feeds the briefing's `due_for_review`
- **Clock** (`app/clock.py`): real time plus an offset in days, so a demo can be moved forward

## Spaced retrieval

Each briefing can tell a tutor which earlier topics the pupil is due to recall. Nothing in `learner_state` fades;
retention lives in its own projection, `review_state`, rebuilt from the evidence log whenever `learner_state` is
(delete it and it is recreated).

**Schedule.** Leitner-style intervals `[1, 3, 7, 14, 30, 60]` days, per topic, from direct evidence only — never the
inferred credit a success gives its prerequisites. First evidence: level 1 on success (outcome ≥ 0.5), level 0 on
failure. A success on or after the due date: level + 1 (capped) and one more review. A success before the due date:
the last-seen and last-success dates move, the level does not. A failure: level 0 and one more lapse (a first
failure, with nothing yet held, is not a lapse). `due_at = last_success + INTERVALS[level]`. A failure blamed on a
misconception that affects a *different* topic is not counted against this one, the same credit-assignment rule
`learner_state` uses, so the two projections never disagree about whether a topic lapsed.

**The briefing block** (`due_for_review`, after `how_to_support`; `items: []` when nothing is due):

```json
"due_for_review": {
  "rule": "Advisory. Use at most one, as a short warm-up recall question, and only when it doesn't get in the way of what the pupil asked. Never describe the pupil by these.",
  "as_of": "2026-11-10",
  "items": [{"id": "cc:maths/alg/lin-eq-two-step", "label": "Solve two-step linear equations", "taught_week": 2,
             "last_success": "2026-09-23", "interval_days": 3, "overdue_days": 45,
             "why": "last recalled correctly 6 weeks ago; a prerequisite of the current focus or next step"}]
}
```

Eligible: taught to the class (in `taught_so_far`), at least one piece of direct evidence, due, not the focus, and the
learner is `secure` or `developing` on it (never `gap`). Ranked by `overdue_days / interval_days`, × 1.5 for
prerequisites of the focus or of `next_step`; ties go to a topic from a different scheme week than the focus. At most 3.
`why` is about the topic, never a label for the pupil. Inspect the whole schedule at `GET /api/learners/{id}/review` or
the MCP tool `get_review_schedule`, and on the Evidence page ("Due for review").

**Clock.** `GET /api/admin/clock`; `POST /api/admin/clock` with `{"advance_days": n}`, `{"set": "YYYY-MM-DD"}` or
`{"reset": true}`. Every "now" in the map goes through it: evidence timestamps, process events, agreed-adjustment
expiry, `as_of` in the briefing. It is one global offset; it does not move a class's `current_week`. Seed evidence
carries `days_ago`, so Amara, Ben and Priya have histories spread over 3–6 weeks.

**Evidence from a tutor.** `POST /api/evidence` and `record_evidence` take an optional `provenance: "tutor"` with
`concepts`: the answer is stored with `concept_provenance = "tutor"` and weighted between the alignment service and a
teacher (`TUTOR_W = 0.8`, `TUTOR_BASE = 0.4` in `app/learner.py`). A `retrieval` process event
(`{"type": "retrieval", "concept": "...", "prompted_by": "tutor" | "quiz" | "teacher"}`) records that an answer was a
recall of an earlier topic; it is stored and no indicators are derived from it yet. Without the field or the event,
behaviour is exactly as before.

**Comparability.** The briefing carries `briefing_version: "2"` (new keys `as_of`, `briefing_version`,
`due_for_review`). `graph_version` is unchanged: the map is the same. Measured on Amara's practice turn, the v2
briefing is about 240 tokens longer (1 303 → 1 539 approximate tokens); the raw pack gains a relative time such as
"(5 weeks ago)" on each `recent_activity` line, and the no-context condition is unchanged. Tutor-test results recorded
before this change are therefore not strictly comparable with runs made after it.

Tests: `pip install -r requirements-dev.txt && pytest` (database-free: schedule rules, eligibility and ranking, clock).

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
