"""In-memory view of the reference graph, loaded from Postgres and cached until a release/reset.

One graph store holds several subjects. Every node carries a `subject`; lookups are scoped by subject.
Maths is a ladder (prerequisites); English is a web ('relatesTo' links between knowledge topics, with only the
writing skills forming a ladder), plus quotations that 'evidence' topics and sections (acts) of the text.
"""
from collections import defaultdict

from . import db

NODE_TYPES = ("concept", "misconception", "method", "representation", "quotation", "section")
REL_PREREQ = "prerequisiteOf"   # src is a prerequisite of dst
REL_AFFECTS = "affects"         # misconception -> concept
REL_TEACHES = "teaches"         # method -> concept
REL_RELATED = "relatesTo"       # concept <-> concept (undirected; stored once)
REL_EVIDENCES = "evidences"     # quotation -> concept
REL_IN_SECTION = "inSection"    # quotation -> section


class Graph:
    def __init__(self):
        rows = db.q("select * from node")
        self.version = db.meta_get("graph_version", "?")  # maths (the subject the release demo acts on)
        self.nodes = {r["id"]: r for r in rows}
        self.prereqs = defaultdict(list)    # concept -> [(prereq, w)]
        self.dependents = defaultdict(list)  # concept -> [(next, w)]
        self.affects = defaultdict(list)    # misconception -> [concept]
        self.affected_by = defaultdict(list)  # concept -> [misconception]
        self.teaches = defaultdict(list)    # method -> [concept]
        self.taught_by = defaultdict(list)  # concept -> [method]
        self.related = defaultdict(list)    # concept -> [concept]
        self.evidences = defaultdict(list)  # quotation -> [concept]
        self.evidenced_by = defaultdict(list)  # concept -> [quotation]
        self.section_of = {}                # quotation -> section
        for e in db.q("select * from edge"):
            s, d, w = e["src"], e["dst"], e["weight"]
            if e["rel"] == REL_PREREQ:
                self.prereqs[d].append((s, w)); self.dependents[s].append((d, w))
            elif e["rel"] == REL_AFFECTS:
                self.affects[s].append(d); self.affected_by[d].append(s)
            elif e["rel"] == REL_TEACHES:
                self.teaches[s].append(d); self.taught_by[d].append(s)
            elif e["rel"] == REL_RELATED:
                self.related[s].append(d); self.related[d].append(s)
            elif e["rel"] == REL_EVIDENCES:
                self.evidences[s].append(d); self.evidenced_by[d].append(s)
            elif e["rel"] == REL_IN_SECTION:
                self.section_of[s] = d
        self.crosswalk = defaultdict(list)
        for c in db.q("select * from crosswalk order by scheme, external_id"):
            self.crosswalk[c["concept"]].append({"scheme": c["scheme"], "id": c["external_id"], "match": c["match"]})

    # ---- lookups -------------------------------------------------------
    def of_type(self, t, active_only=True, subject="maths"):
        return [n for n in self.nodes.values() if n["type"] == t and (not active_only or n["status"] == "active")
                and (subject is None or n.get("subject", "maths") == subject)]

    def subject_of(self, nid):
        n = self.nodes.get(nid)
        return n.get("subject", "maths") if n else None

    def version_for(self, subject="maths"):
        return self.version if subject == "maths" else db.meta_get(f"graph_version:{subject}", "?")

    def label(self, nid):
        n = self.nodes.get(nid)
        return n["label"] if n else nid

    def resolve(self, nid):
        """Follow replacedBy chains so evidence/alignments tagged against older versions still land."""
        seen = set()
        while nid in self.nodes and self.nodes[nid].get("replaced_by") and nid not in seen:
            seen.add(nid)
            nid = self.nodes[nid]["replaced_by"]
        return nid

    def ref(self, nid, **extra):
        return {"id": nid, "label": self.label(nid), **extra}

    def strand(self, nid):
        n = self.nodes.get(nid)
        return n.get("strand") if n else None

    def ancestors(self, cid, depth=6):
        out, frontier, seen = [], [(cid, 0)], {cid}
        while frontier:
            c, d = frontier.pop(0)
            if d >= depth:
                continue
            for p, w in self.prereqs.get(c, []):
                if p not in seen:
                    seen.add(p); out.append({"id": p, "label": self.label(p), "depth": d + 1, "strength": w})
                    frontier.append((p, d + 1))
        return out

    def layers(self, subject="maths"):
        """Longest-path depth for concept layout (left→right by prerequisite depth)."""
        concepts = [n["id"] for n in self.of_type("concept", subject=subject)]
        memo = {}

        def depth(c, stack=()):
            if c in memo:
                return memo[c]
            ps = [p for p, _ in self.prereqs.get(c, []) if p in self.nodes and self.nodes[p]["status"] == "active" and p not in stack]
            memo[c] = 0 if not ps else 1 + max(depth(p, stack + (c,)) for p in ps)
            return memo[c]

        return {c: depth(c) for c in concepts}

    def quote(self, qid):
        n = self.nodes[qid]
        x = n.get("extra") or {}
        sec = self.section_of.get(qid)
        return {"id": qid, "text": n["label"], "speaker": x.get("speaker"), "act": x.get("act"),
                "section": self.ref(sec) if sec else None}

    def sections(self, subject):
        return sorted(self.of_type("section", subject=subject), key=lambda n: (n.get("extra") or {}).get("order", 0))

    def to_json(self, subject="maths"):
        from . import seed as S  # subject metadata lives with the seed definitions
        layers = self.layers(subject)
        meta = S.subject_meta(subject) if subject in S.SUBJECTS else {}
        concepts = self.of_type("concept", active_only=False, subject=subject)
        ids = {n["id"] for n in concepts}
        return {
            "subject": subject, "title": meta.get("label"), "layout": meta.get("layout", "layers"),
            "strands": meta.get("strands", []),
            "version": self.version_for(subject),
            "concepts": [{**_pub(n), "layer": layers.get(n["id"], 0), "crosswalk": self.crosswalk.get(n["id"], [])}
                         for n in concepts],
            "misconceptions": [{**_pub(n), "affects": self.affects.get(n["id"], [])} for n in self.of_type("misconception", subject=subject)],
            "methods": [{**_pub(n), "teaches": self.teaches.get(n["id"], [])} for n in self.of_type("method", subject=subject)],
            "representations": [_pub(n) for n in self.of_type("representation", subject=subject)],
            "prerequisites": [{"src": p, "dst": c, "weight": w} for c, ps in self.prereqs.items() if c in ids for p, w in ps],
            "related": sorted({tuple(sorted((a, b))) for a, bs in self.related.items() if a in ids for b in bs}),
            "quotations": [self.quote(n["id"]) | {"evidences": self.evidences.get(n["id"], [])} for n in self.of_type("quotation", subject=subject)],
            "sections": [_pub(n) for n in self.sections(subject)],
        }

    def node_detail(self, nid):
        n = self.nodes.get(nid)
        if not n:
            return None
        d = _pub(n)
        if n["type"] == "concept":
            d.update(
                prerequisites=[self.ref(p, strength=w) for p, w in self.prereqs.get(nid, [])],
                leads_to=[self.ref(c, strength=w) for c, w in self.dependents.get(nid, [])],
                related=[self.ref(c) for c in self.related.get(nid, [])],
                quotations=[self.quote(q) for q in self.evidenced_by.get(nid, [])],
                misconceptions=[self.ref(m, description=self.nodes[m].get("description")) for m in self.affected_by.get(nid, [])],
                methods=[self.ref(m) for m in self.taught_by.get(nid, [])],
                crosswalk=self.crosswalk.get(nid, []),
                ancestors=self.ancestors(nid),
            )
        elif n["type"] == "misconception":
            d.update(affects=[self.ref(c) for c in self.affects.get(nid, [])])
        elif n["type"] == "method":
            d.update(teaches=[self.ref(c) for c in self.teaches.get(nid, [])])
        elif n["type"] == "quotation":
            d.update(**self.quote(nid), evidences=[self.ref(c) for c in self.evidences.get(nid, [])])
        return d


def _pub(n):
    return {k: n.get(k) for k in ("id", "type", "label", "description", "key_stage", "keywords", "graph_version", "status",
                                  "replaced_by", "subject", "strand")}


_cache: dict = {}


def get() -> Graph:
    if "g" not in _cache:
        _cache["g"] = Graph()
    return _cache["g"]


def invalidate():
    _cache.clear()
