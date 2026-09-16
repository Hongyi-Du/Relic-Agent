"""Typed, harness-agnostic pre-execution organization gates.

Hosts translate tool or domain state into named facts.  Organization rules
then match those facts without importing the host, its action classes, or its
world model.  The host remains responsible for applying the returned decision
and producing an acknowledgement receipt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from organization_core.contracts import (
    BindingDirective,
    BindingLevel,
    DecisionDisposition,
    DecisionRequest,
    OrganizationDecision,
    _json_value,
)


@dataclass(frozen=True)
class FactPredicate:
    """An exact comparison against one host-provided fact."""

    fact_key: str
    expected: Any

    def __post_init__(self) -> None:
        if not str(self.fact_key or "").strip():
            raise ValueError("fact_key is required")
        object.__setattr__(
            self,
            "expected",
            _json_value(self.expected, path=f"predicate.{self.fact_key}"),
        )

    def matches(self, facts: Mapping[str, Any]) -> bool:
        return self.fact_key in facts and facts[self.fact_key] == self.expected

    def as_dict(self) -> dict[str, Any]:
        return {"fact_key": self.fact_key, "expected": self.expected}

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "FactPredicate":
        return cls(
            fact_key=str(row.get("fact_key") or ""),
            expected=row.get("expected"),
        )


@dataclass(frozen=True)
class GateClause:
    """A disposition activated when every predicate matches."""

    all_of: tuple[FactPredicate, ...]
    disposition: DecisionDisposition = DecisionDisposition.DENY
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "all_of", tuple(self.all_of))
        if not self.all_of:
            raise ValueError("gate clause requires at least one predicate")
        if not all(isinstance(item, FactPredicate) for item in self.all_of):
            raise TypeError("all_of must contain FactPredicate values")
        object.__setattr__(self, "disposition", DecisionDisposition(self.disposition))
        if self.disposition is DecisionDisposition.ALLOW:
            raise ValueError("matching gate clauses must constrain the action")
        if not str(self.reason or "").strip():
            raise ValueError("gate clause reason is required")

    def matches(self, facts: Mapping[str, Any]) -> bool:
        return all(predicate.matches(facts) for predicate in self.all_of)

    def as_dict(self) -> dict[str, Any]:
        return {
            "all_of": [predicate.as_dict() for predicate in self.all_of],
            "disposition": self.disposition.value,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "GateClause":
        return cls(
            all_of=tuple(
                FactPredicate.from_dict(predicate)
                for predicate in (row.get("all_of") or ())
            ),
            disposition=DecisionDisposition(
                row.get("disposition") or DecisionDisposition.DENY
            ),
            reason=str(row.get("reason") or ""),
        )


@dataclass(frozen=True)
class TypedGateRule:
    """A machine-checkable organizational rule for named action types."""

    rule_id: str
    family: str
    governed_actions: frozenset[str]
    clauses: tuple[GateClause, ...]
    binding_level: BindingLevel = BindingLevel.ENFORCED

    def __post_init__(self) -> None:
        for name in ("rule_id", "family"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        actions = frozenset(
            str(item).strip()
            for item in self.governed_actions
            if str(item or "").strip()
        )
        if not actions:
            raise ValueError("governed_actions cannot be empty")
        object.__setattr__(self, "governed_actions", actions)
        object.__setattr__(self, "clauses", tuple(self.clauses))
        if not self.clauses:
            raise ValueError("typed gate rule requires at least one clause")
        if not all(isinstance(item, GateClause) for item in self.clauses):
            raise TypeError("clauses must contain GateClause values")
        level = BindingLevel(self.binding_level)
        if level not in {BindingLevel.ENFORCED, BindingLevel.VERIFIED_ENFORCED}:
            raise ValueError("typed gate rules require an enforced binding level")
        object.__setattr__(self, "binding_level", level)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "family": self.family,
            "governed_actions": sorted(self.governed_actions),
            "clauses": [clause.as_dict() for clause in self.clauses],
            "binding_level": self.binding_level.value,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "TypedGateRule":
        return cls(
            rule_id=str(row.get("rule_id") or ""),
            family=str(row.get("family") or ""),
            governed_actions=frozenset(row.get("governed_actions") or ()),
            clauses=tuple(
                GateClause.from_dict(clause)
                for clause in (row.get("clauses") or ())
            ),
            binding_level=BindingLevel(
                row.get("binding_level") or BindingLevel.ENFORCED
            ),
        )


@dataclass(frozen=True)
class GateRequest:
    decision: DecisionRequest
    facts: Mapping[str, Any] = field(default_factory=dict)
    rules: tuple[TypedGateRule, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.decision, DecisionRequest):
            raise TypeError("decision must be a DecisionRequest")
        object.__setattr__(self, "facts", _json_value(self.facts, path="facts"))
        object.__setattr__(self, "rules", tuple(self.rules))
        if not all(isinstance(item, TypedGateRule) for item in self.rules):
            raise TypeError("rules must contain TypedGateRule values")


@dataclass(frozen=True)
class GateDecision:
    request_id: str
    disposition: DecisionDisposition
    rule_id: str | None = None
    family: str | None = None
    reason: str | None = None
    binding_level: BindingLevel | None = None

    def __post_init__(self) -> None:
        if not str(self.request_id or "").strip():
            raise ValueError("request_id is required")
        disposition = DecisionDisposition(self.disposition)
        object.__setattr__(self, "disposition", disposition)
        if self.binding_level is not None:
            object.__setattr__(
                self,
                "binding_level",
                BindingLevel(self.binding_level),
            )
        if disposition is not DecisionDisposition.ALLOW:
            for name in ("rule_id", "family", "reason", "binding_level"):
                if not str(getattr(self, name) or "").strip():
                    raise ValueError(f"binding gate decision requires {name}")

    @property
    def allowed(self) -> bool:
        return self.disposition is DecisionDisposition.ALLOW

    def as_organization_decision(self) -> OrganizationDecision:
        """Convert a gate result into the host-facing decision contract."""
        if self.allowed:
            return OrganizationDecision(request_id=self.request_id)
        directive = BindingDirective(
            rule_id=str(self.rule_id),
            level=self.binding_level,
            decision=self.disposition,
            reason=str(self.reason),
        )
        return OrganizationDecision(
            request_id=self.request_id,
            disposition=self.disposition,
            reason_codes=(str(self.family), str(self.reason)),
            binding_directives=(directive,),
        )


@runtime_checkable
class OrganizationGateEngine(Protocol):
    def evaluate(self, request: GateRequest) -> GateDecision: ...


class TypedGateEngine:
    """Apply ordered typed rules and return the first binding decision."""

    def evaluate(self, request: GateRequest) -> GateDecision:
        action_type = request.decision.effective_action_type
        for rule in request.rules:
            if action_type not in rule.governed_actions:
                continue
            for clause in rule.clauses:
                if clause.matches(request.facts):
                    return GateDecision(
                        request_id=request.decision.request_id,
                        disposition=clause.disposition,
                        rule_id=rule.rule_id,
                        family=rule.family,
                        reason=clause.reason,
                        binding_level=rule.binding_level,
                    )
        return GateDecision(
            request_id=request.decision.request_id,
            disposition=DecisionDisposition.ALLOW,
        )


__all__ = [
    "FactPredicate",
    "GateClause",
    "GateDecision",
    "GateRequest",
    "OrganizationGateEngine",
    "TypedGateEngine",
    "TypedGateRule",
]
