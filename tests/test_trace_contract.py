import copy
import json
from pathlib import Path

import pytest

from relic_agent.config import load_config
from relic_agent.core.hashing import canonical_sha256
from relic_agent.replay import TraceError, load_trace, validate_trace
from relic_agent.runtime import OrganizationRuntime
from environments.org_env.backend.simulation import OrgWorld


ROOT = Path(__file__).resolve().parents[1]


def _run(tmp_path: Path, *, run_id: str = "trace-contract") -> tuple[dict, Path]:
    result = OrganizationRuntime(load_config(ROOT / "configs" / "minimal.yaml")).run(
        output_root=tmp_path,
        run_id=run_id,
    )
    return load_trace(result.trace_path), result.trace_path


def _resign(payload: dict) -> dict:
    payload.pop("trace_sha256", None)
    payload["trace_sha256"] = canonical_sha256(payload)
    return payload


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _last_public_event(trace: dict) -> dict:
    for frame in reversed(trace["frames"]):
        if frame["events"]:
            return frame["events"][0]
    frame = trace["frames"][-1]
    organization = frame["organization"]
    event = {
        "event_id": "event_trace_contract_fixture",
        "tick": frame["tick"],
        "event_type": "public_notice",
        "actor_id": organization["agents"][0]["agent_id"],
        "object_ids": [organization["tasks"][0]["task_id"]],
        "payload": {"summary": "Trace-contract fixture event."},
        "visibility": "organization",
    }
    frame["events"].append(event)
    return event


@pytest.mark.replay
def test_public_trace_is_tick_synchronized_and_excludes_private_policy_audit(
    tmp_path: Path,
) -> None:
    trace, _ = _run(tmp_path)

    assert trace["frames"][0]["tick"] == 0
    assert all(frame["sequence"] == index for index, frame in enumerate(trace["frames"]))
    assert all(len(frame["events"]) <= 1 for frame in trace["frames"])
    assert all(frame["events"] == [] for frame in trace["frames"])

    decisions = [decision for frame in trace["frames"] for decision in frame["decisions"]]
    assert decisions
    serialized = json.dumps(trace, sort_keys=True)
    for evaluator_only_field in ('"candidate_scores"', '"features"', '"utility"', '"prompt"'):
        assert evaluator_only_field not in serialized

    final = trace["frames"][-1]["organization"]
    assert final["tasks"]
    assert all(decision["agent_id"] in {row["agent_id"] for row in final["agents"]} for decision in decisions)


@pytest.mark.replay
def test_public_trace_projects_source_decisions_without_private_ledger_data(
    tmp_path: Path,
) -> None:
    trace, _ = _run(tmp_path)
    final = trace["frames"][-1]["organization"]
    assert final["tasks"]
    assert any(frame["decisions"] for frame in trace["frames"])
    assert all(event["data"] == {} for frame in trace["frames"] for event in frame["governance_events"])


@pytest.mark.replay
@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda trace: trace["privacy"].__setitem__("private_memories_included", True),
            "must be false",
        ),
        (
            lambda trace: trace["frames"][0].__setitem__("private_memory", {"text": "hidden"}),
            "blocked field",
        ),
        (
            lambda trace: trace["frames"][-1]["organization"]["tasks"][0].__setitem__(
                "description", "Read /home/alice/private.txt"
            ),
            "local filesystem path",
        ),
        (
            lambda trace: trace["frames"][-1]["organization"]["tasks"][0].__setitem__(
                "description", "Use sk-abcdefghijklmnopqrstuvwxyz"
            ),
            "credential-like",
        ),
        (
            lambda trace: trace["frames"][-1]["organization"]["tasks"][0].__setitem__(
                "description", "Read /tmp/private.txt"
            ),
            "local filesystem path",
        ),
        (
            lambda trace: trace["frames"][-1]["organization"]["tasks"][0].__setitem__(
                "description", r"Read C:\Users\alice\private.txt"
            ),
            "local filesystem path",
        ),
        (
            lambda trace: trace["frames"][-1]["organization"]["tasks"][0].__setitem__(
                "description", r"Read \\server\share\private.txt"
            ),
            "local filesystem path",
        ),
        (
            lambda trace: _last_public_event(trace)["payload"].__setitem__(
                "messages", [{"role": "assistant", "content": "private transcript"}]
            ),
            "blocked field",
        ),
        (
            lambda trace: _last_public_event(trace)["payload"].__setitem__(
                "policy_trace", {"candidate_scores": {"work": 0.9}}
            ),
            "blocked field",
        ),
        (
            lambda trace: trace.__setitem__(
                "evaluation_annotations", {"hidden_tests": ["private evaluator case"]}
            ),
            "blocked field",
        ),
        (
            lambda trace: trace["frames"][-1]["organization"]["agents"][0].__setitem__(
                "local_state", {"apiKey": "test-only-unredacted-value"}
            ),
            "blocked field",
        ),
        (
            lambda trace: trace["frames"][-1]["organization"]["agents"][0].__setitem__(
                "local_state", {"mode": "focused"}
            ),
            "must be a string",
        ),
        (
            lambda trace: _last_public_event(trace)["payload"].__setitem__(
                "metadata", {"label": "untyped container"}
            ),
            "unsupported fields",
        ),
    ],
)
def test_digest_valid_private_or_sensitive_trace_is_rejected(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    trace, path = _run(tmp_path)
    mutate(trace)
    _write(path, _resign(trace))

    with pytest.raises(TraceError, match=message):
        load_trace(path)


@pytest.mark.replay
def test_private_event_and_non_finite_number_are_rejected(tmp_path: Path) -> None:
    trace, path = _run(tmp_path)
    _last_public_event(trace)["visibility"] = "private"
    _write(path, _resign(trace))
    with pytest.raises(TraceError, match="not a public event"):
        load_trace(path)

    trace, path = _run(tmp_path, run_id="private-event-type")
    _last_public_event(trace)["event_type"] = "reflection_completed"
    _write(path, _resign(trace))
    with pytest.raises(TraceError, match="private event type"):
        load_trace(path)

    trace, _ = _run(tmp_path, run_id="non-finite")
    invalid = copy.deepcopy(trace)
    invalid["frames"][-1]["organization"]["tasks"][0]["progress_score"] = float("nan")
    with pytest.raises(TraceError, match="non-finite"):
        validate_trace(invalid, verify_digest=False)


@pytest.mark.replay
def test_public_frames_are_single_event_post_event_snapshots(tmp_path: Path) -> None:
    trace, _ = _run(tmp_path)
    event = _last_public_event(trace)
    frame = trace["frames"][-1]
    second = copy.deepcopy(event)
    second["event_id"] = "event_duplicate_snapshot"
    frame["events"].append(second)

    with pytest.raises(TraceError, match="at most one post-event snapshot event"):
        validate_trace(_resign(trace))


@pytest.mark.replay
@pytest.mark.parametrize(
    ("field", "value"),
    [("actor_id", "ghost_agent"), ("object_ids", ["ghost_object"])],
)
def test_public_event_references_must_resolve(tmp_path: Path, field: str, value) -> None:
    trace, _ = _run(tmp_path)
    _last_public_event(trace)[field] = value

    with pytest.raises(TraceError, match="references an unpublished object"):
        validate_trace(_resign(trace))


@pytest.mark.integration
def test_runtime_atomically_publishes_running_trace_and_failure_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(ROOT / "configs" / "minimal.yaml")
    writes: list[tuple[str, dict]] = []
    original = OrganizationRuntime._atomic_json

    def recording_write(path: Path, payload: dict) -> None:
        writes.append((path.name, copy.deepcopy(payload)))
        original(path, payload)

    monkeypatch.setattr(OrganizationRuntime, "_atomic_json", staticmethod(recording_write))
    result = OrganizationRuntime(config).run(output_root=tmp_path, ticks=3, run_id="live-write")

    trace_writes = [payload for name, payload in writes if name == "trace.json"]
    status_writes = [payload for name, payload in writes if name == "status.json"]
    assert len(trace_writes) == 4
    assert [payload["frames"][-1]["tick"] for payload in trace_writes] == [0, 1, 2, 3]
    assert all(validate_trace(payload) for payload in trace_writes)
    assert status_writes[0]["status"] == "running"
    assert status_writes[-1]["status"] == "completed"
    assert load_trace(result.trace_path)["frames"][-1]["tick"] == 3

    failing = OrganizationRuntime(config)
    original_step = OrgWorld.step

    def fail_after_first_source_step(world: OrgWorld) -> None:
        if world.world_tick >= 1:
            raise RuntimeError("sentinel failure")
        original_step(world)

    monkeypatch.setattr(OrgWorld, "step", fail_after_first_source_step)
    with pytest.raises(RuntimeError, match="sentinel failure"):
        failing.run(output_root=tmp_path, ticks=2, run_id="failed-live")
    status = json.loads((tmp_path / "failed-live" / "status.json").read_text(encoding="utf-8"))
    assert status == {
        "authority": "source_native_orgworld",
        "error_type": "RuntimeError",
        "run_id": "failed-live",
        "schema_version": "relic-agent-status-v2",
        "status": "failed",
        "tick": 1,
    }
    assert load_trace(tmp_path / "failed-live" / "trace.json")["frames"][-1]["tick"] == 1


@pytest.mark.unit
def test_run_id_cannot_escape_the_output_root(tmp_path: Path) -> None:
    runtime = OrganizationRuntime(load_config(ROOT / "configs" / "minimal.yaml"))
    with pytest.raises(ValueError, match="safe"):
        runtime.run(output_root=tmp_path, run_id="../outside")
