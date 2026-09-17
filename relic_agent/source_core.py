"""Bridge source-compatible organization evidence to the vendored core.

The release trace shell can publish clock envelopes here, and a narrow HCI B3
protocol-lifecycle closure may additionally publish real source events.  This
is still not an OrgWorld/HCI execution host adapter and cannot accept a shell
task as an action result.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol

from organization_core import (
    OrganizationEvent,
    OrganizationEventType,
    OrganizationEpisodeState,
    OrganizationMemberState,
    OrganizationModule,
    OrganizationProvenance,
    OrganizationStateBundle,
    OrganizationVisibility,
)

from relic_agent.core.provenance import source_core_provenance

if TYPE_CHECKING:
    from relic_agent.config import OrganizationConfig


class LegacyEventLike(Protocol):
    """Minimum compatibility event shape accepted by the observation bridge."""

    event_id: str
    tick: int
    event_type: str
    actor_id: str
    object_ids: tuple[str, ...]
    payload: dict[str, Any]
    visibility: str


class HciHostAdapterUnavailableError(RuntimeError):
    """Raised instead of inventing an active adapter for the HCI OrgWorld."""


@dataclass(frozen=True)
class SourceCoreBridgeStatus:
    """Auditable statement of what this extraction stage actually provides."""

    observed_event_count: int
    bootstrap_state_sha256: str
    source_b3_protocol_lifecycle: Mapping[str, object] | None = None
    source_b3_episode_lifecycle: Mapping[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": "relic-agent-source-core-status-v1",
            **source_core_provenance(),
            "mode": self._mode(),
            "execution_authority": "compatibility_trace_shell_unbound",
            "workflow_acceptance": "unavailable_fail_closed",
            "workflow_acceptance_reason": "source_orgworld_action_host_not_mounted",
            "state_materialization": self._state_materialization(),
            "active_hci_host_adapter": "unavailable_fail_closed",
            "observed_event_count": self.observed_event_count,
            "bootstrap_state_sha256": self.bootstrap_state_sha256,
        }
        if self.source_b3_protocol_lifecycle is not None:
            payload["source_b3_protocol_lifecycle"] = dict(
                self.source_b3_protocol_lifecycle
            )
        if self.source_b3_episode_lifecycle is not None:
            payload["source_b3_episode_lifecycle"] = dict(
                self.source_b3_episode_lifecycle
            )
        return payload

    def _mode(self) -> str:
        if (
            self.source_b3_protocol_lifecycle is not None
            and self.source_b3_episode_lifecycle is not None
        ):
            return "source_b3_protocol_and_episode_lifecycle_plus_shadow_observation"
        if self.source_b3_protocol_lifecycle is not None:
            return "source_b3_protocol_lifecycle_plus_shadow_observation"
        if self.source_b3_episode_lifecycle is not None:
            return "source_b3_episode_lifecycle_plus_shadow_observation"
        return "shadow_observation_only"

    def _state_materialization(self) -> str:
        if (
            self.source_b3_protocol_lifecycle is not None
            and self.source_b3_episode_lifecycle is not None
        ):
            return "bootstrap_plus_source_b3_protocol_adoption_and_closed_episode"
        if self.source_b3_protocol_lifecycle is not None:
            return "bootstrap_plus_source_b3_protocol_adoption"
        if self.source_b3_episode_lifecycle is not None:
            return "bootstrap_plus_source_b3_closed_episode"
        return "bootstrap_only"


class SourceCoreObservationBridge:
    """Validate trace-shell and source events through source-core contracts.

    This class intentionally does not translate task execution, reflection, or
    full OrgWorld semantics into a substitute HCI world.  It can observe legacy
    envelopes and, when a source-ported lifecycle is explicitly mounted,
    preserve the source's protocol events and immutable adoption record.
    """

    _SOURCE = "relic-agent.compatibility"
    _HOST = "relic-agent.compatibility_trace_shell"
    _SOURCE_B3_PROTOCOL = "source_b3_protocol_lifecycle"
    _SOURCE_B3_PROTOCOL_HOST = "relic-agent.source_b3_protocol_adapter"
    _SOURCE_B3_EPISODE = "source_b3_episode_lifecycle"
    _SOURCE_B3_EPISODE_HOST = "relic-agent.source_b3_episode_adapter"

    def __init__(self, *, state: OrganizationStateBundle, run_id: str) -> None:
        self.state = state
        self.run_id = str(run_id)
        self.module = OrganizationModule(
            organization_id=state.organization_id,
            run_id=self.run_id,
            state=state,
        )
        self._provenance = OrganizationProvenance(
            source=self._SOURCE,
            host=self._HOST,
            run_id=self.run_id,
        )
        self._completed_ticks: set[int] = set()
        self._source_b3_protocol_status: Callable[[], object] | None = None
        self._source_b3_adopted_protocol_ids: set[str] = set()
        self._source_b3_episode_status: Callable[[], object] | None = None
        self._source_b3_closed_episode_ids: set[str] = set()
        self._publish_initialization()

    @classmethod
    def from_config(
        cls,
        config: OrganizationConfig,
        *,
        run_id: str,
    ) -> "SourceCoreObservationBridge":
        """Build only the portable state that the public config actually supplies."""

        state = OrganizationStateBundle(
            organization_id=config.organization_id,
            source_repository_id="relic-agent-config",
            source_run_id=str(run_id),
            source_seed=config.runtime.seed,
            source_step=0,
            members=tuple(
                OrganizationMemberState(
                    member_id=agent.agent_id,
                    role=agent.role,
                    profile=dict(agent.profile),
                    skills=dict(agent.skills),
                )
                for agent in config.agents
            ),
        )
        return cls(state=state, run_id=run_id)

    def _publish_initialization(self) -> None:
        self.module.publish(
            OrganizationEvent(
                event_id="source-core:organization-initialized",
                event_type=OrganizationEventType.ORGANIZATION_INITIALIZED,
                step=0,
                provenance=self._provenance,
                payload={"members": [member.as_dict() for member in self.state.members]},
                visibility=OrganizationVisibility.ORGANIZATION,
            )
        )

    def publish_legacy_event(self, event: LegacyEventLike) -> bool:
        """Observe one legacy event through the source-core event contract.

        The original event type and payload are retained as data beneath the
        generic ``domain.event_recorded`` envelope. No claim is made that the
        legacy event has source-compatible organization semantics.
        """

        visibility = self._visibility(event.visibility)
        accepted = self.module.publish(
            OrganizationEvent(
                event_id=f"source-core:legacy:{event.event_id}",
                event_type=OrganizationEventType.DOMAIN_EVENT_RECORDED,
                step=int(event.tick),
                provenance=self._provenance,
                actor_id=event.actor_id or None,
                subject_refs=tuple(event.object_ids),
                payload={
                    "legacy_event_id": event.event_id,
                    "legacy_event_type": event.event_type,
                    "legacy_payload": dict(event.payload),
                },
                visibility=visibility,
            )
        )
        return accepted

    def complete_tick(self, tick: int) -> bool:
        """Publish one source-core clock envelope without implying action work."""

        normalized_tick = int(tick)
        if normalized_tick in self._completed_ticks:
            return False
        accepted = self.module.publish(
            OrganizationEvent(
                event_id=f"source-core:tick:{normalized_tick}",
                event_type=OrganizationEventType.TICK_COMPLETED,
                step=normalized_tick,
                provenance=self._provenance,
                payload={
                    "compatibility_tick": normalized_tick,
                    "action_execution": "unavailable_fail_closed",
                },
                visibility=OrganizationVisibility.ORGANIZATION,
            )
        )
        if accepted:
            self._completed_ticks.add(normalized_tick)
        return accepted

    def register_source_b3_protocol_lifecycle(
        self,
        status: Callable[[], object],
    ) -> None:
        """Register one source-backed lifecycle status provider.

        The source-port adapter owns this callback.  A second adapter would
        make protocol evidence ambiguous, so it is rejected rather than merged.
        """

        if not callable(status):
            raise TypeError("source_b3_protocol_lifecycle_status_must_be_callable")
        if self._source_b3_protocol_status is not None and self._source_b3_protocol_status != status:
            raise RuntimeError("source_b3_protocol_lifecycle_already_registered")
        self._source_b3_protocol_status = status

    def publish_source_b3_protocol_event(
        self,
        event: Any,
        protocol: Any,
    ) -> dict[str, bool]:
        """Project one source registry event without inventing a host action.

        Every source lifecycle event remains a generic domain envelope.  The
        source-core formation state receives exactly one immutable record when
        the source registry emits an adoption; later lifecycle mutations remain
        in the source ledger rather than masquerading as mutable core records.
        """

        if self._source_b3_protocol_status is None:
            raise RuntimeError("source_b3_protocol_lifecycle_not_registered")
        event_id = self._required_text(getattr(event, "event_id", None), "event_id")
        event_type = self._required_text(
            getattr(event, "event_type", None), "event_type"
        )
        protocol_id = self._required_text(
            getattr(event, "protocol_id", None), "protocol_id"
        )
        if protocol_id != self._required_text(
            getattr(protocol, "protocol_id", None), "protocol.protocol_id"
        ):
            raise ValueError("source_b3_protocol_event_protocol_mismatch")
        try:
            tick = int(getattr(event, "tick", None))
        except (TypeError, ValueError) as exc:
            raise ValueError("source_b3_protocol_event_tick_invalid") from exc
        if tick < 0:
            raise ValueError("source_b3_protocol_event_tick_invalid")
        raw_data = getattr(event, "data", None)
        if not isinstance(raw_data, Mapping):
            raise TypeError("source_b3_protocol_event_data_must_be_mapping")
        provenance = OrganizationProvenance(
            source=self._SOURCE_B3_PROTOCOL,
            host=self._SOURCE_B3_PROTOCOL_HOST,
            run_id=self.run_id,
        )
        event_accepted = self.module.publish(
            OrganizationEvent(
                event_id=f"source-core:source-b3-protocol:{event_id}",
                event_type=OrganizationEventType.DOMAIN_EVENT_RECORDED,
                step=tick,
                provenance=provenance,
                actor_id=(str(getattr(event, "actor_id", "") or "") or None),
                subject_refs=(protocol_id,),
                payload={
                    "source_b3_protocol_event": {
                        "event_id": event_id,
                        "event_type": event_type,
                        "protocol_id": protocol_id,
                        "actor_id": str(getattr(event, "actor_id", "") or ""),
                        "tick": tick,
                        "data": dict(raw_data),
                    }
                },
                visibility=OrganizationVisibility.ORGANIZATION,
            )
        )
        adoption_record_projected = False
        if event_type == "adoption" and protocol_id not in self._source_b3_adopted_protocol_ids:
            adoption_record_projected = self.module.publish(
                OrganizationEvent(
                    event_id=f"source-core:source-b3-protocol-adoption:{event_id}",
                    event_type=OrganizationEventType.PROTOCOL_ADOPTED,
                    step=tick,
                    provenance=provenance,
                    actor_id=(str(getattr(event, "actor_id", "") or "") or None),
                    subject_refs=(protocol_id,),
                    payload={"record": self._source_b3_protocol_record(protocol, event_id)},
                    visibility=OrganizationVisibility.ORGANIZATION,
                )
            )
            self._source_b3_adopted_protocol_ids.add(protocol_id)
        return {
            "event_projected": event_accepted,
            "adoption_record_projected": adoption_record_projected,
        }

    def register_source_b3_episode_lifecycle(
        self,
        status: Callable[[], object],
    ) -> None:
        """Register one source-backed episode lifecycle status provider.

        The source port projects only terminal episode snapshots, because an
        ``OrganizationEpisodeState`` is immutable once published to the core
        formation ledger.  A second provider would make the provenance of a
        closed episode ambiguous, so it is rejected.
        """

        if not callable(status):
            raise TypeError("source_b3_episode_lifecycle_status_must_be_callable")
        if self._source_b3_episode_status is not None and self._source_b3_episode_status != status:
            raise RuntimeError("source_b3_episode_lifecycle_already_registered")
        self._source_b3_episode_status = status

    def publish_source_b3_episode(self, episode: Any) -> bool:
        """Project one closed source episode without inventing a host action."""

        if self._source_b3_episode_status is None:
            raise RuntimeError("source_b3_episode_lifecycle_not_registered")
        episode_id = self._required_text(
            getattr(episode, "episode_id", None), "episode.episode_id"
        )
        episode_type = self._required_text(
            getattr(episode, "episode_type", None), "episode.episode_type"
        )
        status = self._required_text(getattr(episode, "status", None), "episode.status")
        if status == "open":
            raise ValueError("source_b3_episode_must_be_closed_before_projection")
        start_step = self._required_non_negative_int(
            getattr(episode, "start_tick", None), "episode.start_tick"
        )
        end_step = self._required_non_negative_int(
            getattr(episode, "end_tick", None), "episode.end_tick"
        )
        if end_step < start_step:
            raise ValueError("source_b3_episode_end_before_start")
        if episode_id in self._source_b3_closed_episode_ids:
            return False
        provenance = OrganizationProvenance(
            source=self._SOURCE_B3_EPISODE,
            host=self._SOURCE_B3_EPISODE_HOST,
            run_id=self.run_id,
        )
        accepted = self.module.publish(
            OrganizationEvent(
                event_id=(
                    f"source-core:source-b3-episode:{episode_id}:{end_step}"
                ),
                event_type=OrganizationEventType.EPISODE_RECORDED,
                step=end_step,
                provenance=provenance,
                actor_id=(
                    str(getattr(episode, "primary_agent_id", "") or "") or None
                ),
                subject_refs=(episode_id,),
                payload={
                    "record": self._source_b3_episode_record(
                        episode,
                        episode_id=episode_id,
                        episode_type=episode_type,
                        status=status,
                        start_step=start_step,
                        end_step=end_step,
                    )
                },
                visibility=OrganizationVisibility.ORGANIZATION,
            )
        )
        self._source_b3_closed_episode_ids.add(episode_id)
        return accepted

    def require_active_hci_host_adapter(self) -> None:
        """Fail closed until a source-compatible HCI adapter is ported."""

        raise HciHostAdapterUnavailableError(
            "hci_orgworld_host_adapter_unavailable: "
            "source-core active execution is intentionally disabled"
        )

    def status(self) -> SourceCoreBridgeStatus:
        source_b3_status = self._source_b3_protocol_status_mapping()
        source_b3_episode_status = self._source_b3_episode_status_mapping()
        return SourceCoreBridgeStatus(
            observed_event_count=self.module.snapshot().event_count,
            bootstrap_state_sha256=self.state.canonical_sha256(),
            source_b3_protocol_lifecycle=source_b3_status,
            source_b3_episode_lifecycle=source_b3_episode_status,
        )

    def _source_b3_protocol_status_mapping(self) -> dict[str, object] | None:
        if self._source_b3_protocol_status is None:
            return None
        status = self._source_b3_protocol_status()
        as_dict = getattr(status, "as_dict", None)
        payload = as_dict() if callable(as_dict) else status
        if not isinstance(payload, Mapping):
            raise TypeError("source_b3_protocol_lifecycle_status_must_be_mapping")
        return {str(key): value for key, value in payload.items()}

    def _source_b3_episode_status_mapping(self) -> dict[str, object] | None:
        if self._source_b3_episode_status is None:
            return None
        status = self._source_b3_episode_status()
        as_dict = getattr(status, "as_dict", None)
        payload = as_dict() if callable(as_dict) else status
        if not isinstance(payload, Mapping):
            raise TypeError("source_b3_episode_lifecycle_status_must_be_mapping")
        return {str(key): value for key, value in payload.items()}

    @staticmethod
    def _source_b3_protocol_record(protocol: Any, adoption_event_id: str) -> dict[str, object]:
        """Freeze source protocol fields that the portable core can represent."""

        protocol_id = SourceCoreObservationBridge._required_text(
            getattr(protocol, "protocol_id", None), "protocol.protocol_id"
        )
        return {
            "protocol_id": protocol_id,
            "protocol_type": SourceCoreObservationBridge._required_text(
                getattr(protocol, "protocol_type", None), "protocol.protocol_type"
            ),
            "rule_summary": str(getattr(protocol, "rule_summary", "") or ""),
            "scope": str(getattr(protocol, "scope", "review") or "review"),
            "target_process": str(getattr(protocol, "target_process", "") or ""),
            "supporters": [str(item) for item in getattr(protocol, "supporters", ())],
            "emergence_level": str(getattr(protocol, "emergence_level", "none") or "none"),
            "capability": "",
            "evidence_refs": {
                "protocol_event": [
                    str(getattr(protocol, "proposal_event_id", "") or ""),
                    adoption_event_id,
                ]
            },
            "gate_rules": [],
            "attributes": {
                "source_lifecycle": "source_hci_protocol_registry",
                "adoption_status": str(getattr(protocol, "adoption_status", "") or ""),
                "source_first_tick": int(getattr(protocol, "first_tick", 0) or 0),
            },
        }

    @staticmethod
    def _source_b3_episode_record(
        episode: Any,
        *,
        episode_id: str,
        episode_type: str,
        status: str,
        start_step: int,
        end_step: int,
    ) -> dict[str, object]:
        """Freeze the source fields the portable episode record can represent."""

        linked = SourceCoreObservationBridge._source_string_refs
        trigger_event_id = str(getattr(episode, "trigger_event_id", "") or "")
        trigger_object_id = str(getattr(episode, "trigger_object_id", "") or "")
        lineage = {
            "event": linked(getattr(episode, "linked_event_ids", ()), "linked_event_ids"),
            "object": linked(getattr(episode, "linked_object_ids", ()), "linked_object_ids"),
            "protocol": linked(
                getattr(episode, "linked_protocol_ids", ()), "linked_protocol_ids"
            ),
            "proposal": linked(
                getattr(episode, "linked_proposal_ids", ()), "linked_proposal_ids"
            ),
            "reflection": linked(
                getattr(episode, "linked_reflection_ids", ()), "linked_reflection_ids"
            ),
            "wish": linked(getattr(episode, "linked_wish_ids", ()), "linked_wish_ids"),
            "related_episode": linked(
                getattr(episode, "related_episode_ids", ()), "related_episode_ids"
            ),
        }
        if trigger_event_id:
            lineage["trigger_event"] = (trigger_event_id,)
        if trigger_object_id:
            lineage["trigger_object"] = (trigger_object_id,)
        return OrganizationEpisodeState(
            episode_id=episode_id,
            episode_type=episode_type,
            status=status,
            start_step=start_step,
            end_step=end_step,
            participant_ids=linked(getattr(episode, "participants", ()), "participants"),
            primary_member_id=(
                str(getattr(episode, "primary_agent_id", "") or "") or None
            ),
            lineage_refs=lineage,
            attributes={
                "source_lifecycle": "source_hci_episode_manager",
                "title": str(getattr(episode, "title", "") or ""),
                "problem_statement": str(
                    getattr(episode, "problem_statement", "") or ""
                ),
                "conflict_summary": getattr(episode, "conflict_summary", None),
                "decision_summary": getattr(episode, "decision_summary", None),
                "outcome_summary": getattr(episode, "outcome_summary", None),
                "produced_artifacts": list(
                    linked(getattr(episode, "produced_artifacts", ()), "produced_artifacts")
                ),
                "produced_protocols": list(
                    linked(getattr(episode, "produced_protocols", ()), "produced_protocols")
                ),
                "produced_tasks": list(
                    linked(getattr(episode, "produced_tasks", ()), "produced_tasks")
                ),
                "produced_product_changes": list(
                    linked(
                        getattr(episode, "produced_product_changes", ()),
                        "produced_product_changes",
                    )
                ),
                "state_delta": dict(getattr(episode, "state_delta", {}) or {}),
                "graph_delta": dict(getattr(episode, "graph_delta", {}) or {}),
            },
        ).as_dict()

    @staticmethod
    def _source_string_refs(value: Any, label: str) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise TypeError(f"source_b3_episode_{label}_must_be_sequence")
        refs = []
        for item in value:
            refs.append(SourceCoreObservationBridge._required_text(item, label))
        return tuple(refs)

    @staticmethod
    def _required_non_negative_int(value: object, label: str) -> int:
        try:
            result = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"source_b3_episode_{label}_required_integer") from exc
        if result < 0:
            raise ValueError(f"source_b3_episode_{label}_must_be_non_negative")
        return result

    @staticmethod
    def _required_text(value: object, label: str) -> str:
        result = str(value or "").strip()
        if not result:
            raise ValueError(f"source_b3_protocol_{label}_required")
        return result

    @staticmethod
    def _visibility(value: str) -> OrganizationVisibility:
        try:
            return OrganizationVisibility(value)
        except ValueError as exc:
            raise ValueError(f"unsupported legacy event visibility: {value!r}") from exc


__all__ = [
    "HciHostAdapterUnavailableError",
    "SourceCoreBridgeStatus",
    "SourceCoreObservationBridge",
]
