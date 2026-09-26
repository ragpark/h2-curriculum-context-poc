"""Load the seed ontology and synthetic fixtures. Idempotent: reset() wipes and reloads everything."""
from pathlib import Path

import yaml

from . import db
from . import graph as G

SEED = Path(__file__).resolve().parent.parent / "seed"


def _y(name):
    return yaml.safe_load((SEED / name).read_text())


def load_graph(version_override=None):
    g = _y("graph.yaml")
    v = version_override or g["graph_version"]
    db.ex("delete from node; delete from edge; delete from crosswalk;")
    for c in g["concepts"]:
        db.ex("insert into node(id,type,label,description,key_stage,keywords,graph_version) values(%s,'concept',%s,%s,%s,%s,%s)",
              (c["id"], c["label"], c.get("description"), db.J(c.get("key_stage", [])), db.J(c.get("keywords", [])), v))
    for t, key in (("misconception", "misconceptions"), ("method", "methods"), ("representation", "representations")):
        for n in g[key]:
            db.ex("insert into node(id,type,label,description,keywords,graph_version) values(%s,%s,%s,%s,%s,%s)",
                  (n["id"], t, n["label"], n.get("description"), db.J(n.get("keywords", [])), v))
    for s, d, w in g["prerequisites"]:
        db.ex("insert into edge(src,rel,dst,weight) values(%s,'prerequisiteOf',%s,%s) on conflict do nothing", (s, d, w))
    for m in g["misconceptions"]:
        for c in m["affects"]:
            db.ex("insert into edge(src,rel,dst) values(%s,'affects',%s) on conflict do nothing", (m["id"], c))
    for m in g["methods"]:
        for c in m["teaches"]:
            db.ex("insert into edge(src,rel,dst) values(%s,'teaches',%s) on conflict do nothing", (m["id"], c))
    for c, scheme, ext, match in g["crosswalk"]:
        db.ex("insert into crosswalk values(%s,%s,%s,%s) on conflict do nothing", (c, scheme, ext, match))
    db.meta_set("graph_version", v)
    G.invalidate()


def load_fixtures():
    items = _y("items.yaml")
    mats = _y("materials.yaml")
    db.ex("delete from item; delete from pupil; delete from class; delete from material; delete from content_unit;")
    for i in items["items"]:
        db.ex("insert into item values(%s,%s,%s,%s,%s)", (i["id"], i["prompt"], db.J(i["concepts"]), i["answer"], db.J(i.get("distractors", []))))
    for p in items["pupils"]:
        db.ex("insert into pupil values(%s,%s,%s)", (p["id"], p["name"], p["class"]))
    for c in mats["classes"]:
        db.ex("insert into class values(%s,%s,%s,%s)", (c["id"], c["teacher"], c["current_week"], c["scheme"]))
    for m in mats["materials"]:
        db.ex("insert into material(id,class,week,title,body) values(%s,%s,%s,%s,%s)", (m["id"], m["class"], m["week"], m["title"], m["body"]))


def scenarios():
    return _y("items.yaml")["scenarios"]


def gold():
    return _y("gold_alignments.yaml")["gold"]


SEED_VERSION = "2"  # bump when fixtures change; the database is reseeded on next start


def reset():
    db.ex("delete from alignment; delete from evidence; delete from learner_state; delete from learner_misconception;")
    load_graph()
    load_fixtures()
    db.meta_set("seeded", "yes")
    db.meta_set("seed_version", SEED_VERSION)


def ensure():
    db.init_schema()
    if db.meta_get("seeded") != "yes" or db.meta_get("seed_version") != SEED_VERSION:
        reset()
