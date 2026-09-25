"""H3 pipeline: teacher materials → semantic units → alignment → hybrid index (vector + graph facets)."""
import re
from collections import Counter

from . import align as A
from . import db
from . import graph as G
from .textvec import cosine, embed

CONF_MIN = 0.5  # alignments below this go to the (optional) teacher review queue


def split_units(body: str) -> list[tuple[str, str]]:
    parts = re.split(r"^##\s+", body.strip(), flags=re.M)
    units = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        head, _, rest = p.partition("\n")
        units.append((head.strip(), rest.strip()))
    return units or [("Untitled", body.strip())]


def ingest(material_id: str, mode: str | None = None) -> dict:
    m = db.q1("select * from material where id=%s", (material_id,))
    if not m:
        raise KeyError(material_id)
    g = G.get()
    db.ex("delete from alignment where subject like %s", (material_id + "#%",))
    db.ex("delete from content_unit where material_id=%s", (material_id,))
    out = []
    for idx, (head, body) in enumerate(split_units(m["body"])):
        uid = f"{material_id}#{idx}"
        text = f"{head}\n{body}"
        res = A.align(f"{m['title']}\n{text}", mode)  # material title gives the unit its lesson context
        db.ex("insert into content_unit values(%s,%s,%s,%s,%s,%s,%s,%s)",
              (uid, material_id, m["class"], m["week"], idx, head, body, db.J(embed(text))))
        for r in A.flatten(res):
            db.ex("insert into alignment(subject,subject_kind,target,facet,confidence,provenance,graph_version) values(%s,'content',%s,%s,%s,%s,%s)",
                  (uid, r["target"], r["facet"], r["confidence"], res["provenance"], g.version))
        out.append({"unit": uid, "heading": head, "alignment": res})
    db.ex("update material set ingested=true where id=%s", (material_id,))
    return {"material": material_id, "units": out}


def ingest_all(class_id: str | None = None, mode: str | None = None) -> list[dict]:
    rows = db.q("select id from material where (%s::text is null or class=%s) order by class, week", (class_id, class_id))
    return [ingest(r["id"], mode) for r in rows]


def unit_alignments(uid_prefix: str) -> dict[str, list[dict]]:
    g = G.get()
    out: dict[str, list[dict]] = {}
    for a in db.q("select * from alignment where subject like %s order by confidence desc", (uid_prefix + "%",)):
        out.setdefault(a["subject"], []).append({"id": a["target"], "label": g.label(a["target"]), "facet": a["facet"],
                                                  "confidence": a["confidence"], "provenance": a["provenance"],
                                                  "graph_version": a["graph_version"],
                                                  "review": a["confidence"] < CONF_MIN})
    return out


def materials(class_id: str | None = None) -> list[dict]:
    mats = db.q("select * from material where (%s::text is null or class=%s) order by class, week, id", (class_id, class_id))
    cls = {c["id"]: c for c in db.q("select * from class")}
    for m in mats:
        units = db.q("select id, idx, heading, body from content_unit where material_id=%s order by idx", (m["id"],))
        al = unit_alignments(m["id"] + "#")
        for u in units:
            u["alignments"] = al.get(u["id"], [])
        m["units"] = units
        m["status"] = "taught" if m["week"] <= cls[m["class"]]["current_week"] else "planned"
    return mats


def coverage(class_id: str) -> dict:
    """Taught-so-far and planned concept sets for a class, derived from aligned, dated materials."""
    c = db.q1("select * from class where id=%s", (class_id,))
    g = G.get()
    rows = db.q("""select a.target, a.confidence, u.week from alignment a join content_unit u on u.id=a.subject
                   where u.class=%s and a.facet='concept' and a.confidence >= %s""", (class_id, CONF_MIN))
    taught, planned = {}, {}
    for r in rows:
        cid = g.resolve(r["target"])
        bucket = taught if r["week"] <= c["current_week"] else planned
        bucket[cid] = min(bucket.get(cid, 99), r["week"])
    planned = {k: v for k, v in planned.items() if k not in taught}
    return {"class": c, "taught": [g.ref(k, week=v) for k, v in sorted(taught.items(), key=lambda x: x[1])],
            "planned": [g.ref(k, week=v) for k, v in sorted(planned.items(), key=lambda x: x[1])]}


def preferred_method(class_id: str, concept: str) -> dict:
    """What method/representation does THIS teacher use for this concept (falling back to the whole taught scheme)?"""
    c = db.q1("select * from class where id=%s", (class_id,))
    g = G.get()
    units = db.q("""select distinct u.id from content_unit u join alignment a on a.subject=u.id
                    where u.class=%s and u.week <= %s and a.facet='concept' and a.target=%s""", (class_id, c["current_week"], concept))
    scope = "concept"
    if not units:
        units = db.q("select id from content_unit where class=%s and week <= %s", (class_id, c["current_week"]))
        scope = "scheme"
    ids = [u["id"] for u in units]
    if not ids:
        return {"method": None, "representation": None, "scope": None}
    rows = db.q("select target, facet from alignment where subject = any(%s) and facet in ('method','representation') and confidence >= %s", (ids, CONF_MIN))
    mc = Counter(r["target"] for r in rows if r["facet"] == "method")
    rc = Counter(r["target"] for r in rows if r["facet"] == "representation")
    return {"method": g.ref(mc.most_common(1)[0][0], uses=mc.most_common(1)[0][1]) if mc else None,
            "representation": g.ref(rc.most_common(1)[0][0], uses=rc.most_common(1)[0][1]) if rc else None,
            "scope": scope}


def search(class_id: str, query: str, *, concept: str | None = None, method: str | None = None,
           misconceptions: list[str] | None = None, taught_only: bool = True, k: int = 3) -> list[dict]:
    """Hybrid retrieval. With graph facets: filter by concept, boost method match and misconception coverage.
    Without (concept=None): vector similarity only — the no-H2 baseline."""
    c = db.q1("select * from class where id=%s", (class_id,))
    units = db.q("select * from content_unit where class=%s" + (" and week <= %s" if taught_only else ""),
                 (class_id, c["current_week"]) if taught_only else (class_id,))
    if not units:
        return []
    al = {}
    for a in db.q("select subject, target, facet, confidence from alignment where subject = any(%s)", ([u["id"] for u in units],)):
        al.setdefault(a["subject"], []).append(a)
    qv = embed(query or "")
    g = G.get()
    scored = []
    for u in units:
        tags = al.get(u["id"], [])
        sim = cosine(qv, u["embedding"])
        why, score = [], sim
        if concept:
            if not any(t["facet"] == "concept" and g.resolve(t["target"]) == concept for t in tags):
                continue
            why.append(f"aligned to {g.label(concept)}")
            if method and any(t["facet"] == "method" and t["target"] == method for t in tags):
                score += 0.5; why.append(f"uses {g.label(method)}")
            hit = [t["target"] for t in tags if t["facet"] == "misconception" and t["target"] in (misconceptions or [])]
            if hit:
                score += 1.0; why.append("addresses " + ", ".join(g.label(h) for h in hit))
        else:
            why.append(f"text similarity {sim:.2f}")
        scored.append({"ref": u["id"], "heading": u["heading"], "week": u["week"], "excerpt": u["body"][:400],
                       "score": round(score, 3), "why": "; ".join(why)})
    scored.sort(key=lambda x: -x["score"])
    return scored[:k]
