"""Simulated graph release, to show why the layers are kept separate.

Release 2026.3 merges 'one-step' and 'two-step' equations into a single concept.
  • Reference graph: new node; old nodes deprecated with replacedBy (never deleted); edges rewired.
  • Alignments (derived): migrated to the new ID and re-stamped with the new graph version.
  • Evidence (immutable): untouched — the projector resolves old IDs via replacedBy.
  • Learner state (projection): rebuilt from evidence.
"""
from . import db
from . import graph as G
from . import learner as L

OLD = ["cc:maths/alg/lin-eq-one-step", "cc:maths/alg/lin-eq-two-step"]
NEW = "cc:maths/alg/lin-eq-simple"
TO = "2026.3"


def release() -> dict:
    g = G.get()
    frm = g.version
    if frm == TO:
        raise ValueError("Release 2026.3 has already been applied — reset the demo to return to 2026.2.")
    kw = sorted({k for o in OLD for k in (g.nodes[o]["keywords"] or [])})
    before = {r["learner"]: r for r in db.q("select learner, concept, mastery, status from learner_state where concept = any(%s)", (OLD,))}
    db.ex("insert into node(id,type,label,description,key_stage,keywords,graph_version) values(%s,'concept',%s,%s,%s,%s,%s)",
          (NEW, "Solve one- and two-step linear equations", "Merged in 2026.3 from one-step and two-step equations.", db.J(["KS3"]), db.J(kw), TO))
    db.ex("update node set status='deprecated', replaced_by=%s, graph_version=%s where id = any(%s)", (NEW, TO, OLD))
    edges = db.q("select * from edge where src = any(%s) or dst = any(%s)", (OLD, OLD))
    for e in edges:
        s = NEW if e["src"] in OLD else e["src"]
        d = NEW if e["dst"] in OLD else e["dst"]
        if s != d:
            db.ex("insert into edge values(%s,%s,%s,%s) on conflict do nothing", (s, e["rel"], d, e["weight"]))
    db.ex("delete from edge where src = any(%s) or dst = any(%s)", (OLD, OLD))
    db.ex("update crosswalk set concept=%s where concept = any(%s) and not exists (select 1 from crosswalk c2 where c2.concept=%s and c2.scheme=crosswalk.scheme and c2.external_id=crosswalk.external_id)", (NEW, OLD, NEW))
    db.ex("delete from crosswalk where concept = any(%s)", (OLD,))
    n_align = db.q1("select count(*) n from alignment where target = any(%s)", (OLD,))["n"]
    db.ex("update alignment set target=%s, graph_version=%s, provenance = provenance || ' +migrated' where target = any(%s)", (NEW, TO, OLD))
    n_ev = db.q1("select count(*) n from evidence where concepts ?| %s", (OLD,))["n"]
    db.meta_set("graph_version", TO)
    G.invalidate()
    learners = [r["learner"] for r in db.q("select distinct learner from evidence")]
    for lid in learners:
        L.project(lid)
    after = {r["learner"]: r for r in db.q("select learner, mastery, status from learner_state where concept=%s", (NEW,))}
    return {
        "from": frm, "to": TO,
        "graph_changes": [f"added {NEW}", *[f"deprecated {o} → replacedBy {NEW}" for o in OLD], f"rewired {len(edges)} edges"],
        "alignments_migrated": n_align,
        "evidence_rows_referencing_deprecated_ids": n_ev,
        "evidence_rows_modified": 0,
        "learners_reprojected": len(learners),
        "learner_state_new_concept": list(after.values()),
        "note": "Evidence is immutable. The projector resolves deprecated IDs via replacedBy, so no learner history is lost.",
    }
