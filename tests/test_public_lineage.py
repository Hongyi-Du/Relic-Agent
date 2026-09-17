from types import SimpleNamespace as NS

import pytest

from relic_agent.replay.trace import TraceError, build_trace
from relic_agent.source_host.projection import project_public_frame


def test_structural_lineage_excludes_private_text_and_exposes_configuration():
    world = NS(
        world_tick=3,
        agents={"lead": NS(name="Lead", role="custom_role", active_tasks=[])},
        tasks={}, action_log=[],
        events=[{"type": "tool_use_event", "tool_id": "tool1", "agent_id": "lead", "tick": 3,
                 "private_arguments": "PRIVATE_THOUGHT"}],
        episode_manager=NS(episodes={}),
        proposal_manager=NS(proposals={}, protocol_specs={}, tools={"tool1": NS(
            created_from_proposal_id="p1", source_wish_id="w1", source_episode_ids=["ep1"],
            creator_agent_id="lead", description="PRIVATE_THOUGHT")}),
        reflection_manager=NS(
            reflections={"r1": NS(agent_id="lead", tick=2, source_episode_ids=["ep1"],
                                   created_wish_ids=["w1"], raw_text="PRIVATE_THOUGHT")},
            wishes={"w1": NS(agent_id="lead", created_at_tick=2, source_reflection_id="r1",
                              generated_proposal_ids=["p1"], raw_reflection_excerpt="PRIVATE_THOUGHT")}),
        protocol_registry=NS(protocols={"rule": NS(protocol_type="task_ownership")}, events=[
            NS(event_id="amend1", event_type="protocol_amended", protocol_id="rule", tick=3,
               data={"revision_kind": "relax", "raw_text": "PRIVATE_THOUGHT"})]),
        protocol_origins={"rule": "initial"},
        generic_config={"organization": {"description": "Demo", "channels": ["general"]},
                        "providers": {"local": {"default_model": "mock-v2", "api_key_env": "TOKEN"}},
                        "agents": [{"id": "lead", "provider": "local", "tools": ["files"],
                                    "permissions": ["review"]}],
                        "governance": {"protocol_quorum": 1},
                        "learning": {"capability_learning": True}})
    frame = project_public_frame(world, sequence=0, organization_id="org", organization_name="Org",
                                 action_start=0, protocol_event_start=0)
    trace = build_trace(run_id="lineage-test", organization_id="org", config_digest="0" * 64,
                        frames=[frame])
    assert "PRIVATE_THOUGHT" not in str(trace)
    assert "TOKEN" not in str(trace)
    assert frame["lineage"][0]["created_wish_ids"] == ["w1"]
    assert frame["lineage"][1]["source_reflection_id"] == "r1"
    assert frame["lineage"][2]["created_from_proposal_id"] == "p1"
    assert frame["lineage"][2]["source_wish_id"] == "w1"
    assert frame["tool_events"][0]["tool_id"] == "tool1"
    assert frame["tool_events"][0]["event_type"] == "tool_use"
    assert frame["organization"]["agents"][0]["model"] == "mock-v2"
    assert frame["organization"]["protocols"][0]["origin"] == "initial"
    assert frame["organization"]["protocols"][0]["revisions"][0]["revision_kind"] == "relax"
    assert frame["organization"]["config_summary"]["protocol_quorum"] == 1
    world.generic_config["governance"]["decision_visibility"] = "private"
    world.proposal_manager.proposals["proposal-private"] = NS(
        proposal_type="protocol", title="SECRET_REVIEW", summary="SECRET_REVIEW",
        status="adopted", approved_by=["lead"])
    private = project_public_frame(world, sequence=0, organization_id="org", organization_name="Org",
                                   action_start=0, protocol_event_start=0)
    assert "SECRET_REVIEW" not in str(private)
    assert private["organization"]["proposals"][0]["proposal_id"] == "proposal-private"
    assert "approved_by" not in private["organization"]["proposals"][0]
    frame["lineage"][0]["raw_text"] = "must remain private"
    with pytest.raises(TraceError, match="unsupported fields"):
        build_trace(run_id="invalid", organization_id="org", config_digest="0" * 64, frames=[frame])
