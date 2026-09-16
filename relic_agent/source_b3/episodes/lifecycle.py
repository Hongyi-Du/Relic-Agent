"""Explicit host boundary for the HCI source episode lifecycle.

``OrgEpisodeManager`` is a direct HCI source port.  The original manager is
driven by an ``OrgWorld`` and its ``ExecutionResult`` objects; Relic Agent does
not fabricate either from the compatibility runner.  This adapter therefore
accepts only caller-supplied source-shaped inputs and projects *closed* source
episodes to the existing append-only organization-core boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from relic_agent.source_b3.episodes.episode import EpEvent, OrgEpisode
from relic_agent.source_b3.episodes.episode_manager import OrgEpisodeManager
from relic_agent.source_b3.episodes.provenance import source_b3_episode_provenance

if TYPE_CHECKING:
    from relic_agent.source_core import SourceCoreObservationBridge


class SourceB3EpisodeHostUnavailableError(RuntimeError):
    """A source episode operation was requested without its source-shaped host."""


@dataclass(frozen=True)
class SourceB3EpisodeLifecycleStatus:
    """Auditable capability statement for the bounded source episode port."""

    source_core_projection: str
    observed_source_events: int
    episode_count: int
    closed_episode_count: int
    closed_episode_records_projected: int

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "relic-agent-source-b3-episode-status-v1",
            **source_b3_episode_provenance(),
            "activation": "explicit_source_orgworld_input_only",
            "scope": [
                "source_event_normalization",
                "source_episode_triggering",
                "source_episode_attachment",
                "source_episode_closure",
                "source_template_episode_summary",
                "closed_episode_core_projection",
            ],
            "source_core_projection": self.source_core_projection,
            "observed_source_events": self.observed_source_events,
            "episode_count": self.episode_count,
            "closed_episode_count": self.closed_episode_count,
            "closed_episode_records_projected": self.closed_episode_records_projected,
            "unavailable_fail_closed": [
                "legacy_compatibility_event_to_episode_translation",
                "source_orgworld_action_execution",
                "source_llm_reflection",
                "source_proposal_generation",
                "source_growth_policy_execution",
                "hci_human_seat_host_adapter",
            ],
        }


class SourceB3EpisodeLifecycleAdapter:
    """Forward the source manager without constructing an alternate OrgWorld.

    The adapter deliberately has no ``observe(Event)`` compatibility method.
    The release-shell ledger's event names and payloads are not HCI world
    events, so treating them as such would create fabricated episode evidence.
    A real host supplies either a source ``ExecutionResult`` or a source
    world-event mapping together with the world object that owns its state.
    """

    _RESULT_ATTRIBUTES = (
        "action_type",
        "agent_id",
        "created_objects",
        "modified_objects",
        "events",
        "state_delta",
        "success",
    )

    def __init__(self) -> None:
        self._manager = OrgEpisodeManager()
        self._bridge: SourceCoreObservationBridge | None = None
        self._projected_closed_episode_ids: set[str] = set()

    @property
    def manager(self) -> OrgEpisodeManager:
        """The source manager for integrations that already speak its API."""

        return self._manager

    @property
    def episodes(self) -> dict[str, OrgEpisode]:
        """Live source episode objects; mutations remain owned by the manager."""

        return self._manager.episodes

    @property
    def events_by_id(self) -> dict[str, EpEvent]:
        """Normalized source event ledger maintained by the source manager."""

        return self._manager.events_by_id

    def bind_source_core_bridge(self, bridge: SourceCoreObservationBridge) -> None:
        """Bind the append-only core projection before source episodes close."""

        if self._bridge is not None and self._bridge is not bridge:
            raise RuntimeError("source_b3_episode_lifecycle_bridge_already_bound")
        self._bridge = bridge
        bridge.register_source_b3_episode_lifecycle(self.status)
        self._project_closed_episodes()

    def observe_source_result(self, result: Any, *, world: Any) -> list[OrgEpisode]:
        """Observe a caller-supplied HCI ``ExecutionResult`` and its world."""

        self._require_world(world)
        self._require_result(result)
        touched = self._manager.observe_result(result, world)
        self._project_closed_episodes()
        return touched

    def observe_source_world_event(
        self,
        raw: Mapping[str, Any],
        *,
        world: Any,
    ) -> OrgEpisode | None:
        """Observe one caller-supplied HCI world event without reinterpretation."""

        self._require_world(world)
        if not isinstance(raw, Mapping):
            raise TypeError("source_b3_episode_world_event_must_be_mapping")
        touched = self._manager.observe_world_event(dict(raw), world)
        self._project_closed_episodes()
        return touched

    def update_open_episodes(self, *, world: Any) -> None:
        """Run the source manager's regular close sweep against the supplied world."""

        self._require_world(world)
        self._manager.update_open_episodes(world)
        self._project_closed_episodes()

    def open_for_agent(self, agent_id: str) -> list[OrgEpisode]:
        """Read-only convenience view over source-manager episode state.

        This does not attach events or create an episode.  It exists solely so
        the unported reflection host can inspect an explicitly supplied source
        episode set without reviving the former compatibility classifier.
        """

        return [
            episode
            for episode in self._manager.episodes.values()
            if episode.status == "open" and str(agent_id) in episode.participants
        ]

    def snapshot(self) -> dict[str, Any]:
        """Return the source manager's own inspector-oriented snapshot."""

        return self._manager.snapshot()

    def status(self) -> SourceB3EpisodeLifecycleStatus:
        closed = sum(
            1 for episode in self._manager.episodes.values() if episode.status != "open"
        )
        if self._bridge is None:
            projection = "unbound_no_source_host_projection"
        elif self._projected_closed_episode_ids:
            projection = "bound_closed_episode_projection"
        else:
            projection = "bound_no_closed_episode_yet"
        return SourceB3EpisodeLifecycleStatus(
            source_core_projection=projection,
            observed_source_events=len(self._manager.events_by_id),
            episode_count=len(self._manager.episodes),
            closed_episode_count=closed,
            closed_episode_records_projected=len(self._projected_closed_episode_ids),
        )

    @staticmethod
    def _require_world(world: Any) -> None:
        if world is None:
            raise SourceB3EpisodeHostUnavailableError(
                "source_b3_episode_orgworld_required: pass the source OrgWorld explicitly"
            )

    @classmethod
    def _require_result(cls, result: Any) -> None:
        if result is None:
            raise SourceB3EpisodeHostUnavailableError(
                "source_b3_episode_execution_result_required"
            )
        missing = [name for name in cls._RESULT_ATTRIBUTES if not hasattr(result, name)]
        if missing:
            raise SourceB3EpisodeHostUnavailableError(
                "source_b3_episode_execution_result_missing_fields: " + ",".join(missing)
            )

    def _project_closed_episodes(self) -> None:
        if self._bridge is None:
            return
        closed = [
            episode
            for episode in self._manager.episodes.values()
            if episode.status != "open"
            and episode.episode_id not in self._projected_closed_episode_ids
        ]
        for episode in sorted(
            closed,
            key=lambda item: (item.end_tick or item.updated_at_tick, item.episode_id),
        ):
            self._bridge.publish_source_b3_episode(episode)
            self._projected_closed_episode_ids.add(episode.episode_id)


__all__ = [
    "SourceB3EpisodeHostUnavailableError",
    "SourceB3EpisodeLifecycleAdapter",
    "SourceB3EpisodeLifecycleStatus",
]
