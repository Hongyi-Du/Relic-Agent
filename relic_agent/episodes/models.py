"""Generic event episode schema for organization replay and reflection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def classify_object(object_id: str) -> str:
    prefix = str(object_id).split("_", 1)[0]
    return {
        "task": "task",
        "protocol": "protocol",
        "proposal": "proposal",
        "event": "event",
        "doc": "document",
        "artifact": "artifact",
        "tool": "tool",
    }.get(prefix, "object")


@dataclass
class OrgEpisode:
    episode_id: str
    episode_type: str
    start_tick: int
    end_tick: int | None = None
    status: str = "open"
    trigger_event_id: str | None = None
    trigger_object_id: str | None = None
    participants: list[str] = field(default_factory=list)
    primary_agent_id: str | None = None
    linked_event_ids: list[str] = field(default_factory=list)
    linked_task_ids: list[str] = field(default_factory=list)
    linked_protocol_ids: list[str] = field(default_factory=list)
    linked_proposal_ids: list[str] = field(default_factory=list)
    linked_artifact_ids: list[str] = field(default_factory=list)
    linked_object_ids: list[str] = field(default_factory=list)
    linked_reflection_ids: list[str] = field(default_factory=list)
    linked_wish_ids: list[str] = field(default_factory=list)
    problem_statement: str = ""
    conflict_summary: str | None = None
    decision_summary: str | None = None
    outcome_summary: str | None = None
    produced_artifacts: list[str] = field(default_factory=list)
    produced_protocols: list[str] = field(default_factory=list)
    produced_tasks: list[str] = field(default_factory=list)
    state_delta: dict[str, Any] = field(default_factory=dict)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    title: str = ""
    created_at_tick: int = 0
    updated_at_tick: int = 0

    def link_object(self, object_id: str) -> None:
        if not object_id or object_id in self.linked_object_ids:
            return
        self.linked_object_ids.append(object_id)
        kind = classify_object(object_id)
        destination = {
            "task": self.linked_task_ids,
            "protocol": self.linked_protocol_ids,
            "proposal": self.linked_proposal_ids,
            "artifact": self.linked_artifact_ids,
        }.get(kind)
        if destination is not None and object_id not in destination:
            destination.append(object_id)

    def add_participant(self, agent_id: str | None) -> None:
        if agent_id and agent_id not in self.participants:
            self.participants.append(agent_id)


__all__ = ["OrgEpisode", "classify_object"]
