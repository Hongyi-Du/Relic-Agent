from pathlib import Path

import pytest

from relic_agent.config import CONFIG_SCHEMA_VERSION, ConfigError, load_config


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.unit
@pytest.mark.parametrize("name,agents,tasks", [("minimal.yaml", 2, 1), ("default.yaml", 4, 3)])
def test_bundled_configs_are_strict_and_load(name: str, agents: int, tasks: int) -> None:
    config = load_config(ROOT / "configs" / name)

    assert config.schema_version == CONFIG_SCHEMA_VERSION
    assert len(config.agents) == agents
    assert len(config.tasks) == tasks
    assert config.runtime.provider == "mock"
    assert len(config.digest) == 64


@pytest.mark.unit
def test_config_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        """schema_version: relic-agent-config-v1
organization: {id: demo, name: Demo}
agents:
  - {id: a, display_name: A, role: builder, surprise: true}
tasks:
  - {id: t, title: T}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="unknown keys"):
        load_config(path)


@pytest.mark.unit
def test_config_rejects_unknown_provider(tmp_path: Path) -> None:
    original = (ROOT / "configs" / "minimal.yaml").read_text(encoding="utf-8")
    path = tmp_path / "live.yaml"
    path.write_text(
        original.replace("provider: mock", "provider: unqualified-live"), encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="not enabled"):
        load_config(path)


@pytest.mark.unit
def test_config_rejects_non_source_protocol_review_latency(tmp_path: Path) -> None:
    original = (ROOT / "configs" / "minimal.yaml").read_text(encoding="utf-8")
    path = tmp_path / "non-source-review-latency.yaml"
    path.write_text(original.replace("review_ticks: 3", "review_ticks: 4"), encoding="utf-8")

    with pytest.raises(ConfigError, match="active source protocol lifecycle"):
        load_config(path)


@pytest.mark.unit
def test_config_rejects_non_source_distinct_approver_floor(tmp_path: Path) -> None:
    original = (ROOT / "configs" / "minimal.yaml").read_text(encoding="utf-8")
    path = tmp_path / "non-source-approver-floor.yaml"
    path.write_text(original.replace("min_approvers: 2", "min_approvers: 1"), encoding="utf-8")

    with pytest.raises(ConfigError, match="active source proposal"):
        load_config(path)
