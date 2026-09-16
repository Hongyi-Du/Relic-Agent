"""Portable proposal review lifecycle for organizational formation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from organization_core.approval import (
    ApprovalCheckDecision,
    ApprovalCheckRequest,
    ApprovalMember,
    ApprovalPolicy,
    ApprovalRoutingRequest,
    RoleApprovalRouter,
)


PROPOSAL_REVIEW_STATUSES = (
    "draft",
    "under_review",
    "approved",
    "rejected",
    "adopted",
    "implemented",
    "failed",
)

ORGANIZATION_PROPOSAL_TYPES = (
    "task_proposal",
    "tool_proposal",
    "workflow_proposal",
    "protocol_proposal",
    "role_proposal",
    "policy_repair_proposal",
    "artifact_template_proposal",
)


@dataclass(frozen=True)
class ProposalValidationRequest:
    proposal_id: str
    proposal_type: str
    title: str
    summary: str
    required_actions: tuple[str, ...] = ()
    known_actions: tuple[str, ...] = ()
    active_tool_names: tuple[str, ...] = ()
    adopted_protocol_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.proposal_id or "").strip():
            raise ValueError("proposal validation proposal_id is required")
        for name in (
            "required_actions",
            "known_actions",
            "active_tool_names",
            "adopted_protocol_names",
        ):
            object.__setattr__(
                self,
                name,
                tuple(dict.fromkeys(str(value) for value in getattr(self, name))),
            )


@dataclass(frozen=True)
class ProposalValidationDecision:
    accepted: bool
    reason_codes: tuple[str, ...] = ()
    reason: str = ""


class ProposalValidationPolicy:
    """Portable reality checks before a draft can enter review."""

    def __init__(
        self,
        allowed_types: tuple[str, ...] = ORGANIZATION_PROPOSAL_TYPES,
    ) -> None:
        self.allowed_types = tuple(dict.fromkeys(allowed_types))
        if not self.allowed_types:
            raise ValueError("proposal validation requires allowed types")

    def validate(
        self,
        request: ProposalValidationRequest,
    ) -> ProposalValidationDecision:
        if request.proposal_type not in self.allowed_types:
            return ProposalValidationDecision(
                accepted=False,
                reason_codes=("illegal_proposal_type",),
                reason=f"illegal proposal_type '{request.proposal_type}'",
            )
        if not str(request.title or "").strip() or not str(request.summary or "").strip():
            return ProposalValidationDecision(
                accepted=False,
                reason_codes=("missing_title_or_summary",),
                reason="missing title/summary",
            )
        known = set(request.known_actions)
        if known:
            for action in request.required_actions:
                if action not in known:
                    return ProposalValidationDecision(
                        accepted=False,
                        reason_codes=("unknown_required_action",),
                        reason=f"required action '{action}' does not exist",
                    )
        if request.title in set(request.active_tool_names):
            return ProposalValidationDecision(
                accepted=False,
                reason_codes=("duplicate_active_tool",),
                reason="duplicate of an active tool",
            )
        if request.title in set(request.adopted_protocol_names):
            return ProposalValidationDecision(
                accepted=False,
                reason_codes=("duplicate_adopted_protocol",),
                reason="duplicate of an adopted protocol",
            )
        return ProposalValidationDecision(
            accepted=True,
            reason_codes=("proposal_valid",),
        )


@dataclass(frozen=True)
class ProposalApproverRule:
    proposal_type: str
    required_roles: tuple[str, ...]

    def __post_init__(self) -> None:
        if not str(self.proposal_type or "").strip():
            raise ValueError("proposal_type is required")
        object.__setattr__(
            self,
            "required_roles",
            tuple(dict.fromkeys(str(role) for role in self.required_roles if role)),
        )


@dataclass(frozen=True)
class ProposalLifecycleConfig:
    approver_rules: tuple[ProposalApproverRule, ...] = (
        ProposalApproverRule("protocol_proposal", ("founder", "cofounder")),
        ProposalApproverRule("policy_repair_proposal", ("founder", "cofounder")),
        ProposalApproverRule("role_proposal", ("founder", "cofounder")),
        ProposalApproverRule("tool_proposal", ("cofounder", "reliability")),
        ProposalApproverRule("workflow_proposal", ("cofounder", "reliability")),
        ProposalApproverRule("artifact_template_proposal", ("cofounder",)),
        ProposalApproverRule("task_proposal", ("founder", "cofounder")),
    )
    fallback_required_roles: tuple[str, ...] = ("founder", "cofounder")
    min_review_steps: int = 2
    min_protocol_review_steps: int = 3
    min_task_review_steps: int = 0
    min_distinct_protocol_approvers: int = 2
    fallback_min_approvers: int = 2

    def __post_init__(self) -> None:
        rules = tuple(self.approver_rules)
        if not all(isinstance(rule, ProposalApproverRule) for rule in rules):
            raise TypeError("approver_rules must contain ProposalApproverRule values")
        proposal_types = [rule.proposal_type for rule in rules]
        if len(proposal_types) != len(set(proposal_types)):
            raise ValueError("approver_rules proposal types must be unique")
        object.__setattr__(self, "approver_rules", rules)
        object.__setattr__(
            self,
            "fallback_required_roles",
            tuple(dict.fromkeys(self.fallback_required_roles)),
        )
        for name in (
            "min_review_steps",
            "min_protocol_review_steps",
            "min_task_review_steps",
            "min_distinct_protocol_approvers",
            "fallback_min_approvers",
        ):
            value = int(getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)

    def required_roles_for(self, proposal_type: str) -> tuple[str, ...]:
        for rule in self.approver_rules:
            if rule.proposal_type == proposal_type:
                return rule.required_roles
        return self.fallback_required_roles

    def min_review_steps_for(self, proposal_type: str) -> int:
        if proposal_type == "protocol_proposal":
            return self.min_protocol_review_steps
        if proposal_type == "task_proposal":
            return self.min_task_review_steps
        return self.min_review_steps


DEFAULT_PROPOSAL_LIFECYCLE_CONFIG = ProposalLifecycleConfig()


@dataclass(frozen=True)
class ProposalLifecycleState:
    proposal_id: str
    proposal_type: str
    status: str = "draft"
    created_step: int = 0
    updated_step: int = 0
    approval_required_from: tuple[str, ...] = ()
    approved_by: tuple[str, ...] = ()
    rejected_by: tuple[str, ...] = ()
    rejection_reason: str = ""

    def __post_init__(self) -> None:
        if not str(self.proposal_id or "").strip():
            raise ValueError("proposal_id is required")
        if not str(self.proposal_type or "").strip():
            raise ValueError("proposal_type is required")
        if self.status not in PROPOSAL_REVIEW_STATUSES:
            raise ValueError(f"unsupported proposal lifecycle status: {self.status}")
        object.__setattr__(self, "created_step", int(self.created_step))
        object.__setattr__(self, "updated_step", int(self.updated_step))
        for name in ("approval_required_from", "approved_by", "rejected_by"):
            object.__setattr__(
                self, name, tuple(str(value) for value in getattr(self, name))
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "proposal_type": self.proposal_type,
            "status": self.status,
            "created_step": self.created_step,
            "updated_step": self.updated_step,
            "approval_required_from": list(self.approval_required_from),
            "approved_by": list(self.approved_by),
            "rejected_by": list(self.rejected_by),
            "rejection_reason": self.rejection_reason,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProposalLifecycleState":
        return cls(
            proposal_id=str(payload.get("proposal_id") or ""),
            proposal_type=str(payload.get("proposal_type") or ""),
            status=str(payload.get("status") or "draft"),
            created_step=int(payload.get("created_step") or 0),
            updated_step=int(payload.get("updated_step") or 0),
            approval_required_from=tuple(payload.get("approval_required_from") or ()),
            approved_by=tuple(payload.get("approved_by") or ()),
            rejected_by=tuple(payload.get("rejected_by") or ()),
            rejection_reason=str(payload.get("rejection_reason") or ""),
        )


@dataclass(frozen=True)
class ProposalLifecycleDecision:
    state: ProposalLifecycleState
    accepted: bool
    ready_for_adoption: bool = False
    reason_codes: tuple[str, ...] = ()


class ProposalLifecyclePolicy:
    """Own review routing and transitions, never host-specific adoption effects."""

    def __init__(
        self,
        config: ProposalLifecycleConfig = DEFAULT_PROPOSAL_LIFECYCLE_CONFIG,
    ) -> None:
        if not isinstance(config, ProposalLifecycleConfig):
            raise TypeError("config must be ProposalLifecycleConfig")
        self.config = config
        self._approval = ApprovalPolicy()
        self._router = RoleApprovalRouter()

    def route_for_approval(
        self,
        state: ProposalLifecycleState,
        *,
        members: tuple[ApprovalMember, ...],
        current_step: int,
    ) -> ProposalLifecycleDecision:
        roles = self.config.required_roles_for(state.proposal_type)
        routed = self._router.route(
            ApprovalRoutingRequest(
                request_id=f"approval-route:{state.proposal_id}",
                members=members,
                required_roles=roles,
            )
        )
        return ProposalLifecycleDecision(
            state=replace(
                state,
                status="under_review",
                approval_required_from=routed.approver_ids,
                updated_step=int(current_step),
            ),
            accepted=True,
            reason_codes=("proposal_routed_for_approval",),
        )

    def record_approval(
        self,
        state: ProposalLifecycleState,
        *,
        approver_id: str,
        current_step: int,
    ) -> ProposalLifecycleDecision:
        if state.status not in {"draft", "under_review"}:
            return ProposalLifecycleDecision(
                state=state,
                accepted=False,
                reason_codes=("proposal_not_reviewable",),
            )
        approved_by = state.approved_by
        if approver_id not in approved_by:
            approved_by = (*approved_by, str(approver_id))
        candidate = replace(state, approved_by=approved_by)
        readiness = self.evaluate_readiness(candidate, current_step=current_step)
        return ProposalLifecycleDecision(
            state=readiness.state,
            accepted=True,
            ready_for_adoption=readiness.ready_for_adoption,
            reason_codes=readiness.reason_codes,
        )

    def record_rejection(
        self,
        state: ProposalLifecycleState,
        *,
        rejector_id: str,
        reason: str,
    ) -> ProposalLifecycleDecision:
        return ProposalLifecycleDecision(
            state=replace(
                state,
                status="rejected",
                rejected_by=(*state.rejected_by, str(rejector_id)),
                rejection_reason=str(reason),
            ),
            accepted=True,
            reason_codes=("proposal_rejected",),
        )

    def evaluate_readiness(
        self,
        state: ProposalLifecycleState,
        *,
        current_step: int,
    ) -> ProposalLifecycleDecision:
        quorum = self.quorum_decision(state)
        review = self.review_decision(state, current_step=current_step)
        reasons = (*quorum.reason_codes, *review.reason_codes)
        ready = quorum.ready and review.ready
        return ProposalLifecycleDecision(
            state=replace(state, status="approved") if ready else state,
            accepted=True,
            ready_for_adoption=ready,
            reason_codes=reasons,
        )

    def quorum_decision(
        self,
        state: ProposalLifecycleState,
    ) -> ApprovalCheckDecision:
        return self._approval.evaluate(
            ApprovalCheckRequest(
                request_id=f"approval-quorum:{state.proposal_id}",
                required_approver_ids=state.approval_required_from,
                approved_by_ids=state.approved_by,
                fallback_min_approvers=self.config.fallback_min_approvers,
            )
        )

    def review_decision(
        self,
        state: ProposalLifecycleState,
        *,
        current_step: int,
    ) -> ApprovalCheckDecision:
        # Preserve legacy tick-zero semantics: a zero updated step falls back to
        # the creation step exactly as ProposalManager historically did.
        review_entry = state.updated_step or state.created_step or 0
        is_protocol = state.proposal_type == "protocol_proposal"
        return self._approval.evaluate(
            ApprovalCheckRequest(
                request_id=f"approval-readiness:{state.proposal_id}",
                approved_by_ids=state.approved_by,
                elapsed_steps=int(current_step) - review_entry,
                min_review_steps=self.config.min_review_steps_for(state.proposal_type),
                min_distinct_approvers=(
                    self.config.min_distinct_protocol_approvers if is_protocol else 0
                ),
            )
        )


__all__ = [
    "DEFAULT_PROPOSAL_LIFECYCLE_CONFIG",
    "ORGANIZATION_PROPOSAL_TYPES",
    "PROPOSAL_REVIEW_STATUSES",
    "ProposalApproverRule",
    "ProposalLifecycleConfig",
    "ProposalLifecycleDecision",
    "ProposalLifecyclePolicy",
    "ProposalLifecycleState",
    "ProposalValidationDecision",
    "ProposalValidationPolicy",
    "ProposalValidationRequest",
]
