from organization_core import (
    DecisionDisposition,
    DecisionRequest,
    FactPredicate,
    GateClause,
    GateDecision,
    GateRequest,
    OrganizationGateEngine,
    TypedGateEngine,
    TypedGateRule,
)


def _rule(*clauses: GateClause) -> TypedGateRule:
    return TypedGateRule(
        rule_id="review-rule",
        family="review_before_merge",
        governed_actions=frozenset({"merge_pr"}),
        clauses=clauses,
    )


def _request(*, action: str = "merge_pr", facts=None, rules=()) -> GateRequest:
    return GateRequest(
        decision=DecisionRequest("request-1", "agent-1", action),
        facts=facts or {},
        rules=tuple(rules),
    )


def test_typed_gate_denies_only_when_every_fact_predicate_matches():
    clause = GateClause(
        all_of=(
            FactPredicate("pull_request.exists", True),
            FactPredicate("pull_request.reviewed", False),
        ),
        reason="pull request has had no review",
    )
    engine = TypedGateEngine()
    assert isinstance(engine, OrganizationGateEngine)

    denied = engine.evaluate(
        _request(
            facts={"pull_request.exists": True, "pull_request.reviewed": False},
            rules=(_rule(clause),),
        )
    )
    assert denied.disposition is DecisionDisposition.DENY
    assert denied.rule_id == "review-rule"
    assert denied.family == "review_before_merge"
    assert denied.reason == "pull request has had no review"
    assert not denied.allowed

    for facts in (
        {},
        {"pull_request.exists": False, "pull_request.reviewed": False},
        {"pull_request.exists": True, "pull_request.reviewed": True},
    ):
        assert engine.evaluate(
            _request(facts=facts, rules=(_rule(clause),))
        ).allowed


def test_gate_ignores_rules_for_a_different_action_type():
    clause = GateClause(
        all_of=(FactPredicate("blocked", True),),
        reason="blocked",
    )
    result = TypedGateEngine().evaluate(
        _request(action="edit_file", facts={"blocked": True}, rules=(_rule(clause),))
    )
    assert result.allowed
    assert result.rule_id is None


def test_gate_supports_require_approval_without_knowing_the_host():
    rule = TypedGateRule(
        rule_id="release-approval",
        family="release_approval",
        governed_actions=frozenset({"release"}),
        clauses=(
            GateClause(
                all_of=(FactPredicate("release.approved", False),),
                disposition=DecisionDisposition.REQUIRE_APPROVAL,
                reason="release approval is missing",
            ),
        ),
    )
    result = TypedGateEngine().evaluate(
        _request(
            action="release",
            facts={"release.approved": False},
            rules=(rule,),
        )
    )
    assert result.disposition is DecisionDisposition.REQUIRE_APPROVAL
    assert result.reason == "release approval is missing"


def test_gate_request_detaches_host_facts():
    source = {"reviewers": ["a"]}
    request = _request(facts=source)
    source["reviewers"].append("b")
    assert request.facts == {"reviewers": ["a"]}


def test_gate_decision_normalizes_string_dispositions():
    decision = GateDecision(request_id="request-1", disposition="allow")
    assert decision.allowed
    assert decision.disposition is DecisionDisposition.ALLOW
