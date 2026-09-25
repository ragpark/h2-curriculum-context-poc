"""Thin Anthropic wrapper. Everything degrades gracefully when ANTHROPIC_API_KEY is not set."""
import os

_client = None


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")


def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(timeout=60.0, max_retries=2)
    return _client


def complete(prompt: str, *, system: str | None = None, max_tokens: int = 800, temperature: float = 0.2) -> str:
    if not available():
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    kw = dict(model=model(), max_tokens=max_tokens, temperature=temperature,
              messages=[{"role": "user", "content": prompt}])
    if system:
        kw["system"] = system
    msg = client().messages.create(**kw)
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
