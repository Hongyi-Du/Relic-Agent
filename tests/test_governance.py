import pytest

from relic_agent.governance.manager import GovernanceError, GovernanceManager
from relic_agent.reflection.models import Wish


def _wish() -> Wish:
    return Wish(
        wish_id="wish_1",
        agent_id="builder",
        source_reflection_id="reflection_1",
        source_reflection_ids=["reflection_1"],
        source_episode_id="episode_1",
        related_episode_ids=["episode_1"],
        source_event_ids=["event_1"],
        target_problem="work lacks peer review",
        suggested_improvement="require peer review",
        expected_benefit="auditable completion",
        risk_if_unaddressed="unreviewed completion",
    )


@pytest.mark.unit
def test_wish_cannot_skip_explicit_governance_or_review_latency() -> None:
    manager = GovernanceManager(agent_ids=("builder", "reviewer"), min_approvers=2, review_ticks=3)
    wish = _wish()
    proposal = manager.propose_from_wish(wish, tick=5)

    assert wish.status == "converted_to_proposal"
    assert proposal.source_wish_id == wish.wish_id
    assert set(manager.protocol_registry.protocols) == {"proto_task_ownership_review"}
    source_protocol = manager.protocol_registry.protocols["proto_task_ownership_review"]
    assert source_protocol.adoption_status == "proposed"
    assert source_protocol.proposal_event_id == "pev_1"
    with pytest.raises(GovernanceError, match="not_ready"):
        manager.adopt(proposal.proposal_id, tick=5)

    for agent_id in proposal.approval_required_from:
        manager.approve(proposal.proposal_id, agent_id, tick=6)
    with pytest.raises(GovernanceError, match="not_ready"):
        manager.adopt(proposal.proposal_id, tick=7)

    protocol_id = manager.adopt(proposal.proposal_id, tick=8)
    protocol = manager.protocol_registry.protocols[protocol_id]
    assert proposal.status == "adopted"
    assert protocol.adoption_status == "adopted"
    assert len(set(protocol.supporters)) == 2


@pytest.mark.unit
def test_only_designated_approvers_can_approve() -> None:
    manager = GovernanceManager(agent_ids=("a", "b", "c"), min_approvers=2, review_ticks=3)
    proposal = manager.propose_from_wish(_wish(), tick=1)
    outsider = next(
        agent for agent in manager.agent_ids if agent not in proposal.approval_required_from
    )

    with pytest.raises(GovernanceError, match="not_designated"):
        manager.approve(proposal.proposal_id, outsider, tick=2)
