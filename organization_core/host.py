"""Reusable adapters and validation for the organization ↔ harness boundary."""

from __future__ import annotations

from collections.abc import Callable

from organization_core.contracts import (
    DecisionDisposition,
    DecisionRequest,
    HarnessCapabilities,
    HostDecisionResult,
    OrganizationDecision,
)


class HostAdapterConformanceError(ValueError):
    """Raised when a host acknowledgement contradicts the public contract."""


DecisionApplier = Callable[
    [DecisionRequest, OrganizationDecision],
    HostDecisionResult,
]


def validate_host_decision_result(
    *,
    request: DecisionRequest,
    decision: OrganizationDecision,
    capabilities: HarnessCapabilities,
    result: HostDecisionResult,
) -> HostDecisionResult:
    """Validate an acknowledgement before it becomes organization evidence.

    A host owns execution, so the core cannot infer enforcement from a product
    name or a configured flag.  It can, however, reject internally inconsistent
    claims at the boundary.
    """
    if request.request_id != decision.request_id:
        raise HostAdapterConformanceError(
            "organization decision belongs to a different request"
        )
    if result.request_id != request.request_id:
        raise HostAdapterConformanceError(
            "host result belongs to a different request"
        )
    if not result.decision_applied and not result.degraded_to_advisory:
        raise HostAdapterConformanceError(
            "an unapplied decision must explicitly degrade to advisory"
        )
    if result.degraded_to_advisory and result.binding_receipts:
        raise HostAdapterConformanceError(
            "an advisory result cannot contain enforcement receipts"
        )
    if not result.decision_applied:
        return result

    disposition = decision.disposition
    if disposition in {DecisionDisposition.DENY, DecisionDisposition.DEFER}:
        supported = capabilities.pre_execution_interception
        capability_name = "pre_execution_interception"
    elif disposition is DecisionDisposition.REQUIRE_APPROVAL:
        supported = capabilities.approval
        capability_name = "approval"
    elif disposition is DecisionDisposition.REROUTE:
        supported = capabilities.delegation
        capability_name = "delegation"
    else:
        supported = True
        capability_name = ""
    if not supported:
        raise HostAdapterConformanceError(
            f"host applied {disposition.value!r} without {capability_name} capability"
        )

    if disposition in {
        DecisionDisposition.DENY,
        DecisionDisposition.DEFER,
        DecisionDisposition.REQUIRE_APPROVAL,
    } and result.action_permitted:
        raise HostAdapterConformanceError(
            f"applied {disposition.value!r} must stop immediate action execution"
        )

    if disposition is not DecisionDisposition.ALLOW:
        if not result.binding_receipts:
            raise HostAdapterConformanceError(
                "an applied non-allow decision requires a binding receipt"
            )
        directives = {
            (directive.rule_id, directive.decision)
            for directive in decision.binding_directives
        }
        for receipt in result.binding_receipts:
            if not receipt.host_acknowledged:
                raise HostAdapterConformanceError(
                    "an enforcement receipt must be acknowledged by the host"
                )
            if directives and (receipt.rule_id, receipt.decision) not in directives:
                raise HostAdapterConformanceError(
                    "binding receipt does not match an organization directive"
                )
    return result


class CallbackHostAdapter:
    """Mount organization decisions on any in-process harness hook.

    Codex-, Cursor-, DeepSeek-, or custom-harness integrations can wrap their
    native pre-tool or delegation callback with this adapter.  The harness loop
    remains outside ``organization_core``.
    """

    def __init__(
        self,
        *,
        capabilities: HarnessCapabilities,
        apply: DecisionApplier,
    ) -> None:
        if not isinstance(capabilities, HarnessCapabilities):
            raise TypeError("capabilities must be HarnessCapabilities")
        if not callable(apply):
            raise TypeError("apply must be callable")
        self._capabilities = capabilities
        self._apply = apply

    def capabilities(self) -> HarnessCapabilities:
        return self._capabilities

    def apply_decision(
        self,
        request: DecisionRequest,
        decision: OrganizationDecision,
    ) -> HostDecisionResult:
        result = self._apply(request, decision)
        if not isinstance(result, HostDecisionResult):
            raise TypeError("host apply callback must return HostDecisionResult")
        return validate_host_decision_result(
            request=request,
            decision=decision,
            capabilities=self._capabilities,
            result=result,
        )


__all__ = [
    "CallbackHostAdapter",
    "DecisionApplier",
    "HostAdapterConformanceError",
    "validate_host_decision_result",
]
