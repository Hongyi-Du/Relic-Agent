"""Proposal, tool, and protocol specifications used by organization governance."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

PROPOSAL_TYPES = (
    "task_proposal",
    "tool_proposal",
    "workflow_proposal",
    "protocol_proposal",
    "role_proposal",
    "policy_repair_proposal",
    "artifact_template_proposal",
)


@dataclass
class Proposal:
    proposal_id: str
    proposal_type: str
    title: str = ""
    summary: str = ""
    proposer_agent_id: str | None = None
    source_wish_id: str | None = None
    source_wish_ids: list[str] = field(default_factory=list)
    source_reflection_id: str | None = None
    source_episode_id: str | None = None
    source_episode_ids: list[str] = field(default_factory=list)
    source_event_ids: list[str] = field(default_factory=list)
    target_problem: str = ""
    proposed_solution: str = ""
    required_actions: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    required_artifacts: list[str] = field(default_factory=list)
    required_participants: list[str] = field(default_factory=list)
    affected_agents: list[str] = field(default_factory=list)
    affected_objects: list[str] = field(default_factory=list)
    affected_protocols: list[str] = field(default_factory=list)
    expected_benefits: list[str] = field(default_factory=list)
    expected_costs: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)
    feasibility_score: float | None = None
    usefulness_score: float | None = None
    risk_score: float | None = None
    adoption_score: float | None = None
    suggested_revision: str = ""
    family: str = ""
    status: str = "draft"
    approval_required_from: list[str] = field(default_factory=list)
    approved_by: list[str] = field(default_factory=list)
    supporters: list[str] = field(default_factory=list)
    rejected_by: list[str] = field(default_factory=list)
    rejection_reason: str = ""
    object_created_id: str | None = None
    adopted_tick: int | None = None
    impact: dict[str, Any] = field(default_factory=dict)
    created_at_tick: int = 0
    updated_at_tick: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolSpec:
    tool_id: str
    name: str
    description: str = ""
    created_from_proposal_id: str | None = None
    creator_agent_id: str | None = None
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    required_actions: list[str] = field(default_factory=list)
    required_permissions: list[str] = field(default_factory=list)
    callable_by_roles: list[str] = field(default_factory=list)
    status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProtocolSpec:
    protocol_id: str
    name: str
    created_from_proposal_id: str | None = None
    trigger_condition: str = ""
    required_steps: list[str] = field(default_factory=list)
    enforcement_rule: str = ""
    exception_rule: str | None = None
    scope: str = "organization"
    responsible_roles: dict[str, list[str]] = field(default_factory=dict)
    success_metric: str = ""
    status: str = "proposed"
    revision: int = 0
    revisions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = ["PROPOSAL_TYPES", "Proposal", "ProtocolSpec", "ToolSpec"]
