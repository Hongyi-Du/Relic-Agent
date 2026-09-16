"""Narrow, source-audited B3 ports from SocioGenesis HCI.

This package deliberately contains only closures whose source dependencies are
known and whose behavior can be tested without a paper workload, an OrgWorld,
or a model provider.  It is not a second runtime implementation.
"""

from relic_agent.source_b3.protocol_lifecycle import (
    SourceB3ProtocolLifecycleAdapter,
    SourceB3ProtocolLifecycleUnavailableError,
)

__all__ = [
    "SourceB3ProtocolLifecycleAdapter",
    "SourceB3ProtocolLifecycleUnavailableError",
]
