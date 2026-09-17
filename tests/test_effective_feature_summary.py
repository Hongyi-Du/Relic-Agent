"""The public Inspector summary reflects the configured runtime switches."""

import json
from pathlib import Path

import pytest
import yaml

from relic_agent.config import load_config
from relic_agent.replay.trace import build_trace, load_trace
from relic_agent.runtime.builder import build_generic_world
from relic_agent.source_host.projection import project_public_frame


ROOT = Path(__file__).resolve().parents[1]


def _build_world(
    tmp_path: Path,
    *,
    top_level: bool,
    nested: bool,
    retirement_behavior: str = "review",
    allow_retirement: bool = True,
    retirement_enabled: bool = True,
):
    data = yaml.safe_load((ROOT / "configs/minimal.yaml").read_text(encoding="utf-8"))
    data["learning"].update(
        {
            "wish_extraction": top_level,
            "wish": {"enabled": nested},
            "proposal_generation": top_level,
            "proposal": {"enabled": nested},
            "protocol_formation": top_level,
            "protocol": {"enabled": nested, "retirement_enabled": retirement_enabled},
        }
    )
    data["governance"]["retirement_behavior"] = retirement_behavior
    data["protocols"]["retirement_behavior"] = retirement_behavior
    data["protocols"]["allow_retirement"] = allow_retirement
    data["observability"].update(
        {"local_debug": True, "token_logging": False, "cost_logging": False}
    )
    config_path = tmp_path / "organization.yaml"
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return build_generic_world(load_config(config_path))


@pytest.mark.parametrize(
    ("top_level", "nested"),
    [(True, False), (False, True)],
    ids=["nested-disabled", "top-level-disabled"],
)
def test_summary_uses_lifecycle_switches_after_both_aliases_are_merged(
    tmp_path: Path, top_level: bool, nested: bool
) -> None:
    world = _build_world(tmp_path, top_level=top_level, nested=nested)

    assert world.reflection_manager.wish_enabled is False
    assert world.proposal_manager.proposal_enabled is False
    assert world.proposal_manager.protocol_allow_proposals is False
    assert world.proposal_manager.protocol_allow_retirement is False

    frame = project_public_frame(
        world,
        sequence=0,
        organization_id="summary-test",
        organization_name="Summary Test",
        action_start=0,
        protocol_event_start=0,
    )
    summary = frame["organization"]["config_summary"]
    assert "wish_extraction" in summary["features_disabled"]
    assert "proposal_generation" in summary["features_disabled"]
    assert "protocol_formation" in summary["features_disabled"]


@pytest.mark.parametrize(
    ("behavior", "allow", "enabled", "expected"),
    [("review", True, True, True), ("keep", True, True, False),
     ("review", False, True, False), ("review", True, False, False)],
)
def test_summary_exposes_retirement_and_observability_without_private_config(
    tmp_path: Path, behavior: str, allow: bool, enabled: bool, expected: bool,
) -> None:
    world = _build_world(
        tmp_path, top_level=True, nested=True, retirement_behavior=behavior,
        allow_retirement=allow, retirement_enabled=enabled,
    )
    frame = project_public_frame(
        world,
        sequence=0,
        organization_id="summary-test",
        organization_name="Summary Test",
        action_start=0,
        protocol_event_start=0,
    )
    summary = frame["organization"]["config_summary"]
    assert summary["features_enabled"]
    assert "protocol_formation" in summary["features_enabled"]
    assert (world.proposal_manager.protocol_allow_retirement
            and world.proposal_manager.protocol_retirement_enabled) is expected
    assert "retirement" in summary["features_enabled" if expected else "features_disabled"]
    assert "retirement" not in summary["features_disabled" if expected else "features_enabled"]
    assert summary["retirement_behavior"] == behavior
    assert summary["retirement_enabled"] is expected
    assert summary["local_debug"] is True
    assert summary["token_logging"] is False
    assert summary["cost_logging"] is False

    # New summary fields remain inside the strict public trace contract and do
    # not expose provider credentials or private configuration values.
    trace = build_trace(
        run_id="summary-test",
        organization_id="summary-test",
        config_digest="0" * 64,
        frames=[frame],
    )
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(json.dumps(trace), encoding="utf-8")
    assert load_trace(trace_path)["trace_sha256"] == trace["trace_sha256"]
    assert "api_key_env" not in json.dumps(trace)
