import random

import pytest

from relic_agent.decision import Candidate, OrganizationPolicy
from relic_agent.organization import AgentState


@pytest.mark.unit
def test_profile_changes_protocol_utility_and_trace_is_complete() -> None:
    policy = OrganizationPolicy()
    process_agent = AgentState(
        agent_id="process",
        display_name="Process",
        role="reviewer",
        profile={"process_commitment": 1.0, "conformity": 1.0},
    )
    resistant_agent = AgentState(
        agent_id="resistant",
        display_name="Resistant",
        role="builder",
        profile={"process_resistance": 1.0},
    )
    candidate = Candidate(
        action_id="use_protocol",
        object_id="protocol_review",
        features={"protocol_use_potential": 1.0},
    )

    assert policy.score(process_agent, candidate) > policy.score(resistant_agent, candidate)
    chosen, trace = policy.select(
        tick=4,
        agent=process_agent,
        candidates=[candidate],
        rng=random.Random(7),
    )

    assert chosen is candidate
    assert trace.chosen_action_id == "use_protocol"
    assert trace.candidates[0]["selected"] is True
    assert trace.candidates[0]["features"] == {"protocol_use_potential": 1.0}
