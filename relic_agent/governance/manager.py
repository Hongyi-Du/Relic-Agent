"""Compatibility proposal shell with source-backed protocol lifecycle.

The input proposal objects remain a temporary release-shell boundary.  Once a
proposal is ready, its protocol lifecycle is executed by the source-ported HCI
registry rather than the former bespoke registry.
"""

from __future__ import annotations

from organization_core import ApprovalCheckDecision, ApprovalCheckRequest, ApprovalPolicy

from relic_agent.governance.models import Proposal
from relic_agent.reflection.models import Wish
from relic_agent.source_b3.protocol_lifecycle import SourceB3ProtocolLifecycleAdapter
from relic_agent.source_core import SourceCoreObservationBridge


class GovernanceError(ValueError):
    pass


class GovernanceManager:
    def __init__(
        self,
        *,
        agent_ids: tuple[str, ...],
        min_approvers: int,
        review_ticks: int,
    ) -> None:
        self.agent_ids = agent_ids
        self.min_approvers = min_approvers
        self.review_ticks = review_ticks
        self.proposals: dict[str, Proposal] = {}
        self.protocol_registry = SourceB3ProtocolLifecycleAdapter(
            min_supporters=min_approvers,
            review_ticks=review_ticks,
        )
        self._source_protocol_id_by_proposal: dict[str, str] = {}
        self._approval_policy = ApprovalPolicy()
        self._sequence = 0

    def bind_source_core_bridge(self, bridge: SourceCoreObservationBridge) -> None:
        """Attach the active source lifecycle to the existing host boundary."""

        self.protocol_registry.bind_source_core_bridge(bridge)

    def propose_from_wish(self, wish: Wish, *, tick: int) -> Proposal:
        if wish.status not in {"open", "interpreted"}:
            raise GovernanceError("wish_not_open")
        self._sequence += 1
        proposal_id = f"proposal_{self._sequence:05d}"
        required_approvers = [agent_id for agent_id in self.agent_ids if agent_id != wish.agent_id][
            : self.min_approvers
        ]
        if len(required_approvers) < self.min_approvers:
            required_approvers = list(self.agent_ids[: self.min_approvers])
        proposal = Proposal(
            proposal_id=proposal_id,
            proposal_type="protocol_proposal",
            title="Task ownership and peer review",
            summary="Record task ownership before work and peer review before completion.",
            proposer_agent_id=wish.agent_id,
            source_wish_id=wish.wish_id,
            source_wish_ids=[wish.wish_id],
            source_reflection_id=wish.source_reflection_id,
            source_episode_id=wish.source_episode_id,
            source_episode_ids=list(wish.related_episode_ids),
            source_event_ids=list(wish.source_event_ids),
            target_problem=wish.target_problem,
            proposed_solution=wish.suggested_improvement,
            required_actions=["claim_task", "review_task"],
            affected_agents=list(self.agent_ids),
            affected_objects=list(wish.related_object_ids),
            expected_benefits=[wish.expected_benefit],
            risks=[wish.risk_if_unaddressed],
            feasibility_score=0.9,
            usefulness_score=0.9,
            risk_score=0.1,
            adoption_score=0.9,
            family="task_ownership_review",
            status="under_review",
            approval_required_from=required_approvers,
            created_at_tick=tick,
            updated_at_tick=tick,
        )
        self.proposals[proposal_id] = proposal
        source_protocol_id = f"proto_{proposal.family}"
        self.protocol_registry.propose(
            proposer_id=proposal.proposer_agent_id or "",
            protocol_type=proposal.family,
            rule_summary=proposal.summary,
            scope="organization",
            target_process="task_lifecycle",
            tick=tick,
            protocol_id=source_protocol_id,
        )
        self._source_protocol_id_by_proposal[proposal_id] = source_protocol_id
        wish.status = "converted_to_proposal"
        wish.generated_proposal_ids.append(proposal_id)
        wish.updated_at_tick = tick
        return proposal

    def approve(self, proposal_id: str, agent_id: str, *, tick: int) -> Proposal:
        proposal = self.proposals[proposal_id]
        if proposal.status != "under_review":
            raise GovernanceError("proposal_not_under_review")
        if agent_id not in proposal.approval_required_from:
            raise GovernanceError("agent_not_designated_approver")
        if agent_id not in proposal.approved_by:
            proposal.approved_by.append(agent_id)
            source_protocol_id = self._source_protocol_id_by_proposal[proposal_id]
            self.protocol_registry.support(agent_id, source_protocol_id, tick=tick)
        proposal.updated_at_tick = tick
        return proposal

    def ready(self, proposal: Proposal, *, tick: int) -> bool:
        source_protocol_id = self._source_protocol_id_by_proposal.get(proposal.proposal_id)
        if source_protocol_id is None:
            return False
        self.protocol_registry.tick_adoptions(tick=tick)
        source_protocol = self.protocol_registry.protocols[source_protocol_id]
        return (
            proposal.status == "under_review"
            and self.approval_decision(proposal, tick=tick).ready
            and source_protocol.adoption_status == "adopted"
        )

    def approval_decision(
        self,
        proposal: Proposal,
        *,
        tick: int,
    ) -> ApprovalCheckDecision:
        """Route compatibility approval state through the canonical core policy."""

        return self._approval_policy.evaluate(
            ApprovalCheckRequest(
                request_id=proposal.proposal_id,
                required_approver_ids=tuple(proposal.approval_required_from),
                approved_by_ids=tuple(proposal.approved_by),
                elapsed_steps=max(0, tick - proposal.created_at_tick),
                min_review_steps=self.review_ticks,
                min_distinct_approvers=self.min_approvers,
                fallback_min_approvers=self.min_approvers,
            )
        )

    def adopt(self, proposal_id: str, *, tick: int) -> str:
        proposal = self.proposals[proposal_id]
        if not self.ready(proposal, tick=tick):
            raise GovernanceError("proposal_not_ready")
        protocol_id = self._source_protocol_id_by_proposal[proposal_id]
        protocol = self.protocol_registry.protocols[protocol_id]
        if protocol.adoption_status != "adopted":
            raise GovernanceError("protocol_registry_refused_adoption")
        proposal.status = "adopted"
        proposal.object_created_id = protocol_id
        proposal.adopted_tick = tick
        proposal.updated_at_tick = tick
        return protocol_id
