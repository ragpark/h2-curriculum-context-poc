"""Load the seed ontology and synthetic fixtures. Idempotent: reset() wipes and reloads everything.

Two subjects share one store:
  • maths   — KS3–4 linear equations: a ladder of prerequisites
  • english — GCSE English Literature, Macbeth: a web of knowledge topics, a ladder of writing skills,
              key quotations and the acts of the play
"""
import re
from pathlib import Path

import yaml

from . import db
from . import graph as G

SEED = Path(__file__).resolve().parent.parent / "seed"

SUBJECTS = {
    "maths": {"label": "Maths: linear equations", "graph": "graph.yaml", "year": 10, "layout": "layers",
              "tutor": "an AI maths tutor working one-to-one with a Year 10 pupil in an English secondary school",
              "cross_cutting": []},
    "english": {"label": "English: Macbeth", "graph": "graph_english_macbeth.yaml", "fixtures": "english_fixtures.yaml",
                "prefix": "cc:english/macbeth/", "year": 11, "layout": "strands",
                "tutor": "an AI English Literature tutor working one-to-one with a Year 11 pupil in an English secondary school, "
                         "who is studying Macbeth for GCSE",
                # Writing skills are exercised by every task, whatever the topic: a misconception about a skill
                # (e.g. retelling the plot) is relevant to any question the pupil asks.
                "cross_cutting": ["skills"]},
}


def _y(name):
    return yaml.safe_load((SEED / name).read_text())


def subject_meta(subject: str) -> dict:
    m = dict(SUBJECTS[subject])
    if m.get("layout") == "strands":
        m["strands"] = _y(m["graph"]).get("strands", [])
    return m


def _full(subject, sid):
    """Expand a short concept ID (e.g. 'ch-lady') to the full ID for subjects whose seed uses short IDs."""
    pre = SUBJECTS[subject].get("prefix")
    return sid if (not pre or ":" in sid) else pre + sid


def load_graph(version_override=None):
    db.ex("delete from node; delete from edge; delete from crosswalk;")
    for subject, meta in SUBJECTS.items():
        _load_subject_graph(subject, _y(meta["graph"]), version_override if subject == "maths" else None)
    G.invalidate()


def _load_subject_graph(subject, g, version_override=None):
    from . import validate as VAL
    from pathlib import Path as _P
    rep = VAL.validate((SEED / SUBJECTS[subject]["graph"]).read_text())
    if not rep["ok"]:
        first = rep["errors"][0]
        raise RuntimeError(f"map '{subject}' failed structural validation: {first['where']}: {first['message']}")
    v = version_override or g["graph_version"]
    f = lambda sid: _full(subject, sid)
    for c in g["concepts"]:
        db.ex("insert into node(id,type,label,description,key_stage,keywords,graph_version,subject,strand) values(%s,'concept',%s,%s,%s,%s,%s,%s,%s)",
              (c["id"], c["label"], c.get("description"), db.J(c.get("key_stage", [])), db.J(c.get("keywords", [])), v, subject, c.get("strand")))
    for t, key in (("misconception", "misconceptions"), ("method", "methods"), ("representation", "representations")):
        for n in g.get(key, []):
            db.ex("insert into node(id,type,label,description,keywords,graph_version,subject) values(%s,%s,%s,%s,%s,%s,%s)",
                  (n["id"], t, n["label"], n.get("description"), db.J(n.get("keywords", [])), v, subject))
    for s in g.get("sections", []):
        db.ex("insert into node(id,type,label,keywords,graph_version,subject,extra) values(%s,'section',%s,%s,%s,%s,%s)",
              (s["id"], s["label"], db.J(s.get("keywords", [])), v, subject, db.J({"order": s["order"]})))
    sec_by_act = {str(s["order"]): s["id"] for s in g.get("sections", [])}
    for qd in g.get("quotations", []):
        db.ex("insert into node(id,type,label,description,graph_version,subject,extra) values(%s,'quotation',%s,%s,%s,%s,%s)",
              (qd["id"], qd["text"], f"{qd['speaker']}, {qd['act']}", v, subject, db.J({"speaker": qd["speaker"], "act": qd["act"]})))
        for c in qd.get("evidences", []):
            db.ex("insert into edge(src,rel,dst) values(%s,'evidences',%s) on conflict do nothing", (qd["id"], f(c)))
        sec = sec_by_act.get(str(qd["act"]).split(".")[0])
        if sec:
            db.ex("insert into edge(src,rel,dst) values(%s,'inSection',%s) on conflict do nothing", (qd["id"], sec))
    for s, d, w in g.get("prerequisites", []):
        db.ex("insert into edge(src,rel,dst,weight) values(%s,'prerequisiteOf',%s,%s) on conflict do nothing", (f(s), f(d), w))
    for a, b in g.get("related", []):
        db.ex("insert into edge(src,rel,dst) values(%s,'relatesTo',%s) on conflict do nothing", (f(a), f(b)))
    for m in g.get("misconceptions", []):
        for c in m["affects"]:
            db.ex("insert into edge(src,rel,dst) values(%s,'affects',%s) on conflict do nothing", (m["id"], f(c)))
    for m in g.get("methods", []):
        for c in m["teaches"]:
            db.ex("insert into edge(src,rel,dst) values(%s,'teaches',%s) on conflict do nothing", (m["id"], f(c)))
    for c, scheme, ext, match in g.get("crosswalk", []):
        db.ex("insert into crosswalk values(%s,%s,%s,%s) on conflict do nothing", (f(c), scheme, ext, match))
    db.meta_set("graph_version" if subject == "maths" else f"graph_version:{subject}", v)


def load_fixtures():
    items = _y("items.yaml")
    mats = _y("materials.yaml")
    db.ex("delete from item; delete from pupil; delete from class; delete from material; delete from content_unit;")
    for i in items["items"]:
        db.ex("insert into item values(%s,%s,%s,%s,%s)", (i["id"], i["prompt"], db.J(i["concepts"]), i["answer"], db.J(i.get("distractors", []))))
    pupils = list(items["pupils"])
    classes = [dict(c, subject="maths") for c in mats["classes"]]
    materials = list(mats["materials"])
    for subject, meta in SUBJECTS.items():
        if meta.get("fixtures"):
            fx = _y(meta["fixtures"])
            classes += [dict(c, subject=subject) for c in fx["classes"]]
            materials += fx["materials"]
            pupils += fx["pupils"]
    for p in pupils:
        db.ex("insert into pupil values(%s,%s,%s)", (p["id"], p["name"], p["class"]))
    for c in classes:
        db.ex("insert into class(id,teacher,current_week,scheme,subject) values(%s,%s,%s,%s,%s)",
              (c["id"], c["teacher"], c["current_week"], c["scheme"], c["subject"]))
    for m in materials:
        db.ex("insert into material(id,class,week,title,body) values(%s,%s,%s,%s,%s)", (m["id"], m["class"], m["week"], m["title"], m["body"]))


def scenarios(subject: str | None = None):
    out = [dict(s, subject="maths") for s in _y("items.yaml")["scenarios"]]
    for sub, meta in SUBJECTS.items():
        if meta.get("fixtures"):
            for s in _y(meta["fixtures"])["scenarios"]:
                s = dict(s, subject=sub)
                s["events"] = [dict(e, concepts=[_full(sub, c) for c in e.get("concepts", [])]) for e in s["events"]]
                out.append(s)
    return [s for s in out if subject is None or s["subject"] == subject]


def gold():
    return _y("gold_alignments.yaml")["gold"]


def subject_of_class(class_id: str) -> str:
    r = db.q1("select subject from class where id=%s", (class_id,))
    return r["subject"] if r else "maths"


def subject_of_pupil(pupil_id: str) -> str:
    r = db.q1("select c.subject from pupil p join class c on c.id=p.class where p.id=%s", (pupil_id,))
    return r["subject"] if r else "maths"


SEED_VERSION = "6"  # bump when fixtures change; the database is reseeded on next start


def reset():
    db.ex("delete from alignment; delete from evidence; delete from learner_state; delete from learner_misconception; delete from behaviour_event; delete from learner_indicator; delete from learner_construct;")
    load_graph()
    load_fixtures()
    db.meta_set("seeded", "yes")
    import datetime
    db.meta_set("seeded_at", datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat())
    db.meta_set("seed_version", SEED_VERSION)


def ensure():
    db.init_schema()
    if db.meta_get("seeded") != "yes" or db.meta_get("seed_version") != SEED_VERSION:
        reset()
