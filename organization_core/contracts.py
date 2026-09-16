"""Public contracts at the organization ↔ harness boundary.

This module is deliberately dependency-free.  A harness publishes frozen event
envelopes with detached JSON-compatible payloads and asks for decisions;
organization implementations do not import the harness, a provider SDK, or a
domain environment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Mapping, Protocol, runtime_checkable


SCHEMA_VERSION = "organization-event/v1"


class OrganizationVisibility(str, Enum):
    PUBLIC = "public"
    ORGANIZATION = "organization"
    RESTRICTED = "restricted"
    PRIVATE = "private"


class OrganizationEventType(str, Enum):
    ORGANIZATION_INITIALIZED = "organization.initialized"
    OBSERVATION_RECEIVED = "observation.received"
    DECISION_REQUESTED = "decision.requested"
    DECISION_RECORDED = "decision.recorded"
    BINDING_ACKNOWLEDGED = "binding.acknowledged"
    ACTION_EXECUTED = "action.executed"
    DOMAIN_EVENT_RECORDED = "domain.event_recorded"
    TICK_COMPLETED = "tick.completed"
    CHECKPOINT_CREATED = "checkpoint.created"
    EPISODE_RECORDED = "organization.episode_recorded"
    REFLECTION_RECORDED = "organization.reflection_recorded"
    WISH_RECORDED = "organization.wish_recorded"
    PROPOSAL_RECORDED = "organization.proposal_recorded"
    TOOL_ADOPTED = "organization.tool_adopted"
    PROTOCOL_ADOPTED = "organization.protocol_adopted"
    POLICY_HARM_DETECTED = "organization.policy_harm_detected"


class DecisionDisposition(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    REROUTE = "reroute"
    DEFER = "defer"


class BindingLevel(str, Enum):
    ADVISORY = "advisory"
    DECISION_SHAPING = "decision_shaping"
    ENFORCED = "enforced"
    VERIFIED_ENFORCED = "verified_enforced"


def _json_value(value: Any, *, path: str = "payload") -> Any:
    """Return a detached JSON-compatible value or fail at the boundary."""
    if isinstance(value, float) and not math.isfinite(value):
        raise TypeError(f"{path} must contain finite JSON numbers")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return _json_value(value.value, path=path)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} keys must be strings, got {type(key).__name__}")
            out[key] = _json_value(item, path=f"{path}.{key}")
        return out
    if isinstance(value, (list, tuple)):
        return [_json_value(item, path=f"{path}[]") for item in value]
    raise TypeError(f"{path} must be JSON-compatible, got {type(value).__name__}")


@dataclass(frozen=True)
class OrganizationProvenance:
    source: str
    host: str
    run_id: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("source", "host", "run_id", "schema_version"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"provenance {name} is required")

    def as_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "host": self.host,
            "run_id": self.run_id,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class OrganizationMember:
    member_id: str
    role: str = ""
    display_name: str = ""
    authority: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.member_id or "").strip():
            raise ValueError("member_id is required")
        object.__setattr__(self, "authority", _json_value(self.authority, path="authority"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "member_id": self.member_id,
            "role": self.role,
            "display_name": self.display_name,
            "authority": dict(self.authority),
        }


@dataclass(frozen=True)
class OrganizationEvent:
    event_id: str
    event_type: OrganizationEventType | str
    step: int
    provenance: OrganizationProvenance
    actor_id: str | None = None
    subject_refs: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)
    visibility: OrganizationVisibility = OrganizationVisibility.ORGANIZATION

    def __post_init__(self) -> None:
        if not str(self.event_id or "").strip():
            raise ValueError("event_id is required")
        if int(self.step) < 0:
            raise ValueError("event step must be non-negative")
        event_type = (
            self.event_type.value
            if isinstance(self.event_type, OrganizationEventType)
            else str(self.event_type or "").strip()
        )
        if not event_type:
            raise ValueError("event_type is required")
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "step", int(self.step))
        object.__setattr__(self, "subject_refs", tuple(str(ref) for ref in self.subject_refs))
        object.__setattr__(self, "payload", _json_value(self.payload))
        object.__setattr__(self, "visibility", OrganizationVisibility(self.visibility))

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "step": self.step,
            "actor_id": self.actor_id,
            "subject_refs": list(self.subject_refs),
            "payload": _json_value(self.payload),
            "visibility": self.visibility.value,
            "provenance": self.provenance.as_dict(),
        }


@dataclass(frozen=True)
class HarnessCapabilities:
    pre_execution_interception: bool = False
    approval: bool = False
    delegation: bool = False
    shared_context: bool = False
    checkpoint: bool = False
    verifiable_tool_receipts: bool = False
    persistent_sessions: bool = False
    dynamic_tools: bool = False
    structured_output: bool = False
    synthesis: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "pre_execution_interception": self.pre_execution_interception,
            "approval": self.approval,
            "delegation": self.delegation,
            "shared_context": self.shared_context,
            "checkpoint": self.checkpoint,
            "verifiable_tool_receipts": self.verifiable_tool_receipts,
            "persistent_sessions": self.persistent_sessions,
            "dynamic_tools": self.dynamic_tools,
            "structured_output": self.structured_output,
            "synthesis": self.synthesis,
        }


@dataclass(frozen=True)
class DecisionRequest:
    request_id: str
    actor_id: str
    action_type: str
    subject_refs: tuple[str, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)
    organization_action_type: str = ""

    def __post_init__(self) -> None:
        for name in ("request_id", "actor_id", "action_type"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        object.__setattr__(self, "subject_refs", tuple(str(ref) for ref in self.subject_refs))
        object.__setattr__(self, "parameters", _json_value(self.parameters, path="parameters"))
        object.__setattr__(
            self,
            "organization_action_type",
            str(self.organization_action_type or "").strip(),
        )

    @property
    def effective_action_type(self) -> str:
        return self.organization_action_type or self.action_type

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "request_id": self.request_id,
            "actor_id": self.actor_id,
            "action_type": self.action_type,
            "subject_refs": list(self.subject_refs),
            "parameters": _json_value(self.parameters, path="parameters"),
        }
        if self.organization_action_type:
            payload["organization_action_type"] = self.organization_action_type
        return payload


@dataclass(frozen=True)
class BindingDirective:
    """A rule application awaiting acknowledgement from the host."""

    rule_id: str
    level: BindingLevel
    decision: DecisionDisposition
    reason: str
    subject_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.rule_id or "").strip():
            raise ValueError("binding directive rule_id is required")
        if not str(self.reason or "").strip():
            raise ValueError("binding directive reason is required")
        object.__setattr__(self, "level", BindingLevel(self.level))
        object.__setattr__(self, "decision", DecisionDisposition(self.decision))
        object.__setattr__(self, "subject_refs", tuple(str(item) for item in self.subject_refs))

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "level": self.level.value,
            "decision": self.decision.value,
            "reason": self.reason,
            "subject_refs": list(self.subject_refs),
        }


@dataclass(frozen=True)
class BindingReceipt:
    receipt_id: str
    rule_id: str
    level: BindingLevel
    decision: DecisionDisposition
    host_acknowledged: bool
    subject_refs: tuple[str, ...] = ()
    outcome_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "level", BindingLevel(self.level))
        object.__setattr__(self, "decision", DecisionDisposition(self.decision))
        object.__setattr__(self, "subject_refs", tuple(self.subject_refs))
        if not self.receipt_id or not self.rule_id:
            raise ValueError("binding receipt and rule ids are required")
        if self.level in {BindingLevel.ENFORCED, BindingLevel.VERIFIED_ENFORCED}:
            if not self.host_acknowledged:
                raise ValueError("enforced binding requires host acknowledgement")
        if self.level is BindingLevel.VERIFIED_ENFORCED and not self.outcome_ref:
            raise ValueError("verified enforcement requires an outcome_ref")

    def as_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "rule_id": self.rule_id,
            "level": self.level.value,
            "decision": self.decision.value,
            "host_acknowledged": self.host_acknowledged,
            "subject_refs": list(self.subject_refs),
            "outcome_ref": self.outcome_ref,
        }


@dataclass(frozen=True)
class OrganizationDecision:
    request_id: str
    disposition: DecisionDisposition = DecisionDisposition.ALLOW
    priority_delta: float = 0.0
    assignee: str | None = None
    required_roles: tuple[str, ...] = ()
    context_overlay: tuple[Mapping[str, Any], ...] = ()
    reason_codes: tuple[str, ...] = ()
    binding_directives: tuple[BindingDirective, ...] = ()
    binding_receipts: tuple[BindingReceipt, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.request_id or "").strip():
            raise ValueError("request_id is required")
        object.__setattr__(self, "disposition", DecisionDisposition(self.disposition))
        object.__setattr__(self, "required_roles", tuple(self.required_roles))
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        object.__setattr__(self, "binding_directives", tuple(self.binding_directives))
        if not all(
            isinstance(item, BindingDirective) for item in self.binding_directives
        ):
            raise TypeError("binding_directives must contain BindingDirective values")
        object.__setattr__(self, "binding_receipts", tuple(self.binding_receipts))
        if not all(isinstance(item, BindingReceipt) for item in self.binding_receipts):
            raise TypeError("binding_receipts must contain BindingReceipt values")
        object.__setattr__(
            self,
            "context_overlay",
            tuple(_json_value(item, path="context_overlay") for item in self.context_overlay),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "disposition": self.disposition.value,
            "priority_delta": float(self.priority_delta),
            "assignee": self.assignee,
            "required_roles": list(self.required_roles),
            "context_overlay": _json_value(
                self.context_overlay, path="context_overlay"
            ),
            "reason_codes": list(self.reason_codes),
            "binding_directives": [
                item.as_dict() for item in self.binding_directives
            ],
            "binding_receipts": [item.as_dict() for item in self.binding_receipts],
        }


@dataclass(frozen=True)
class HostDecisionResult:
    """What a harness actually did with an organization decision."""

    request_id: str
    decision_applied: bool
    action_permitted: bool
    binding_receipts: tuple[BindingReceipt, ...] = ()
    degraded_to_advisory: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        if not str(self.request_id or "").strip():
            raise ValueError("request_id is required")
        object.__setattr__(self, "decision_applied", bool(self.decision_applied))
        object.__setattr__(self, "action_permitted", bool(self.action_permitted))
        object.__setattr__(self, "binding_receipts", tuple(self.binding_receipts))
        if not all(isinstance(item, BindingReceipt) for item in self.binding_receipts):
            raise TypeError("binding_receipts must contain BindingReceipt values")
        object.__setattr__(
            self,
            "degraded_to_advisory",
            bool(self.degraded_to_advisory),
        )
        if self.decision_applied and self.degraded_to_advisory:
            raise ValueError("an applied decision cannot be degraded to advisory")

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "decision_applied": self.decision_applied,
            "action_permitted": self.action_permitted,
            "binding_receipts": [item.as_dict() for item in self.binding_receipts],
            "degraded_to_advisory": self.degraded_to_advisory,
            "reason": self.reason,
        }


@runtime_checkable
class OrganizationEventSink(Protocol):
    """Minimal interface required to consume a harness event stream."""

    def publish(self, event: OrganizationEvent) -> bool: ...


@runtime_checkable
class OrganizationDecisionEngine(Protocol):
    """Optional active interface; shadow runtimes intentionally do not implement it."""

    def decide(self, request: DecisionRequest) -> OrganizationDecision: ...


@runtime_checkable
class OrganizationHostAdapter(Protocol):
    """Capabilities a concrete harness exposes to an organization runtime."""

    def capabilities(self) -> HarnessCapabilities: ...

    def apply_decision(
        self,
        request: DecisionRequest,
        decision: OrganizationDecision,
    ) -> HostDecisionResult: ...


__all__ = [
    "BindingLevel",
    "BindingDirective",
    "BindingReceipt",
    "DecisionDisposition",
    "DecisionRequest",
    "HarnessCapabilities",
    "HostDecisionResult",
    "OrganizationDecision",
    "OrganizationDecisionEngine",
    "OrganizationEvent",
    "OrganizationEventSink",
    "OrganizationEventType",
    "OrganizationHostAdapter",
    "OrganizationMember",
    "OrganizationProvenance",
    "OrganizationVisibility",
    "SCHEMA_VERSION",
]
