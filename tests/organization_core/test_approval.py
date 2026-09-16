from organization_core import (
    ApprovalCheckRequest,
    ApprovalMember,
    ApprovalPolicy,
    ApprovalRoutingRequest,
    DecisionDisposition,
    RoleApprovalRouter,
)


def test_role_approval_router_preserves_roster_order():
    decision = RoleApprovalRouter().route(
        ApprovalRoutingRequest(
            request_id="route-proposal",
            members=(
                ApprovalMember("builder", "fast_engineer"),
                ApprovalMember("founder", "founder"),
                ApprovalMember("reliability", "reliability"),
                ApprovalMember("cofounder", "cofounder"),
            ),
            required_roles=("cofounder", "reliability"),
        )
    )

    assert decision.approver_ids == ("reliability", "cofounder")


def test_approval_policy_reports_quorum_distinctness_and_latency_separately():
    policy = ApprovalPolicy()
    pending = policy.evaluate(
        ApprovalCheckRequest(
            request_id="proposal-1",
            required_approver_ids=("founder", "cofounder"),
            approved_by_ids=("founder",),
            elapsed_steps=1,
            min_review_steps=3,
            min_distinct_approvers=2,
        )
    )

    assert pending.ready is False
    assert pending.missing_approver_ids == ("cofounder",)
    assert pending.reason_codes == (
        "approval_quorum_pending",
        "distinct_approver_floor_pending",
        "review_latency_pending",
    )
    assert (
        pending.as_organization_decision().disposition
        is DecisionDisposition.REQUIRE_APPROVAL
    )

    ready = policy.evaluate(
        ApprovalCheckRequest(
            request_id="proposal-1",
            required_approver_ids=("founder", "cofounder"),
            approved_by_ids=("founder", "cofounder"),
            elapsed_steps=3,
            min_review_steps=3,
            min_distinct_approvers=2,
        )
    )
    assert ready.ready is True
    assert ready.as_organization_decision().disposition is DecisionDisposition.ALLOW


def test_approval_policy_supports_fallback_quorum_without_named_approvers():
    policy = ApprovalPolicy()
    assert not policy.evaluate(
        ApprovalCheckRequest(
            request_id="fallback",
            approved_by_ids=("one",),
            fallback_min_approvers=2,
        )
    ).ready
    assert policy.evaluate(
        ApprovalCheckRequest(
            request_id="fallback",
            approved_by_ids=("one", "two"),
            fallback_min_approvers=2,
        )
    ).ready
