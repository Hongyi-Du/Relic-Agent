import pytest

from organization_core import (
    ApprovalMember,
    ProposalLifecyclePolicy,
    ProposalLifecycleState,
    ProposalValidationPolicy,
    ProposalValidationRequest,
)


MEMBERS = (
    ApprovalMember("builder", "fast_engineer"),
    ApprovalMember("founder", "founder"),
    ApprovalMember("reliability", "reliability"),
    ApprovalMember("cofounder", "cofounder"),
)


def _state(proposal_type="protocol_proposal", **changes):
    payload = {
        "proposal_id": "proposal-1",
        "proposal_type": proposal_type,
        "status": "draft",
        "created_step": 10,
        "updated_step": 10,
    }
    payload.update(changes)
    return ProposalLifecycleState(**payload)


@pytest.mark.parametrize(
    ("proposal_type", "expected"),
    [
        ("protocol_proposal", ("founder", "cofounder")),
        ("tool_proposal", ("reliability", "cofounder")),
        ("artifact_template_proposal", ("cofounder",)),
        ("unknown_proposal", ("founder", "cofounder")),
    ],
)
def test_proposal_lifecycle_routes_roles_without_host_objects(
    proposal_type,
    expected,
):
    decision = ProposalLifecyclePolicy().route_for_approval(
        _state(proposal_type),
        members=MEMBERS,
        current_step=12,
    )

    assert decision.state.status == "under_review"
    assert decision.state.approval_required_from == expected
    assert decision.state.updated_step == 12


@pytest.mark.parametrize(
    ("proposal_type", "ready_step"),
    [
        ("protocol_proposal", 13),
        ("tool_proposal", 12),
        ("task_proposal", 10),
    ],
)
def test_proposal_lifecycle_owns_review_latency_and_approval_transition(
    proposal_type,
    ready_step,
):
    policy = ProposalLifecyclePolicy()
    state = _state(
        proposal_type,
        status="under_review",
        approval_required_from=("founder", "cofounder"),
    )
    first = policy.record_approval(
        state,
        approver_id="founder",
        current_step=ready_step,
    )
    final = policy.record_approval(
        first.state,
        approver_id="cofounder",
        current_step=ready_step,
    )

    assert first.ready_for_adoption is False
    assert final.ready_for_adoption is True
    assert final.state.status == "approved"


def test_proposal_lifecycle_deduplicates_approval_but_preserves_rejection_log():
    policy = ProposalLifecyclePolicy()
    state = _state(status="under_review")
    once = policy.record_approval(state, approver_id="founder", current_step=10)
    twice = policy.record_approval(
        once.state,
        approver_id="founder",
        current_step=10,
    )
    rejected = policy.record_rejection(
        twice.state,
        rejector_id="reliability",
        reason="too risky",
    )

    assert twice.state.approved_by == ("founder",)
    assert rejected.state.status == "rejected"
    assert rejected.state.rejected_by == ("reliability",)
    assert rejected.state.rejection_reason == "too risky"


def test_proposal_lifecycle_state_round_trips_as_portable_json_shape():
    original = _state(
        status="under_review",
        approval_required_from=("founder", "cofounder"),
        approved_by=("founder",),
    )

    assert ProposalLifecycleState.from_dict(original.as_dict()) == original


def test_pending_sweep_readiness_returns_the_approved_transition():
    decision = ProposalLifecyclePolicy().evaluate_readiness(
        _state(
            status="under_review",
            approval_required_from=("founder", "cofounder"),
            approved_by=("founder", "cofounder"),
        ),
        current_step=13,
    )

    assert decision.ready_for_adoption is True
    assert decision.state.status == "approved"


@pytest.mark.parametrize(
    ("changes", "accepted", "reason_code"),
    [
        ({}, True, "proposal_valid"),
        ({"proposal_type": "bogus"}, False, "illegal_proposal_type"),
        ({"title": ""}, False, "missing_title_or_summary"),
        (
            {"required_actions": ("missing_action",)},
            False,
            "unknown_required_action",
        ),
        (
            {"active_tool_names": ("Review Helper",)},
            False,
            "duplicate_active_tool",
        ),
        (
            {"adopted_protocol_names": ("Review Helper",)},
            False,
            "duplicate_adopted_protocol",
        ),
    ],
)
def test_proposal_validation_is_host_neutral(changes, accepted, reason_code):
    payload = {
        "proposal_id": "proposal-1",
        "proposal_type": "tool_proposal",
        "title": "Review Helper",
        "summary": "Collect review evidence before merge.",
        "required_actions": ("review_pr",),
        "known_actions": ("review_pr", "merge_pr"),
    }
    payload.update(changes)

    decision = ProposalValidationPolicy().validate(
        ProposalValidationRequest(**payload)
    )

    assert decision.accepted is accepted
    assert reason_code in decision.reason_codes
