"""Source-pinned HCI reflection contracts and explicit host boundary.

The direct source manager stays private to this package's lifecycle adapter so
the source template fallback cannot be reached through a release-facing API.
"""

from relic_agent.source_b3.reflection.lifecycle import (
    SourceB3ReflectionHostUnavailableError,
    SourceB3ReflectionLifecycleAdapter,
    SourceB3ReflectionLifecycleStatus,
)
from relic_agent.source_b3.reflection.objects import (
    AgentLogEntry,
    AgentMemory,
    AgentReflection,
    Wish,
)
from relic_agent.source_b3.reflection.provenance import source_b3_reflection_provenance

__all__ = [
    "AgentLogEntry",
    "AgentMemory",
    "AgentReflection",
    "SourceB3ReflectionHostUnavailableError",
    "SourceB3ReflectionLifecycleAdapter",
    "SourceB3ReflectionLifecycleStatus",
    "Wish",
    "source_b3_reflection_provenance",
]
