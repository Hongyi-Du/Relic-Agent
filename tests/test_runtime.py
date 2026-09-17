import json
from pathlib import Path

import pytest

from relic_agent.config import load_config
from relic_agent.replay import load_trace
from relic_agent.runtime import OrganizationRuntime


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_default_runtime_runs_the_real_source_workflow_and_exports_public_trace(
    tmp_path: Path,
) -> None:
    result = OrganizationRuntime(load_config(ROOT / "configs" / "source-b3.yaml")).run(
        output_root=tmp_path,
        run_id="source-native-72",
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert result.status == "completed"
    assert result.ticks == 72
    assert result.event_count > 0
    assert manifest["runtime"] == {
        "action_execution": "source_orgworld_step",
        "action_selection": "source_profile_policy",
        "authority": "source_native_orgworld",
        "paper_result_evidence": "not_produced_by_standalone_org_host",
        "provider": "source_native",
        "provider_calls_made": 0,
        "seed": 42,
        "ticks": 72,
        "workflow_acceptance": "passed",
        "workflow_acceptance_reason": "real_source_action_episode_reflection_proposal_protocol_evidence_present",
    }
    assert manifest["summary"]["episodes"] > 0
    assert manifest["summary"]["reflections"] > 0
    assert manifest["summary"]["wishes"] > 0
    assert manifest["summary"]["proposals"] > 0
    assert manifest["summary"]["protocol_specs"] > 0
    assert manifest["source"]["critical_blob_verification"]["verified"] is True
    assert manifest["source"]["loaded_module_boundary"]["status"] == "passed"
    assert manifest["source"]["loaded_module_boundary"]["forbidden_modules_loaded"] == []
    assert (result.run_directory / "config.yaml").read_text(encoding="utf-8") == (
        ROOT / "configs" / "source-b3.yaml"
    ).read_text(encoding="utf-8")
    assert json.loads((result.run_directory / "status.json").read_text(encoding="utf-8")) == {
        "authority": "source_native_orgworld",
        "run_id": "source-native-72",
        "schema_version": "relic-agent-status-v2",
        "status": "completed",
        "tick": 72,
    }

    trace = load_trace(result.trace_path)
    assert len(trace["frames"]) == 73
    assert trace["frames"][-1]["tick"] == 72
    assert sum(len(frame["decisions"]) for frame in trace["frames"]) > 0
    assert trace["frames"][-1]["episodes"]
    assert trace["frames"][-1]["organization"]["proposals"]
    serialized = json.dumps(trace, sort_keys=True)
    assert "raw_reflection_excerpt" not in serialized
    assert "private_reflections_included\": true" not in serialized.lower()


@pytest.mark.integration
def test_short_source_run_can_be_incomplete_without_falling_back_to_a_shell(tmp_path: Path) -> None:
    result = OrganizationRuntime(load_config(ROOT / "configs" / "source-b3.yaml")).run(
        output_root=tmp_path,
        ticks=12,
        run_id="source-native-short",
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert result.event_count > 0
    assert manifest["runtime"]["authority"] == "source_native_orgworld"
    assert manifest["runtime"]["action_execution"] == "source_orgworld_step"
    assert manifest["runtime"]["provider_calls_made"] == 0
    assert manifest["runtime"]["workflow_acceptance"] == "unavailable_fail_closed"
    assert manifest["runtime"]["workflow_acceptance_reason"].startswith(
        "missing_real_source_evidence:"
    )


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
