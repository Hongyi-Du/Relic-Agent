"""Strict source-native configuration for the Relic-Agent B3 host.

The previous generic organization YAML described an invented release-shell
state model.  It cannot faithfully instantiate the vendored Relic B3
``OrgWorld`` and is therefore rejected rather than partially translated.
Custom organization configuration is a future source-backed adapter, not a
silent fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from relic_agent.core.hashing import canonical_sha256


CONFIG_SCHEMA_VERSION = "relic-agent-source-native-v1"
SOURCE_NATIVE_SCENARIO = "org_default"
SOURCE_NATIVE_PROVIDER = "source_native"
_B3_ALIASES = frozenset({"", "b3", "full", "sociogenesis"})


class ConfigError(ValueError):
    """Raised when a config cannot faithfully mount the source B3 host."""


@dataclass(frozen=True)
class RuntimeConfig:
    seed: int = 42
    ticks: int = 72
    scenario: str = SOURCE_NATIVE_SCENARIO
    baseline: str = "b3"
    provider: str = SOURCE_NATIVE_PROVIDER
    profile_causality: str = "source_recorded_no_op"
    capability_transfer: str = "empty_no_op"


@dataclass(frozen=True)
class OrganizationConfig:
    schema_version: str
    organization_id: str
    name: str
    runtime: RuntimeConfig
    source_path: Path
    digest: str


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a mapping")
    return value


def _only_keys(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigError(f"{label} has unknown keys: {', '.join(unknown)}")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string")
    return value.strip()


def _integer(value: Any, label: str, *, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ConfigError(f"{label} must be an integer >= {minimum}")
    return value


def _parse_source_runtime(value: Any) -> RuntimeConfig:
    runtime = _mapping(value or {}, "runtime")
    _only_keys(
        runtime,
        {
            "seed",
            "ticks",
            "scenario",
            "baseline",
            "profile_causality",
            "capability_transfer",
        },
        "runtime",
    )
    seed = _integer(runtime.get("seed", 42), "runtime.seed", minimum=0)
    ticks = _integer(runtime.get("ticks", 72), "runtime.ticks", minimum=1)

    scenario = str(runtime.get("scenario", SOURCE_NATIVE_SCENARIO) or "").strip().lower()
    if scenario != SOURCE_NATIVE_SCENARIO:
        raise ConfigError(
            "runtime.scenario cannot be mapped to the source-native B3 host; "
            "only 'org_default' is shipped"
        )

    baseline = str(runtime.get("baseline", "b3") or "").strip().lower()
    if baseline not in _B3_ALIASES:
        raise ConfigError(
            "runtime.baseline cannot be mapped to the B3-only adapter; "
            "accepted values: empty, b3, full, sociogenesis"
        )

    profile_causality = runtime.get("profile_causality")
    if profile_causality not in (None, "", "source_recorded"):
        raise ConfigError(
            "runtime.profile_causality is source-recorded only; custom interventions "
            "are not supported by this host"
        )

    capability_transfer = runtime.get("capability_transfer")
    if capability_transfer is not None and capability_transfer != {}:
        raise ConfigError(
            "runtime.capability_transfer accepts only an empty mapping/no arguments; "
            "transfer injection is fail-closed in the standalone host"
        )

    return RuntimeConfig(seed=seed, ticks=ticks, scenario=scenario, baseline="b3")


def load_config(path: str | Path) -> OrganizationConfig:
    source_path = Path(path).expanduser().resolve()
    if not source_path.is_file():
        raise ConfigError(f"config not found: {source_path}")
    try:
        payload = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc

    root = _mapping(payload, "config")
    schema_version = _text(root.get("schema_version"), "schema_version")
    if schema_version != CONFIG_SCHEMA_VERSION:
        if schema_version == "relic-agent-config-v1":
            raise ConfigError(
                "legacy generic organization YAML cannot be mapped to the source-native "
                "B3 host; use relic-agent-source-native-v1"
            )
        raise ConfigError(
            f"unsupported schema_version {schema_version!r}; expected {CONFIG_SCHEMA_VERSION!r}"
        )
    _only_keys(root, {"schema_version", "organization", "runtime"}, "config")

    organization = _mapping(root.get("organization"), "organization")
    _only_keys(organization, {"id", "name"}, "organization")
    runtime = _parse_source_runtime(root.get("runtime", {}))
    return OrganizationConfig(
        schema_version=schema_version,
        organization_id=_text(organization.get("id"), "organization.id"),
        name=_text(organization.get("name"), "organization.name"),
        runtime=runtime,
        source_path=source_path,
        digest=canonical_sha256(root),
    )


__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "SOURCE_NATIVE_PROVIDER",
    "SOURCE_NATIVE_SCENARIO",
    "ConfigError",
    "OrganizationConfig",
    "RuntimeConfig",
    "load_config",
]
