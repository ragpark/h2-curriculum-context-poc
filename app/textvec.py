"""Lightweight text utilities. The embedding is a hashed bag of words + bigrams — deliberately simple so the
POC has no model download. Swap for a real embedding model (e.g. via Azure AI Search / Voyage) in production."""
import hashlib
import math
import re

DIM = 512
_STOP = set("a an the and or of to in on for is are be by with as at it this that we you our your then so if each every from into".split())


def normalise(text: str) -> str:
    t = (text or "").lower().replace("−", "-").replace("–", "-").replace("—", " ").replace("’", "'")
    t = t.replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t)


def tokens(text: str) -> list[str]:
    toks = re.findall(r"[a-z]+|\d+", normalise(text))
    return [_stem(t) for t in toks if t not in _STOP]


def _stem(t: str) -> str:
    for suf in ("ing", "es", "s"):
        if len(t) > 4 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def embed(text: str) -> list[float]:
    toks = tokens(text)
    feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
    v = [0.0] * DIM
    for f in feats:
        h = int(hashlib.md5(f.encode()).hexdigest(), 16)
        v[h % DIM] += 1.0 if (h >> 12) & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [round(x / n, 5) for x in v]


def cosine(a, b) -> float:
    if not a or not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def phrase_in(phrase: str, text_norm: str, text_toks: set) -> bool:
    p = normalise(phrase).strip()
    if not p:
        return False
    if " " in p or not re.fullmatch(r"[a-z]+", p):
        return p in text_norm
    return _stem(p) in text_toks
