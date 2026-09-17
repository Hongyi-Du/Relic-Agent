"""Public learning switches must control the shared engine, including startup."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from environments.org_env.experiments.ablations import EXTERNAL_BRIDGE, EVENT_GRAPH
from environments.org_env.growth.objects import new_authority, new_reputation
from relic_agent.config import load_config
from relic_agent.runtime.builder import build_generic_world

ROOT = Path(__file__).resolve().parents[1]


def _world(tmp_path, **learning):
    data = yaml.safe_load((ROOT / "configs/minimal.yaml").read_text())
    data["learning"].update(learning)
    path = tmp_path / "organization.yaml"
    path.write_text(yaml.safe_dump(data))
    return build_generic_world(load_config(path))


@pytest.mark.parametrize("enabled", [False, True])
def test_external_signal_config_controls_shared_step(tmp_path, monkeypatch, enabled):
    # Explicit generic configuration wins over the legacy source env toggle,
    # without discarding unrelated source ablations.
    monkeypatch.setenv("ORG_MECHANISM_ABLATIONS", "event_graph,external_bridge" if enabled else "event_graph")
    monkeypatch.delenv("ORG_EXTERNAL_SOCIETY", raising=False)
    world = _world(tmp_path, external_signal_loop=enabled)
    calls = []
    monkeypatch.setattr(world.community, "tick", lambda tick: calls.append(("community", tick)))
    monkeypatch.setattr(world, "_apply_external_events", lambda tick: calls.append(("external", tick)))
    world.step()
    assert calls == ([("community", 1), ("external", 1)] if enabled else [])
    assert world.mechanism_ablations.is_disabled(EXTERNAL_BRIDGE) is not enabled
    assert world.mechanism_ablations.is_disabled(EVENT_GRAPH)


@pytest.mark.parametrize("enabled", [False, True])
def test_capability_learning_controls_startup_and_daily_growth(tmp_path, monkeypatch, enabled):
    monkeypatch.delenv("ORG_MECHANISM_ABLATIONS", raising=False)
    monkeypatch.delenv("ORG_EXTERNAL_SOCIETY", raising=False)
    world = _world(tmp_path, capability_learning=enabled, external_signal_loop=False)
    if enabled:
        assert any(agent.authority != new_authority() for agent in world.agents.values())
    else:
        assert world.growth_events == []
        assert all(agent.authority == new_authority() for agent in world.agents.values())
        assert all(agent.reputation == new_reputation() for agent in world.agents.values())
    initial = {
        aid: deepcopy((agent.skills, agent.reputation, agent.authority))
        for aid, agent in world.agents.items()
    }
    for _ in range(25):
        world.step()
    assert world.tasks["research-note"].status.value == "done"
    final = {
        aid: (agent.skills, agent.reputation, agent.authority)
        for aid, agent in world.agents.items()
    }
    if enabled:
        assert final != initial
        assert any(event.tick > 0 for event in world.growth_events)
    else:
        assert final == initial
        assert world.growth_events == []
        assert world._growth_signals == []
