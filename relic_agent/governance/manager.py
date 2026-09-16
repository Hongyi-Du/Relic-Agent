"""Explicit-approval governance extracted from the B3 proposal lifecycle.

Raw reflections and wishes cannot directly create a protocol. The only path is
wish -> proposal -> review latency -> distinct approvals -> adoption.
"""

from __future__ import annotations

from relic_agent.governance.models import Proposal
from relic_agent.protocols.registry import ProtocolRegistry
from relic_agent.reflection.models import Wish


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
        self.protocol_registry = ProtocolRegistry(
            min_supporters=min_approvers,
            review_ticks=review_ticks,
        )
        self._sequence = 0

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
        proposal.updated_at_tick = tick
        return proposal

    def ready(self, proposal: Proposal, *, tick: int) -> bool:
        return (
            proposal.status == "under_review"
            and len(set(proposal.approved_by)) >= self.min_approvers
            and tick - proposal.created_at_tick >= self.review_ticks
        )

    def adopt(self, proposal_id: str, *, tick: int) -> str:
        proposal = self.proposals[proposal_id]
        if not self.ready(proposal, tick=tick):
            raise GovernanceError("proposal_not_ready")
        protocol_id = f"protocol_{proposal.family}"
        protocol = self.protocol_registry.propose(
            proposer_id=proposal.proposer_agent_id or "",
            protocol_type=proposal.family,
            rule_summary=proposal.summary,
            scope="organization",
            target_process="task_lifecycle",
            tick=proposal.created_at_tick,
            protocol_id=protocol_id,
            created_from_proposal_id=proposal.proposal_id,
        )
        for approver in proposal.approved_by:
            if protocol.adoption_status == "proposed":
                self.protocol_registry.support(approver, protocol_id, tick=tick)
        self.protocol_registry.tick_adoptions(tick)
        if protocol.adoption_status != "adopted":
            raise GovernanceError("protocol_registry_refused_adoption")
        proposal.status = "adopted"
        proposal.object_created_id = protocol_id
        proposal.adopted_tick = tick
        proposal.updated_at_tick = tick
        return protocol_id
