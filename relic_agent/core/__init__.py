"""Small, lazy compatibility boundary for historical core contracts.

The source-native runtime only needs the deterministic hashing helpers.  The
older ``organization_core`` extraction remains importable for archival tests
and downstream callers, but is deliberately not imported by the default CLI
or ``OrgWorld`` host.
"""

from __future__ import annotations

from typing import Any

from relic_agent.core.hashing import canonical_sha256, stable_fingerprint
from relic_agent.core.provenance import (
    SOURCE_CORE_COMMIT,
    SOURCE_CORE_FILE_BLOBS,
    SOURCE_CORE_SOURCE_PATH,
    SOURCE_CORE_SOURCE_REPOSITORY,
)

_LEGACY_CORE_EXPORTS = frozenset(
    {
        "ApprovalCheckRequest",
        "ApprovalPolicy",
        "DecisionRequest",
        "OrganizationEvent",
        "OrganizationEventType",
        "OrganizationModule",
        "OrganizationProvenance",
        "OrganizationStateBundle",
        "TypedGateEngine",
    }
)


def __getattr__(name: str) -> Any:
    """Load archived ``organization_core`` symbols only on an explicit request."""

    if name in _LEGACY_CORE_EXPORTS:
        import organization_core

        return getattr(organization_core, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    *_LEGACY_CORE_EXPORTS,
    "SOURCE_CORE_COMMIT",
    "SOURCE_CORE_FILE_BLOBS",
    "SOURCE_CORE_SOURCE_PATH",
    "SOURCE_CORE_SOURCE_REPOSITORY",
    "canonical_sha256",
    "stable_fingerprint",
]
