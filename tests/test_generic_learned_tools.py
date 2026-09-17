"""Regression coverage for generic learned-tool and deadline behavior."""

from pathlib import Path

import yaml

from agent_sdk.lived.core.contracts import ActionCandidate
from environments.org_env.backend.workspace.company import CompanyWorkspace
from environments.org_env.backend.workspace.objects import FileObject, Visibility
from environments.org_env.runtime_adapter.perception import OrgPerceptionAdapter
from environments.org_env.proposals.objects import ToolSpec
from relic_agent.config import load_config
from relic_agent.runtime.builder import (
    GenericActionMapper,
    GenericExecution,
    GenericFeatures,
    build_generic_world,
)
from relic_agent.runtime.tools import GenericToolRegistry


ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path, *, deadline=None, learned_permission=False):
    data = yaml.safe_load((ROOT / "configs/minimal.yaml").read_text())
    if deadline is not None:
        data["tasks"][0]["deadline"] = deadline
    if learned_permission:
        data["agents"][0]["permissions"].append("use_learned_tools")
    path = tmp_path / f"generic-{deadline or 'none'}.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_config(path)


def test_overwrite_preserves_private_visibility_and_owner_and_blocks_cross_owner_publish():
    config = load_config(ROOT / "configs/minimal.yaml")
    company = CompanyWorkspace(members={"researcher", "reviewer"})
    company.register_file(
        FileObject(
            object_id="private-note",
            owner_id="researcher",
            creator_id="researcher",
            visibility=Visibility.PRIVATE,
            raw_payload="before",
        )
    )
    world = type("World", (), {
        "world_tick": 1,
        "events": [],
        "tasks": {},
        "company": company,
    })()
    registry = GenericToolRegistry(config, world)

    updated = registry.execute(
        "researcher",
        "files",
        {"operation": "write", "id": "private-note", "content": "after"},
    )
    assert updated["status"] == "completed"
    record = company.files["private-note"]
    assert record.visibility is Visibility.PRIVATE
    assert record.owner_id == "researcher"
    assert record.raw_payload == "after"

    company.register_file(
        FileObject(
            object_id="team-note",
            owner_id="researcher",
            creator_id="researcher",
            visibility=Visibility.TEAM,
            raw_payload="before",
        )
    )
    denied = registry.execute(
        "reviewer",
        "files",
        {
            "operation": "write",
            "id": "team-note",
            "content": "attempted publication",
            "visibility": "public",
        },
    )
    assert denied == {"status": "failed", "error_type": "PermissionError"}
    assert company.files["team-note"].raw_payload == "before"
    assert company.files["team-note"].visibility is Visibility.TEAM


def test_adopted_tool_spec_is_catalogued_composed_and_permission_checked(tmp_path):
    world = build_generic_world(_config(tmp_path, learned_permission=True))
    world.proposal_manager.tools["learned-draft"] = ToolSpec(
        tool_id="learned-draft",
        name="Draft deliverables",
        description="Use the existing generic work action to draft task files.",
        required_actions=["work_on_task"],
        required_permissions=["use_learned_tools"],
        callable_by_agents=["researcher"],
    )
    assert any(item["id"] == "learned-draft" for item in world.tool_registry.catalog("researcher"))
    perception = OrgPerceptionAdapter().build_perception("researcher", world, 0)
    assert any(
        candidate.action_type == "use_tool"
        and candidate.parameters.get("tool_id") == "learned-draft"
        for candidate in GenericActionMapper().to_core_candidates(perception, world)
    )

    result = GenericExecution().execute(
        "researcher",
        ActionCandidate("use_tool", {"tool_id": "learned-draft", "task_id": "research-note"}),
        world,
    )
    assert result.success
    assert any(event["type"] == "tool_use_event" for event in result.events)
    assert {"research-note", "review-report"} <= set(world.company.files)

    denied = GenericExecution().execute(
        "reviewer",
        ActionCandidate("use_tool", {"tool_id": "learned-draft", "task_id": "research-note"}),
        world,
    )
    assert not denied.success
    assert denied.failure_reason == "PermissionError"
    assert not any(event["type"] == "tool_use_event" for event in denied.events)


def test_learned_tool_without_work_change_has_no_tool_use_event(tmp_path):
    world = build_generic_world(_config(tmp_path, learned_permission=True))
    world.proposal_manager.tools["notify"] = ToolSpec(
        tool_id="notify",
        name="Notify team",
        required_actions=["send_message"],
        required_permissions=["use_learned_tools"],
        callable_by_agents=["researcher"],
    )
    result = GenericExecution().execute(
        "researcher",
        ActionCandidate("use_tool", {"tool_id": "notify", "arguments": {"text": "hello"}}),
        world,
    )
    assert not result.success
    assert result.failure_reason == "tool_steps_changed_nothing"
    assert not any(event["type"] == "tool_use_event" for event in result.events)


def test_metadata_completion_uses_the_same_lifecycle_boundary(tmp_path):
    world = build_generic_world(_config(tmp_path))
    world.work_on_generic_task("researcher", "research-note")
    world.review_generic_task("reviewer", "research-note")

    class Hooks:
        def __init__(self):
            self.calls = []

        def before_action(self, agent_id, action, task_id):
            self.calls.append(("before", agent_id, action, task_id))
            return True

        def after_action(self, agent_id, action, task_id, result):
            self.calls.append(("after", agent_id, action, task_id, result.success))

    hooks = Hooks()
    world.generic_lifecycle = hooks
    outcome = world.tool_registry.execute(
        "researcher",
        "task_board",
        {"operation": "complete", "task_id": "research-note"},
    )
    assert outcome["status"] == "completed"
    assert [call[:4] for call in hooks.calls] == [
        ("before", "researcher", "complete_task", "research-note"),
        ("after", "researcher", "complete_task", "research-note"),
    ]


def test_deadline_urgency_and_overdue_event_are_observable_without_hard_rejection(tmp_path):
    early = build_generic_world(_config(tmp_path, deadline=2))
    late = build_generic_world(_config(tmp_path, deadline=8))
    extractor = GenericFeatures()
    early_perception = OrgPerceptionAdapter().build_perception("researcher", early, 0)
    late_perception = OrgPerceptionAdapter().build_perception("researcher", late, 0)
    candidate = ActionCandidate("work_on_task", {"task_id": "research-note"})
    early_urgency = extractor.extract(candidate, early_perception, early).deadline_urgency
    late_urgency = extractor.extract(candidate, late_perception, late).deadline_urgency
    assert early_urgency > late_urgency

    early.world_tick = 3
    early._record_deadline_events(early.world_tick)
    overdue = [event for event in early.events if event.get("subtype") == "overdue"]
    assert overdue and overdue[0]["task_id"] == "research-note"
    assert overdue[0]["overdue_ticks"] == 1

    # Completing after the deadline remains allowed and records that it was late.
    completed_world = build_generic_world(_config(tmp_path, deadline=2))
    completed_world.work_on_generic_task("researcher", "research-note")
    completed_world.review_generic_task("reviewer", "research-note")
    completed_world.world_tick = 3
    completed = GenericExecution().execute(
        "researcher",
        ActionCandidate("complete_task", {"task_id": "research-note"}),
        completed_world,
    )
    assert completed.success
    completed_world._record_deadline_events(completed_world.world_tick)
    assert any(
        event.get("completed_late")
        for event in completed_world.events
        if event.get("subtype") == "overdue"
    )
