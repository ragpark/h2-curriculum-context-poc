"""In-memory view of the reference graph, loaded from Postgres and cached until a release/reset."""
from collections import defaultdict
from functools import lru_cache

from . import db

NODE_TYPES = ("concept", "misconception", "method", "representation")
REL_PREREQ = "prerequisiteOf"   # src is a prerequisite of dst
REL_AFFECTS = "affects"         # misconception -> concept
REL_TEACHES = "teaches"         # method -> concept


class Graph:
    def __init__(self):
        rows = db.q("select * from node")
        self.version = db.meta_get("graph_version", "?")
        self.nodes = {r["id"]: r for r in rows}
        self.prereqs = defaultdict(list)    # concept -> [(prereq, w)]
        self.dependents = defaultdict(list)  # concept -> [(next, w)]
        self.affects = defaultdict(list)    # misconception -> [concept]
        self.affected_by = defaultdict(list)  # concept -> [misconception]
        self.teaches = defaultdict(list)    # method -> [concept]
        self.taught_by = defaultdict(list)  # concept -> [method]
        for e in db.q("select * from edge"):
            s, d, w = e["src"], e["dst"], e["weight"]
            if e["rel"] == REL_PREREQ:
                self.prereqs[d].append((s, w)); self.dependents[s].append((d, w))
            elif e["rel"] == REL_AFFECTS:
                self.affects[s].append(d); self.affected_by[d].append(s)
            elif e["rel"] == REL_TEACHES:
                self.teaches[s].append(d); self.taught_by[d].append(s)
        self.crosswalk = defaultdict(list)
        for c in db.q("select * from crosswalk order by scheme, external_id"):
            self.crosswalk[c["concept"]].append({"scheme": c["scheme"], "id": c["external_id"], "match": c["match"]})

    # ---- lookups -------------------------------------------------------
    def of_type(self, t, active_only=True):
        return [n for n in self.nodes.values() if n["type"] == t and (not active_only or n["status"] == "active")]

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

    def layers(self):
        """Longest-path depth for concept layout (left→right by prerequisite depth)."""
        concepts = [n["id"] for n in self.of_type("concept")]
        memo = {}

        def depth(c, stack=()):
            if c in memo:
                return memo[c]
            ps = [p for p, _ in self.prereqs.get(c, []) if p in self.nodes and self.nodes[p]["status"] == "active" and p not in stack]
            memo[c] = 0 if not ps else 1 + max(depth(p, stack + (c,)) for p in ps)
            return memo[c]

        return {c: depth(c) for c in concepts}

    def to_json(self):
        layers = self.layers()
        return {
            "version": self.version,
            "concepts": [{**_pub(n), "layer": layers.get(n["id"], 0), "crosswalk": self.crosswalk.get(n["id"], [])}
                         for n in self.of_type("concept", active_only=False)],
            "misconceptions": [{**_pub(n), "affects": self.affects.get(n["id"], [])} for n in self.of_type("misconception")],
            "methods": [{**_pub(n), "teaches": self.teaches.get(n["id"], [])} for n in self.of_type("method")],
            "representations": [_pub(n) for n in self.of_type("representation")],
            "prerequisites": [{"src": p, "dst": c, "weight": w} for c, ps in self.prereqs.items() for p, w in ps],
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
                misconceptions=[self.ref(m, description=self.nodes[m].get("description")) for m in self.affected_by.get(nid, [])],
                methods=[self.ref(m) for m in self.taught_by.get(nid, [])],
                crosswalk=self.crosswalk.get(nid, []),
                ancestors=self.ancestors(nid),
            )
        elif n["type"] == "misconception":
            d.update(affects=[self.ref(c) for c in self.affects.get(nid, [])])
        elif n["type"] == "method":
            d.update(teaches=[self.ref(c) for c in self.teaches.get(nid, [])])
        return d


def _pub(n):
    return {k: n.get(k) for k in ("id", "type", "label", "description", "key_stage", "keywords", "graph_version", "status", "replaced_by")}


_cache: dict = {}


def get() -> Graph:
    if "g" not in _cache:
        _cache["g"] = Graph()
    return _cache["g"]


def invalidate():
    _cache.clear()
