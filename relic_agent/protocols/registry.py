"""Legacy protocol registry kept behind the compatibility runtime boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from relic_agent.core.hashing import stable_fingerprint
from relic_agent.protocols.models import Protocol, ProtocolEvent

USE_MIN = 2
PERSIST_MIN = 48
REVIEW_MIN_TICKS = 3
ADOPT_MIN_SUPPORTERS = 2


def protocol_is_live(protocol: Any) -> bool:
    return bool(
        protocol is not None
        and getattr(protocol, "status", None) == "active"
        and getattr(protocol, "adoption_status", None) in {"proposed", "adopted"}
    )


def effective_min_supporters(roster_size: int) -> int:
    return max(1, min(ADOPT_MIN_SUPPORTERS, int(roster_size or 1)))


class ProtocolRegistry:
    def __init__(
        self,
        *,
        min_supporters: int = ADOPT_MIN_SUPPORTERS,
        review_ticks: int = REVIEW_MIN_TICKS,
    ) -> None:
        self.protocols: dict[str, Protocol] = {}
        self.events: list[ProtocolEvent] = []
        self.min_supporters = max(1, int(min_supporters))
        self.review_ticks = max(1, int(review_ticks))
        self._sequence = 0

    def _event(
        self,
        event_type: str,
        protocol_id: str,
        actor_id: str,
        tick: int,
        data: dict[str, Any] | None = None,
    ) -> ProtocolEvent:
        self._sequence += 1
        event = ProtocolEvent(
            event_id=f"protocol_event_{self._sequence:06d}",
            event_type=event_type,
            protocol_id=protocol_id,
            actor_id=actor_id,
            tick=tick,
            data=dict(data or {}),
        )
        self.events.append(event)
        return event

    def propose(
        self,
        *,
        proposer_id: str,
        protocol_type: str,
        rule_summary: str,
        scope: str = "organization",
        target_process: str = "",
        tick: int = 0,
        protocol_id: str | None = None,
        created_from_proposal_id: str | None = None,
    ) -> Protocol:
        identifier = protocol_id or f"protocol_{protocol_type}"
        if identifier in self.protocols:
            raise ValueError("protocol_id_already_exists")
        proposal_event = self._event("proposal", identifier, proposer_id, tick)
        protocol = Protocol(
            protocol_id=identifier,
            protocol_type=protocol_type,
            proposer_id=proposer_id,
            created_from_proposal_id=created_from_proposal_id,
            proposal_event_id=proposal_event.event_id,
            rule_summary=rule_summary,
            scope=scope,
            target_process=target_process,
            supporters=[proposer_id],
            first_tick=tick,
            last_active_tick=tick,
        )
        self.protocols[identifier] = protocol
        return protocol

    def support(self, agent_id: str, protocol_id: str, tick: int = 0) -> None:
        protocol = self._require_live(protocol_id)
        if agent_id not in protocol.supporters:
            protocol.supporters.append(agent_id)
        self._event("support", protocol_id, agent_id, tick)
        self._touch(protocol, tick)
        if self._can_adopt(protocol, tick):
            self.adopt(protocol_id, tick=tick, approver_id=agent_id)

    def oppose(self, agent_id: str, protocol_id: str, tick: int = 0) -> None:
        protocol = self._require_live(protocol_id)
        if agent_id not in protocol.opposers:
            protocol.opposers.append(agent_id)
        self._event("oppose", protocol_id, agent_id, tick)
        self._touch(protocol, tick)

    def _can_adopt(self, protocol: Protocol, tick: int) -> bool:
        return (
            protocol.adoption_status == "proposed"
            and len(set(protocol.supporters)) >= self.min_supporters
            and tick - protocol.first_tick >= self.review_ticks
            and len(set(protocol.opposers)) < len(set(protocol.supporters))
        )

    def tick_adoptions(self, tick: int = 0) -> None:
        for protocol in list(self.protocols.values()):
            if self._can_adopt(protocol, tick):
                self.adopt(protocol.protocol_id, tick=tick)
            self.classify_emergence(protocol.protocol_id)

    def adopt(
        self,
        protocol_id: str,
        tick: int = 0,
        *,
        approver_id: str | None = None,
        force: bool = False,
    ) -> bool:
        protocol = self._require_live(protocol_id)
        if protocol.adoption_status != "proposed":
            return False
        if not force and not self._can_adopt(protocol, tick):
            return False
        protocol.adoption_status = "adopted"
        actor = approver_id or sorted(set(protocol.supporters))[0]
        self._event("adoption", protocol_id, actor, tick)
        self._touch(protocol, tick)
        return True

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
        protocol = self._require_adopted(protocol_id)
        event = self._event(
            "use",
            protocol_id,
            agent_id,
            tick,
            self._present(
                context_id=context_id,
                episode_id=episode_id,
                task_id=task_id,
            ),
        )
        protocol.usage_events.append(event.event_id)
        self._touch(protocol, tick)
        self.classify_emergence(protocol_id)
        return event

    def violate(
        self,
        agent_id: str,
        protocol_id: str,
        tick: int = 0,
        *,
        context_id: str | None = None,
    ) -> ProtocolEvent:
        protocol = self._require_adopted(protocol_id)
        event = self._event(
            "violation",
            protocol_id,
            agent_id,
            tick,
            self._present(context_id=context_id),
        )
        protocol.violation_events.append(event.event_id)
        self._touch(protocol, tick)
        return event

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
        state_before: Mapping[str, Any] | None = None,
        state_after: Mapping[str, Any] | None = None,
        **unsupported: Any,
    ) -> ProtocolEvent:
        if unsupported:
            raise ValueError(f"unsupported enforcement fields: {', '.join(sorted(unsupported))}")
        if (state_before is None) != (state_after is None):
            raise ValueError("enforcement_state_transition_requires_both_states")
        protocol = self._require_adopted(protocol_id)
        data = self._present(
            violation_event_id=violation_event_id,
            state_impact_ref=state_impact_ref,
            blocked=blocked,
            context_id=context_id,
        )
        if state_before is not None and state_after is not None:
            data.update(
                {
                    "state_before_hash": stable_fingerprint(dict(state_before)),
                    "state_after_hash": stable_fingerprint(dict(state_after)),
                }
            )
        event = self._event("enforcement", protocol_id, agent_id, tick, data)
        protocol.enforcement_events.append(event.event_id)
        self._touch(protocol, tick)
        self.classify_emergence(protocol_id)
        return event

    def set_impact(self, protocol_id: str, metrics: dict[str, float]) -> None:
        protocol = self._require_adopted(protocol_id)
        protocol.impact_metrics.update({key: float(value) for key, value in metrics.items()})
        self.classify_emergence(protocol_id)

    def amend(
        self,
        agent_id: str,
        protocol_id: str,
        tick: int = 0,
        *,
        revision_kind: str = "clarify",
        source_proposal_id: str | None = None,
        rule_summary: str | None = None,
    ) -> ProtocolEvent:
        protocol = self._require_adopted(protocol_id)
        revision = self._present(
            revision_kind=revision_kind,
            source_proposal_id=source_proposal_id,
            previous_rule_summary=protocol.rule_summary,
            rule_summary=rule_summary,
        )
        if rule_summary:
            protocol.rule_summary = rule_summary
        event = self._event("amendment", protocol_id, agent_id, tick, revision)
        protocol.revisions.append({"event_id": event.event_id, "tick": tick, **revision})
        self._touch(protocol, tick)
        return event

    def retire(
        self,
        agent_id: str,
        protocol_id: str,
        tick: int = 0,
        *,
        source_proposal_id: str | None = None,
    ) -> ProtocolEvent:
        protocol = self._require_adopted(protocol_id)
        protocol.status = "retired"
        protocol.adoption_status = "retired"
        event = self._event(
            "retirement",
            protocol_id,
            agent_id,
            tick,
            self._present(source_proposal_id=source_proposal_id),
        )
        self._touch(protocol, tick)
        return event

    def classify_emergence(self, protocol_id: str) -> str:
        protocol = self.protocols[protocol_id]
        weak = (
            protocol.adoption_status == "adopted"
            and len(protocol.usage_events) >= USE_MIN
            and protocol.persistence_ticks >= PERSIST_MIN
        )
        strong = (
            weak
            and bool(protocol.enforcement_events)
            and any(value > 0 for value in protocol.impact_metrics.values())
        )
        protocol.emergence_level = "strong" if strong else "weak" if weak else "none"
        return protocol.emergence_level

    def emerged(self, level: str = "weak") -> list[Protocol]:
        rank = {"none": 0, "weak": 1, "strong": 2}
        if level not in rank:
            raise ValueError("unknown_emergence_level")
        return [
            protocol
            for protocol in self.protocols.values()
            if rank[self.classify_emergence(protocol.protocol_id)] >= rank[level]
        ]

    def _require_live(self, protocol_id: str) -> Protocol:
        protocol = self.protocols[protocol_id]
        if not protocol_is_live(protocol):
            raise ValueError("protocol_not_live")
        return protocol

    def _require_adopted(self, protocol_id: str) -> Protocol:
        protocol = self._require_live(protocol_id)
        if protocol.adoption_status != "adopted":
            raise ValueError("protocol_not_adopted")
        return protocol

    @staticmethod
    def _present(**values: Any) -> dict[str, Any]:
        return {key: value for key, value in values.items() if value not in {None, "", False}}

    @staticmethod
    def _touch(protocol: Protocol, tick: int) -> None:
        protocol.last_active_tick = max(protocol.last_active_tick, tick)
        protocol.persistence_ticks = max(0, protocol.last_active_tick - protocol.first_tick)


__all__ = [
    "ADOPT_MIN_SUPPORTERS",
    "PERSIST_MIN",
    "REVIEW_MIN_TICKS",
    "USE_MIN",
    "ProtocolRegistry",
    "effective_min_supporters",
    "protocol_is_live",
]
