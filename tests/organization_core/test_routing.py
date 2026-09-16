from organization_core import (
    AuthorityRoutingEngine,
    OrganizationRoutingEngine,
    RoutingCandidate,
    RoutingRequest,
)


def test_authority_routing_combines_standing_and_availability():
    engine = AuthorityRoutingEngine()
    assert isinstance(engine, OrganizationRoutingEngine)
    decision = engine.route(
        RoutingRequest(
            request_id="route-1",
            domain="engineering",
            candidates=(
                RoutingCandidate(
                    "high-but-busy",
                    authority={"engineering": 0.9},
                    availability=0.1,
                ),
                RoutingCandidate(
                    "available",
                    authority={"engineering": 0.5},
                    availability=1.0,
                ),
            ),
            concentration=2.0,
        )
    )

    assert decision.assignee_ids == ("available",)
    assert decision.scores[1] > decision.scores[0]


def test_authority_routing_is_stable_for_ties_and_supports_multiple_assignees():
    decision = AuthorityRoutingEngine().route(
        RoutingRequest(
            request_id="route-2",
            domain="review",
            candidates=(
                RoutingCandidate("first"),
                RoutingCandidate("second"),
                RoutingCandidate("third", authority={"review": 0.5}),
            ),
            count=2,
            concentration=2.0,
        )
    )

    assert decision.assignee_ids == ("third", "first")
