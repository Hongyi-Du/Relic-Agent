"""Composition pipeline for harness-independent organizational decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable

from organization_core.contracts import (
    DecisionDisposition,
    DecisionRequest,
    HarnessCapabilities,
    OrganizationDecision,
    OrganizationDecisionEngine,
    _json_value,
)
from organization_core.gates import (
    GateRequest,
    OrganizationGateEngine,
    TypedGateEngine,
    TypedGateRule,
)
from organization_core.selection import (
    OrganizationSelectionEngine,
    RandomSource,
    SelectionDecision,
    SelectionMode,
    SelectionOption,
    SelectionRequest,
    UtilitySelectionEngine,
)


@dataclass(frozen=True)
class DecisionCandidateRequest:
    """One host candidate plus organization-visible scoring and gate inputs."""

    decision: DecisionRequest
    utility: float = 0.0
    allowed: bool = True
    facts: Mapping[str, object] = field(default_factory=dict)
    rules: tuple[TypedGateRule, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.decision, DecisionRequest):
            raise TypeError("decision must be a DecisionRequest")
        object.__setattr__(self, "utility", float(self.utility))
        object.__setattr__(self, "allowed", bool(self.allowed))
        object.__setattr__(self, "facts", _json_value(self.facts, path="facts"))
        object.__setattr__(self, "rules", tuple(self.rules))
        if not all(isinstance(rule, TypedGateRule) for rule in self.rules):
            raise TypeError("rules must contain TypedGateRule values")


@dataclass(frozen=True)
class DecisionPipelineRequest:
    request_id: str
    candidates: tuple[DecisionCandidateRequest, ...]
    mode: SelectionMode = SelectionMode.SOFTMAX
    temperature: float = 1.0
    jitter: float = 0.0
    harness_capabilities: HarnessCapabilities = field(
        default_factory=HarnessCapabilities
    )

    def __post_init__(self) -> None:
        if not str(self.request_id or "").strip():
            raise ValueError("request_id is required")
        object.__setattr__(self, "candidates", tuple(self.candidates))
        if not all(
            isinstance(candidate, DecisionCandidateRequest)
            for candidate in self.candidates
        ):
            raise TypeError(
                "candidates must contain DecisionCandidateRequest values"
            )
        ids = [candidate.decision.request_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate decision request ids must be unique")
        object.__setattr__(self, "mode", SelectionMode(self.mode))
        if not isinstance(self.harness_capabilities, HarnessCapabilities):
            raise TypeError("harness_capabilities must be HarnessCapabilities")


@dataclass(frozen=True)
class DecisionCandidateOutcome:
    decision: OrganizationDecision
    utility: float
    selectable: bool


@dataclass(frozen=True)
class DecisionPipelineResult:
    request_id: str
    candidates: tuple[DecisionCandidateOutcome, ...]
    selection: SelectionDecision | None = None

    @property
    def selected_index(self) -> int | None:
        return self.selection.selected_index if self.selection is not None else None

    @property
    def selected_decision(self) -> OrganizationDecision | None:
        index = self.selected_index
        return self.candidates[index].decision if index is not None else None


@runtime_checkable
class OrganizationDecisionPipeline(Protocol):
    def decide_candidate(
        self,
        candidate: DecisionCandidateRequest,
    ) -> OrganizationDecision: ...

    def evaluate(
        self,
        request: DecisionPipelineRequest,
        *,
        rng: RandomSource,
    ) -> DecisionPipelineResult: ...

    def evaluate_candidates(
        self,
        request: DecisionPipelineRequest,
    ) -> DecisionPipelineResult: ...

    def select_candidates(
        self,
        request: DecisionPipelineRequest,
        evaluation: DecisionPipelineResult,
        *,
        rng: RandomSource,
        selectable_overrides: tuple[bool, ...] | None = None,
    ) -> DecisionPipelineResult: ...


class DefaultOrganizationDecisionPipeline:
    """Compose decision stages, typed gates, and seeded utility selection.

    Host adapters remain responsible for translating domain state into facts
    and acknowledging whether a returned directive was actually applied.
    """

    def __init__(
        self,
        *,
        decision_engines: tuple[OrganizationDecisionEngine, ...] = (),
        gate_engine: OrganizationGateEngine | None = None,
        selection_engine: OrganizationSelectionEngine | None = None,
    ) -> None:
        self.decision_engines = tuple(decision_engines)
        self.gate_engine = gate_engine or TypedGateEngine()
        self.selection_engine = selection_engine or UtilitySelectionEngine()

    def decide_candidate(
        self,
        candidate: DecisionCandidateRequest,
    ) -> OrganizationDecision:
        decisions = [
            engine.decide(candidate.decision) for engine in self.decision_engines
        ]
        if candidate.rules:
            decisions.append(
                self.gate_engine.evaluate(
                    GateRequest(
                        decision=candidate.decision,
                        facts=candidate.facts,
                        rules=candidate.rules,
                    )
                ).as_organization_decision()
            )
        if not decisions:
            return OrganizationDecision(request_id=candidate.decision.request_id)
        return merge_organization_decisions(
            candidate.decision.request_id,
            tuple(decisions),
        )

    def evaluate(
        self,
        request: DecisionPipelineRequest,
        *,
        rng: RandomSource,
    ) -> DecisionPipelineResult:
        evaluation = self.evaluate_candidates(request)
        return self.select_candidates(request, evaluation, rng=rng)

    def evaluate_candidates(
        self,
        request: DecisionPipelineRequest,
    ) -> DecisionPipelineResult:
        outcomes = []
        for candidate in request.candidates:
            decision = self.decide_candidate(candidate)
            utility = candidate.utility + decision.priority_delta
            outcomes.append(
                DecisionCandidateOutcome(
                    decision=decision,
                    utility=utility,
                    selectable=(
                        candidate.allowed
                        and _disposition_supported(
                            decision.disposition,
                            request.harness_capabilities,
                        )
                    ),
                )
            )
        return DecisionPipelineResult(
            request_id=request.request_id,
            candidates=tuple(outcomes),
        )

    def select_candidates(
        self,
        request: DecisionPipelineRequest,
        evaluation: DecisionPipelineResult,
        *,
        rng: RandomSource,
        selectable_overrides: tuple[bool, ...] | None = None,
    ) -> DecisionPipelineResult:
        if evaluation.request_id != request.request_id:
            raise ValueError("pipeline evaluation belongs to a different request")
        if len(evaluation.candidates) != len(request.candidates):
            raise ValueError("pipeline evaluation candidate count changed")
        if selectable_overrides is not None:
            selectable_overrides = tuple(bool(value) for value in selectable_overrides)
            if len(selectable_overrides) != len(request.candidates):
                raise ValueError("selectable override count changed")
        effective_outcomes = tuple(
            DecisionCandidateOutcome(
                decision=outcome.decision,
                utility=outcome.utility,
                selectable=(
                    selectable_overrides[index]
                    if selectable_overrides is not None
                    else outcome.selectable
                ),
            )
            for index, outcome in enumerate(evaluation.candidates)
        )
        selection = self.selection_engine.select(
            SelectionRequest(
                request_id=request.request_id,
                options=tuple(
                    SelectionOption(
                        option_id=candidate.decision.request_id,
                        utility=outcome.utility,
                        allowed=outcome.selectable,
                    )
                    for candidate, outcome in zip(
                        request.candidates, effective_outcomes
                    )
                ),
                mode=request.mode,
                temperature=request.temperature,
                jitter=request.jitter,
            ),
            rng=rng,
        )
        return DecisionPipelineResult(
            request_id=request.request_id,
            candidates=effective_outcomes,
            selection=selection,
        )


_DISPOSITION_PRECEDENCE = {
    DecisionDisposition.ALLOW: 0,
    DecisionDisposition.REROUTE: 1,
    DecisionDisposition.DEFER: 2,
    DecisionDisposition.REQUIRE_APPROVAL: 3,
    DecisionDisposition.DENY: 4,
}


def merge_organization_decisions(
    request_id: str,
    decisions: tuple[OrganizationDecision, ...],
) -> OrganizationDecision:
    """Merge independent organization stages without losing their evidence."""
    if any(decision.request_id != request_id for decision in decisions):
        raise ValueError("cannot merge decisions for different requests")
    strongest = max(
        decisions,
        key=lambda decision: _DISPOSITION_PRECEDENCE[decision.disposition],
    )
    assignees = tuple(
        dict.fromkeys(
            decision.assignee for decision in decisions if decision.assignee
        )
    )
    if len(assignees) > 1:
        raise ValueError("decision stages produced conflicting assignees")
    return OrganizationDecision(
        request_id=request_id,
        disposition=strongest.disposition,
        priority_delta=sum(decision.priority_delta for decision in decisions),
        assignee=assignees[0] if assignees else None,
        required_roles=tuple(
            dict.fromkeys(
                role for decision in decisions for role in decision.required_roles
            )
        ),
        context_overlay=tuple(
            item for decision in decisions for item in decision.context_overlay
        ),
        reason_codes=tuple(
            dict.fromkeys(
                reason for decision in decisions for reason in decision.reason_codes
            )
        ),
        binding_directives=tuple(
            directive
            for decision in decisions
            for directive in decision.binding_directives
        ),
        binding_receipts=tuple(
            receipt
            for decision in decisions
            for receipt in decision.binding_receipts
        ),
    )


def _disposition_supported(
    disposition: DecisionDisposition,
    capabilities: HarnessCapabilities,
) -> bool:
    if disposition is DecisionDisposition.ALLOW:
        return True
    if disposition is DecisionDisposition.REQUIRE_APPROVAL:
        return capabilities.approval
    if disposition is DecisionDisposition.REROUTE:
        return capabilities.delegation
    return False


__all__ = [
    "DecisionCandidateOutcome",
    "DecisionCandidateRequest",
    "DecisionPipelineRequest",
    "DecisionPipelineResult",
    "DefaultOrganizationDecisionPipeline",
    "OrganizationDecisionPipeline",
    "merge_organization_decisions",
]
