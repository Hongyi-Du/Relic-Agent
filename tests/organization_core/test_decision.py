import random

import pytest

from organization_core import (
    BindingLevel,
    DecisionCandidateRequest,
    DecisionDisposition,
    DecisionPipelineRequest,
    DecisionRequest,
    DefaultOrganizationDecisionPipeline,
    FactPredicate,
    GateClause,
    HarnessCapabilities,
    OrganizationDecision,
    OrganizationDecisionPipeline,
    SelectionMode,
    TypedGateRule,
    UtilitySelectionEngine,
    merge_organization_decisions,
)
from organization_core.selection import SelectionOption, SelectionRequest


class PriorityEngine:
    def decide(self, request):
        return OrganizationDecision(
            request_id=request.request_id,
            priority_delta=0.75 if request.action_type == "review" else 0.0,
            reason_codes=("review_priority",),
        )


class ApprovalEngine:
    def decide(self, request):
        return OrganizationDecision(
            request_id=request.request_id,
            disposition=DecisionDisposition.REQUIRE_APPROVAL,
            required_roles=("maintainer",),
        )


def _candidate(
    request_id,
    action_type,
    utility,
    *,
    facts=None,
    rules=(),
):
    return DecisionCandidateRequest(
        decision=DecisionRequest(request_id, "member-1", action_type),
        utility=utility,
        facts=facts or {},
        rules=rules,
    )


def test_pipeline_composes_priority_and_gate_before_selection():
    rule = TypedGateRule(
        rule_id="review-before-merge",
        family="review_gate",
        governed_actions=frozenset({"merge"}),
        clauses=(
            GateClause(
                all_of=(FactPredicate("reviewed", False),),
                disposition=DecisionDisposition.DENY,
                reason="review is required",
            ),
        ),
        binding_level=BindingLevel.ENFORCED,
    )
    pipeline = DefaultOrganizationDecisionPipeline(
        decision_engines=(PriorityEngine(),)
    )
    assert isinstance(pipeline, OrganizationDecisionPipeline)
    result = pipeline.evaluate(
        DecisionPipelineRequest(
            request_id="batch-1",
            candidates=(
                _candidate(
                    "merge-1",
                    "merge",
                    10.0,
                    facts={"reviewed": False},
                    rules=(rule,),
                ),
                _candidate("review-1", "review", 1.0),
            ),
            mode=SelectionMode.ARGMAX,
            jitter=0.0,
        ),
        rng=random.Random(7),
    )

    assert result.candidates[0].decision.disposition is DecisionDisposition.DENY
    assert result.candidates[0].selectable is False
    assert result.candidates[1].utility == pytest.approx(1.75)
    assert result.selected_decision.request_id == "review-1"


def test_pipeline_preserves_the_selection_engines_rng_semantics():
    candidates = (
        _candidate("0", "a", 0.1),
        _candidate("1", "b", 0.4),
        _candidate("2", "c", -0.2),
    )
    seed = 17
    pipeline_result = DefaultOrganizationDecisionPipeline().evaluate(
        DecisionPipelineRequest(
            request_id="batch",
            candidates=candidates,
            mode=SelectionMode.SOFTMAX,
            temperature=0.6,
            jitter=0.05,
        ),
        rng=random.Random(seed),
    )
    reference = UtilitySelectionEngine().select(
        SelectionRequest(
            request_id="batch",
            options=tuple(
                SelectionOption(item.decision.request_id, item.utility)
                for item in candidates
            ),
            mode=SelectionMode.SOFTMAX,
            temperature=0.6,
            jitter=0.05,
        ),
        rng=random.Random(seed),
    )

    assert pipeline_result.selection == reference


def test_approval_is_selectable_only_when_the_harness_supports_it():
    pipeline = DefaultOrganizationDecisionPipeline(
        decision_engines=(ApprovalEngine(),)
    )
    candidate = _candidate("publish-1", "publish", 1.0)

    unsupported = pipeline.evaluate(
        DecisionPipelineRequest(request_id="unsupported", candidates=(candidate,)),
        rng=random.Random(1),
    )
    supported = pipeline.evaluate(
        DecisionPipelineRequest(
            request_id="supported",
            candidates=(candidate,),
            harness_capabilities=HarnessCapabilities(approval=True),
        ),
        rng=random.Random(1),
    )

    assert unsupported.selection is None
    assert supported.selected_decision.disposition is DecisionDisposition.REQUIRE_APPROVAL


def test_merge_rejects_conflicting_routing_assignments():
    with pytest.raises(ValueError, match="conflicting assignees"):
        merge_organization_decisions(
            "request-1",
            (
                OrganizationDecision(request_id="request-1", assignee="a"),
                OrganizationDecision(request_id="request-1", assignee="b"),
            ),
        )
