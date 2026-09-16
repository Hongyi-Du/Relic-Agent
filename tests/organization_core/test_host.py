import random

import pytest

from organization_core import (
    BindingDirective,
    BindingLevel,
    BindingReceipt,
    CallbackHostAdapter,
    DecisionCandidateRequest,
    DecisionDisposition,
    DecisionPipelineRequest,
    DecisionRequest,
    FactPredicate,
    GateClause,
    HarnessCapabilities,
    HostAdapterConformanceError,
    HostDecisionResult,
    OrganizationDecision,
    OrganizationHostAdapter,
    OrganizationModule,
    SelectionMode,
    TypedGateRule,
)


def _request() -> DecisionRequest:
    return DecisionRequest("merge-1", "agent-1", "merge", ("pr-1",))


def _deny() -> OrganizationDecision:
    return OrganizationDecision(
        request_id="merge-1",
        disposition=DecisionDisposition.DENY,
        binding_directives=(
            BindingDirective(
                rule_id="review-before-merge",
                level=BindingLevel.ENFORCED,
                decision=DecisionDisposition.DENY,
                reason="review missing",
                subject_refs=("pr-1",),
            ),
        ),
    )


def _receipt() -> BindingReceipt:
    return BindingReceipt(
        receipt_id="host:merge-1",
        rule_id="review-before-merge",
        level=BindingLevel.ENFORCED,
        decision=DecisionDisposition.DENY,
        host_acknowledged=True,
        subject_refs=("pr-1",),
    )


def test_callback_adapter_is_a_portable_host_adapter():
    adapter = CallbackHostAdapter(
        capabilities=HarnessCapabilities(pre_execution_interception=True),
        apply=lambda request, decision: HostDecisionResult(
            request_id=request.request_id,
            decision_applied=True,
            action_permitted=False,
            binding_receipts=(_receipt(),),
        ),
    )

    assert isinstance(adapter, OrganizationHostAdapter)
    assert adapter.apply_decision(_request(), _deny()).binding_receipts == (
        _receipt(),
    )


@pytest.mark.parametrize(
    ("capabilities", "result", "message"),
    (
        (
            HarnessCapabilities(),
            HostDecisionResult(
                request_id="merge-1",
                decision_applied=True,
                action_permitted=False,
                binding_receipts=(_receipt(),),
            ),
            "without pre_execution_interception",
        ),
        (
            HarnessCapabilities(pre_execution_interception=True),
            HostDecisionResult(
                request_id="merge-1",
                decision_applied=True,
                action_permitted=False,
            ),
            "requires a binding receipt",
        ),
        (
            HarnessCapabilities(pre_execution_interception=True),
            HostDecisionResult(
                request_id="other",
                decision_applied=False,
                action_permitted=True,
                degraded_to_advisory=True,
            ),
            "different request",
        ),
    ),
)
def test_callback_adapter_rejects_false_enforcement_claims(
    capabilities, result, message
):
    adapter = CallbackHostAdapter(
        capabilities=capabilities,
        apply=lambda request, decision: result,
    )

    with pytest.raises(HostAdapterConformanceError, match=message):
        adapter.apply_decision(_request(), _deny())


def test_callback_adapter_mounts_without_replacing_the_harness_loop():
    gate = TypedGateRule(
        rule_id="review-before-merge",
        family="review_before_merge",
        governed_actions=frozenset({"merge"}),
        clauses=(
            GateClause(
                all_of=(FactPredicate("reviewed", False),),
                disposition=DecisionDisposition.DENY,
                reason="review missing",
            ),
        ),
    )
    candidates = (
        DecisionCandidateRequest(
            decision=_request(), utility=10.0, facts={"reviewed": False}, rules=(gate,)
        ),
        DecisionCandidateRequest(
            decision=DecisionRequest("review-1", "agent-1", "review"),
            utility=1.0,
        ),
    )
    intercepted = []

    def before_tool(request, decision):
        intercepted.append(request.request_id)
        directive = decision.binding_directives[0]
        return HostDecisionResult(
            request_id=request.request_id,
            decision_applied=True,
            action_permitted=False,
            binding_receipts=(
                BindingReceipt(
                    receipt_id=f"native-hook:{request.request_id}",
                    rule_id=directive.rule_id,
                    level=directive.level,
                    decision=directive.decision,
                    host_acknowledged=True,
                    subject_refs=directive.subject_refs,
                ),
            ),
        )

    adapter = CallbackHostAdapter(
        capabilities=HarnessCapabilities(pre_execution_interception=True),
        apply=before_tool,
    )
    cycle = OrganizationModule(
        organization_id="org-1", run_id="run-1"
    ).evaluate_with_host(
        DecisionPipelineRequest(
            request_id="turn-1", candidates=candidates, mode=SelectionMode.ARGMAX
        ),
        host=adapter,
        rng=random.Random(7),
    )

    assert intercepted == ["merge-1"]
    assert cycle.pipeline_result.selected_decision.request_id == "review-1"
    receipt = cycle.pipeline_result.candidates[0].decision.binding_receipts[0]
    assert receipt.receipt_id == "native-hook:merge-1"
    assert receipt.rule_id == "review-before-merge"
    assert receipt.host_acknowledged is True
