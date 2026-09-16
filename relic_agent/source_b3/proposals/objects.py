"""Proposal / ToolSpec / ProtocolSpec from the authoritative HCI source.

This is a source-blob port of
``environments/org_env/proposals/objects.py`` at the pinned HCI revision.
Only its import location changes; the data contracts intentionally remain
source-shaped rather than being reduced to the compatibility runtime's former
story-specific schemas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


PROPOSAL_TYPES = (
    "task_proposal",
    "tool_proposal",
    "workflow_proposal",
    "protocol_proposal",
    "role_proposal",
    "policy_repair_proposal",
    "artifact_template_proposal",
)
PROPOSAL_STATUS = (
    "draft",
    "under_review",
    "approved",
    "rejected",
    "adopted",
    "implemented",
    "failed",
)


def ensure_list(x: Any) -> List[str]:
    """Normalize an LLM-provided value without splitting bare strings."""

    if x is None:
        return []
    if isinstance(x, str):
        s = x.strip()
        return [s] if s else []
    if isinstance(x, (list, tuple, set)):
        out = []
        for v in x:
            if isinstance(v, str):
                v = v.strip()
                if v:
                    out.append(v)
            elif v is not None:
                out.append(str(v))
        return out
    return [str(x)]


@dataclass
class Proposal:
    proposal_id: str
    proposal_type: str
    title: str = ""
    summary: str = ""
    proposer_agent_id: Optional[str] = None

    source_wish_id: Optional[str] = None
    source_wish_ids: List[str] = field(default_factory=list)
    source_reflection_id: Optional[str] = None
    source_episode_id: Optional[str] = None
    source_episode_ids: List[str] = field(default_factory=list)
    source_event_ids: List[str] = field(default_factory=list)

    target_problem: str = ""
    proposed_solution: str = ""

    required_actions: List[str] = field(default_factory=list)
    required_capabilities: List[str] = field(default_factory=list)
    required_artifacts: List[str] = field(default_factory=list)
    required_participants: List[str] = field(default_factory=list)

    affected_agents: List[str] = field(default_factory=list)
    affected_objects: List[str] = field(default_factory=list)
    affected_protocols: List[str] = field(default_factory=list)

    expected_benefits: List[str] = field(default_factory=list)
    expected_costs: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    failure_modes: List[str] = field(default_factory=list)

    feasibility_score: Optional[float] = None
    usefulness_score: Optional[float] = None
    risk_score: Optional[float] = None
    adoption_score: Optional[float] = None
    suggested_revision: str = ""

    family: str = ""
    status: str = "draft"
    approval_required_from: List[str] = field(default_factory=list)
    approved_by: List[str] = field(default_factory=list)
    supporters: List[str] = field(default_factory=list)
    rejected_by: List[str] = field(default_factory=list)
    rejection_reason: str = ""
    object_created_id: Optional[str] = None
    adopted_tick: Optional[int] = None
    impact: Dict[str, Any] = field(default_factory=dict)
    amends_protocol_id: Optional[str] = None
    repair_kind: Optional[str] = None
    repair_target_protocol_id: Optional[str] = None

    created_at_tick: int = 0
    updated_at_tick: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            key: (list(value) if isinstance(value, list) else value)
            for key, value in self.__dict__.items()
        }


@dataclass
class ToolSpec:
    tool_id: str
    name: str
    description: str = ""
    created_from_proposal_id: Optional[str] = None
    creator_agent_id: Optional[str] = None
    source_wish_id: Optional[str] = None
    source_episode_id: Optional[str] = None
    source_episode_ids: List[str] = field(default_factory=list)
    tool_type: str = "composed_action_tool"
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    required_actions: List[str] = field(default_factory=list)
    required_capabilities: List[str] = field(default_factory=list)
    required_permissions: List[str] = field(default_factory=list)
    risk_tags: List[str] = field(default_factory=list)
    validation_rules: List[str] = field(default_factory=list)
    callable_by_roles: List[str] = field(default_factory=list)
    callable_by_agents: List[str] = field(default_factory=list)
    family: str = ""
    supporters: List[str] = field(default_factory=list)
    support_count: int = 0
    folded_proposal_ids: List[str] = field(default_factory=list)
    status: str = "active"
    created_at_tick: int = 0
    adopted_at_tick: int = 0
    updated_at_tick: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            key: (
                list(value)
                if isinstance(value, list)
                else (dict(value) if isinstance(value, dict) else value)
            )
            for key, value in self.__dict__.items()
        }


@dataclass
class ProtocolSpec:
    protocol_id: str
    name: str = ""
    created_from_proposal_id: Optional[str] = None
    source_episode_id: Optional[str] = None
    source_episode_ids: List[str] = field(default_factory=list)
    source_wish_id: Optional[str] = None
    source_wish_ids: List[str] = field(default_factory=list)
    source_reflection_id: Optional[str] = None
    trigger_condition: str = ""
    required_steps: List[str] = field(default_factory=list)
    required_fields: List[str] = field(default_factory=list)
    enforcement_rule: str = ""
    violation_condition: str = ""
    exception_rule: Optional[str] = None
    family: str = ""
    problem_evidence: List[str] = field(default_factory=list)
    scope: str = ""
    responsible_roles: Dict[str, List[str]] = field(default_factory=dict)
    success_metric: str = ""
    enforcement_action: str = ""
    sunset_rule: str = ""
    affected_agents: List[str] = field(default_factory=list)
    affected_actions: List[str] = field(default_factory=list)
    affected_artifacts: List[str] = field(default_factory=list)
    benefits: List[str] = field(default_factory=list)
    costs: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    status: str = "proposed"
    proposed_by: Optional[str] = None
    adopted_by: List[str] = field(default_factory=list)
    enforced_by: List[str] = field(default_factory=list)
    created_at_tick: int = 0
    adopted_at_tick: Optional[int] = None
    revision: int = 0
    last_revised_tick: Optional[int] = None
    revisions: List[Dict[str, Any]] = field(default_factory=list)
    superseded_proposal_ids: List[str] = field(default_factory=list)
    use_count: int = 0
    enforcement_count: int = 0
    violation_count: int = 0
    last_used_tick: Optional[int] = None
    affected_action_ids: List[str] = field(default_factory=list)
    affected_task_ids: List[str] = field(default_factory=list)
    use_event_ids: List[str] = field(default_factory=list)
    enforcement_event_ids: List[str] = field(default_factory=list)
    violation_event_ids: List[str] = field(default_factory=list)

    def declared_action_ids(self) -> set:
        out = set()
        for action in self.affected_actions or []:
            if isinstance(action, dict):
                action = action.get("action") or action.get("action_type") or action.get("name") or ""
            action = str(action).strip().lower().replace(" ", "_").replace("-", "_")
            if action:
                out.add(action)
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            key: (list(value) if isinstance(value, list) else value)
            for key, value in self.__dict__.items()
        }


__all__ = [
    "Proposal",
    "ToolSpec",
    "ProtocolSpec",
    "PROPOSAL_TYPES",
    "PROPOSAL_STATUS",
    "ensure_list",
]
