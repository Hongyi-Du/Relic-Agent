"""A deliberately narrow bridge to the vendored organization core.

The vendored core is the canonical contract/state/host seam. The historical
mock runtime remains temporarily available only to keep release wrappers and
the public Inspector runnable. It is observed through this bridge; it is not
an HCI host adapter and cannot activate source-core execution semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
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

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "relic-agent-source-core-status-v1",
            **source_core_provenance(),
            "mode": "shadow_observation_only",
            "execution_authority": "legacy_compatibility_runtime",
            "state_materialization": "bootstrap_only",
            "active_hci_host_adapter": "unavailable_fail_closed",
            "observed_event_count": self.observed_event_count,
            "bootstrap_state_sha256": self.bootstrap_state_sha256,
        }


class SourceCoreObservationBridge:
    """Validate compatibility events through source-core append-only contracts.

    This class intentionally does not translate task execution, reflection, or
    protocol semantics into the HCI world. That mapping must be sourced from
    the HCI implementation in a later stage. The only supported operation is
    observing detached event envelopes and an initial portable state bundle.
    """

    _SOURCE = "relic-agent.compatibility"
    _HOST = "relic-agent.mock"

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

    def require_active_hci_host_adapter(self) -> None:
        """Fail closed until a source-compatible HCI adapter is ported."""

        raise HciHostAdapterUnavailableError(
            "hci_orgworld_host_adapter_unavailable: "
            "source-core active execution is intentionally disabled"
        )

    def status(self) -> SourceCoreBridgeStatus:
        return SourceCoreBridgeStatus(
            observed_event_count=self.module.snapshot().event_count,
            bootstrap_state_sha256=self.state.canonical_sha256(),
        )

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
