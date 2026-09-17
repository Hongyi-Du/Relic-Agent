"""Configuration loading for source-native and generic Relic-Agent worlds.

``relic-agent-source-native-v1`` remains the strict compatibility parser for
the canonical B3 host.  ``relic-agent-v2`` is dispatched to
``relic_agent.config_schema`` and exposes a typed, normalized organization
configuration for the generic runtime.  Parsing is local and deterministic;
provider credentials are intentionally never read while loading a config.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from relic_agent.core.hashing import canonical_sha256
from relic_agent.config_schema import (
    GENERIC_CONFIG_SCHEMA_VERSION,
    ConfigError,
    GenericConfig,
    parse_generic_config,
)


CONFIG_SCHEMA_VERSION = "relic-agent-source-native-v1"
SOURCE_NATIVE_SCENARIO = "org_default"
SOURCE_NATIVE_PROVIDER = "source_native"
_B3_ALIASES = frozenset({"", "b3", "full", "sociogenesis"})


@dataclass(frozen=True)
class RuntimeConfig:
    seed: int = 42
    ticks: int = 72
    scenario: str = SOURCE_NATIVE_SCENARIO
    baseline: str = "b3"
    provider: str = SOURCE_NATIVE_PROVIDER
    profile_causality: str = "source_recorded_no_op"
    capability_transfer: str = "empty_no_op"
    decision_mode: str = "source_profile_policy"
    timeout_seconds: float = 60.0
    retry_count: int = 0
    max_concurrent_agents: int | None = None


@dataclass(frozen=True)
class OrganizationConfig:
    schema_version: str
    organization_id: str
    name: str
    runtime: RuntimeConfig
    source_path: Path
    digest: str
    data: Mapping[str, Any] | None = None
    generic: GenericConfig | None = None
    mode: str = "source_native"

    @property
    def is_generic(self) -> bool:
        return self.mode == "generic"

    @property
    def agents(self) -> Any:
        if self.generic is None:
            raise AttributeError("agents is available only for generic configurations")
        return self.generic.agents

    @property
    def tasks(self) -> Any:
        if self.generic is None:
            raise AttributeError("tasks is available only for generic configurations")
        return self.generic.tasks

    @property
    def providers(self) -> Any:
        if self.generic is None:
            raise AttributeError("providers is available only for generic configurations")
        return self.generic.providers

    @property
    def organization(self) -> Any:
        if self.generic is None:
            raise AttributeError("organization is available only for generic configurations")
        return self.generic.organization


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
    if schema_version == GENERIC_CONFIG_SCHEMA_VERSION:
        generic = parse_generic_config(root, source_path=source_path, digest=canonical_sha256(root))
        return OrganizationConfig(
            schema_version=schema_version,
            organization_id=generic.organization.id,
            name=generic.organization.name,
            runtime=RuntimeConfig(
                seed=generic.runtime.seed,
                ticks=generic.runtime.ticks,
                scenario="generic",
                baseline="generic",
                provider=generic.runtime.provider,
                profile_causality="generic",
                capability_transfer="generic",
                decision_mode=generic.runtime.decision_mode,
                timeout_seconds=generic.runtime.timeout_seconds,
                retry_count=generic.runtime.retry_count,
                max_concurrent_agents=generic.runtime.max_concurrent_agents,
            ),
            source_path=source_path,
            digest=generic.digest,
            data=generic.data,
            generic=generic,
            mode="generic",
        )
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
    "GENERIC_CONFIG_SCHEMA_VERSION",
    "SOURCE_NATIVE_PROVIDER",
    "SOURCE_NATIVE_SCENARIO",
    "ConfigError",
    "OrganizationConfig",
    "RuntimeConfig",
    "load_config",
]
