"""Reusable conformance runner for third-party organization host adapters."""

from __future__ import annotations

from dataclasses import dataclass

from organization_core.contracts import (
    BindingLevel,
    DecisionRequest,
    HarnessCapabilities,
    HostDecisionResult,
    OrganizationDecision,
    OrganizationHostAdapter,
)
from organization_core.host import validate_host_decision_result


_BINDING_RANK = {
    BindingLevel.ADVISORY: 0,
    BindingLevel.DECISION_SHAPING: 1,
    BindingLevel.ENFORCED: 2,
    BindingLevel.VERIFIED_ENFORCED: 3,
}


class HostConformanceFailure(AssertionError):
    """A host response violated its declared or expected behavior."""


@dataclass(frozen=True)
class HostConformanceCase:
    case_id: str
    request: DecisionRequest
    decision: OrganizationDecision
    expected_action_permitted: bool
    expected_decision_applied: bool = True
    expected_degraded_to_advisory: bool = False
    minimum_binding_level: BindingLevel | None = None

    def __post_init__(self) -> None:
        if not str(self.case_id or "").strip():
            raise ValueError("conformance case_id is required")
        if self.request.request_id != self.decision.request_id:
            raise ValueError("conformance request and decision ids must match")
        if self.minimum_binding_level is not None:
            object.__setattr__(
                self,
                "minimum_binding_level",
                BindingLevel(self.minimum_binding_level),
            )


@dataclass(frozen=True)
class HostConformanceCaseResult:
    case_id: str
    disposition: str
    result: HostDecisionResult

    def as_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "disposition": self.disposition,
            "result": self.result.as_dict(),
        }


@dataclass(frozen=True)
class HostConformanceReport:
    capabilities: HarnessCapabilities
    cases: tuple[HostConformanceCaseResult, ...]

    @property
    def passed(self) -> bool:
        return True

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "capabilities": self.capabilities.as_dict(),
            "cases": [case.as_dict() for case in self.cases],
        }


def run_host_adapter_conformance(
    host: OrganizationHostAdapter,
    cases: tuple[HostConformanceCase, ...],
) -> HostConformanceReport:
    """Apply captured host requests and fail on any contract contradiction.

    Product adapters commonly require a native capture step before
    ``apply_decision``. Callers perform that capture, then pass its portable
    request here. The runner deliberately does not know a product protocol.
    """
    capabilities = host.capabilities()
    if not isinstance(capabilities, HarnessCapabilities):
        raise HostConformanceFailure(
            "host capabilities() must return HarnessCapabilities"
        )
    if not cases:
        raise ValueError("at least one conformance case is required")
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("conformance case ids must be unique")

    results = []
    for case in cases:
        try:
            result = host.apply_decision(case.request, case.decision)
            result = validate_host_decision_result(
                request=case.request,
                decision=case.decision,
                capabilities=capabilities,
                result=result,
            )
            _validate_expectations(case, result)
        except Exception as exc:
            if isinstance(exc, HostConformanceFailure):
                raise
            raise HostConformanceFailure(f"{case.case_id}: {exc}") from exc
        results.append(
            HostConformanceCaseResult(
                case_id=case.case_id,
                disposition=case.decision.disposition.value,
                result=result,
            )
        )
    return HostConformanceReport(
        capabilities=capabilities,
        cases=tuple(results),
    )


def _validate_expectations(
    case: HostConformanceCase,
    result: HostDecisionResult,
) -> None:
    fields = (
        ("decision_applied", result.decision_applied, case.expected_decision_applied),
        ("action_permitted", result.action_permitted, case.expected_action_permitted),
        (
            "degraded_to_advisory",
            result.degraded_to_advisory,
            case.expected_degraded_to_advisory,
        ),
    )
    for name, actual, expected in fields:
        if actual is not expected:
            raise HostConformanceFailure(
                f"{case.case_id}: expected {name}={expected}, got {actual}"
            )
    minimum = case.minimum_binding_level
    if minimum is None:
        return
    if not result.binding_receipts:
        raise HostConformanceFailure(
            f"{case.case_id}: expected binding receipts at least {minimum.value}"
        )
    weak = [
        receipt.level.value
        for receipt in result.binding_receipts
        if _BINDING_RANK[receipt.level] < _BINDING_RANK[minimum]
    ]
    if weak:
        raise HostConformanceFailure(
            f"{case.case_id}: receipt levels below {minimum.value}: {weak}"
        )


__all__ = [
    "HostConformanceCase",
    "HostConformanceCaseResult",
    "HostConformanceFailure",
    "HostConformanceReport",
    "run_host_adapter_conformance",
]
