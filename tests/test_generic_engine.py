"""Run isolation and credential handling across persisted artifacts."""
import json
from pathlib import Path

import yaml

from relic_agent.config import load_config
from relic_agent.runtime import OrganizationRuntime

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_can_run_twice_without_carrying_frames_or_event_marks(tmp_path):
    runtime = OrganizationRuntime(load_config(ROOT / "configs/minimal.yaml"))
    first = runtime.run(output_root=tmp_path, ticks=6, run_id="first")
    first_trace = json.loads(first.trace_path.read_text())
    second = runtime.run(output_root=tmp_path, ticks=6, run_id="second")
    second_trace = json.loads(second.trace_path.read_text())
    assert second_trace["frames"] == first_trace["frames"]
    assert second.event_count == first.event_count
    assert len(runtime.frames) == 7


def test_named_credentials_in_user_metadata_are_redacted_from_artifacts(tmp_path):
    payload = yaml.safe_load((ROOT / "configs/minimal.yaml").read_text())
    secret = "private-metadata-credential-83d915"
    payload["organization"]["metadata"] = {"api_key": secret}
    payload["agents"][0]["initial_context"] = {"token": secret}
    payload["observability"]["local_debug"] = True
    path = tmp_path / "organization.yaml"
    path.write_text(yaml.safe_dump(payload))
    result = OrganizationRuntime(load_config(path)).run(output_root=tmp_path / "runs", ticks=2)
    for artifact in result.run_directory.iterdir():
        if artifact.is_file():
            assert secret not in artifact.read_text(), artifact.name
