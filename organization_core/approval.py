"""Portable role routing and quorum checks for organizational approvals."""

from __future__ import annotations

from dataclasses import dataclass

from organization_core.contracts import DecisionDisposition, OrganizationDecision


def _refs(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value or "")))


@dataclass(frozen=True)
class ApprovalMember:
    member_id: str
    role: str

    def __post_init__(self) -> None:
        if not str(self.member_id or "").strip():
            raise ValueError("member_id is required")


@dataclass(frozen=True)
class ApprovalRoutingRequest:
    request_id: str
    members: tuple[ApprovalMember, ...]
    required_roles: tuple[str, ...]

    def __post_init__(self) -> None:
        if not str(self.request_id or "").strip():
            raise ValueError("request_id is required")
        object.__setattr__(self, "members", tuple(self.members))
        if not all(isinstance(member, ApprovalMember) for member in self.members):
            raise TypeError("members must contain ApprovalMember values")
        object.__setattr__(self, "required_roles", _refs(self.required_roles))


@dataclass(frozen=True)
class ApprovalRoutingDecision:
    request_id: str
    approver_ids: tuple[str, ...]
    required_roles: tuple[str, ...]


class RoleApprovalRouter:
    def route(self, request: ApprovalRoutingRequest) -> ApprovalRoutingDecision:
        roles = set(request.required_roles)
        return ApprovalRoutingDecision(
            request_id=request.request_id,
            approver_ids=tuple(
                member.member_id
                for member in request.members
                if member.role in roles
            ),
            required_roles=request.required_roles,
        )


@dataclass(frozen=True)
class ApprovalCheckRequest:
    request_id: str
    required_approver_ids: tuple[str, ...] = ()
    approved_by_ids: tuple[str, ...] = ()
    elapsed_steps: int = 0
    min_review_steps: int = 0
    min_distinct_approvers: int = 0
    fallback_min_approvers: int = 0

    def __post_init__(self) -> None:
        if not str(self.request_id or "").strip():
            raise ValueError("request_id is required")
        object.__setattr__(
            self, "required_approver_ids", _refs(self.required_approver_ids)
        )
        object.__setattr__(self, "approved_by_ids", _refs(self.approved_by_ids))
        object.__setattr__(self, "elapsed_steps", int(self.elapsed_steps))
        for name in (
            "min_review_steps",
            "min_distinct_approvers",
            "fallback_min_approvers",
        ):
            value = int(getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class ApprovalCheckDecision:
    request_id: str
    ready: bool
    missing_approver_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()

    def as_organization_decision(self) -> OrganizationDecision:
        return OrganizationDecision(
            request_id=self.request_id,
            disposition=(
                DecisionDisposition.ALLOW
                if self.ready
                else DecisionDisposition.REQUIRE_APPROVAL
            ),
            reason_codes=self.reason_codes,
        )


class ApprovalPolicy:
    def evaluate(self, request: ApprovalCheckRequest) -> ApprovalCheckDecision:
        approved = set(request.approved_by_ids)
        missing = tuple(
            member_id
            for member_id in request.required_approver_ids
            if member_id not in approved
        )
        if request.required_approver_ids:
            quorum_met = not missing
        else:
            quorum_met = len(approved) >= request.fallback_min_approvers
        distinct_met = len(approved) >= request.min_distinct_approvers
        latency_met = request.elapsed_steps >= request.min_review_steps
        reasons = []
        if not quorum_met:
            reasons.append("approval_quorum_pending")
        if not distinct_met:
            reasons.append("distinct_approver_floor_pending")
        if not latency_met:
            reasons.append("review_latency_pending")
        return ApprovalCheckDecision(
            request_id=request.request_id,
            ready=not reasons,
            missing_approver_ids=missing,
            reason_codes=tuple(reasons),
        )


__all__ = [
    "ApprovalCheckDecision",
    "ApprovalCheckRequest",
    "ApprovalMember",
    "ApprovalPolicy",
    "ApprovalRoutingDecision",
    "ApprovalRoutingRequest",
    "RoleApprovalRouter",
]
