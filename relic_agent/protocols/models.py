"""Protocol state and append-only lifecycle events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PROTOCOL_EVENT_TYPES = (
    "proposal",
    "support",
    "oppose",
    "adoption",
    "use",
    "violation",
    "enforcement",
    "amendment",
    "retirement",
    "impact",
)


@dataclass(frozen=True)
class ProtocolEvent:
    event_id: str
    event_type: str
    protocol_id: str
    actor_id: str = ""
    tick: int = 0
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Protocol:
    protocol_id: str
    protocol_type: str
    proposer_id: str = ""
    proposal_event_id: str = ""
    rule_summary: str = ""
    scope: str = "organization"
    target_process: str = ""
    supporters: list[str] = field(default_factory=list)
    opposers: list[str] = field(default_factory=list)
    adoption_status: str = "proposed"
    usage_events: list[str] = field(default_factory=list)
    violation_events: list[str] = field(default_factory=list)
    enforcement_events: list[str] = field(default_factory=list)
    revisions: list[dict[str, Any]] = field(default_factory=list)
    first_tick: int = 0
    last_active_tick: int = 0
    persistence_ticks: int = 0
    impact_metrics: dict[str, float] = field(default_factory=dict)
    emergence_level: str = "none"
    status: str = "active"


__all__ = ["PROTOCOL_EVENT_TYPES", "Protocol", "ProtocolEvent"]
