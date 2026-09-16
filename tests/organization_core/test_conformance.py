import pytest

from organization_core import (
    BindingDirective,
    BindingLevel,
    BindingReceipt,
    CallbackHostAdapter,
    DecisionDisposition,
    DecisionRequest,
    HarnessCapabilities,
    HostConformanceCase,
    HostConformanceFailure,
    HostDecisionResult,
    OrganizationDecision,
    run_host_adapter_conformance,
)


def _deny_pair():
    request = DecisionRequest(
        request_id="request-1",
        actor_id="member-1",
        action_type="tool.execute",
    )
    decision = OrganizationDecision(
        request_id=request.request_id,
        disposition=DecisionDisposition.DENY,
        binding_directives=(
            BindingDirective(
                rule_id="deny-danger",
                level=BindingLevel.ENFORCED,
                decision=DecisionDisposition.DENY,
                reason="danger denied",
            ),
        ),
    )
    return request, decision


def test_conformance_runner_produces_portable_report():
    request, decision = _deny_pair()
    host = CallbackHostAdapter(
        capabilities=HarnessCapabilities(pre_execution_interception=True),
        apply=lambda req, org_decision: HostDecisionResult(
            request_id=req.request_id,
            decision_applied=True,
            action_permitted=False,
            binding_receipts=(
                BindingReceipt(
                    receipt_id="receipt-1",
                    rule_id="deny-danger",
                    level=BindingLevel.ENFORCED,
                    decision=org_decision.disposition,
                    host_acknowledged=True,
                ),
            ),
        ),
    )

    report = run_host_adapter_conformance(
        host,
        (
            HostConformanceCase(
                case_id="deny",
                request=request,
                decision=decision,
                expected_action_permitted=False,
                minimum_binding_level=BindingLevel.ENFORCED,
            ),
        ),
    )

    assert report.passed is True
    assert report.as_dict()["cases"][0]["result"]["action_permitted"] is False


def test_conformance_runner_rejects_expectation_mismatch():
    request, decision = _deny_pair()
    host = CallbackHostAdapter(
        capabilities=HarnessCapabilities(pre_execution_interception=True),
        apply=lambda req, org_decision: HostDecisionResult(
            request_id=req.request_id,
            decision_applied=True,
            action_permitted=False,
            binding_receipts=(
                BindingReceipt(
                    receipt_id="receipt-1",
                    rule_id="deny-danger",
                    level=BindingLevel.ENFORCED,
                    decision=org_decision.disposition,
                    host_acknowledged=True,
                ),
            ),
        ),
    )

    with pytest.raises(HostConformanceFailure, match="action_permitted"):
        run_host_adapter_conformance(
            host,
            (
                HostConformanceCase(
                    case_id="wrong-expectation",
                    request=request,
                    decision=decision,
                    expected_action_permitted=True,
                ),
            ),
        )


def test_conformance_runner_requires_unique_nonempty_cases():
    host = CallbackHostAdapter(
        capabilities=HarnessCapabilities(),
        apply=lambda request, decision: HostDecisionResult(
            request_id=request.request_id,
            decision_applied=True,
            action_permitted=True,
        ),
    )
    with pytest.raises(ValueError, match="at least one"):
        run_host_adapter_conformance(host, ())

    request = DecisionRequest("request-1", "member-1", "tool.execute")
    decision = OrganizationDecision(request_id="request-1")
    case = HostConformanceCase(
        case_id="duplicate",
        request=request,
        decision=decision,
        expected_action_permitted=True,
    )
    with pytest.raises(ValueError, match="unique"):
        run_host_adapter_conformance(host, (case, case))
