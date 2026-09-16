import pytest

from relic_agent.governance.manager import (
    GovernanceError,
    GovernanceManager,
    SourceProposalGenerationUnavailableError,
)
from relic_agent.governance.models import Proposal
from relic_agent.reflection.models import Wish


def _manager() -> GovernanceManager:
    return GovernanceManager(
        agent_ids=("paul", "victor", "sean"),
        agent_roles={"paul": "founder", "victor": "cofounder", "sean": "fast_engineer"},
        known_actions=("claim_task", "use_protocol"),
        min_approvers=2,
        review_ticks=3,
    )


def _proposal() -> Proposal:
    return Proposal(
        proposal_id="proposal_1",
        proposal_type="protocol_proposal",
        title="Review Before Completion",
        summary="Require a review before a task is marked complete.",
        proposer_agent_id="sean",
        source_wish_id="wish_1",
        source_wish_ids=["wish_1", "wish_2"],
        source_episode_ids=["episode_1", "episode_2"],
        source_event_ids=["event_1", "event_2"],
        target_problem="task completion lacks review evidence",
        proposed_solution="require a recorded review before task completion",
        required_actions=["claim_task", "use_protocol"],
        required_artifacts=["review_record"],
        expected_benefits=["auditable completion"],
    )


@pytest.mark.unit
def test_source_proposal_manager_preserves_review_then_registry_materialization() -> None:
    manager = _manager()
    proposal = manager.submit(_proposal(), tick=0)

    assert proposal.status == "under_review"
    assert proposal.approval_required_from == ["paul", "victor"]
    assert manager.protocol_registry.protocols == {}
    assert manager.approval_decision(proposal, tick=0).ready is False

    manager.approve(proposal.proposal_id, "paul", tick=1)
    manager.approve(proposal.proposal_id, "victor", tick=1)
    with pytest.raises(GovernanceError, match="not_ready"):
        manager.adopt(proposal.proposal_id, tick=2)

    protocol_id = manager.adopt(proposal.proposal_id, tick=3)
    assert protocol_id == "proto_spec_1"
    assert proposal.status == "adopted"
    # The source object itself retains the ProtocolSpec relationship; only the
    # public trace adapter projects it onto the registry mirror id.
    assert proposal.object_created_id == "protospec_1"
    assert manager.public_proposal_object_ids() == {"proposal_1": "proto_spec_1"}
    source_protocol = manager.protocol_registry.protocols[protocol_id]
    assert source_protocol.adoption_status == "adopted"
    assert source_protocol.target_process == proposal.target_problem
    assert [event.event_id for event in manager.protocol_registry.events[:2]] == [
        "pev_1",
        "pev_2",
    ]
    spec = manager.protocol_specs["protospec_1"]
    assert spec.problem_evidence == ["episode_1", "episode_2", "event_1", "event_2"]
    assert spec.scope and spec.success_metric and spec.sunset_rule


@pytest.mark.unit
def test_source_validation_rejects_unknown_action_without_local_substitution() -> None:
    manager = _manager()
    proposal = _proposal()
    proposal.required_actions = ["invent_review_action"]

    submitted = manager.submit(proposal, tick=1)

    assert submitted.status == "rejected"
    assert submitted.rejection_reason == "required action 'invent_review_action' does not exist"
    assert manager.protocol_registry.protocols == {}


@pytest.mark.unit
def test_wish_to_proposal_generation_fails_closed_instead_of_using_fixed_story() -> None:
    manager = _manager()
    wish = Wish(
        wish_id="wish_1",
        agent_id="sean",
        target_problem="missing review",
        suggested_improvement="write a review rule",
        status="open",
    )

    with pytest.raises(SourceProposalGenerationUnavailableError, match="generation_unavailable"):
        manager.propose_from_wish(wish, tick=1)

    assert manager.proposals == {}
    assert wish.status == "open"
    assert wish.generated_proposal_ids == []


@pytest.mark.unit
def test_source_role_quorum_does_not_use_legacy_designated_approver_veto() -> None:
    manager = _manager()
    proposal = manager.submit(_proposal(), tick=0)

    manager.approve(proposal.proposal_id, "sean", tick=1)
    assert proposal.approved_by == ["sean"]
    assert manager.approval_decision(proposal, tick=3).ready is False
    assert "approval_quorum_pending" in manager.approval_decision(proposal, tick=3).reason_codes

    manager.approve(proposal.proposal_id, "paul", tick=3)
    manager.approve(proposal.proposal_id, "victor", tick=3)
    assert manager.adopt(proposal.proposal_id, tick=3) == "proto_spec_1"


@pytest.mark.unit
def test_unbound_policy_repair_fails_closed_without_orgworld_harm_detector() -> None:
    manager = _manager()
    proposal = manager.submit(
        Proposal(
            proposal_id="repair_1",
            proposal_type="policy_repair_proposal",
            title="Relax an unknown review rule",
            summary="remove a harmful unbound rule",
            proposer_agent_id="sean",
            repair_kind="relax",
        ),
        tick=0,
    )
    manager.approve(proposal.proposal_id, "paul", tick=1)
    manager.approve(proposal.proposal_id, "victor", tick=1)

    with pytest.raises(GovernanceError, match="target_unavailable"):
        manager.adopt(proposal.proposal_id, tick=2)
    assert proposal.status == "rejected"
    assert proposal.rejection_reason == "source_policy_repair_target_unavailable"
