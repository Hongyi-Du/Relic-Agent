"""Strict configuration loading for Relic Agent organizations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from relic_agent.core.hashing import canonical_sha256
from relic_agent.source_b3.protocols import REVIEW_MIN_TICKS
from relic_agent.source_b3.proposals.manager import MIN_DISTINCT_PROTOCOL_APPROVERS

CONFIG_SCHEMA_VERSION = "relic-agent-config-v1"
SUPPORTED_PROVIDERS = ("mock",)


class ConfigError(ValueError):
    """Raised when an organization config violates the public schema."""


@dataclass(frozen=True)
class AgentConfig:
    agent_id: str
    display_name: str
    role: str
    profile: dict[str, float] = field(default_factory=dict)
    skills: dict[str, float] = field(default_factory=dict)
    tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskConfig:
    task_id: str
    title: str
    description: str = ""
    priority: int = 3
    owner_id: str | None = None
    required_skills: tuple[str, ...] = ()


@dataclass(frozen=True)
class GovernanceConfig:
    min_approvers: int = 2
    review_ticks: int = 3


@dataclass(frozen=True)
class RuntimeConfig:
    seed: int = 42
    ticks: int = 12
    provider: str = "mock"
    # Retained to accept prior public configs. The compatibility runtime no
    # longer invokes reflection from mock events; active source reflection has
    # its own HCI batch cadence and explicit host gate.
    reflection_interval: int = 3


@dataclass(frozen=True)
class OrganizationConfig:
    schema_version: str
    organization_id: str
    name: str
    agents: tuple[AgentConfig, ...]
    tasks: tuple[TaskConfig, ...]
    governance: GovernanceConfig
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


def _bounded_scores(value: Any, label: str) -> dict[str, float]:
    raw = _mapping(value or {}, label)
    result: dict[str, float] = {}
    for key, score in raw.items():
        name = _text(key, f"{label} key")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise ConfigError(f"{label}.{name} must be numeric")
        number = float(score)
        if not 0.0 <= number <= 1.0:
            raise ConfigError(f"{label}.{name} must be between 0 and 1")
        result[name] = number
    return result


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{label} must be a list")
    items = tuple(_text(item, f"{label} item") for item in value)
    if len(items) != len(set(items)):
        raise ConfigError(f"{label} must not contain duplicates")
    return items


def load_config(path: str | Path) -> OrganizationConfig:
    source_path = Path(path).expanduser().resolve()
    if not source_path.is_file():
        raise ConfigError(f"config not found: {source_path}")
    try:
        payload = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc
    root = _mapping(payload, "config")
    _only_keys(
        root,
        {"schema_version", "organization", "agents", "tasks", "governance", "runtime"},
        "config",
    )
    schema_version = _text(root.get("schema_version"), "schema_version")
    if schema_version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"unsupported schema_version {schema_version!r}; expected {CONFIG_SCHEMA_VERSION!r}"
        )

    organization = _mapping(root.get("organization"), "organization")
    _only_keys(organization, {"id", "name"}, "organization")
    organization_id = _text(organization.get("id"), "organization.id")
    organization_name = _text(organization.get("name"), "organization.name")

    raw_agents = root.get("agents")
    if not isinstance(raw_agents, list) or not raw_agents:
        raise ConfigError("agents must be a non-empty list")
    agents: list[AgentConfig] = []
    for index, item in enumerate(raw_agents):
        agent = _mapping(item, f"agents[{index}]")
        _only_keys(
            agent, {"id", "display_name", "role", "profile", "skills", "tools"}, f"agents[{index}]"
        )
        agents.append(
            AgentConfig(
                agent_id=_text(agent.get("id"), f"agents[{index}].id"),
                display_name=_text(agent.get("display_name"), f"agents[{index}].display_name"),
                role=_text(agent.get("role"), f"agents[{index}].role"),
                profile=_bounded_scores(agent.get("profile"), f"agents[{index}].profile"),
                skills=_bounded_scores(agent.get("skills"), f"agents[{index}].skills"),
                tools=_string_tuple(agent.get("tools"), f"agents[{index}].tools"),
            )
        )
    agent_ids = [agent.agent_id for agent in agents]
    if len(agent_ids) != len(set(agent_ids)):
        raise ConfigError("agent ids must be unique")

    raw_tasks = root.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ConfigError("tasks must be a non-empty list")
    tasks: list[TaskConfig] = []
    for index, item in enumerate(raw_tasks):
        task = _mapping(item, f"tasks[{index}]")
        _only_keys(
            task,
            {"id", "title", "description", "priority", "owner", "required_skills"},
            f"tasks[{index}]",
        )
        priority = task.get("priority", 3)
        if not isinstance(priority, int) or isinstance(priority, bool) or not 1 <= priority <= 5:
            raise ConfigError(f"tasks[{index}].priority must be an integer from 1 to 5")
        owner = task.get("owner")
        if owner is not None:
            owner = _text(owner, f"tasks[{index}].owner")
            if owner not in agent_ids:
                raise ConfigError(f"tasks[{index}].owner references unknown agent {owner!r}")
        tasks.append(
            TaskConfig(
                task_id=_text(task.get("id"), f"tasks[{index}].id"),
                title=_text(task.get("title"), f"tasks[{index}].title"),
                description=str(task.get("description", "")).strip(),
                priority=priority,
                owner_id=owner,
                required_skills=_string_tuple(
                    task.get("required_skills"), f"tasks[{index}].required_skills"
                ),
            )
        )
    task_ids = [task.task_id for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ConfigError("task ids must be unique")

    raw_governance = _mapping(root.get("governance", {}), "governance")
    _only_keys(raw_governance, {"min_approvers", "review_ticks"}, "governance")
    min_approvers = raw_governance.get("min_approvers", min(2, len(agents)))
    review_ticks = raw_governance.get("review_ticks", 3)
    if not isinstance(min_approvers, int) or isinstance(min_approvers, bool) or min_approvers < 1:
        raise ConfigError("governance.min_approvers must be a positive integer")
    if min_approvers > len(agents):
        raise ConfigError("governance.min_approvers cannot exceed the agent count")
    if min_approvers != MIN_DISTINCT_PROTOCOL_APPROVERS:
        raise ConfigError(
            "governance.min_approvers must equal the active source proposal "
            f"distinct-approver floor ({MIN_DISTINCT_PROTOCOL_APPROVERS})"
        )
    if not isinstance(review_ticks, int) or isinstance(review_ticks, bool) or review_ticks < 1:
        raise ConfigError("governance.review_ticks must be a positive integer")
    if review_ticks != REVIEW_MIN_TICKS:
        raise ConfigError(
            "governance.review_ticks must equal the active source protocol "
            f"lifecycle latency ({REVIEW_MIN_TICKS})"
        )

    raw_runtime = _mapping(root.get("runtime", {}), "runtime")
    _only_keys(raw_runtime, {"seed", "ticks", "provider", "reflection_interval"}, "runtime")
    seed = raw_runtime.get("seed", 42)
    ticks = raw_runtime.get("ticks", 12)
    reflection_interval = raw_runtime.get("reflection_interval", 3)
    provider = str(raw_runtime.get("provider", "mock")).strip().lower()
    for value, label in (
        (seed, "runtime.seed"),
        (ticks, "runtime.ticks"),
        (reflection_interval, "runtime.reflection_interval"),
    ):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConfigError(f"{label} must be an integer")
    if ticks < 1 or reflection_interval < 1:
        raise ConfigError("runtime.ticks and runtime.reflection_interval must be positive")
    if provider not in SUPPORTED_PROVIDERS:
        raise ConfigError(
            f"provider {provider!r} is not enabled in this milestone; supported: {', '.join(SUPPORTED_PROVIDERS)}"
        )

    return OrganizationConfig(
        schema_version=schema_version,
        organization_id=organization_id,
        name=organization_name,
        agents=tuple(agents),
        tasks=tuple(tasks),
        governance=GovernanceConfig(min_approvers=min_approvers, review_ticks=review_ticks),
        runtime=RuntimeConfig(
            seed=seed,
            ticks=ticks,
            provider=provider,
            reflection_interval=reflection_interval,
        ),
        source_path=source_path,
        digest=canonical_sha256(root),
    )
