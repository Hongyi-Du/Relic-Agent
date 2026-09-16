"""Mount the closed HCI B3 protocol registry on Relic Agent's core boundary.

The registry itself is ported from the authoritative HCI revision.  This file
only provides an integration seam: it forwards the source API, emits immutable
source-core evidence, and refuses configuration that would alter source review
latency.  It does not emulate OrgWorld, model decisions, proposal generation,
or domain execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from relic_agent.source_b3.provenance import source_b3_protocol_provenance
from relic_agent.source_b3.protocols import (
    ADOPT_MIN_SUPPORTERS,
    PERSIST_MIN,
    REVIEW_MIN_TICKS,
    USE_MIN,
    GovernedObjectSnapshot,
    IndependentOutcomeOracle,
    Protocol,
    ProtocolEvent,
    ProtocolRegistry,
    effective_min_supporters,
    protocol_is_live,
)

if TYPE_CHECKING:
    from relic_agent.source_core import SourceCoreObservationBridge


class SourceB3ProtocolLifecycleUnavailableError(RuntimeError):
    """Raised when a requested lifecycle would depart from the source closure."""


@dataclass(frozen=True)
class SourceB3ProtocolLifecycleStatus:
    """Auditable capability statement for the narrow active source port."""

    min_supporters: int
    source_core_projection: str
    source_events_projected: int
    adoption_records_projected: int

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "relic-agent-source-b3-protocol-status-v1",
            **source_b3_protocol_provenance(),
            "activation": "active_source_hci_protocol_registry",
            "scope": [
                "proposal",
                "support",
                "oppose",
                "adoption",
                "use",
                "violation",
                "enforcement",
                "amendment",
                "obsolescence",
                "emergence_evidence",
                "independent_outcome_attestation",
            ],
            "review_latency_ticks": REVIEW_MIN_TICKS,
            "min_supporters": self.min_supporters,
            "source_core_projection": self.source_core_projection,
            "source_events_projected": self.source_events_projected,
            "adoption_records_projected": self.adoption_records_projected,
            "unavailable_fail_closed": [
                "source_orgworld_action_execution",
                "source_llm_reflection",
                "source_proposal_generation",
                "source_growth_policy_execution",
                "hci_human_seat_host_adapter",
                "custom_review_latency",
            ],
        }


class SourceB3ProtocolLifecycleAdapter:
    """Source API forwarding with an append-only organization-core projection.

    ``ProtocolRegistry`` remains the sole lifecycle authority.  The adapter is
    intentionally thin: it has no alternate transition rules and does not
    synthesize a substitute world.  When bound, every source event is sent to
    the existing host boundary, while an adoption creates one immutable
    portable protocol record for the vendored organization core.
    """

    def __init__(
        self,
        *,
        min_supporters: int = ADOPT_MIN_SUPPORTERS,
        review_ticks: int = REVIEW_MIN_TICKS,
    ) -> None:
        if int(review_ticks) != REVIEW_MIN_TICKS:
            raise SourceB3ProtocolLifecycleUnavailableError(
                "source_b3_protocol_lifecycle_review_ticks_unsupported: "
                f"requested={review_ticks} source={REVIEW_MIN_TICKS}"
            )
        self._registry = ProtocolRegistry(min_supporters=min_supporters)
        self._bridge: SourceCoreObservationBridge | None = None
        self._event_cursor = 0
        self._source_events_projected = 0
        self._adoption_records_projected = 0

    @property
    def protocols(self) -> dict[str, Protocol]:
        """Live source registry state; callers must use adapter methods to mutate it."""

        return self._registry.protocols

    @property
    def events(self) -> list[ProtocolEvent]:
        """Append-only source event ledger."""

        return self._registry.events

    @property
    def min_supporters(self) -> int:
        return self._registry.min_supporters

    @property
    def compilation_frozen(self) -> bool:
        return self._registry.compilation_frozen

    @compilation_frozen.setter
    def compilation_frozen(self, value: bool) -> None:
        self._registry.compilation_frozen = bool(value)

    def bind_source_core_bridge(self, bridge: SourceCoreObservationBridge) -> None:
        """Bind the real host boundary before active lifecycle events occur."""

        if self._bridge is not None and self._bridge is not bridge:
            raise RuntimeError("source_b3_protocol_lifecycle_bridge_already_bound")
        self._bridge = bridge
        bridge.register_source_b3_protocol_lifecycle(self.status)
        self._flush_source_events()

    def status(self) -> SourceB3ProtocolLifecycleStatus:
        return SourceB3ProtocolLifecycleStatus(
            min_supporters=self._registry.min_supporters,
            source_core_projection=("bound_active" if self._bridge is not None else "unbound"),
            source_events_projected=self._source_events_projected,
            adoption_records_projected=self._adoption_records_projected,
        )

    # The methods below preserve the HCI registry's public API and only flush
    # events after the source implementation has made its own transition.
    def propose(
        self,
        *,
        proposer_id: str,
        protocol_type: str,
        rule_summary: str,
        scope: str = "review",
        target_process: str = "",
        tick: int = 0,
        protocol_id: str | None = None,
    ) -> Protocol:
        result = self._registry.propose(
            proposer_id=proposer_id,
            protocol_type=protocol_type,
            rule_summary=rule_summary,
            scope=scope,
            target_process=target_process,
            tick=tick,
            protocol_id=protocol_id,
        )
        self._flush_source_events()
        return result

    def support(self, agent_id: str, protocol_id: str, tick: int = 0) -> None:
        self._registry.support(agent_id, protocol_id, tick)
        self._flush_source_events()

    def oppose(self, agent_id: str, protocol_id: str, tick: int = 0) -> None:
        self._registry.oppose(agent_id, protocol_id, tick)
        self._flush_source_events()

    def tick_adoptions(self, tick: int = 0) -> None:
        self._registry.tick_adoptions(tick)
        self._flush_source_events()

    def adopt(
        self,
        protocol_id: str,
        tick: int = 0,
        *,
        approver_id: str | None = None,
        force: bool = False,
    ) -> bool:
        result = self._registry.adopt(
            protocol_id,
            tick,
            approver_id=approver_id,
            force=force,
        )
        self._flush_source_events()
        return result

    def use(
        self,
        agent_id: str,
        protocol_id: str,
        tick: int = 0,
        *,
        context_id: str | None = None,
        episode_id: str | None = None,
        task_id: str | None = None,
    ) -> ProtocolEvent:
        result = self._registry.use(
            agent_id,
            protocol_id,
            tick,
            context_id=context_id,
            episode_id=episode_id,
            task_id=task_id,
        )
        self._flush_source_events()
        return result

    def violate(
        self,
        agent_id: str,
        protocol_id: str,
        tick: int = 0,
        *,
        context_id: str | None = None,
    ) -> ProtocolEvent:
        result = self._registry.violate(
            agent_id,
            protocol_id,
            tick,
            context_id=context_id,
        )
        self._flush_source_events()
        return result

    def enforce(
        self,
        agent_id: str,
        protocol_id: str,
        tick: int = 0,
        *,
        violation_event_id: str | None = None,
        state_impact_ref: str | None = None,
        blocked: bool = False,
        context_id: str | None = None,
        state_before_hash: str | None = None,
        state_after_hash: str | None = None,
        state_before: Mapping[str, Any] | None = None,
        state_after: Mapping[str, Any] | None = None,
        governed_object_before: GovernedObjectSnapshot | None = None,
        governed_object_after: GovernedObjectSnapshot | None = None,
    ) -> ProtocolEvent:
        result = self._registry.enforce(
            agent_id,
            protocol_id,
            tick,
            violation_event_id=violation_event_id,
            state_impact_ref=state_impact_ref,
            blocked=blocked,
            context_id=context_id,
            state_before_hash=state_before_hash,
            state_after_hash=state_after_hash,
            state_before=state_before,
            state_after=state_after,
            governed_object_before=governed_object_before,
            governed_object_after=governed_object_after,
        )
        self._flush_source_events()
        return result

    def record_independent_outcome(
        self,
        protocol_id: str,
        observation: IndependentOutcomeOracle,
    ) -> None:
        self._registry.record_independent_outcome(protocol_id, observation)
        self._flush_source_events()

    def set_impact(self, protocol_id: str, metrics: dict[str, float]) -> None:
        self._registry.set_impact(protocol_id, metrics)
        self._flush_source_events()

    def amend(
        self,
        agent_id: str,
        protocol_id: str,
        tick: int = 0,
        *,
        revision_kind: str = "extend",
        source_proposal_id: str | None = None,
    ) -> ProtocolEvent:
        result = self._registry.amend(
            agent_id,
            protocol_id,
            tick,
            revision_kind=revision_kind,
            source_proposal_id=source_proposal_id,
        )
        self._flush_source_events()
        return result

    def obsolete(
        self,
        agent_id: str,
        protocol_id: str,
        tick: int = 0,
        *,
        source_proposal_id: str | None = None,
    ) -> ProtocolEvent:
        result = self._registry.obsolete(
            agent_id,
            protocol_id,
            tick,
            source_proposal_id=source_proposal_id,
        )
        self._flush_source_events()
        return result

    def attribute_impact(self, tick: int = 0) -> None:
        self._registry.attribute_impact(tick)
        self._flush_source_events()

    def emergence_evidence(self, protocol_id: str) -> dict:
        return self._registry.emergence_evidence(protocol_id)

    def shuffled_event_history_replay(self, protocol_id: str, *, seed: int) -> dict:
        return self._registry.shuffled_event_history_replay(protocol_id, seed=seed)

    def classify_emergence(self, protocol_id: str) -> str:
        return self._registry.classify_emergence(protocol_id)

    def emerged(self, level: str = "weak") -> list[Protocol]:
        return self._registry.emerged(level)

    def _flush_source_events(self) -> None:
        if self._bridge is None:
            return
        while self._event_cursor < len(self._registry.events):
            event = self._registry.events[self._event_cursor]
            protocol = self._registry.protocols.get(event.protocol_id)
            if protocol is None:
                raise RuntimeError(
                    "source_b3_protocol_event_references_missing_protocol: "
                    f"{event.protocol_id}"
                )
            projection = self._bridge.publish_source_b3_protocol_event(event, protocol)
            self._source_events_projected += 1
            if projection.get("adoption_record_projected"):
                self._adoption_records_projected += 1
            self._event_cursor += 1


__all__ = [
    "ADOPT_MIN_SUPPORTERS",
    "PERSIST_MIN",
    "REVIEW_MIN_TICKS",
    "USE_MIN",
    "SourceB3ProtocolLifecycleAdapter",
    "SourceB3ProtocolLifecycleStatus",
    "SourceB3ProtocolLifecycleUnavailableError",
    "effective_min_supporters",
    "protocol_is_live",
]
