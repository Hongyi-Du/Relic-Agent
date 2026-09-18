"""Generic SDL customization without changing the canonical source policy."""

from pathlib import Path

import pytest
import yaml

from agent_sdk.lived.core.contracts import ActionCandidate
from environments.org_env.runtime_adapter.policy import BASE_WEIGHTS, TAU
from relic_agent.config import ConfigError, load_config
from relic_agent.runtime import OrganizationRuntime
from relic_agent.runtime.builder import build_generic_world


ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path, sdl):
    payload = yaml.safe_load((ROOT / "configs/minimal.yaml").read_text(encoding="utf-8"))
    payload["runtime"]["sdl"] = sdl
    path = tmp_path / "organization.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_sdl_weight_coefficients_and_sampler_are_per_generic_world(tmp_path):
    path = _config(tmp_path, {
        "temperature": 0.2,
        "jitter": 0,
        "base_weights": {"progress_gain": 2.5},
        "profile_coefficients": [
            {"trait": "curiosity", "feature": "learning_gain", "coefficient": 1.1},
        ],
    })
    world = build_generic_world(load_config(path))
    policy = world._loop["policy"]
    assert policy.base_weights["progress_gain"] == 2.5
    assert ("curiosity", "learning_gain", 1.1) in policy.profile_coeffs
    assert policy.temperature == 0.2
    assert policy.jitter == 0
    assert policy.score(world.agents["researcher"], {"progress_gain": 1}, world) == 2.5
    assert BASE_WEIGHTS["progress_gain"] == 0.55
    assert TAU == 0.6


def test_sdl_scorer_receives_candidate_and_run_starts(tmp_path):
    scorer = ROOT / "examples/generic/sdl_score.py"
    path = _config(tmp_path, {
        "scorer": {"path": str(scorer), "entrypoint": "execute",
                   "config": {"progress": 2, "review": 0, "tool_bonus": 3}},
    })
    config = load_config(path)
    world = build_generic_world(config)
    policy = world._loop["policy"]
    agent = world.agents["researcher"]
    assert policy.score(agent, {"progress_gain": 1}, world,
                        candidate=ActionCandidate("use_tool", {})) == 5
    assert policy.score(agent, {"progress_gain": 1}, world,
                        candidate=ActionCandidate("work_on_task", {})) == 2
    result = OrganizationRuntime(config).run(output_root=tmp_path / "runs", ticks=3)
    assert result.trace_path.is_file()


def test_custom_sdl_example_is_runnable(tmp_path):
    config = load_config(ROOT / "examples/generic/custom-sdl.yaml")
    result = OrganizationRuntime(config).run(output_root=tmp_path, ticks=12)
    assert result.status == "completed"
    assert result.trace_path.is_file()


@pytest.mark.parametrize("sdl", [
    {"temperature": 0},
    {"jitter": -1},
    {"base_weights": {"not_a_feature": 1}},
    {"profile_coefficients": [{"trait": "curiosity", "feature": "not_a_feature", "coefficient": 1}]},
    {"scorer": {"path": "missing.py"}},
])
def test_bad_sdl_config_fails_validation(tmp_path, sdl):
    with pytest.raises(ConfigError):
        load_config(_config(tmp_path, sdl))
