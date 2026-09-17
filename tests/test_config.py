from pathlib import Path

import pytest

from relic_agent.config import (
    CONFIG_SCHEMA_VERSION,
    SOURCE_NATIVE_PROVIDER,
    ConfigError,
    load_config,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "seed", "ticks"),
    [("source-b3.yaml", 42, 72)],
)
def test_bundled_configs_select_the_source_native_host(
    name: str, seed: int, ticks: int
) -> None:
    config = load_config(ROOT / "configs" / name)

    assert config.schema_version == CONFIG_SCHEMA_VERSION
    assert config.runtime.provider == SOURCE_NATIVE_PROVIDER
    assert config.runtime.scenario == "org_default"
    assert config.runtime.baseline == "b3"
    assert config.runtime.seed == seed
    assert config.runtime.ticks == ticks
    assert len(config.digest) == 64
    assert not hasattr(config, "agents")
    assert not hasattr(config, "tasks")


@pytest.mark.unit
def test_config_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        """schema_version: relic-agent-source-native-v1
organization: {id: demo, name: Demo}
runtime: {scenario: org_default, ticks: 12, invented_provider: mock}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="unknown keys"):
        load_config(path)


@pytest.mark.unit
def test_config_rejects_legacy_generic_organization_yaml(tmp_path: Path) -> None:
    path = tmp_path / "legacy.yaml"
    path.write_text(
        """schema_version: relic-agent-config-v1
organization: {id: demo, name: Demo}
agents: []
tasks: []
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="legacy generic organization YAML"):
        load_config(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("scenario: another_world", "cannot be mapped"),
        ("baseline: p2", "cannot be mapped"),
        ("capability_transfer: {inject: true}", "fail-closed"),
        ("profile_causality: custom", "source-recorded"),
    ],
)
def test_config_rejects_unmapped_source_interventions(
    tmp_path: Path, replacement: str, message: str
) -> None:
    path = tmp_path / "unsupported.yaml"
    path.write_text(
        """schema_version: relic-agent-source-native-v1
organization: {id: demo, name: Demo}
runtime:
  seed: 42
  ticks: 12
  %s
"""
        % replacement,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=message):
        load_config(path)
