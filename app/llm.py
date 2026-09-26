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
        _client = anthropic.Anthropic(timeout=180.0, max_retries=2)
    return _client


THINKING_HEADROOM = int(os.environ.get("ANTHROPIC_THINKING_HEADROOM", "4000"))


def complete(prompt: str, *, system: str | None = None, max_tokens: int = 800, temperature: float | None = None) -> str:
    """temperature is accepted for call-site compatibility but not sent: current models reject it.
    Output budget includes headroom because the model may think before answering; only text blocks are returned."""
    if not available():
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    kw = dict(model=model(), max_tokens=max_tokens + THINKING_HEADROOM,
              messages=[{"role": "user", "content": prompt}])
    if system:
        kw["system"] = system
    msg = client().messages.create(**kw)
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    if not text and msg.stop_reason == "max_tokens":
        raise RuntimeError("model used its whole output budget before answering")
    return text
