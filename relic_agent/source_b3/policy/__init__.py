"""Source-pinned structural protocol affordance with an explicit HCI boundary.

The package intentionally does not export a replacement policy selector or
AttractorGuard. Callers may only ask the lifecycle adapter to apply the exact
source structural mask to source action candidates from a supplied OrgWorld.
"""

from relic_agent.source_b3.policy.lifecycle import (
    SourceB3PolicyHostUnavailableError,
    SourceB3PolicyLifecycleAdapter,
    SourceB3PolicyLifecycleStatus,
)
from relic_agent.source_b3.policy.provenance import source_b3_policy_provenance

__all__ = [
    "SourceB3PolicyHostUnavailableError",
    "SourceB3PolicyLifecycleAdapter",
    "SourceB3PolicyLifecycleStatus",
    "source_b3_policy_provenance",
]
