"""Append-only shadow runtime for organization events.

The first extraction stage observes an existing harness without taking over its
loop.  This runtime therefore reconstructs portable organizational state from
events but makes no decisions and performs no domain mutations.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Iterable

from organization_core.contracts import OrganizationEvent, OrganizationEventType


@dataclass(frozen=True)
class OrganizationSnapshot:
    organization_id: str
    run_id: str
    last_step: int
    event_count: int
    members: tuple[dict, ...] = ()
    last_action_by_actor: dict[str, str] = field(default_factory=dict)
    successful_actions: int = 0
    failed_actions: int = 0

    def as_dict(self) -> dict:
        return {
            "organization_id": self.organization_id,
            "run_id": self.run_id,
            "last_step": self.last_step,
            "event_count": self.event_count,
            "members": copy.deepcopy(list(self.members)),
            "last_action_by_actor": dict(self.last_action_by_actor),
            "successful_actions": self.successful_actions,
            "failed_actions": self.failed_actions,
        }


class ShadowOrganizationRuntime:
    """Idempotent event consumer used while the legacy host remains authoritative."""

    def __init__(self, organization_id: str, run_id: str):
        if not organization_id or not run_id:
            raise ValueError("organization_id and run_id are required")
        self.organization_id = str(organization_id)
        self.run_id = str(run_id)
        self._events: list[OrganizationEvent] = []
        self._event_payloads: dict[str, dict] = {}
        self._last_step = 0
        self._members: tuple[dict, ...] = ()
        self._last_action_by_actor: dict[str, str] = {}
        self._successful_actions = 0
        self._failed_actions = 0

    @property
    def events(self) -> tuple[OrganizationEvent, ...]:
        return tuple(self._events)

    def publish(self, event: OrganizationEvent) -> bool:
        """Consume an event; return False for an identical duplicate.

        Reusing an id with different content is corruption and fails closed.
        Step order may stay equal within a harness tick, but cannot move back.
        """
        if event.provenance.run_id != self.run_id:
            raise ValueError(
                f"event run_id {event.provenance.run_id!r} does not match {self.run_id!r}"
            )
        payload = event.as_dict()
        existing = self._event_payloads.get(event.event_id)
        if existing is not None:
            if existing != payload:
                raise ValueError(f"event id reused with different content: {event.event_id}")
            return False
        if event.step < self._last_step:
            raise ValueError(
                f"event step moved backwards: {event.step} < {self._last_step}"
            )
        self._events.append(event)
        self._event_payloads[event.event_id] = payload
        self._last_step = event.step
        self._reduce(event)
        return True

    def replay(self, events: Iterable[OrganizationEvent]) -> None:
        for event in events:
            self.publish(event)

    def snapshot(self) -> OrganizationSnapshot:
        return OrganizationSnapshot(
            organization_id=self.organization_id,
            run_id=self.run_id,
            last_step=self._last_step,
            event_count=len(self._events),
            members=self._members,
            last_action_by_actor=dict(self._last_action_by_actor),
            successful_actions=self._successful_actions,
            failed_actions=self._failed_actions,
        )

    def _reduce(self, event: OrganizationEvent) -> None:
        if event.event_type == OrganizationEventType.ORGANIZATION_INITIALIZED.value:
            raw_members = event.payload.get("members", [])
            self._members = tuple(copy.deepcopy(dict(member)) for member in raw_members)
            return
        if event.event_type != OrganizationEventType.ACTION_EXECUTED.value:
            return
        if event.actor_id:
            self._last_action_by_actor[event.actor_id] = str(
                event.payload.get("action_type") or ""
            )
        if bool(event.payload.get("success")):
            self._successful_actions += 1
        else:
            self._failed_actions += 1


__all__ = ["OrganizationSnapshot", "ShadowOrganizationRuntime"]
