"""Structural validation of a curriculum map file (the YAML format in seed/).

Structural checks are the ones a machine can make: the graph is well-formed, every reference resolves, nothing is
missing that the tutor logic depends on. They say nothing about whether the map is *right*; that is the reviewer's
job (see the Authoring page). A map with any 'error' is refused; 'warn' findings are reported for the author.

Usage: validate(yaml_text) -> {"ok": bool, "errors": [...], "warnings": [...], "summary": {...}}
"""
import re
from collections import defaultdict

import yaml

ID_RE = re.compile(r"^[a-z]+:[a-z0-9][a-z0-9/_.-]*$")
PREFIX = {"concepts": "cc:", "misconceptions": "mc:", "methods": "md:", "representations": "rp:", "quotations": "q:", "sections": "sec:"}
MIN_KEYWORDS = 3


class Report:
    def __init__(self):
        self.errors, self.warnings = [], []

    def err(self, code, where, msg):
        self.errors.append({"code": code, "where": where, "message": msg})

    def warn(self, code, where, msg):
        self.warnings.append({"code": code, "where": where, "message": msg})


def _full(prefix, sid):
    return sid if (not prefix or ":" in str(sid)) else prefix + str(sid)


def validate(text: str) -> dict:
    r = Report()
    try:
        g = yaml.safe_load(text)
    except yaml.YAMLError as e:
        r.err("yaml", "file", f"not valid YAML: {str(e).splitlines()[0][:120]}")
        return _out(r, {})
    if not isinstance(g, dict):
        r.err("yaml", "file", "the file must be a mapping with keys such as subject, graph_version, concepts")
        return _out(r, {})

    # ---- header
    subject = g.get("subject")
    if not subject or not re.fullmatch(r"[a-z][a-z0-9-]*", str(subject)):
        r.err("header", "subject", "subject is required: a short lower-case word such as 'maths', 'english', 'science'")
    if not g.get("graph_version"):
        r.err("header", "graph_version", "graph_version is required, e.g. \"2026.1\" (quoted, so it stays a string)")
    layout = g.get("layout", "layers")
    if layout not in ("layers", "strands"):
        r.err("header", "layout", "layout must be 'layers' (a ladder of prerequisites) or 'strands' (a web grouped into strands)")
    prefix = g.get("id_prefix")  # optional: short concept IDs are expanded with this
    strands = {s.get("id"): s for s in (g.get("strands") or []) if isinstance(s, dict)}
    if layout == "strands" and not strands:
        r.err("header", "strands", "layout 'strands' needs a strands list, e.g. [{id: context, label: Context}, ...]")

    # ---- collect nodes
    nodes: dict[str, dict] = {}
    by_type: dict[str, list] = defaultdict(list)

    def add(kind, n, i):
        where = f"{kind}[{i}]"
        if not isinstance(n, dict) or not n.get("id"):
            r.err("node", where, "every entry needs an id"); return None
        nid = _full(prefix, n["id"]) if kind == "concepts" else str(n["id"])
        if not ID_RE.match(nid):
            r.err("id", nid, "ids are lower-case, prefix:path, e.g. cc:maths/alg/notation, mc:maths/sign-error; no spaces or capitals")
        elif not nid.startswith(PREFIX[kind]):
            r.err("id", nid, f"{kind} ids start with '{PREFIX[kind]}'")
        if nid in nodes:
            r.err("id", nid, "duplicate id"); return None
        label = n.get("label") if kind != "quotations" else n.get("text")
        if not label or not str(label).strip():
            r.err("node", nid, "label is required" if kind != "quotations" else "text is required")
        nodes[nid] = n | {"_kind": kind, "_id": nid}
        by_type[kind].append(nid)
        return nid

    for kind in PREFIX:
        for i, n in enumerate(g.get(kind) or []):
            add(kind, n, i)
    if not by_type["concepts"]:
        r.err("node", "concepts", "a map needs at least one concept")

    ids = set(nodes)
    concepts = set(by_type["concepts"])

    def ref(sid, where, expect="concepts"):
        """Resolve a reference; concept refs may be short."""
        full = _full(prefix, sid) if expect == "concepts" else str(sid)
        if full not in ids:
            r.err("ref", where, f"refers to '{sid}', which is not defined in {expect}")
            return None
        if nodes[full]["_kind"] != expect:
            r.err("ref", where, f"'{sid}' is a {nodes[full]['_kind'][:-1]}, not a {expect[:-1]}")
            return None
        return full

    # ---- per-node content checks
    kw_index: dict[str, list] = defaultdict(list)
    for nid, n in nodes.items():
        k = n["_kind"]
        if k in ("concepts", "misconceptions", "methods", "representations"):
            kws = [str(x).strip().lower() for x in (n.get("keywords") or []) if str(x).strip()]
            if len(kws) < MIN_KEYWORDS:
                (r.warn if k != "concepts" else r.err)("keywords", nid, f"needs at least {MIN_KEYWORDS} keywords (the tagger matches on them); has {len(kws)}")
            for kw in kws:
                kw_index[(k, kw)].append(nid)
        if k == "misconceptions":
            if not n.get("description"):
                r.err("content", nid, "misconceptions need a description: what the pupil does and why it is wrong")
            aff = n.get("affects") or []
            if not aff:
                r.err("ref", nid, "a misconception must affect at least one concept")
            for a in aff:
                ref(a, f"{nid}.affects")
        if k == "methods":
            t = n.get("teaches") or []
            if not t:
                r.err("ref", nid, "a method must teach at least one concept")
            for c in t:
                ref(c, f"{nid}.teaches")
        if k == "concepts":
            if layout == "strands":
                if not n.get("strand"):
                    r.err("strand", nid, "in a strands layout every concept needs a strand")
                elif n["strand"] not in strands:
                    r.err("strand", nid, f"strand '{n['strand']}' is not in the strands list")
            if len(str(n.get("label", ""))) > 80:
                r.warn("content", nid, "label is over 80 characters; the map shows it in a small box")
        if k == "quotations":
            for c in n.get("evidences") or []:
                ref(c, f"{nid}.evidences")
            if not n.get("act"):
                r.warn("content", nid, "quotation has no act (e.g. \"2.2\"), so it cannot be placed in a section")
            elif by_type["sections"]:
                acts = {str(s.get("order")) for s in (g.get("sections") or []) if isinstance(s, dict)}
                if str(n["act"]).split(".")[0] not in acts:
                    r.err("ref", nid, f"act {n['act']} does not match any section order")
        if k == "sections" and not isinstance(n.get("order"), int):
            r.err("content", nid, "sections need an integer 'order'")

    for (k, kw), owners in kw_index.items():
        if len(owners) > 1:
            r.warn("keywords", kw, f"keyword shared by {len(owners)} {k}: {', '.join(owners[:4])}. Shared keywords weaken tagging.")

    # ---- structure
    prereq = defaultdict(list)
    for i, e in enumerate(g.get("prerequisites") or []):
        where = f"prerequisites[{i}]"
        if not (isinstance(e, list) and len(e) in (2, 3)):
            r.err("edge", where, "a prerequisite is [before, after, weight] with weight 0–1 (weight optional)"); continue
        a, b = ref(e[0], where), ref(e[1], where)
        w = e[2] if len(e) == 3 else 1.0
        if not isinstance(w, (int, float)) or not 0 < w <= 1:
            r.err("edge", where, f"weight must be a number in (0, 1]; got {w!r}")
        if a and b:
            if a == b:
                r.err("edge", where, "a concept cannot be its own prerequisite")
            prereq[b].append(a)
    related = []
    for i, e in enumerate(g.get("related") or []):
        where = f"related[{i}]"
        if not (isinstance(e, list) and len(e) == 2):
            r.err("edge", where, "a related link is [a, b]"); continue
        a, b = ref(e[0], where), ref(e[1], where)
        if a and b:
            if a == b:
                r.err("edge", where, "a concept cannot relate to itself")
            related.append((a, b))
    for i, e in enumerate(g.get("crosswalk") or []):
        where = f"crosswalk[{i}]"
        if not (isinstance(e, list) and len(e) == 4):
            r.err("edge", where, "a crosswalk row is [concept, scheme, external_id, match]"); continue
        ref(e[0], where)
        if e[3] not in ("exactMatch", "closeMatch", "broadMatch", "narrowMatch", "relatedMatch"):
            r.err("edge", where, f"match must be a SKOS mapping type (exactMatch, closeMatch, broadMatch, narrowMatch, relatedMatch); got {e[3]!r}")

    # cycles (prerequisites must be a DAG)
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {c: WHITE for c in concepts}
    cycle = []

    def dfs(c, path):
        colour[c] = GREY
        for p in prereq.get(c, []):
            if colour.get(p) == GREY:
                cycle.append(path + [c, p]); return True
            if colour.get(p) == WHITE and dfs(p, path + [c]):
                return True
        colour[c] = BLACK
        return False

    for c in concepts:
        if colour[c] == WHITE and dfs(c, []):
            break
    if cycle:
        cyc = cycle[0]
        start = cyc.index(cyc[-1])
        r.err("cycle", " → ".join(cyc[start:]), "prerequisites form a cycle; a topic cannot depend on itself")

    # orphans and connectivity
    linked = set()
    for b, ps in prereq.items():
        linked.add(b); linked.update(ps)
    for a, b in related:
        linked.update((a, b))
    for nid, n in nodes.items():
        if n["_kind"] == "misconceptions":
            linked.update(_full(prefix, x) for x in (n.get("affects") or []))
        if n["_kind"] == "methods":
            linked.update(_full(prefix, x) for x in (n.get("teaches") or []))
        if n["_kind"] == "quotations":
            linked.update(_full(prefix, x) for x in (n.get("evidences") or []))
    for c in sorted(concepts - linked):
        r.warn("orphan", c, "concept has no prerequisite, related, misconception, method or quotation links; the tutor can do little with it")
    no_mc = sorted(c for c in concepts if not any(_full(prefix, a) == c for m in by_type["misconceptions"] for a in (nodes[m].get("affects") or [])))
    if concepts and len(no_mc) / len(concepts) > 0.6:
        r.warn("coverage", "misconceptions", f"{len(no_mc)} of {len(concepts)} concepts have no misconception attached; diagnosis will be weak on them")
    no_md = sorted(c for c in concepts if not any(_full(prefix, t) == c for m in by_type["methods"] for t in (nodes[m].get("teaches") or [])))
    if concepts and len(no_md) / len(concepts) > 0.6:
        r.warn("coverage", "methods", f"{len(no_md)} of {len(concepts)} concepts have no method attached; the briefing cannot say how this teacher teaches them")
    if layout == "layers" and concepts and not prereq:
        r.warn("structure", "prerequisites", "a layers map with no prerequisites is just a list; consider 'strands' with related links, or add prerequisites")
    if layout == "strands" and not related and not prereq:
        r.warn("structure", "related", "a strands map with no related or prerequisite links gives the tutor no structure to reason over")

    summary = {"subject": subject, "graph_version": g.get("graph_version"), "layout": layout,
               **{k: len(v) for k, v in by_type.items()},
               "prerequisites": sum(len(v) for v in prereq.values()), "related": len(related),
               "crosswalk": len(g.get("crosswalk") or [])}
    return _out(r, summary)


def _out(r, summary):
    return {"ok": not r.errors, "errors": r.errors, "warnings": r.warnings, "summary": summary,
            "counts": {"errors": len(r.errors), "warnings": len(r.warnings)}}
