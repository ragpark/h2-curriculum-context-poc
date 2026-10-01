"""Scheme of work: a teacher's declared plan for a class, uploaded as CSV, tagged week by week against the map, and
reconciled with what the class's materials already say.

A scheme is school data, scoped to one class. It is held in its own table and never merged into the reference graph.
Where it is used:
  • coverage(): a declared week corroborates 'planned' topics that no material yet exists for, and supplies the
    acts a class will reach (English), so the briefing's 'not yet taught' reflects the plan, not only the materials.
  • reconciliation: for each week, the scheme's tags are compared with the materials' tags, so a teacher can see where
    the plan and the classroom record disagree before accepting the upload.

CSV columns (header row required, any order; extra columns ignored):
  class, week, title, learning_objectives, key_content, teaching_approach, resources, assessment
Only 'week' and one of title / learning_objectives / key_content are needed to tag a row.
"""
import csv
import io

from . import align as A
from . import db
from . import graph as G
from . import seed as S

TEXT_COLS = ("title", "learning_objectives", "key_content", "teaching_approach", "resources", "assessment")
CONF_MIN = 0.5


def parse_csv(text: str, class_id: str | None = None) -> list[dict]:
    text = text.lstrip("﻿")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError("the file has no rows")
    cols = {c.strip().lower(): c for c in rows[0].keys() if c}
    if "week" not in cols:
        raise ValueError("the CSV needs a 'week' column (and ideally title, learning_objectives, key_content, teaching_approach)")
    out = []
    for i, r in enumerate(rows, start=2):
        g = lambda k: (r.get(cols[k]) or "").strip() if k in cols else ""
        cid = (g("class") or class_id or "").strip()
        if not cid:
            raise ValueError(f"row {i}: no class; add a 'class' column or choose the class when uploading")
        try:
            wk = int(g("week"))
        except ValueError:
            raise ValueError(f"row {i}: week must be a whole number, got {g('week')!r}")
        body = {k: g(k) for k in TEXT_COLS}
        if not any(body[k] for k in ("title", "learning_objectives", "key_content")):
            raise ValueError(f"row {i}: nothing to tag (title, learning_objectives and key_content are all empty)")
        out.append({"class": cid, "week": wk, **body})
    classes = {r["class"] for r in out}
    if len(classes) > 1:
        raise ValueError(f"one class per upload; this file has {sorted(classes)}")
    known = {c["id"] for c in db.q("select id from class")}
    if out[0]["class"] not in known:
        raise ValueError(f"unknown class {out[0]['class']!r}; known classes: {sorted(known)}")
    return out


def _tag_text(r: dict) -> str:
    # The teaching_approach column names the method; key_content names topics and acts; objectives carry skills.
    return "\n".join(x for x in (r["title"], r["learning_objectives"], r["key_content"], r["teaching_approach"]) if x)


def tag_rows(rows: list[dict], mode: str | None = None) -> list[dict]:
    subject = S.subject_of_class(rows[0]["class"])
    out = []
    for r in rows:
        res = A.align(_tag_text(r), mode, subject)
        out.append({**r, "tags": {
            "concepts": [{"id": c["id"], "label": c["label"], "confidence": c["confidence"]} for c in res["concepts"]],
            "method": ({"id": res["method"]["id"], "label": res["method"]["label"], "confidence": res["method"]["confidence"]} if res.get("method") else None),
            "sections": [{"id": s["id"], "label": s["label"]} for s in res.get("sections", [])],
            "provenance": res["provenance"], "graph_version": res["graph_version"]}})
    return out


def reconcile(class_id: str, tagged: list[dict]) -> list[dict]:
    """Per week: do the plan's tags agree with the materials' tags?"""
    from . import content as C
    g = G.get()
    mats = C.materials(class_id)
    by_week: dict[int, dict] = {}
    for m in mats:
        w = by_week.setdefault(m["week"], {"concepts": set(), "methods": set(), "sections": set(), "titles": []})
        w["titles"].append(m["title"])
        for u in m["units"]:
            for a in u["alignments"]:
                if a["confidence"] < CONF_MIN:
                    continue
                if a["facet"] == "concept":
                    w["concepts"].add(g.resolve(a["id"]))
                elif a["facet"] == "method":
                    w["methods"].add(a["id"])
                elif a["facet"] == "section":
                    w["sections"].add(a["id"])
    out = []
    for r in tagged:
        m = by_week.get(r["week"])
        plan_c = {c["id"] for c in r["tags"]["concepts"] if c["confidence"] >= CONF_MIN}
        plan_m = r["tags"]["method"]["id"] if r["tags"]["method"] and r["tags"]["method"]["confidence"] >= CONF_MIN else None
        plan_s = {s["id"] for s in r["tags"]["sections"]}
        if not m:
            status, detail = "plan_only", "no materials for this week yet; the plan will count as 'planned' until materials arrive"
        else:
            both = plan_c & m["concepts"]
            only_plan = plan_c - m["concepts"]
            only_mats = m["concepts"] - plan_c
            method_clash = bool(plan_m and m["methods"] and plan_m not in m["methods"])
            if method_clash:
                status = "method_differs"
                detail = f"plan says {g.label(plan_m)}; materials use {', '.join(g.label(x) for x in m['methods'])}"
            elif both and not only_plan and not only_mats:
                status, detail = "agree", "plan and materials name the same topics"
            elif both:
                status = "partly"
                detail = "plan adds " + (", ".join(g.label(x) for x in only_plan) or "nothing") + "; materials add " + (", ".join(g.label(x) for x in only_mats) or "nothing")
            elif not plan_c:
                status, detail = "untagged", "nothing on the map was recognised in this row; check the wording"
            else:
                status = "differ"
                detail = "plan: " + ", ".join(g.label(x) for x in plan_c) + " · materials: " + (", ".join(g.label(x) for x in m["concepts"]) or "no topics tagged")
            if plan_s - m["sections"] and status in ("agree", "partly"):
                detail += " · plan also reaches " + ", ".join(g.label(x) for x in sorted(plan_s - m["sections"]))
        out.append({"week": r["week"], "title": r["title"], "status": status, "detail": detail,
                    "materials": m["titles"] if m else [], "plan_concepts": sorted(plan_c), "plan_method": plan_m, "plan_sections": sorted(plan_s)})
    # weeks that have materials but no plan row
    planned_weeks = {r["week"] for r in tagged}
    for w, m in sorted(by_week.items()):
        if w not in planned_weeks:
            out.append({"week": w, "title": None, "status": "materials_only", "detail": "materials exist for a week the plan does not mention",
                        "materials": m["titles"], "plan_concepts": [], "plan_method": None, "plan_sections": []})
    out.sort(key=lambda x: x["week"])
    return out


def preview(text: str, class_id: str | None = None, mode: str | None = None) -> dict:
    rows = parse_csv(text, class_id)
    tagged = tag_rows(rows, mode)
    cid = rows[0]["class"]
    return {"class": cid, "weeks": tagged, "reconciliation": reconcile(cid, tagged),
            "subject": S.subject_of_class(cid)}


def accept(class_id: str, tagged: list[dict], source_name: str | None = None) -> dict:
    """Store the tagged scheme for the class (replacing any previous one)."""
    db.ex("delete from scheme_week where class=%s", (class_id,))
    for r in tagged:
        db.ex("""insert into scheme_week(class,week,title,objectives,key_content,approach,resources,assessment,concepts,method,sections,provenance,graph_version,source)
                 values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
              (class_id, r["week"], r["title"], r["learning_objectives"], r["key_content"], r["teaching_approach"], r["resources"], r["assessment"],
               db.J([c for c in r["tags"]["concepts"] if c["confidence"] >= CONF_MIN]), r["tags"]["method"]["id"] if r["tags"]["method"] else None,
               db.J([s["id"] for s in r["tags"]["sections"]]), r["tags"]["provenance"], r["tags"]["graph_version"], source_name))
    return get(class_id)


def clear(class_id: str) -> None:
    db.ex("delete from scheme_week where class=%s", (class_id,))


def get(class_id: str) -> dict:
    g = G.get()
    rows = db.q("select * from scheme_week where class=%s order by week", (class_id,))
    return {"class": class_id, "source": rows[0]["source"] if rows else None,
            "weeks": [{"week": r["week"], "title": r["title"], "objectives": r["objectives"], "key_content": r["key_content"],
                       "approach": r["approach"], "resources": r["resources"], "assessment": r["assessment"],
                       "concepts": [{**c, "label": g.label(c["id"])} for c in r["concepts"]],
                       "method": g.ref(r["method"]) if r["method"] else None,
                       "sections": [g.ref(s) for s in r["sections"]], "provenance": r["provenance"]} for r in rows]}


def declared(class_id: str) -> dict:
    """What the plan says, for coverage(): {concept: first week} and {section: first week}."""
    g = G.get()
    cw, sw = {}, {}
    for r in db.q("select week, concepts, sections from scheme_week where class=%s order by week", (class_id,)):
        for c in r["concepts"]:
            cw.setdefault(g.resolve(c["id"]), r["week"])
        for s in r["sections"]:
            sw.setdefault(s, r["week"])
    return {"concepts": cw, "sections": sw}
