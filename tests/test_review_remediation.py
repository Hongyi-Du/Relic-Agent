"""Regressions from the September review of the generic runtime."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from agent_sdk.lived.core.contracts import ActionCandidate
from relic_agent.config import load_config
from relic_agent.runtime.builder import GenericExecution, build_generic_world


ROOT = Path(__file__).resolve().parents[1]


def world_for(tmp_path, *, protocol=None):
    config = yaml.safe_load((ROOT / "configs/minimal.yaml").read_text(encoding="utf-8"))
    config["protocols"]["initial"] = [protocol] if protocol else []
    path = tmp_path / "organization.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return build_generic_world(load_config(path))


def test_review_receipt_is_bound_to_current_artifact_versions(tmp_path):
    world = world_for(tmp_path)
    world.work_on_generic_task("researcher", "research-note")
    world.review_generic_task("reviewer", "research-note")
    reviewed = deepcopy(world.task_reviews["research-note"]["reviewer"])
    assert reviewed["research-note"][0] == 1

    result = world.tool_registry.execute("researcher", "files", {
        "operation": "write", "id": "research-note", "content": "revised evidence"})
    assert result["status"] == "completed"
    assert world.company.files["research-note"].version == 2
    assert world.task_reviews["research-note"]["reviewer"] == reviewed
    assert world.complete_generic_task("researcher", "research-note", {})["error_type"] == "PendingReview"
    world.review_generic_task("reviewer", "research-note")
    assert world.task_reviews["research-note"]["reviewer"]["research-note"][0] == 2
    assert world.complete_generic_task("researcher", "research-note", {})["status"] == "completed"


def test_plugin_artifact_update_also_requires_a_new_review(tmp_path):
    config = load_config(ROOT / "examples/generic/custom-tool.yaml")
    world = build_generic_world(config)
    world.work_on_generic_task("researcher", "research-note")
    world.review_generic_task("reviewer", "research-note")
    world.tool_registry.functions["word_count"] = lambda _args, _context: {
        "artifacts": [{"id": "research-note", "content": "plugin revision",
                       "task_ids": ["research-note"]}]}
    assert world.tool_registry.execute("researcher", "word_count", {"text": "revision"})["status"] == "completed"
    world.completed_tool_calls.update(("research-note", index) for index, _ in enumerate(
        world.task_specs["research-note"]["metadata"].get("tool_calls", [])))
    assert world.complete_generic_task("researcher", "research-note", {})["error_type"] == "PendingReview"


def test_message_text_reaches_only_visible_next_decision(tmp_path, monkeypatch):
    world = world_for(tmp_path)
    world.comm.create_channel("private-research", members={"researcher"})
    world.comm.send_message(sender_id="researcher", channel_id="private-research",
                            text="PRIVATE_NOT_FOR_REVIEWER", tick=1)
    sent = world.tool_registry.execute("researcher", "messaging", {
        "channel": "general", "text": "INTERFACE_V2_MARKER"})
    assert sent["status"] == "completed"
    prompts = []

    def decide(_aid, _system, prompt, _schema):
        prompts.append(prompt)
        return {"index": 0, "arguments": {"text": "Acknowledged INTERFACE_V2_MARKER"}}

    monkeypatch.setattr(world.provider_registry, "generate_json_for_agent", decide)
    candidate = world._llm_decide("reviewer", world.agents["reviewer"], None,
                                  [ActionCandidate("send_message", {"channel": "general"})])
    assert "INTERFACE_V2_MARKER" in prompts[0]
    assert "PRIVATE_NOT_FOR_REVIEWER" not in prompts[0]
    assert candidate.parameters["text"] == "Acknowledged INTERFACE_V2_MARKER"
    assert GenericExecution().execute("reviewer", candidate, world).success
    assert world.comm.known_messages("reviewer")[-1].full_text == candidate.parameters["text"]
    world._llm_decide("reviewer", world.agents["reviewer"], None,
                      [ActionCandidate("send_message", {"channel": "general"})])
    assert "INTERFACE_V2_MARKER" not in prompts[1]


@pytest.mark.parametrize("action,blocked", [("block", True), ("notify", False)])
def test_protocol_scope_member_and_enforcement_are_real(tmp_path, action, blocked):
    protocol = {"id": "gate", "definition": {
        "trigger_condition": "before completing a task", "scope": "task:research-note",
        "affected_agents": ["reviewer"], "affected_artifacts": ["missing-evidence"],
        "enforcement_action": action}}
    world = world_for(tmp_path, protocol=protocol)
    lifecycle = world.generic_lifecycle
    assert lifecycle.before_action("researcher", "complete_task", "research-note")
    assert lifecycle.before_action("reviewer", "complete_task", "other-task")
    assert lifecycle.before_action("reviewer", "complete_task", "research-note") is (not blocked)
    if not blocked:
        assert any(event.get("type") == "protocol_enforcement_event" and
                   event.get("blocked") is False for event in world.events)


def test_unsupported_trigger_is_rejected_before_running(tmp_path):
    with pytest.raises(ValueError, match="unsupported trigger_condition"):
        world_for(tmp_path, protocol={"id": "bad", "definition": {
            "trigger_condition": "when the constellation changes",
            "affected_actions": ["complete_task"]}})


def test_unsupported_team_scope_is_rejected_instead_of_becoming_org_wide(tmp_path):
    with pytest.raises(ValueError, match="unsupported scope"):
        world_for(tmp_path, protocol={"id": "bad", "definition": {
            "trigger_condition": "complete_task", "scope": "team"}})


def test_shared_tasks_scope_excludes_private_tasks(tmp_path):
    world = world_for(tmp_path, protocol={"id": "shared", "definition": {
        "trigger_condition": "complete_task", "scope": "shared tasks",
        "affected_artifacts": ["not-present"], "enforcement_action": "block"}})
    lifecycle = world.generic_lifecycle
    assert lifecycle.before_action("researcher", "complete_task", "research-note") is False
    world.tasks["research-note"].visibility = "private"
    assert lifecycle.before_action("researcher", "complete_task", "research-note") is True
