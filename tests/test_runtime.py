import json
from pathlib import Path

import pytest

from relic_agent.config import load_config
from relic_agent.replay import load_trace
from relic_agent.runtime import OrganizationRuntime


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_minimal_runtime_closes_the_capability_lifecycle(tmp_path: Path) -> None:
    runtime = OrganizationRuntime(load_config(ROOT / "configs" / "minimal.yaml"))
    result = runtime.run(output_root=tmp_path, run_id="minimal-test")

    assert result.completed_task_count == 1
    assert result.adopted_protocol_count == 1
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["runtime"]["provider_calls_made"] == 0
    assert (result.run_directory / "config.yaml").read_text(encoding="utf-8") == (
        ROOT / "configs" / "minimal.yaml"
    ).read_text(encoding="utf-8")
    assert (
        json.loads((result.run_directory / "status.json").read_text(encoding="utf-8"))["status"]
        == "completed"
    )
    assert manifest["summary"] == {
        "adopted_protocols": 1,
        "agents": 2,
        "completed_tasks": 1,
        "episodes": 2,
        "events": result.event_count,
        "proposals": 1,
        "reflections": 1,
        "tasks": 1,
        "wishes": 1,
    }

    trace = load_trace(result.trace_path)
    event_types = {event["event_type"] for frame in trace["frames"] for event in frame["events"]}
    assert {
        "task_started",
        "task_blocked",
        "proposal_created",
        "proposal_approved",
        "protocol_adopted",
        "protocol_used",
        "task_completed",
    } <= event_types
    assert "reflection_completed" not in event_types
    assert "wish_created" not in event_types
    serialized = json.dumps(trace, sort_keys=True)
    assert "raw_reflection_excerpt" not in serialized
    assert 'private_reflections_included": true' not in serialized.lower()


@pytest.mark.integration
def test_runtime_refuses_to_overwrite_a_run_directory(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs" / "minimal.yaml")
    OrganizationRuntime(config).run(output_root=tmp_path, run_id="same-run")

    with pytest.raises(FileExistsError):
        OrganizationRuntime(config).run(output_root=tmp_path, run_id="same-run")


@pytest.mark.replay
def test_trace_tampering_is_rejected(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs" / "minimal.yaml")
    result = OrganizationRuntime(config).run(output_root=tmp_path, run_id="tamper-test")
    trace = json.loads(result.trace_path.read_text(encoding="utf-8"))
    trace["frames"][-1]["organization"]["name"] = "tampered"
    result.trace_path.write_text(json.dumps(trace), encoding="utf-8")

    with pytest.raises(ValueError, match="digest mismatch"):
        load_trace(result.trace_path)
