"""Focused checks for the generic lifecycle seam around the source world."""

from __future__ import annotations

from pathlib import Path

import yaml

from agent_sdk.lived.core.contracts import ActionCandidate
from relic_agent.config import load_config
from relic_agent.runtime.builder import GenericExecution, build_generic_world
from relic_agent.source_host.projection import project_public_frame


ROOT = Path(__file__).resolve().parents[1]


def _build(tmp_path: Path, data: dict):
    path = tmp_path / "organization.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return build_generic_world(load_config(path))


def _custom_data() -> dict:
    return yaml.safe_load((ROOT / "configs/custom-governance.yaml").read_text())


def test_initial_protocol_gate_records_violation_enforcement_and_use(tmp_path: Path) -> None:
    data = _custom_data()
    data["protocols"]["initial"][0] = {
        "id": "evidence-first",
        "name": "Evidence before completion",
        "definition": {
            "affected_actions": ["complete_task"],
            "required_fields": ["supporting-evidence"],
            "enforcement_action": "block",
            "enforcement_rule": "Require supporting evidence before completion.",
        },
    }
    world = _build(tmp_path, data)
    spec = world.proposal_manager.protocol_specs["evidence-first"]
    assert world.generic_lifecycle._required_artifact_values(spec) == []
    spec.required_fields = ["research-note"]
    assert world.generic_lifecycle._required_artifact_values(spec) == ["research-note"]
    assert (
        world.generic_lifecycle._protocol_gate_failure(
            spec, "complete_task", "research-note"
        )
        == "missing_required_artifact"
    )
    spec.required_fields = ["supporting-evidence"]

    blocked = GenericExecution().execute(
        "researcher",
        ActionCandidate("complete_task", {"task_id": "research-note"}),
        world,
    )
    assert not blocked.success
    assert any(event["type"] == "protocol_violation_event" for event in blocked.events)
    assert any(event["type"] == "protocol_enforcement_event" for event in blocked.events)

    world.work_on_generic_task("researcher", "research-note")
    world.review_generic_task("reviewer", "research-note")
    completed = GenericExecution().execute(
        "researcher",
        ActionCandidate("complete_task", {"task_id": "research-note"}),
        world,
    )
    assert completed.success
    assert any(event["type"] == "protocol_use_event" for event in completed.events)


def test_disabled_proposal_generation_leaves_no_proposal_objects(tmp_path: Path) -> None:
    data = yaml.safe_load((ROOT / "configs/default.yaml").read_text())
    data["learning"]["proposal_generation"] = False
    world = _build(tmp_path, data)
    for _ in range(48):
        world.step()
    assert world.proposal_manager.proposals == {}


def test_approval_requires_exact_quorum_and_permission(tmp_path: Path) -> None:
    data = _custom_data()
    data["governance"]["quorum"] = 2
    data["agents"][1]["permissions"] = ["review"]
    world = _build(tmp_path, data)
    lifecycle = world.generic_lifecycle
    proposal = lifecycle.amend_protocol(
        "evidence-first",
        agent_id="researcher",
        tick=0,
        summary="Add a documented review step for every evidence handoff.",
    )
    assert proposal.status == "under_review"
    assert world.proposal_manager.approve_proposal(
        proposal.proposal_id, "reviewer", world
    ) is None
    assert proposal.approved_by == []
    assert world.proposal_manager.approve_proposal(
        proposal.proposal_id, "researcher", world
    ) is None
    assert proposal.approved_by == ["researcher"]
    # One approval cannot satisfy a two-person quorum even when the proposal's
    # required list happens to contain only that one eligible person.
    proposal.approval_required_from = ["researcher"]
    assert not world.proposal_manager._approver_threshold_met(proposal)


def test_amendment_waits_for_votes_and_review_delay(tmp_path: Path) -> None:
    data = _custom_data()
    data["governance"]["approval_mode"] = "agent"
    data["governance"]["proposal_review_delay_ticks"] = 2
    world = _build(tmp_path, data)
    proposal = world.generic_lifecycle.amend_protocol(
        "evidence-first",
        agent_id="researcher",
        tick=0,
        summary="Add a documented review step for every evidence handoff.",
        required_actions=["review_doc"],
    )
    world.proposal_manager.approve_proposal(proposal.proposal_id, "researcher", world)
    world.proposal_manager.approve_proposal(proposal.proposal_id, "reviewer", world)
    assert proposal.status == "under_review"
    world.world_tick = 1
    assert world.proposal_manager.process_pending_adoptions(world) == []
    assert proposal.status == "under_review"
    world.world_tick = 2
    adopted = world.proposal_manager.process_pending_adoptions(world)
    assert adopted
    assert proposal.status == "adopted"
    assert world.proposal_manager.protocol_specs["evidence-first"].revision == 1


def test_retirement_keep_disables_the_retirement_proposal_path(tmp_path: Path) -> None:
    data = _custom_data()
    data["governance"]["retirement_behavior"] = "keep"
    data["protocols"]["retirement_behavior"] = "keep"
    world = _build(tmp_path, data)
    assert world.generic_lifecycle.retirement_behavior == "keep"
    assert world.generic_lifecycle.retire_protocol("evidence-first") is None
    assert world.proposal_manager.proposals == {}


def test_retirement_review_uses_votes_and_publishes_the_retirement_event(
    tmp_path: Path,
) -> None:
    data = _custom_data()
    data["governance"]["proposal_review_delay_ticks"] = 2
    world = _build(tmp_path, data)
    proposal = world.generic_lifecycle.retire_protocol(
        "evidence-first", agent_id="researcher", tick=0
    )
    world.proposal_manager.approve_proposal(proposal.proposal_id, "researcher", world)
    world.proposal_manager.approve_proposal(proposal.proposal_id, "reviewer", world)
    world.world_tick = 1
    assert world.proposal_manager.process_pending_adoptions(world) == []
    world.world_tick = 2
    assert world.proposal_manager.process_pending_adoptions(world)
    assert world.proposal_manager.protocol_specs["evidence-first"].status == "deprecated"
    registry_protocol = world.protocol_registry.protocols["evidence-first"]
    assert registry_protocol.adoption_status == "obsolete"
    assert any(
        event.event_type == "obsolete"
        and event.data.get("source_proposal_id") == proposal.proposal_id
        for event in world.protocol_registry.events
    )
    frame = project_public_frame(
        world,
        sequence=0,
        organization_id="governed-team",
        organization_name="Governed Research Team",
        action_start=0,
        protocol_event_start=0,
    )
    public = next(
        row for row in frame["organization"]["protocols"]
        if row["protocol_id"] == "evidence-first"
    )
    assert public["adoption_status"] == "obsolete"
    assert public["retired_tick"] == 2


def test_protocol_packages_are_data_only_and_registry_ids_do_not_overwrite_history(
    tmp_path: Path,
) -> None:
    data = _custom_data()
    world = _build(tmp_path, data)
    assert world.generic_lifecycle._load_package({"module": "not_a_package"}) == []

    registry = world.protocol_registry
    registry.dedup = False
    first = registry.propose(
        proposer_id="researcher",
        protocol_type="test",
        rule_summary="same rule",
        protocol_id="test-rule",
    )
    second = registry.propose(
        proposer_id="reviewer",
        protocol_type="test",
        rule_summary="same rule",
        protocol_id="test-rule",
    )
    assert first.protocol_id == "test-rule"
    assert second.protocol_id == "test-rule_2"
    assert set(registry.protocols) >= {"test-rule", "test-rule_2"}
