"""Compatibility trace ledger, not an HCI source-event contract.

New source episode integrations must pass source-shaped world events or an
``ExecutionResult`` to :mod:`relic_agent.source_b3.episodes`; this small ledger
remains only so the existing public ``relic-trace-v1`` release/Inspector path
can run while the complete HCI host adapter is unavailable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Event:
    event_id: str
    tick: int
    event_type: str
    actor_id: str = ""
    object_ids: tuple[str, ...] = ()
    payload: dict[str, Any] = field(default_factory=dict)
    visibility: str = "organization"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["object_ids"] = list(self.object_ids)
        return payload


class EventStore:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self._sequence = 0

    def emit(
        self,
        *,
        tick: int,
        event_type: str,
        actor_id: str = "",
        object_ids: tuple[str, ...] = (),
        payload: dict[str, Any] | None = None,
        visibility: str = "organization",
    ) -> Event:
        self._sequence += 1
        event = Event(
            event_id=f"event_{self._sequence:06d}",
            tick=tick,
            event_type=event_type,
            actor_id=actor_id,
            object_ids=tuple(object_ids),
            payload=dict(payload or {}),
            visibility=visibility,
        )
        self.events.append(event)
        return event

    def public_since(self, index: int) -> list[dict[str, Any]]:
        return [
            event.to_dict()
            for event in self.events[index:]
            if event.visibility in {"organization", "public"}
        ]
