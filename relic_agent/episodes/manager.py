"""Compatibility import path for the source-pinned HCI episode adapter.

The former manager classified release-shell task events as organization
episodes.  That path is intentionally gone: callers must supply HCI-shaped
world events or execution results to the source adapter.
"""

from relic_agent.source_b3.episodes.lifecycle import (
    SourceB3EpisodeHostUnavailableError,
    SourceB3EpisodeLifecycleAdapter,
)


EpisodeManager = SourceB3EpisodeLifecycleAdapter

__all__ = [
    "EpisodeManager",
    "SourceB3EpisodeHostUnavailableError",
    "SourceB3EpisodeLifecycleAdapter",
]
