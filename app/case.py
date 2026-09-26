"""CASE (1EdTech Competencies and Academic Standards Exchange, v1.1) identifiers and export.

Every node keeps its readable prototype ID (e.g. cc:maths/alg/lin-eq-both-sides) as an alias, and gains:
  • identifier — a UUID derived deterministically from the prototype ID (uuid5), so it never changes across reseeds
  • uri        — a resolvable web address: this service's CASE endpoint for that item

Exported: topics (CFItems), strand/act groupings (CFItems), and associations:
  isChildOf (topic → strand / act), precedes (prerequisites), isRelatedTo (related topics; close/broad crosswalks),
  exactMatchOf (exact crosswalks), replacedBy (retired topics).
Not exported: misconceptions, methods, representations and quotations. They are the publisher's teaching layer,
which CASE has no native types for; they stay in the map and point at these CASE URIs.
"""
import os
import uuid

from . import db
from . import graph as G
from . import seed as S

NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://curriculum-map-poc/case")
API = "/ims/case/v1p1"


def uid(alias: str) -> str:
    return str(uuid.uuid5(NS, alias))


def base() -> str:
    b = os.environ.get("CASE_BASE_URI")
    if b:
        return b.rstrip("/")
    d = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    return f"https://{d}" if d else "http://localhost:8077"


def item_uri(alias: str) -> str:
    return f"{base()}{API}/CFItems/{uid(alias)}"


def doc_alias(subject: str) -> str:
    return f"doc:{subject}"


def ref(alias: str) -> dict:
    """The CASE identity of a prototype node (shown alongside the readable alias)."""
    return {"identifier": uid(alias), "uri": item_uri(alias), "alias": alias}


def _changed() -> str:
    return db.meta_get("seeded_at") or "2026-09-26T00:00:00+00:00"


def _link(alias: str, title: str) -> dict:
    return {"title": title, "identifier": uid(alias), "uri": item_uri(alias)}


def _doc_link(subject: str) -> dict:
    d = uid(doc_alias(subject))
    return {"title": S.SUBJECTS[subject]["label"], "identifier": d, "uri": f"{base()}{API}/CFDocuments/{d}"}


# ------------------------------------------------------------------ lookup
def alias_index() -> dict:
    """UUID → prototype alias, for every node, grouping and document."""
    g = G.get()
    idx = {uid(n): n for n in g.nodes}
    for subject in S.SUBJECTS:
        idx[uid(doc_alias(subject))] = doc_alias(subject)
        for st in S.subject_meta(subject).get("strands", []):
            a = f"strand:{subject}/{st['id']}"
            idx[uid(a)] = a
    return idx


def resolve_alias(x: str) -> str:
    """Accept a prototype alias, a CASE UUID or a CASE URI; return the prototype alias."""
    if not x:
        return x
    key = x.rstrip("/").rsplit("/", 1)[-1] if x.startswith("http") else x
    return alias_index().get(key, x)


# ------------------------------------------------------------------ builders
def documents() -> list[dict]:
    g = G.get()
    out = []
    for s, meta in S.SUBJECTS.items():
        d = uid(doc_alias(s))
        out.append({"identifier": d, "uri": f"{base()}{API}/CFDocuments/{d}",
                    "creator": "Curriculum Map POC (illustrative; not an official framework)",
                    "title": f"Curriculum map — {meta['label']}", "version": g.version_for(s),
                    "adoptionStatus": "Draft", "language": "en-GB", "subject": [meta["label"].split(":")[0]],
                    "lastChangeDateTime": _changed(),
                    "CFPackageURI": {"title": meta["label"], "identifier": d, "uri": f"{base()}{API}/CFPackages/{d}"},
                    "notes": "Synthetic prototype. Readable prototype IDs are kept in humanCodingScheme."})
    return out


def _item(alias: str, statement: str, itype: str, subject: str, **extra) -> dict:
    it = {"identifier": uid(alias), "uri": item_uri(alias), "fullStatement": statement,
          "humanCodingScheme": alias, "CFItemType": itype, "language": "en-GB",
          "lastChangeDateTime": _changed(), "CFDocumentURI": _doc_link(subject)}
    it.update({k: v for k, v in extra.items() if v})
    return it


def items(subject: str) -> list[dict]:
    g = G.get()
    out = []
    for st in S.subject_meta(subject).get("strands", []):
        out.append(_item(f"strand:{subject}/{st['id']}", st["label"], "Strand", subject))
    for n in g.sections(subject):
        out.append(_item(n["id"], n["label"], "Act", subject))
    for n in g.of_type("concept", active_only=False, subject=subject):
        ks = n.get("key_stage") or (["KS4"] if subject == "english" else [])
        out.append(_item(n["id"], n["label"], "Concept", subject, conceptKeywords=n.get("keywords") or [],
                         educationLevel=ks, notes=n.get("description"),
                         statusEndDate=_changed()[:10] if n["status"] != "active" else None))
    return out


def _assoc(kind: str, a: str, a_title: str, b: dict, subject: str, seq: int | None = None, notes: str | None = None) -> dict:
    aid = uid(f"assoc:{kind}:{a}->{b['identifier']}")
    x = {"identifier": aid, "associationType": kind, "uri": f"{base()}{API}/CFAssociations/{aid}",
         "originNodeURI": _link(a, a_title), "destinationNodeURI": b,
         "CFDocumentURI": _doc_link(subject), "lastChangeDateTime": _changed()}
    if seq is not None:
        x["sequenceNumber"] = seq
    if notes:
        x["notes"] = notes
    return x


def associations(subject: str) -> list[dict]:
    g = G.get()
    L = g.label
    out = []
    ids = {n["id"] for n in g.of_type("concept", active_only=False, subject=subject)}
    strands = {st["id"]: st["label"] for st in S.subject_meta(subject).get("strands", [])}
    for n in g.of_type("concept", active_only=False, subject=subject):
        c = n["id"]
        if n.get("strand") in strands:
            out.append(_assoc("isChildOf", c, L(c), _link(f"strand:{subject}/{n['strand']}", strands[n["strand"]]), subject))
        for p, w in g.prereqs.get(c, []):
            if p in ids:
                out.append(_assoc("precedes", p, L(p), _link(c, L(c)), subject,
                                  notes=f"Prerequisite (strength {w}): the origin must be secure before the destination."))
        if n.get("replaced_by"):
            out.append(_assoc("replacedBy", c, L(c), _link(n["replaced_by"], L(n["replaced_by"])), subject))
        for x in g.crosswalk.get(c, []):
            dest = {"title": f"{x['scheme']} {x['id']}", "identifier": x["id"], "uri": f"urn:x-scheme:{x['scheme']}:{x['id']}"}
            kind = "exactMatchOf" if x["match"] == "exactMatch" else "isRelatedTo"
            out.append(_assoc(kind, c, L(c), dest, subject,
                              notes=None if kind == "exactMatchOf" else f"{x['match']} (CASE has no close/broad match type). "
                                                                        "Destination URI is a placeholder until that scheme is published in CASE."))
    seen = set()
    for a, bs in g.related.items():
        if a not in ids:
            continue
        for b in bs:
            k = tuple(sorted((a, b)))
            if k in seen:
                continue
            seen.add(k)
            out.append(_assoc("isRelatedTo", k[0], L(k[0]), _link(k[1], L(k[1])), subject))
    return out


def package(doc_id: str) -> dict:
    subject = next((s for s in S.SUBJECTS if uid(doc_alias(s)) == doc_id), None)
    if not subject:
        raise KeyError(doc_id)
    doc = next(d for d in documents() if d["identifier"] == doc_id)
    return {"CFDocument": doc, "CFItems": items(subject), "CFAssociations": associations(subject),
            "CFDefinitions": {"CFItemTypes": [
                {"identifier": uid("type:Concept"), "title": "Concept", "description": "A topic on the curriculum map"},
                {"identifier": uid("type:Strand"), "title": "Strand", "description": "A group of topics"},
                {"identifier": uid("type:Act"), "title": "Act", "description": "A part of the set text"}]}}


def find_item(item_id: str) -> tuple[dict, str]:
    alias = alias_index().get(item_id)
    if not alias or alias.startswith("doc:"):
        raise KeyError(item_id)
    for s in S.SUBJECTS:
        for it in items(s):
            if it["identifier"] == item_id:
                return it, s
    raise KeyError(item_id)


def item_associations(item_id: str) -> dict:
    it, s = find_item(item_id)
    return {"CFItem": it, "CFAssociations": [a for a in associations(s)
                                             if a["originNodeURI"]["identifier"] == item_id or a["destinationNodeURI"]["identifier"] == item_id]}
