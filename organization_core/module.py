"""One facade a harness can mount without giving up ownership of its loop."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from organization_core.approval import (
    ApprovalCheckDecision,
    ApprovalCheckRequest,
    ApprovalPolicy,
)
from organization_core.contracts import (
    DecisionRequest,
    DecisionDisposition,
    HarnessCapabilities,
    HostDecisionResult,
    OrganizationEvent,
    OrganizationHostAdapter,
)
from organization_core.decision import (
    DecisionCandidateOutcome,
    DecisionCandidateRequest,
    DecisionPipelineRequest,
    DecisionPipelineResult,
    DefaultOrganizationDecisionPipeline,
    OrganizationDecisionPipeline,
)
from organization_core.host import validate_host_decision_result
from organization_core.formation import (
    OrganizationFormationRuntime,
    OrganizationToolDefinition,
)
from organization_core.routing import (
    AuthorityRoutingEngine,
    OrganizationRoutingEngine,
    RoutingDecision,
    RoutingRequest,
)
from organization_core.runtime import OrganizationSnapshot, ShadowOrganizationRuntime
from organization_core.selection import RandomSource
from organization_core.state import OrganizationStateBundle
from organization_core.synthesis import (
    OrganizationSynthesisPort,
    OrganizationSynthesisResult,
)


@dataclass(frozen=True)
class OrganizationHostCycleResult:
    pipeline_result: DecisionPipelineResult
    host_results: tuple[HostDecisionResult | None, ...]


class OrganizationModule:
    """Composable organization runtime mounted around an existing harness loop.

    The harness publishes events and asks for decisions; it still owns model
    calls, tools, retries, context management, and action execution.
    """

    def __init__(
        self,
        *,
        organization_id: str,
        run_id: str,
        state: OrganizationStateBundle | None = None,
        decision_pipeline: OrganizationDecisionPipeline | None = None,
        routing_engine: OrganizationRoutingEngine | None = None,
        approval_policy: ApprovalPolicy | None = None,
    ) -> None:
        if state is not None and state.organization_id != organization_id:
            raise ValueError("state organization_id does not match module")
        self.organization_id = str(organization_id)
        self.run_id = str(run_id)
        self.state = state
        self.events = ShadowOrganizationRuntime(organization_id, run_id)
        self.formation = (
            OrganizationFormationRuntime(state, run_id=run_id)
            if state is not None
            else None
        )
        self.decisions = decision_pipeline or DefaultOrganizationDecisionPipeline()
        self.routing = routing_engine or AuthorityRoutingEngine()
        self.approvals = approval_policy or ApprovalPolicy()

    def publish(self, event: OrganizationEvent) -> bool:
        published = self.events.publish(event)
        if self.formation is not None and event.event_type in {
            "organization.episode_recorded",
            "organization.reflection_recorded",
            "organization.wish_recorded",
            "organization.proposal_recorded",
            "organization.tool_adopted",
            "organization.protocol_adopted",
            "organization.policy_harm_detected",
        }:
            self.formation.publish(event)
        return published

    def snapshot(self) -> OrganizationSnapshot:
        return self.events.snapshot()

    def formation_state(self) -> OrganizationStateBundle | None:
        if self.formation is None:
            return self.state
        return self.formation.state_bundle()

    def organization_tools(self) -> tuple[OrganizationToolDefinition, ...]:
        if self.formation is None:
            return ()
        return self.formation.tool_definitions()

    def run_due_synthesis(
        self,
        port: OrganizationSynthesisPort,
        *,
        step: int,
    ) -> tuple[OrganizationSynthesisResult, ...]:
        if self.formation is None:
            return ()
        return self.formation.run_due_synthesis(port, step=step)

    def evaluate(
        self,
        request: DecisionPipelineRequest,
        *,
        rng: RandomSource,
    ) -> DecisionPipelineResult:
        return self.decisions.evaluate(request, rng=rng)

    def candidate_from_state(
        self,
        decision: DecisionRequest,
        *,
        organization_action_type: str,
        utility: float = 0.0,
        allowed: bool = True,
        facts: Mapping[str, object] | None = None,
    ) -> DecisionCandidateRequest:
        """Attach executable rules restored with the organization state."""
        normalized_decision = replace(
            decision,
            organization_action_type=organization_action_type,
        )
        current_state = self.formation_state()
        state_rules = tuple(
            rule
            for protocol in (
                current_state.protocols if current_state is not None else ()
            )
            for rule in protocol.gate_rules
        )
        return DecisionCandidateRequest(
            decision=normalized_decision,
            utility=utility,
            allowed=allowed,
            facts=facts or {},
            rules=state_rules,
        )

    def evaluate_with_host(
        self,
        request: DecisionPipelineRequest,
        *,
        host: OrganizationHostAdapter,
        rng: RandomSource,
        apply_allow: bool = False,
    ) -> OrganizationHostCycleResult:
        capabilities = host.capabilities()
        if not isinstance(capabilities, HarnessCapabilities):
            raise TypeError("host capabilities() must return HarnessCapabilities")
        evaluation = self.decisions.evaluate_candidates(request)
        host_results: list[HostDecisionResult | None] = []
        updated_outcomes = []
        selectable = []
        for candidate, outcome in zip(request.candidates, evaluation.candidates):
            decision = outcome.decision
            host_result = None
            if decision.disposition is not DecisionDisposition.ALLOW:
                host_result = host.apply_decision(candidate.decision, decision)
                host_result = validate_host_decision_result(
                    request=candidate.decision,
                    decision=decision,
                    capabilities=capabilities,
                    result=host_result,
                )
                decision = replace(
                    decision,
                    binding_receipts=host_result.binding_receipts,
                )
                permitted = host_result.action_permitted
            else:
                permitted = True
            host_results.append(host_result)
            selectable.append(candidate.allowed and permitted)
            updated_outcomes.append(
                DecisionCandidateOutcome(
                    decision=decision,
                    utility=outcome.utility,
                    selectable=candidate.allowed and permitted,
                )
            )
        acknowledged = DecisionPipelineResult(
            request_id=evaluation.request_id,
            candidates=tuple(updated_outcomes),
        )
        selected = self.decisions.select_candidates(
            request,
            acknowledged,
            rng=rng,
            selectable_overrides=tuple(selectable),
        )
        selected_index = selected.selected_index
        if apply_allow and selected_index is not None:
            selected_outcome = selected.candidates[selected_index]
            if selected_outcome.decision.disposition is DecisionDisposition.ALLOW:
                candidate = request.candidates[selected_index]
                host_result = host.apply_decision(
                    candidate.decision,
                    selected_outcome.decision,
                )
                host_result = validate_host_decision_result(
                    request=candidate.decision,
                    decision=selected_outcome.decision,
                    capabilities=capabilities,
                    result=host_result,
                )
                if not host_result.action_permitted:
                    raise ValueError(
                        "host cannot reject an organization allow acknowledgement; "
                        "native host policy must be represented before selection"
                    )
                host_results[selected_index] = host_result
                selected_candidates = list(selected.candidates)
                selected_candidates[selected_index] = DecisionCandidateOutcome(
                    decision=replace(
                        selected_outcome.decision,
                        binding_receipts=host_result.binding_receipts,
                    ),
                    utility=selected_outcome.utility,
                    selectable=selected_outcome.selectable,
                )
                selected = DecisionPipelineResult(
                    request_id=selected.request_id,
                    candidates=tuple(selected_candidates),
                    selection=selected.selection,
                )
        return OrganizationHostCycleResult(
            pipeline_result=selected,
            host_results=tuple(host_results),
        )

    def route(self, request: RoutingRequest) -> RoutingDecision:
        return self.routing.route(request)

    def evaluate_approval(
        self,
        request: ApprovalCheckRequest,
    ) -> ApprovalCheckDecision:
        return self.approvals.evaluate(request)


__all__ = ["OrganizationHostCycleResult", "OrganizationModule"]
