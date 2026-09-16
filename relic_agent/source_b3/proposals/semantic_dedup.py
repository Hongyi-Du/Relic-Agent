"""Source HCI semantic proposal/tool de-duplication helper."""
from __future__ import annotations

import re
from typing import Any, Optional, Sequence


_WORD = re.compile(r"[a-z0-9_]+")
_STOP = {
    "the", "a", "an", "to", "of", "and", "or", "for", "with", "is", "are", "be",
    "that", "this", "it", "its", "on", "in", "by", "as", "at", "we", "our", "us",
    "you", "your", "they", "their", "add", "added", "adds", "adding", "change",
    "changed", "changes", "update", "updated", "updates", "make", "made", "makes", "so",
    "not", "no", "into", "from", "more", "less", "than", "then", "if",
}
DUP_HI = 0.80
DUP_LO = 0.45


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(_as_text(item) for item in value)
    return value if isinstance(value, str) else str(value)


def normalize(text: str) -> str:
    return " ".join(_WORD.findall(_as_text(text).lower()))


def tokens(texts: Sequence[Any]) -> set:
    out = set()
    for text in texts:
        for word in _WORD.findall(_as_text(text).lower()):
            if len(word) > 2 and word not in _STOP:
                out.add(word)
    return out


def jaccard(first: set, second: set) -> float:
    if not first and not second:
        return 1.0
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def _cache(world: Any) -> dict:
    if world is None:
        return {}
    return world.__dict__.setdefault("_sem_dedup_cache", {})


def equivalent(
    world: Any,
    a_texts: Sequence[str],
    b_texts: Sequence[str],
    *,
    kind: str,
    client: Optional[Any] = None,
    lo: float = DUP_LO,
    hi: float = DUP_HI,
    criterion: Optional[str] = None,
) -> bool:
    first, second = tokens(a_texts), tokens(b_texts)
    overlap = jaccard(first, second)
    if overlap >= hi:
        return True
    if overlap < lo:
        return False
    client = client if client is not None else getattr(world, "llm_client", None)
    if client is None:
        return False
    key = (
        "eq",
        kind,
        criterion or "",
        normalize(" ".join(_as_text(item) for item in a_texts))[:240],
        normalize(" ".join(_as_text(item) for item in b_texts))[:240],
    )
    cache = _cache(world)
    if key in cache:
        return cache[key]
    result = _llm_same(client, a_texts, b_texts, kind, criterion)
    cache[key] = result
    return result


def _llm_same(
    client: Any,
    a_texts: Sequence[str],
    b_texts: Sequence[str],
    kind: str,
    criterion: Optional[str] = None,
) -> bool:
    rule = criterion or (
        f"two {kind} descriptions are the SAME underlying item — same intent and effect — "
        f"versus merely related or adjacent; be strict and only answer true when one is "
        f"redundant given the other"
    )
    system = f"You decide whether {rule}. Return JSON only."
    user = (
        "A:\n"
        + " | ".join(item for item in (_as_text(value) for value in a_texts) if item)[:600]
        + "\n\nB:\n"
        + " | ".join(item for item in (_as_text(value) for value in b_texts) if item)[:600]
        + f"\n\nAre A and B the same {kind}? Return {{\"same\": true|false}}."
    )
    try:
        data = client.generate_json(system, user, {"same": "bool"})
        return bool(isinstance(data, dict) and data.get("same"))
    except Exception:
        return False


__all__ = ["equivalent", "jaccard", "tokens", "normalize", "DUP_HI", "DUP_LO"]
