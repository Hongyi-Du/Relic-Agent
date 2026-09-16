"""Canonical source-core contracts exposed by the Relic Agent package.

The compatibility runtime has its own historical event/trace objects. New
organization-host integrations must use these vendored, harness-neutral
contracts instead of extending those compatibility objects.
"""

from relic_agent.core.hashing import canonical_sha256, stable_fingerprint
from relic_agent.core.provenance import (
    SOURCE_CORE_COMMIT,
    SOURCE_CORE_FILE_BLOBS,
    SOURCE_CORE_SOURCE_PATH,
    SOURCE_CORE_SOURCE_REPOSITORY,
)

from organization_core import (
    ApprovalCheckRequest,
    ApprovalPolicy,
    DecisionRequest,
    OrganizationEvent,
    OrganizationEventType,
    OrganizationModule,
    OrganizationProvenance,
    OrganizationStateBundle,
    TypedGateEngine,
)

__all__ = [
    "ApprovalCheckRequest",
    "ApprovalPolicy",
    "DecisionRequest",
    "OrganizationEvent",
    "OrganizationEventType",
    "OrganizationModule",
    "OrganizationProvenance",
    "OrganizationStateBundle",
    "SOURCE_CORE_COMMIT",
    "SOURCE_CORE_FILE_BLOBS",
    "SOURCE_CORE_SOURCE_PATH",
    "SOURCE_CORE_SOURCE_REPOSITORY",
    "TypedGateEngine",
    "canonical_sha256",
    "stable_fingerprint",
]
