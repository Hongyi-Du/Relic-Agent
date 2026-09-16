"""The public ``relic-trace-v1`` contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from relic_agent.core.hashing import canonical_sha256

TRACE_SCHEMA_VERSION = "relic-trace-v1"


class TraceError(ValueError):
    pass


def build_trace(
    *,
    run_id: str,
    organization_id: str,
    config_digest: str,
    frames: list[dict[str, Any]],
) -> dict[str, Any]:
    trace = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "run_id": run_id,
        "organization_id": organization_id,
        "config_sha256": config_digest,
        "privacy": {
            "private_reflections_included": False,
            "private_memories_included": False,
            "provider_messages_included": False,
        },
        "frames": frames,
    }
    trace["trace_sha256"] = canonical_sha256(trace)
    return trace


def load_trace(path: str | Path) -> dict[str, Any]:
    trace_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TraceError(f"cannot load trace {trace_path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != TRACE_SCHEMA_VERSION:
        raise TraceError(f"trace must use {TRACE_SCHEMA_VERSION}")
    expected = payload.get("trace_sha256")
    unhashed = dict(payload)
    unhashed.pop("trace_sha256", None)
    actual = canonical_sha256(unhashed)
    if expected != actual:
        raise TraceError("trace digest mismatch")
    frames = payload.get("frames")
    if not isinstance(frames, list) or not frames:
        raise TraceError("trace frames must be a non-empty list")
    last_tick = -1
    for frame in frames:
        if not isinstance(frame, dict) or not isinstance(frame.get("tick"), int):
            raise TraceError("each frame must contain an integer tick")
        if frame["tick"] <= last_tick:
            raise TraceError("trace ticks must be strictly increasing")
        last_tick = frame["tick"]
    return payload
