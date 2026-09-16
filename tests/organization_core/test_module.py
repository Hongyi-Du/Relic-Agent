import random

from organization_core import (
    BindingReceipt,
    DecisionCandidateRequest,
    DecisionDisposition,
    DecisionPipelineRequest,
    DecisionRequest,
    FactPredicate,
    GateClause,
    HarnessCapabilities,
    HostDecisionResult,
    OrganizationModule,
    SelectionMode,
    TypedGateRule,
    OrganizationEpisodeState,
    OrganizationMemberState,
    OrganizationStateBundle,
)


class InterceptingHost:
    def capabilities(self):
        return HarnessCapabilities(pre_execution_interception=True)

    def apply_decision(self, request, decision):
        directive = decision.binding_directives[0]
        return HostDecisionResult(
            request_id=request.request_id,
            decision_applied=True,
            action_permitted=False,
            binding_receipts=(
                BindingReceipt(
                    receipt_id=f"receipt:{request.request_id}",
                    rule_id=directive.rule_id,
                    level=directive.level,
                    decision=directive.decision,
                    host_acknowledged=True,
                    subject_refs=request.subject_refs,
                ),
            ),
        )


class AdvisoryOnlyHost:
    def capabilities(self):
        return HarnessCapabilities()

    def apply_decision(self, request, decision):
        return HostDecisionResult(
            request_id=request.request_id,
            decision_applied=False,
            action_permitted=True,
            degraded_to_advisory=True,
            reason="host cannot intercept",
        )


def _request():
    gate = TypedGateRule(
        rule_id="review-rule",
        family="review_before_merge",
        governed_actions=frozenset({"merge"}),
        clauses=(
            GateClause(
                all_of=(FactPredicate("reviewed", False),),
                disposition=DecisionDisposition.DENY,
                reason="review is required",
            ),
        ),
    )
    return DecisionPipelineRequest(
        request_id="batch-1",
        candidates=(
            DecisionCandidateRequest(
                decision=DecisionRequest(
                    "merge-1",
                    "member-1",
                    "merge",
                    subject_refs=("pr-1",),
                ),
                utility=10.0,
                facts={"reviewed": False},
                rules=(gate,),
            ),
            DecisionCandidateRequest(
                decision=DecisionRequest("review-1", "member-1", "review"),
                utility=1.0,
            ),
        ),
        mode=SelectionMode.ARGMAX,
    )


def test_module_selects_after_real_host_acknowledgement():
    module = OrganizationModule(organization_id="org-1", run_id="run-1")

    enforced = module.evaluate_with_host(
        _request(),
        host=InterceptingHost(),
        rng=random.Random(1),
    )
    advisory = module.evaluate_with_host(
        _request(),
        host=AdvisoryOnlyHost(),
        rng=random.Random(1),
    )

    assert enforced.pipeline_result.selected_decision.request_id == "review-1"
    denied = enforced.pipeline_result.candidates[0].decision
    assert denied.binding_receipts[0].host_acknowledged is True
    assert enforced.host_results[0].action_permitted is False

    assert advisory.pipeline_result.selected_decision.request_id == "merge-1"
    assert advisory.host_results[0].degraded_to_advisory is True
    assert advisory.pipeline_result.candidates[0].decision.binding_receipts == ()


def test_module_exposes_formation_as_tools_without_owning_host_loop():
    module = OrganizationModule(
        organization_id="org-1",
        run_id="run-1",
        state=OrganizationStateBundle(
            organization_id="org-1",
            source_repository_id="repo-1",
            source_seed=1401,
            source_step=0,
            members=(OrganizationMemberState("member-1", role="engineer"),),
        ),
    )
    tools = {tool.name: tool for tool in module.organization_tools()}
    episode = OrganizationEpisodeState(
        episode_id="episode-1",
        episode_type="debugging",
        status="closed",
        start_step=1,
        end_step=2,
        participant_ids=("member-1",),
    )

    result = tools["record_episode"].handler({"episode": episode.as_dict()})

    assert result["recorded"] is True
    assert module.formation_state().episodes == (episode,)
