"""Compatibility import path for the pinned HCI proposal object contracts.

The former local dataclasses were a release-shell invention. Consumers retain
this import path while values now come from the source proposal slice.
"""

from relic_agent.source_b3.proposals.objects import (
    PROPOSAL_STATUS,
    PROPOSAL_TYPES,
    Proposal,
    ProtocolSpec,
    ToolSpec,
    ensure_list,
)

__all__ = [
    "PROPOSAL_STATUS",
    "PROPOSAL_TYPES",
    "Proposal",
    "ProtocolSpec",
    "ToolSpec",
    "ensure_list",
]
