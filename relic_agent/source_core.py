"""Bridge source-compatible organization evidence to the vendored core.

The historical compatibility runtime remains available only for the release
shell.  A narrow HCI B3 protocol-lifecycle closure may additionally publish
its real source events here, but this is still not an OrgWorld/HCI execution
host adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Protocol

from organization_core import (
    OrganizationEvent,
    OrganizationEventType,
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

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": "relic-agent-source-core-status-v1",
            **source_core_provenance(),
            "mode": (
                "source_b3_protocol_lifecycle_plus_shadow_observation"
                if self.source_b3_protocol_lifecycle is not None
                else "shadow_observation_only"
            ),
            "execution_authority": "legacy_compatibility_runtime",
            "state_materialization": (
                "bootstrap_plus_source_b3_protocol_adoption"
                if self.source_b3_protocol_lifecycle is not None
                else "bootstrap_only"
            ),
            "active_hci_host_adapter": "unavailable_fail_closed",
            "observed_event_count": self.observed_event_count,
            "bootstrap_state_sha256": self.bootstrap_state_sha256,
        }
        if self.source_b3_protocol_lifecycle is not None:
            payload["source_b3_protocol_lifecycle"] = dict(
                self.source_b3_protocol_lifecycle
            )
        return payload


class SourceCoreObservationBridge:
    """Validate compatibility events through source-core append-only contracts.

    This class intentionally does not translate task execution, reflection, or
    full OrgWorld semantics into a substitute HCI world.  It can observe legacy
    envelopes and, when a source-ported lifecycle is explicitly mounted,
    preserve the source's protocol events and immutable adoption record.
    """

    _SOURCE = "relic-agent.compatibility"
    _HOST = "relic-agent.mock"
    _SOURCE_B3_PROTOCOL = "source_b3_protocol_lifecycle"
    _SOURCE_B3_PROTOCOL_HOST = "relic-agent.source_b3_protocol_adapter"

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
        """Publish a source-core tick envelope once after compatibility work."""

        normalized_tick = int(tick)
        if normalized_tick in self._completed_ticks:
            return False
        accepted = self.module.publish(
            OrganizationEvent(
                event_id=f"source-core:tick:{normalized_tick}",
                event_type=OrganizationEventType.TICK_COMPLETED,
                step=normalized_tick,
                provenance=self._provenance,
                payload={"compatibility_tick": normalized_tick},
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

    def require_active_hci_host_adapter(self) -> None:
        """Fail closed until a source-compatible HCI adapter is ported."""

        raise HciHostAdapterUnavailableError(
            "hci_orgworld_host_adapter_unavailable: "
            "source-core active execution is intentionally disabled"
        )

    def status(self) -> SourceCoreBridgeStatus:
        source_b3_status = self._source_b3_protocol_status_mapping()
        return SourceCoreBridgeStatus(
            observed_event_count=self.module.snapshot().event_count,
            bootstrap_state_sha256=self.state.canonical_sha256(),
            source_b3_protocol_lifecycle=source_b3_status,
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
