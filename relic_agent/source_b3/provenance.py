"""Provenance and deterministic dependency slice for source B3 ports.

``stable_fingerprint`` is the exact dependency used by the HCI protocol
registry.  The full source module also contains paper-run record helpers whose
transitive evaluator dependencies are intentionally outside Relic Agent's
release boundary, so only this closed standard-library slice is carried here.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Any


SOURCE_B3_REPOSITORY = "Hongyi-Du/SocioGenesis"
SOURCE_B3_COMMIT = "dda36fb563375060ae8d8850300db01eb4695d29"
SOURCE_B3_PROTOCOL_SOURCE_PATH = "environments/org_env/backend/protocol"
SOURCE_B3_PROTOCOL_FILE_BLOBS: dict[str, str] = {
    "environments/org_env/backend/protocol/objects.py": "0f3654f391953f6951a7fed7c6edca0561365afc",
    "environments/org_env/backend/protocol/registry.py": "315c5169fd2ee2e6f0ecad7ee0c07f85cb321670",
}
SOURCE_B3_PROTOCOL_PORT_FILE_BLOBS: dict[str, str] = {
    "relic_agent/source_b3/protocols/objects.py": "0f3654f391953f6951a7fed7c6edca0561365afc",
    "relic_agent/source_b3/protocols/registry.py": "32871a7578f8e7a954e36899822ef9918f95c246",
}
SOURCE_B3_PROTOCOL_IMPORT_REWRITES: dict[str, str] = {
    "environments.org_env.backend.protocol.objects": "relic_agent.source_b3.protocols.objects",
    "environments.org_env.experiments.provenance.stable_fingerprint": "relic_agent.source_b3.provenance.stable_fingerprint",
}
SOURCE_B3_PROTOCOL_DEPENDENCY_SLICES: dict[str, dict[str, object]] = {
    "environments/org_env/experiments/provenance.py": {
        "source_blob": "b0d72ae15dd5a21060c3453ca36797068a237802",
        "symbols": ("_json_compatible", "stable_fingerprint"),
        "reason": "required by the source registry; evaluator/run-record helpers excluded",
    },
}


def source_b3_protocol_provenance() -> dict[str, object]:
    """Return a detached audit record for the source protocol closure."""

    return {
        "source_repository": SOURCE_B3_REPOSITORY,
        "source_commit": SOURCE_B3_COMMIT,
        "source_path": SOURCE_B3_PROTOCOL_SOURCE_PATH,
        "source_file_count": len(SOURCE_B3_PROTOCOL_FILE_BLOBS),
        "vendoring": "source_blob_port_with_two_import_rewrites",
        "dependency_slices": {
            path: {key: value for key, value in details.items() if key != "symbols"}
            | {"symbols": list(details["symbols"])}
            for path, details in SOURCE_B3_PROTOCOL_DEPENDENCY_SLICES.items()
        },
    }


def _json_compatible(value: Any) -> Any:
    """Source-exact normalization used by HCI's ``stable_fingerprint``."""

    if dataclasses.is_dataclass(value):
        return {
            field.name: _json_compatible(getattr(value, field.name))
            for field in dataclasses.fields(value)
            if field.repr
        }
    if isinstance(value, Enum):
        return _json_compatible(value.value)
    if isinstance(value, Mapping):
        return {
            str(key): _json_compatible(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_json_compatible(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(item, sort_keys=True, default=str),
        )
    if isinstance(value, (Path, os.PathLike)):
        return os.fspath(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 12)
    return value


def stable_fingerprint(value: Any) -> str:
    """Hash JSON-like evidence after the source's deterministic normalization."""

    payload = json.dumps(
        _json_compatible(value),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "SOURCE_B3_COMMIT",
    "SOURCE_B3_PROTOCOL_DEPENDENCY_SLICES",
    "SOURCE_B3_PROTOCOL_FILE_BLOBS",
    "SOURCE_B3_PROTOCOL_IMPORT_REWRITES",
    "SOURCE_B3_PROTOCOL_PORT_FILE_BLOBS",
    "SOURCE_B3_PROTOCOL_SOURCE_PATH",
    "SOURCE_B3_REPOSITORY",
    "source_b3_protocol_provenance",
    "stable_fingerprint",
]
