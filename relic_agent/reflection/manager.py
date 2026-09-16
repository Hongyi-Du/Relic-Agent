"""Compatibility import path for the explicit source reflection adapter.

There is intentionally no mock ``reflect`` method here.  A caller must supply
the full HCI OrgWorld, a closed source episode, and an OpenAI-compatible source
provider through :class:`SourceB3ReflectionLifecycleAdapter`.
"""

from relic_agent.source_b3.reflection.lifecycle import (
    SourceB3ReflectionHostUnavailableError,
    SourceB3ReflectionLifecycleAdapter,
)


ReflectionManager = SourceB3ReflectionLifecycleAdapter

__all__ = [
    "ReflectionManager",
    "SourceB3ReflectionHostUnavailableError",
    "SourceB3ReflectionLifecycleAdapter",
]
