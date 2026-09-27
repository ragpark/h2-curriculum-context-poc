"""Tutor adapters: the evaluation harness can drive any tutor, not only the built-in stub.

Contract (one method): respond(context, message, subject) -> reply text.
  context  : the briefing (map arm), the raw pack (raw arm) or {pupil:{name,year}} (none arm) — exactly what the
             built-in tutor receives; the tutor may use as much or as little of it as it likes.
  message  : the pupil's message.
  subject  : 'maths' | 'english', so a tutor can pick a persona.

Three kinds:
  builtin  — the stub in app/tutor.py (fixed prompt + Claude).
  webhook  — the harness POSTs {turn_id, subject, arm, pupil, message, context} to a URL you register and expects
             {"reply": "..."} back (JSON, 2xx). Optional shared secret goes in X-Tutor-Secret. 60 s timeout.
  offline  — no network. Export the suite (every scenario × arm with its context) as a JSON file, run your tutor
             on it wherever it lives, upload a file of replies, and the harness scores them with the same judge.

Registered tutors live in the meta table as JSON so they survive restarts; secrets are stored but never returned.
"""
import json
import uuid

import httpx

from . import db
from . import tutor as T

KINDS = ("builtin", "webhook", "offline")
BUILTIN = {"id": "builtin", "kind": "builtin", "label": "Built-in tutor (fixed prompt + Claude)", "builtin": True}
MAX_REPLY = 4000


def _load() -> dict:
    raw = db.meta_get("tutors")
    return json.loads(raw) if raw else {}


def _save(d: dict) -> None:
    db.meta_set("tutors", json.dumps(d))


def public(t: dict) -> dict:
    return {k: v for k, v in t.items() if k != "secret"} | {"has_secret": bool(t.get("secret"))}


def list_tutors() -> list[dict]:
    return [BUILTIN] + [public(t) for t in _load().values()]


def get(tid: str) -> dict:
    if tid == "builtin":
        return BUILTIN
    t = _load().get(tid)
    if not t:
        raise KeyError(f"tutor {tid}")
    return t


def register(label: str, kind: str, url: str | None = None, secret: str | None = None, notes: str | None = None) -> dict:
    if kind not in ("webhook", "offline"):
        raise ValueError("kind must be webhook or offline")
    if kind == "webhook":
        if not url or not url.startswith(("http://", "https://")):
            raise ValueError("a webhook tutor needs an http(s) url")
    tid = "tutor:" + uuid.uuid4().hex[:8]
    t = {"id": tid, "kind": kind, "label": (label or tid).strip()[:80], "url": url if kind == "webhook" else None,
         "secret": secret or None, "notes": (notes or "")[:400]}
    d = _load(); d[tid] = t; _save(d)
    return public(t)


def remove(tid: str) -> None:
    d = _load()
    if tid not in d:
        raise KeyError(tid)
    del d[tid]; _save(d)
    for k in [r["key"] for r in db.q("select key from meta where key like %s", (f"last_tutor_suite_%:{tid}",))]:
        db.ex("delete from meta where key=%s", (k,))


# ------------------------------------------------------------------ dispatch
def respond(tutor: dict, context: dict, message: str, subject: str, *, arm: str = "h2", pupil_id: str = "",
            turn_id: str | None = None) -> str:
    kind = tutor["kind"]
    if kind == "builtin":
        return T.respond(context, message, subject)
    if kind == "webhook":
        return _webhook(tutor, context, message, subject, arm, pupil_id, turn_id or uuid.uuid4().hex[:12])
    raise ValueError("an offline tutor is scored from an uploaded replies file, not called live")


def _webhook(tutor, context, message, subject, arm, pupil_id, turn_id) -> str:
    payload = {"turn_id": turn_id, "subject": subject, "arm": arm, "pupil": pupil_id, "message": message, "context": context}
    headers = {"content-type": "application/json"}
    if tutor.get("secret"):
        headers["X-Tutor-Secret"] = tutor["secret"]
    with httpx.Client(timeout=60.0) as c:
        r = c.post(tutor["url"], json=payload, headers=headers)
    if r.status_code >= 300:
        raise RuntimeError(f"webhook returned HTTP {r.status_code}: {r.text[:120]}")
    try:
        body = r.json()
    except Exception:
        raise RuntimeError("webhook did not return JSON")
    reply = body.get("reply") if isinstance(body, dict) else None
    if not isinstance(reply, str) or not reply.strip():
        raise RuntimeError('webhook JSON must contain a non-empty "reply" string')
    return reply.strip()[:MAX_REPLY]


def ping(tutor: dict) -> dict:
    """One trial call with a minimal context, so a registrant can check wiring before running a suite."""
    if tutor["kind"] != "webhook":
        return {"ok": True, "note": "nothing to ping for this kind"}
    ctx = {"pupil": {"name": "Test pupil (synthetic)", "year": 10}, "note": "ping from the curriculum map evaluation harness"}
    try:
        reply = respond(tutor, ctx, "Hello, can you hear me?", "maths", arm="none", pupil_id="pupil:test", turn_id="ping")
        return {"ok": True, "reply": reply[:300]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
