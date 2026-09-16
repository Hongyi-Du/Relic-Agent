"""Private reflection, wish, and durable memory schemas."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

WISH_TYPES = (
    "tool_need",
    "protocol_need",
    "workflow_need",
    "artifact_need",
    "role_clarity_need",
    "resource_need",
    "coordination_need",
    "information_need",
    "policy_repair_need",
)

NEED_TYPE_MAP = {
    "technical": "tool_need",
    "tool": "tool_need",
    "process": "workflow_need",
    "workflow": "workflow_need",
    "documentation": "artifact_need",
    "artifact": "artifact_need",
    "evaluation": "protocol_need",
    "protocol": "protocol_need",
    "quality_gate": "protocol_need",
    "coordination": "coordination_need",
    "role": "role_clarity_need",
    "ownership": "role_clarity_need",
    "resource": "resource_need",
    "information": "information_need",
    "policy_repair": "policy_repair_need",
    "amend_protocol": "policy_repair_need",
}


def canon_wish_type(raw: str) -> str:
    key = str(raw or "").strip().lower().replace(" ", "_")
    return NEED_TYPE_MAP.get(key, key if key in WISH_TYPES else "workflow_need")


def support_type_for(wish_type: str) -> str:
    return wish_type.removesuffix("_need") if wish_type.endswith("_need") else "workflow"


def make_wish_fingerprint(
    wish_type: str,
    target_problem: str,
    related_object_ids: list[str] | None = None,
) -> str:
    normalized_problem = " ".join(str(target_problem or "").lower().split())[:80]
    objects = ",".join(sorted(set(related_object_ids or [])))
    return f"{wish_type}|{normalized_problem}|{objects}"


@dataclass
class AgentReflection:
    reflection_id: str
    agent_id: str
    tick: int
    source_episode_ids: list[str] = field(default_factory=list)
    source_event_ids: list[str] = field(default_factory=list)
    source_object_ids: list[str] = field(default_factory=list)
    self_assessment: str = ""
    team_assessment: str = ""
    perceived_blockers: list[str] = field(default_factory=list)
    perceived_repeated_failures: list[str] = field(default_factory=list)
    perceived_team_needs: list[str] = field(default_factory=list)
    perceived_self_needs: list[str] = field(default_factory=list)
    improvement_ideas: list[dict[str, Any]] = field(default_factory=list)
    raw_text: str = ""
    llm_model: str | None = None
    trigger_reason: str = ""
    created_wish_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Wish:
    wish_id: str
    agent_id: str
    source_reflection_id: str = ""
    source_reflection_ids: list[str] = field(default_factory=list)
    supporting_agent_ids: list[str] = field(default_factory=list)
    support_count: int = 1
    source_episode_id: str | None = None
    source_event_ids: list[str] = field(default_factory=list)
    raw_reflection_excerpt: str = ""
    wish_type: str = "workflow_need"
    fingerprint: str = ""
    interpreted_need: str = ""
    target_problem: str = ""
    self_related: bool = False
    team_related: bool = True
    suggested_improvement: str = ""
    missing_support_type: str = "workflow"
    urgency: float = 0.5
    expected_benefit: str = ""
    risk_if_unaddressed: str = ""
    related_object_ids: list[str] = field(default_factory=list)
    related_issue_ids: list[str] = field(default_factory=list)
    related_agent_ids: list[str] = field(default_factory=list)
    related_episode_ids: list[str] = field(default_factory=list)
    related_channel_ids: list[str] = field(default_factory=list)
    status: str = "open"
    generated_proposal_ids: list[str] = field(default_factory=list)
    created_at_tick: int = 0
    updated_at_tick: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentMemory:
    agent_id: str
    reflections: list[str] = field(default_factory=list)
    lessons_learned: list[str] = field(default_factory=list)
    unresolved_needs: list[str] = field(default_factory=list)
    repeated_blockers: list[str] = field(default_factory=list)
    commitments: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    conversation: list[str] = field(default_factory=list)
    conversation_message_ids: list[str] = field(default_factory=list)
    last_reflection_tick: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentLogEntry:
    log_id: str
    agent_id: str
    tick: int
    entry_type: str
    summary: str = ""
    related_event_ids: list[str] = field(default_factory=list)
    related_episode_ids: list[str] = field(default_factory=list)
    related_object_ids: list[str] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = [
    "AgentLogEntry",
    "AgentMemory",
    "AgentReflection",
    "NEED_TYPE_MAP",
    "WISH_TYPES",
    "Wish",
    "canon_wish_type",
    "make_wish_fingerprint",
    "support_type_for",
]
