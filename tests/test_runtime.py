import json
from pathlib import Path

import pytest

from relic_agent.config import load_config
from relic_agent.replay import load_trace
from relic_agent.runtime import OrganizationRuntime


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_minimal_runtime_does_not_fabricate_a_source_proposal_lifecycle(tmp_path: Path) -> None:
    runtime = OrganizationRuntime(load_config(ROOT / "configs" / "minimal.yaml"))
    result = runtime.run(output_root=tmp_path, run_id="minimal-test")

    assert result.completed_task_count == 0
    assert result.adopted_protocol_count == 0
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["runtime"]["provider_calls_made"] == 0
    assert (result.run_directory / "config.yaml").read_text(encoding="utf-8") == (
        ROOT / "configs" / "minimal.yaml"
    ).read_text(encoding="utf-8")
    assert (
        json.loads((result.run_directory / "status.json").read_text(encoding="utf-8"))["status"]
        == "completed"
    )
    assert manifest["summary"]["adopted_protocols"] == 0
    assert manifest["summary"]["completed_tasks"] == 0
    assert manifest["summary"]["proposals"] == 0
    assert "source_llm_proposal_generation" in manifest["source_proposal_lifecycle"][
        "unavailable_fail_closed"
    ]
    reflection_status = manifest["source_reflection_lifecycle"]
    assert reflection_status["activation"] == (
        "explicit_mounted_hci_orgworld_closed_episode_and_"
        "openai_compatible_provider_only"
    )
    assert reflection_status["source_host_binding"] == "unbound_no_source_orgworld"
    assert reflection_status["reflection_count"] == 0
    assert reflection_status["wish_count"] == 0
    assert "legacy_compatibility_event_to_reflection_translation" in reflection_status[
        "unavailable_fail_closed"
    ]
    assert manifest["summary"]["reflections"] == 0
    assert manifest["summary"]["wishes"] == 0

    trace = load_trace(result.trace_path)
    event_types = {event["event_type"] for frame in trace["frames"] for event in frame["events"]}
    assert {"task_started", "task_blocked"} <= event_types
    assert not {
        "proposal_created",
        "proposal_approved",
        "protocol_adopted",
        "protocol_used",
        "task_completed",
    }.intersection(event_types)
    assert "reflection_completed" not in event_types
    assert "wish_created" not in event_types
    serialized = json.dumps(trace, sort_keys=True)
    assert "raw_reflection_excerpt" not in serialized
    assert 'private_reflections_included": true' not in serialized.lower()


@pytest.mark.integration
def test_minimal_runtime_does_not_fabricate_source_episode_evidence(tmp_path: Path) -> None:
    runtime = OrganizationRuntime(load_config(ROOT / "configs" / "minimal.yaml"))
    result = runtime.run(output_root=tmp_path, run_id="minimal-episode-test")

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    episode_status = manifest["source_episode_lifecycle"]
    assert episode_status["activation"] == "explicit_source_orgworld_input_only"
    assert episode_status["source_core_projection"] == "unbound_no_source_host_projection"
    assert episode_status["episode_count"] == 0
    assert "legacy_compatibility_event_to_episode_translation" in episode_status[
        "unavailable_fail_closed"
    ]

    trace = load_trace(result.trace_path)
    assert all(frame["episodes"] == [] for frame in trace["frames"])


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
