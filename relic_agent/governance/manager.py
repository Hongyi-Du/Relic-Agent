"""Source-backed proposal lifecycle boundary for the Relic Agent release shell.

The old manager invented a fixed "task ownership and peer review" proposal
from every compatibility wish.  This manager does not.  It exposes the HCI
proposal manager's actual draft/review/adoption path and fails closed when the
unported source LLM proposal generator is requested.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Mapping

from organization_core import ApprovalCheckDecision

from relic_agent.governance.models import Proposal
from relic_agent.reflection.models import Wish
from relic_agent.source_b3.protocol_lifecycle import SourceB3ProtocolLifecycleAdapter
from relic_agent.source_b3.proposals.manager import (
    MIN_DISTINCT_PROTOCOL_APPROVERS,
    MIN_PROTOCOL_REVIEW_TICKS,
    ProposalManager,
)
from relic_agent.source_b3.proposals.provenance import source_b3_proposal_provenance
from relic_agent.source_core import SourceCoreObservationBridge


class GovernanceError(ValueError):
    """A caller asked for a transition the source lifecycle does not permit."""


class SourceProposalGenerationUnavailableError(GovernanceError):
    """The full source LLM/reflection proposal-generation path is not ported."""


@dataclass
class _SourceProposalHost:
    """Narrow, explicit host shape accepted by the source proposal manager."""

    agents: dict[str, Any]
    known_actions: tuple[str, ...] = ()
    reflection_manager: Any = None
    episode_manager: Any = None
    protocol_registry: SourceB3ProtocolLifecycleAdapter | None = None
    world_tick: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    agent_log: list[Any] | None = None
    llm_client: Any = None
    proposal_manager: ProposalManager | None = None
    # Materialization is enabled only because the exact HCI protocol registry
    # is mounted below. It does not claim generic OrgWorld action execution.
    source_protocol_materialization_enabled: bool = False
    institutionalization_enabled: bool = False
    institutionalization_ablated: bool = False


class GovernanceManager:
    """Thin host adapter around the source proposal manager and registry.

    ``submit`` takes a source-shaped proposal drafted by an external source
    pipeline. ``propose_from_wish`` remains only as an explicit fail-closed
    compatibility API, so the release mock cannot fabricate source behavior.
    """

    def __init__(
        self,
        *,
        agent_ids: tuple[str, ...],
        min_approvers: int,
        review_ticks: int,
        agent_roles: Mapping[str, str] | None = None,
        known_actions: tuple[str, ...] = (),
    ) -> None:
        if int(min_approvers) != MIN_DISTINCT_PROTOCOL_APPROVERS:
            raise GovernanceError(
                "source_proposal_min_approvers_unsupported: "
                f"requested={min_approvers} source={MIN_DISTINCT_PROTOCOL_APPROVERS}"
            )
        if int(review_ticks) != MIN_PROTOCOL_REVIEW_TICKS:
            raise GovernanceError(
                "source_proposal_review_ticks_unsupported: "
                f"requested={review_ticks} source={MIN_PROTOCOL_REVIEW_TICKS}"
            )
        self.agent_ids = tuple(agent_ids)
        self.min_approvers = int(min_approvers)
        self.review_ticks = int(review_ticks)
        self.protocol_registry = SourceB3ProtocolLifecycleAdapter(
            min_supporters=self.min_approvers,
            review_ticks=self.review_ticks,
        )
        self._source = ProposalManager()
        roles = dict(agent_roles or {})
        self._host = _SourceProposalHost(
            agents={
                agent_id: SimpleNamespace(role=str(roles.get(agent_id, "")))
                for agent_id in self.agent_ids
            },
            known_actions=tuple(dict.fromkeys(str(item) for item in known_actions)),
            protocol_registry=self.protocol_registry,
            proposal_manager=self._source,
            source_protocol_materialization_enabled=True,
            institutionalization_enabled=True,
        )
        self.proposals = self._source.proposals
        self.tools = self._source.tools
        self.protocol_specs = self._source.protocol_specs

    def bind_source_core_bridge(self, bridge: SourceCoreObservationBridge) -> None:
        """Keep the existing immutable source-registry projection active."""

        self.protocol_registry.bind_source_core_bridge(bridge)

    def bind_host_context(
        self,
        *,
        agents: Mapping[str, Any],
        reflection_manager: Any = None,
        episode_manager: Any = None,
        known_actions: tuple[str, ...] | None = None,
    ) -> None:
        """Mount only the objects that source proposal validation actually reads.

        This is not an OrgWorld adapter.  A caller must still provide a
        source-shaped proposal; mounting this context never enables synthetic
        proposal generation or action execution.
        """

        if tuple(sorted(agents)) != tuple(sorted(self.agent_ids)):
            raise GovernanceError("source_proposal_host_agent_set_mismatch")
        self._host.agents = dict(agents)
        self._host.reflection_manager = reflection_manager
        self._host.episode_manager = episode_manager
        if known_actions is not None:
            self._host.known_actions = tuple(
                dict.fromkeys(str(item) for item in known_actions)
            )

    def submit(self, proposal: Proposal, *, tick: int) -> Proposal:
        """Run a caller-supplied source proposal through validate and review routing."""

        if not isinstance(proposal, Proposal):
            raise TypeError("source_proposal_required")
        self._set_tick(tick)
        submitted = self._source.create_proposal(proposal, self._host)
        if submitted.status == "draft":
            self._source.route_for_approval(submitted, self._host)
        return submitted

    def propose_from_wish(self, wish: Wish, *, tick: int) -> Proposal:
        """Fail closed instead of turning a wish into a hard-coded proposal."""

        del wish, tick
        raise SourceProposalGenerationUnavailableError(
            "source_proposal_generation_unavailable: "
            "requires the HCI OrgWorld reflection/LLM proposal generator"
        )

    def approve(self, proposal_id: str, agent_id: str, *, tick: int) -> Proposal:
        self._set_tick(tick)
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            raise GovernanceError("proposal_not_found")
        if proposal.status not in {"draft", "under_review"}:
            raise GovernanceError("proposal_not_under_review")
        # The source manager intentionally records a vote before deciding
        # whether the required role quorum is met. Do not add the former
        # compatibility-only designated-approver veto here.
        self._source.approve_proposal(proposal_id, agent_id, self._host)
        return proposal

    def approval_decision(
        self, proposal: Proposal, *, tick: int
    ) -> ApprovalCheckDecision:
        """Expose a read-only portable status view of source transition rules."""

        self._set_tick(tick)
        missing = tuple(
            agent_id
            for agent_id in proposal.approval_required_from
            if agent_id not in set(proposal.approved_by)
        )
        reasons = []
        if not self._source._approver_threshold_met(proposal):
            reasons.append("approval_quorum_pending")
        if (
            proposal.proposal_type == "protocol_proposal"
            and len(set(proposal.approved_by)) < MIN_DISTINCT_PROTOCOL_APPROVERS
        ):
            reasons.append("distinct_approver_floor_pending")
        if not self._source._can_adopt(proposal, self._host):
            reasons.append("review_latency_pending")
        return ApprovalCheckDecision(
            request_id=proposal.proposal_id,
            ready=proposal.status == "adopted" or not reasons,
            missing_approver_ids=missing,
            reason_codes=tuple(dict.fromkeys(reasons)),
        )

    def ready(self, proposal: Proposal, *, tick: int) -> bool:
        """Whether an under-review source proposal may now be materialized."""

        self._set_tick(tick)
        if proposal.status == "adopted":
            return proposal.object_created_id is not None
        return (
            proposal.status == "under_review"
            and self._source._approver_threshold_met(proposal)
            and self._source._can_adopt(proposal, self._host)
        )

    def adopt(self, proposal_id: str, *, tick: int) -> str:
        """Drive the source pending-adoption sweep and return its registry id."""

        self._set_tick(tick)
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            raise GovernanceError("proposal_not_found")
        if proposal.status == "under_review":
            if not self.ready(proposal, tick=tick):
                raise GovernanceError("proposal_not_ready")
            proposal.status = "approved"
            self._source.adopt_proposal(proposal_id, self._host)
        elif proposal.status == "approved":
            self._source.adopt_proposal(proposal_id, self._host)
        elif proposal.status != "adopted":
            raise GovernanceError("proposal_not_ready")
        if proposal.status != "adopted" or not proposal.object_created_id:
            raise GovernanceError(proposal.rejection_reason or "proposal_materialization_failed")
        return self.registry_protocol_id_for(proposal)

    def registry_protocol_id_for(self, proposal: Proposal) -> str:
        """Translate a source ``ProtocolSpec`` id to its source registry mirror id.

        The Proposal object itself retains the source ``protospec_N`` linkage;
        this adapter id is used only by the public trace, whose protocol
        collection is the already-pinned source registry.
        """

        source_id = str(proposal.object_created_id or "")
        if not source_id.startswith("protospec_"):
            raise GovernanceError("source_proposal_has_no_protocol_spec")
        registry_id = f"proto_spec_{source_id.split('_')[-1]}"
        if registry_id not in self.protocol_registry.protocols:
            raise GovernanceError("source_protocol_registry_materialization_missing")
        return registry_id

    def public_proposal_object_ids(self) -> dict[str, str]:
        """Return the explicit release-trace projection, without mutating source data."""

        projected = {}
        for proposal in self.proposals.values():
            if proposal.status != "adopted" or not proposal.object_created_id:
                continue
            try:
                projected[proposal.proposal_id] = self.registry_protocol_id_for(proposal)
            except GovernanceError:
                continue
        return projected

    def source_status(self) -> dict[str, object]:
        """Auditable capability statement for this narrow proposal port."""

        return {
            **source_b3_proposal_provenance(),
            "activation": "active_source_hci_proposal_manager",
            "source_protocol_materialization": "active_source_hci_registry_adapter",
            "proposal_count": len(self.proposals),
            "tool_count": len(self.tools),
            "protocol_spec_count": len(self.protocol_specs),
            "unavailable_fail_closed": [
                "source_llm_proposal_generation",
                "source_orgworld_action_execution",
                "source_registry_repair_profile",
                "hci_human_seat_host_adapter",
            ],
        }

    def _set_tick(self, tick: int) -> None:
        if int(tick) < 0:
            raise ValueError("source_proposal_tick_must_be_non_negative")
        self._host.world_tick = int(tick)


__all__ = [
    "GovernanceError",
    "GovernanceManager",
    "SourceProposalGenerationUnavailableError",
]
