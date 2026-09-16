import pytest

from relic_agent.protocols import ProtocolRegistry


@pytest.mark.unit
def test_protocol_use_violation_enforcement_amendment_and_retirement() -> None:
    registry = ProtocolRegistry(min_supporters=2)
    protocol = registry.propose(
        proposer_id="a",
        protocol_type="peer_review",
        rule_summary="A second member reviews completion evidence.",
        scope="task",
        tick=0,
    )
    registry.support("b", protocol.protocol_id, tick=3)

    assert protocol.adoption_status == "adopted"
    registry.use("a", protocol.protocol_id, tick=4, task_id="task_1")
    violation = registry.violate("a", protocol.protocol_id, tick=5, context_id="task_2")
    enforcement = registry.enforce(
        "b",
        protocol.protocol_id,
        tick=6,
        violation_event_id=violation.event_id,
        blocked=True,
        context_id="task_2",
    )
    amendment = registry.amend(
        "b",
        protocol.protocol_id,
        tick=7,
        revision_kind="clarify",
        source_proposal_id="proposal_2",
    )
    retired = registry.retire("a", protocol.protocol_id, tick=8, source_proposal_id="proposal_3")

    assert violation.event_id in protocol.violation_events
    assert enforcement.event_id in protocol.enforcement_events
    assert amendment.event_type == "amendment"
    assert retired.event_type == "retirement"
    assert protocol.status == "retired"
    with pytest.raises(ValueError, match="not_live"):
        registry.use("a", protocol.protocol_id, tick=9)
