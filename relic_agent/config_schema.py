"""Schema and validation for the configurable ``relic-agent-v2`` format.

The source-native B3 host deliberately has a small, strict configuration
surface.  Generic organizations use the schema in this module instead.  The
schema is intentionally implemented with standard-library dataclasses rather
than a provider-specific validation library so that validating a config is a
pure local operation.

``parse_generic_config`` returns a typed :class:`GenericConfig` and a
JSON-compatible, canonical ``data`` mapping.  Runtime code can use the latter
without knowing about the parser's aliases or default values.  Secrets are
represented by environment variable *names* and are never looked up here.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from importlib import import_module
from importlib.util import module_from_spec, spec_from_file_location
import hashlib
import math
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any, Mapping, Sequence


GENERIC_CONFIG_SCHEMA_VERSION = "relic-agent-v2"


class ConfigError(ValueError):
    """Raised when a configuration cannot be validated locally."""


_MISSING = object()
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DEFAULT_BUILTINS = ("files", "task_board", "messaging", "search")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{label} must be a mapping")
    result = dict(value)
    if any(not isinstance(key, str) for key in result):
        raise ConfigError(f"{label} keys must be strings")
    return result


def _only_keys(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        raise ConfigError(f"{label} has unknown keys: {', '.join(unknown)}")


def _text(value: Any, label: str, *, default: Any = _MISSING) -> str:
    if value is _MISSING:
        if default is _MISSING:
            raise ConfigError(f"{label} must be a non-empty string")
        value = default
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, label: str, *, default: str | None = None) -> str | None:
    if value is _MISSING or value is None:
        return default
    if value == "" and default == "":
        return ""
    return _text(value, label)


def _bool(value: Any, label: str, *, default: bool = False) -> bool:
    if value is _MISSING:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"{label} must be a boolean")
    return value


def _integer(
    value: Any,
    label: str,
    *,
    default: int = 0,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if value is _MISSING:
        value = default
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{label} must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{label} must be an integer <= {maximum}")
    return value


def _number(
    value: Any,
    label: str,
    *,
    default: float = 0.0,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if value is _MISSING:
        value = default
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ConfigError(f"{label} must be a finite number")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ConfigError(f"{label} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise ConfigError(f"{label} must be <= {maximum}")
    return result


def _string_list(value: Any, label: str, *, default: Sequence[str] = ()) -> tuple[str, ...]:
    if value is _MISSING or value is None:
        value = default
    if not isinstance(value, (list, tuple)):
        raise ConfigError(f"{label} must be a list of strings")
    result = tuple(_text(item, f"{label}[{index}]") for index, item in enumerate(value))
    if len(set(result)) != len(result):
        raise ConfigError(f"{label} must not contain duplicate values")
    return result


def _json_value(value: Any, label: str, *, _seen: set[int] | None = None) -> Any:
    """Copy a value while enforcing the JSON-compatible config value shape."""

    if _seen is None:
        _seen = set()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigError(f"{label} must contain finite numbers")
        return value
    identity = id(value)
    if identity in _seen:
        raise ConfigError(f"{label} must not contain recursive values")
    if isinstance(value, Mapping):
        _seen.add(identity)
        try:
            result: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ConfigError(f"{label} keys must be strings")
                result[key] = _json_value(item, f"{label}.{key}", _seen=_seen)
            return result
        finally:
            _seen.remove(identity)
    if isinstance(value, (list, tuple)):
        _seen.add(identity)
        try:
            return [
                _json_value(item, f"{label}[{index}]", _seen=_seen)
                for index, item in enumerate(value)
            ]
        finally:
            _seen.remove(identity)
    raise ConfigError(f"{label} must contain only JSON-compatible values")


def _json_mapping(value: Any, label: str, *, default: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if value is _MISSING or value is None:
        value = {} if default is None else default
    result = _mapping(value, label)
    copied = _json_value(result, label)
    assert isinstance(copied, dict)
    return copied


def _json_list(value: Any, label: str, *, default: Sequence[Any] = ()) -> tuple[Any, ...]:
    if value is _MISSING or value is None:
        value = list(default)
    if not isinstance(value, (list, tuple)):
        raise ConfigError(f"{label} must be a list")
    copied = _json_value(list(value), label)
    assert isinstance(copied, list)
    return tuple(copied)


def _identifier(value: Any, label: str) -> str:
    # IDs are intentionally not limited to canonical source role names.  A
    # stable ID may include spaces for compatibility with existing projects;
    # whitespace-only values are still rejected by _text.
    return _text(value, label)


def _unique(values: Sequence[str], label: str) -> None:
    if len(set(values)) != len(values):
        raise ConfigError(f"{label} must not contain duplicate stable ids")


def _pick(value: Mapping[str, Any], names: Sequence[str], label: str, *, default: Any = _MISSING) -> Any:
    """Return one of a set of aliases and reject conflicting duplicate aliases."""

    present = [(name, value[name]) for name in names if name in value]
    if not present:
        return default
    first_name, first = present[0]
    for name, candidate in present[1:]:
        if candidate != first:
            raise ConfigError(f"{label} has conflicting aliases {first_name!r} and {name!r}")
    return first


def _as_data(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _as_data(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _as_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_data(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value


@dataclass(frozen=True)
class OrganizationSpec:
    id: str
    name: str
    description: str = ""
    brief: str = ""
    shared_goals: tuple[str, ...] = ()
    topology: Mapping[str, Any] = field(default_factory=dict)
    channels: tuple[str, ...] = ()
    shared_workspace: Any = field(default_factory=dict)
    initial_documents: tuple[Any, ...] = ()
    initial_artifacts: tuple[Any, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderSpec:
    type: str
    base_url_env: str | None = None
    api_key_env: str | None = None
    default_model: str | None = None
    timeout_seconds: float = 60.0
    retry_count: int = 0
    fallback_provider: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BuiltinToolSpec:
    id: str
    description: str = ""
    schema: Mapping[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    side_effect_policy: str = "none"
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolPluginSpec:
    id: str
    module: str | None = None
    path: str | None = None
    entrypoint: str | None = None
    description: str = ""
    schema: Mapping[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    side_effect_policy: str = "none"
    permissions: tuple[str, ...] = ()
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolsSpec:
    builtins: tuple[BuiltinToolSpec, ...] = ()
    plugins: tuple[ToolPluginSpec, ...] = ()

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in (*self.builtins, *self.plugins))


@dataclass(frozen=True)
class AgentSpec:
    id: str
    display_name: str
    role: str
    role_mandate: str = ""
    provider: str | None = None
    model: str | None = None
    reasoning: Mapping[str, Any] = field(default_factory=dict)
    generation: Mapping[str, Any] = field(default_factory=dict)
    skills: Mapping[str, float] = field(default_factory=dict)
    profile: Mapping[str, Any] = field(default_factory=dict)
    failure_modes: tuple[str, ...] = ()
    communication_style: Any = ""
    work_schedule: Any = field(default_factory=dict)
    tools: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    private_workspace: Any = field(default_factory=dict)
    initial_context: Any = field(default_factory=dict)
    ownership: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskSpec:
    id: str
    title: str
    description: str = ""
    priority: int | str = 0
    owner: str | None = None
    collaborators: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    input_artifacts: tuple[Any, ...] = ()
    expected_deliverables: tuple[Any, ...] = ()
    acceptance_criteria: tuple[Any, ...] = ()
    deadline: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GovernanceSpec:
    approval_mode: str = "agent"
    approver_roles: tuple[str, ...] = ()
    approver_members: tuple[str, ...] = ()
    quorum: int = 1
    proposal_review_delay_ticks: int = 0
    deadlock_behavior: str = "escalate"
    role_permissions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    decision_visibility: str = "public"
    protocol_adoption_threshold: float = 0.5
    amendment_threshold: float = 0.5
    retirement_behavior: str = "review"


@dataclass(frozen=True)
class ProtocolSpec:
    id: str
    name: str
    description: str = ""
    source: str = "initial"
    package: str | None = None
    definition: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProtocolsSpec:
    initial: tuple[ProtocolSpec, ...] = ()
    packages: tuple[Any, ...] = ()
    allow_proposals: bool = True
    allow_adoption: bool = True
    allow_revision: bool = True
    allow_retirement: bool = True
    adoption_threshold: float = 0.5
    amendment_threshold: float = 0.5
    review_delay_ticks: int = 0
    dedup: bool = True
    retirement_behavior: str = "review"


@dataclass(frozen=True)
class ReflectionSpec:
    enabled: bool = True
    cadence_ticks: int = 6
    per_agent_cooldown: int = 0
    salience_threshold: float = 0.0


@dataclass(frozen=True)
class WishSpec:
    enabled: bool = True
    cap: int = 10
    dedup: bool = True
    salience_threshold: float = 0.0


@dataclass(frozen=True)
class ProposalLearningSpec:
    enabled: bool = True
    promotion_threshold: float = 0.5
    cap: int = 10
    dedup: bool = True


@dataclass(frozen=True)
class ProtocolLearningSpec:
    enabled: bool = True
    dedup: bool = True
    review_delay_ticks: int = 0
    adoption_threshold: float = 0.5
    amendment_threshold: float = 0.5
    retirement_enabled: bool = True


@dataclass(frozen=True)
class LearningSpec:
    profile_conditioning: bool = True
    capability_learning: bool = True
    institutionalization: bool = True
    reflection: ReflectionSpec = field(default_factory=ReflectionSpec)
    wish_extraction: bool = True
    wish: WishSpec = field(default_factory=WishSpec)
    proposal_generation: bool = True
    proposal: ProposalLearningSpec = field(default_factory=ProposalLearningSpec)
    protocol_formation: bool = True
    protocol: ProtocolLearningSpec = field(default_factory=ProtocolLearningSpec)
    company_skill_memory: bool = True
    governance_approval: bool = True
    executable_workflow: bool = True
    external_signal_loop: bool = True
    parameters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PromptSpec:
    organization_brief: str = ""
    global_grounding_rules: tuple[str, ...] = ()
    agent_instruction_suffix: str = ""
    task_context_template: str = ""
    custom_prompt_assets_path: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ObservabilitySpec:
    public_trace: bool = True
    inspector: bool = True
    local_debug: bool = False
    token_logging: bool = True
    cost_logging: bool = True
    redact_secrets: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeSpec:
    seed: int = 42
    ticks: int = 72
    decision_mode: str = "profile_policy"
    provider: str = "generic"
    timeout_seconds: float = 60.0
    retry_count: int = 0
    max_concurrent_agents: int | None = None


@dataclass(frozen=True)
class GenericConfig:
    """Validated generic configuration and its normalized runtime sections."""

    schema_version: str
    organization: OrganizationSpec
    providers: Mapping[str, ProviderSpec]
    agents: tuple[AgentSpec, ...]
    tasks: tuple[TaskSpec, ...]
    tools: ToolsSpec
    governance: GovernanceSpec
    protocols: ProtocolsSpec
    learning: LearningSpec
    runtime: RuntimeSpec
    prompts: PromptSpec
    observability: ObservabilitySpec
    data: Mapping[str, Any]
    source_path: Path | None = None
    digest: str = ""

    @property
    def organization_id(self) -> str:
        return self.organization.id

    @property
    def name(self) -> str:
        return self.organization.name


def _parse_organization(value: Any) -> OrganizationSpec:
    organization = _mapping(value, "organization")
    _only_keys(
        organization,
        {
            "id",
            "name",
            "description",
            "brief",
            "shared_goals",
            "goals",
            "topology",
            "team_structure",
            "channels",
            "shared_workspace",
            "workspace",
            "initial_documents",
            "initial_shared_docs",
            "initial_artifacts",
            "artifacts",
            "metadata",
        },
        "organization",
    )
    goals = _pick(organization, ("shared_goals", "goals"), "organization.shared_goals", default=())
    topology = _pick(organization, ("topology", "team_structure"), "organization.topology", default={})
    workspace = _pick(
        organization,
        ("shared_workspace", "workspace"),
        "organization.shared_workspace",
        default={},
    )
    documents = _pick(
        organization,
        ("initial_documents", "initial_shared_docs"),
        "organization.initial_documents",
        default=(),
    )
    artifacts = _pick(
        organization,
        ("initial_artifacts", "artifacts"),
        "organization.initial_artifacts",
        default=(),
    )
    return OrganizationSpec(
        id=_identifier(organization.get("id"), "organization.id"),
        name=_text(organization.get("name"), "organization.name"),
        description=_optional_text(organization.get("description", _MISSING), "organization.description", default="") or "",
        brief=_optional_text(organization.get("brief", _MISSING), "organization.brief", default="") or "",
        shared_goals=_string_list(goals, "organization.shared_goals"),
        topology=_json_mapping(topology, "organization.topology"),
        channels=_string_list(organization.get("channels", _MISSING), "organization.channels"),
        shared_workspace=_json_value(workspace, "organization.shared_workspace"),
        initial_documents=_json_list(documents, "organization.initial_documents"),
        initial_artifacts=_json_list(artifacts, "organization.initial_artifacts"),
        metadata=_json_mapping(organization.get("metadata", _MISSING), "organization.metadata"),
    )


def _environment_name(value: Any, label: str) -> str:
    name = _text(value, label)
    if not _ENV_NAME_RE.fullmatch(name):
        raise ConfigError(f"{label} must be a valid environment variable name")
    return name


def _parse_provider(value: Any, label: str) -> ProviderSpec:
    provider = _mapping(value, label)
    _only_keys(
        provider,
        {
            "type",
            "base_url_env",
            "api_key_env",
            "default_model",
            "timeout",
            "timeout_seconds",
            "retries",
            "retry_count",
            "fallback",
            "fallback_provider",
            "parameters",
            "generation",
            "reasoning",
        },
        label,
    )
    timeout = _pick(provider, ("timeout_seconds", "timeout"), f"{label}.timeout_seconds", default=60.0)
    retries = _pick(provider, ("retry_count", "retries"), f"{label}.retry_count", default=0)
    fallback = _pick(
        provider,
        ("fallback_provider", "fallback"),
        f"{label}.fallback_provider",
        default=None,
    )
    params: dict[str, Any] = _json_mapping(provider.get("parameters", _MISSING), f"{label}.parameters")
    for key in ("generation", "reasoning"):
        if key in provider:
            params[key] = _json_value(provider[key], f"{label}.{key}")
    return ProviderSpec(
        type=_text(provider.get("type"), f"{label}.type"),
        base_url_env=(
            _environment_name(provider["base_url_env"], f"{label}.base_url_env")
            if "base_url_env" in provider and provider["base_url_env"] is not None
            else None
        ),
        api_key_env=(
            _environment_name(provider["api_key_env"], f"{label}.api_key_env")
            if "api_key_env" in provider and provider["api_key_env"] is not None
            else None
        ),
        default_model=_optional_text(provider.get("default_model", _MISSING), f"{label}.default_model"),
        timeout_seconds=_number(timeout, f"{label}.timeout_seconds", minimum=0.001),
        retry_count=_integer(retries, f"{label}.retry_count", minimum=0),
        fallback_provider=(
            _text(fallback, f"{label}.fallback_provider") if fallback is not None else None
        ),
        parameters=params,
    )


def _parse_providers(value: Any) -> dict[str, ProviderSpec]:
    providers = _mapping(value, "providers")
    if not providers:
        raise ConfigError("providers must define at least one provider")
    result: dict[str, ProviderSpec] = {}
    for raw_id, raw_provider in providers.items():
        provider_id = _identifier(raw_id, "providers key")
        if provider_id in result:
            raise ConfigError(f"duplicate provider id {provider_id!r}")
        result[provider_id] = _parse_provider(raw_provider, f"providers.{provider_id}")
    for provider_id, spec in result.items():
        if spec.fallback_provider is not None and spec.fallback_provider not in result:
            raise ConfigError(
                f"providers.{provider_id}.fallback_provider references unknown provider "
                f"{spec.fallback_provider!r}"
            )
    return result


def _tool_number(value: Any, label: str, default: float = 30.0) -> float:
    return _number(value, label, default=default, minimum=0.001)


def _parse_builtin_item(
    value: Any,
    label: str,
    *,
    base_dir: Path | None = None,
    forced_id: str | None = None,
) -> BuiltinToolSpec:
    if isinstance(value, str):
        tool_id = _identifier(forced_id or value, f"{label}.id")
        return BuiltinToolSpec(id=tool_id, side_effect_policy="workspace" if tool_id != "search" else "none")
    item = _mapping(value, label)
    _only_keys(
        item,
        {
            "id",
            "name",
            "description",
            "schema",
            "timeout",
            "timeout_seconds",
            "side_effect_policy",
            "config",
        },
        label,
    )
    raw_id = forced_id or _pick(item, ("id", "name"), f"{label}.id", default=_MISSING)
    timeout = _pick(item, ("timeout_seconds", "timeout"), f"{label}.timeout_seconds", default=30.0)
    return BuiltinToolSpec(
        id=_identifier(raw_id, f"{label}.id"),
        description=_optional_text(item.get("description", _MISSING), f"{label}.description", default="") or "",
        schema=_json_mapping(item.get("schema", _MISSING), f"{label}.schema"),
        timeout_seconds=_tool_number(timeout, f"{label}.timeout_seconds"),
        side_effect_policy=_text(
            item.get("side_effect_policy", "workspace" if raw_id != "search" else "none"), f"{label}.side_effect_policy"
        ),
        config=_json_mapping(item.get("config", _MISSING), f"{label}.config"),
    )


def _plugin_module_from_path(path: Path, label: str) -> ModuleType:
    if not path.is_file():
        raise ConfigError(f"{label} plugin import failed: file not found: {path}")
    module_name = f"_relic_agent_config_plugin_{hashlib.sha256(str(path).encode()).hexdigest()[:16]}"
    try:
        spec = spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError("no import loader")
        module = module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    except Exception as exc:  # noqa: BLE001 - turn arbitrary plugin errors into ConfigError
        sys.modules.pop(module_name, None)
        raise ConfigError(f"{label} plugin import failed: {exc}") from exc


def _import_plugin(module_name: str | None, path: str | None, label: str, base_dir: Path | None) -> None:
    if module_name is None and path is None:
        raise ConfigError(f"{label} must define module or path")
    if module_name is not None and path is not None:
        raise ConfigError(f"{label} must define only one of module or path")
    if module_name is not None:
        try:
            import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - plugin boundary
            raise ConfigError(f"{label} plugin import failed: {exc}") from exc
    else:
        plugin_path = Path(path or "")
        if not plugin_path.is_absolute() and base_dir is not None:
            plugin_path = base_dir / plugin_path
        _plugin_module_from_path(plugin_path.expanduser().resolve(), label)


def _parse_plugin_item(
    value: Any,
    label: str,
    *,
    base_dir: Path | None,
    forced_id: str | None = None,
) -> ToolPluginSpec:
    if isinstance(value, str):
        raw_ref = value.strip()
        if not raw_ref:
            raise ConfigError(f"{label} must be a non-empty plugin reference")
        if raw_ref.endswith(".py") or "/" in raw_ref or "\\" in raw_ref:
            path = raw_ref
            inferred = Path(raw_ref).stem
            module = None
        else:
            module = raw_ref
            path = None
            inferred = raw_ref.rsplit(".", 1)[-1]
        plugin_id = forced_id or inferred
        _import_plugin(module, path, label, base_dir)
        return ToolPluginSpec(id=_identifier(plugin_id, f"{label}.id"), module=module, path=path)
    item = _mapping(value, label)
    _only_keys(
        item,
        {
            "id",
            "name",
            "module",
            "path",
            "import",
            "entrypoint",
            "factory",
            "description",
            "schema",
            "timeout",
            "timeout_seconds",
            "side_effect_policy",
            "permissions",
            "config",
        },
        label,
    )
    module_value = _pick(item, ("module", "import"), f"{label}.module", default=None)
    module = _optional_text(module_value, f"{label}.module")
    path = _optional_text(item.get("path", _MISSING), f"{label}.path")
    entrypoint_value = _pick(item, ("entrypoint", "factory"), f"{label}.entrypoint", default=None)
    entrypoint = _optional_text(entrypoint_value, f"{label}.entrypoint")
    raw_id = forced_id or _pick(item, ("id", "name"), f"{label}.id", default=_MISSING)
    timeout = _pick(item, ("timeout_seconds", "timeout"), f"{label}.timeout_seconds", default=30.0)
    _import_plugin(module, path, label, base_dir)
    return ToolPluginSpec(
        id=_identifier(raw_id, f"{label}.id"),
        module=module,
        path=path,
        entrypoint=entrypoint,
        description=_optional_text(item.get("description", _MISSING), f"{label}.description", default="") or "",
        schema=_json_mapping(item.get("schema", _MISSING), f"{label}.schema"),
        timeout_seconds=_tool_number(timeout, f"{label}.timeout_seconds"),
        side_effect_policy=_text(
            item.get("side_effect_policy", "none"), f"{label}.side_effect_policy"
        ),
        permissions=_string_list(item.get("permissions", _MISSING), f"{label}.permissions"),
        config=_json_mapping(item.get("config", _MISSING), f"{label}.config"),
    )


def _parse_tool_collection(value: Any, label: str, parser: Any, *, base_dir: Path | None) -> tuple[Any, ...]:
    if value is _MISSING or value is None:
        return ()
    if isinstance(value, list):
        return tuple(
            parser(item, f"{label}[{index}]", base_dir=base_dir)
            for index, item in enumerate(value)
        )
    if isinstance(value, Mapping):
        result = []
        for raw_id, item in value.items():
            tool_id = _identifier(raw_id, f"{label} key")
            result.append(parser(item, f"{label}.{tool_id}", base_dir=base_dir, forced_id=tool_id))
        return tuple(result)
    raise ConfigError(f"{label} must be a list or mapping")


def _validate_tool_schema(schema: Mapping[str, Any], label: str) -> None:
    _only_keys(schema, {"type", "properties", "required", "additionalProperties", "items",
                        "enum", "minimum", "maximum", "description", "title"}, label)
    kind = schema.get("type")
    if kind is not None and kind not in {"object", "array", "string", "number", "integer", "boolean", "null"}:
        raise ConfigError(f"{label}.type is unsupported")
    properties = _mapping(schema.get("properties", {}), f"{label}.properties")
    for key, child in properties.items():
        _validate_tool_schema(_mapping(child, f"{label}.{key}"), f"{label}.{key}")
    if "items" in schema:
        _validate_tool_schema(_mapping(schema["items"], f"{label}.items"), f"{label}.items")
    required = _string_list(schema.get("required", []), f"{label}.required")
    if not set(required) <= set(properties):
        raise ConfigError(f"{label}.required refers to an undefined property")
    if "additionalProperties" in schema:
        _bool(schema["additionalProperties"], f"{label}.additionalProperties")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise ConfigError(f"{label}.enum must be a nonempty list")
    for key in ("minimum", "maximum"):
        if key in schema:
            _number(schema[key], f"{label}.{key}")


def _parse_tools(value: Any, *, base_dir: Path | None) -> ToolsSpec:
    tools = _mapping(value if value is not _MISSING else {}, "tools")
    _only_keys(tools, {"builtins", "plugins"}, "tools")
    builtins_value = tools.get("builtins", _MISSING)
    if builtins_value is _MISSING:
        builtins = tuple(_parse_builtin_item(item, "tools.builtins") for item in _DEFAULT_BUILTINS)
    else:
        builtins = _parse_tool_collection(
            builtins_value,
            "tools.builtins",
            _parse_builtin_item,
            base_dir=base_dir,
        )
    plugins = _parse_tool_collection(
        tools.get("plugins", _MISSING),
        "tools.plugins",
        _parse_plugin_item,
        base_dir=base_dir,
    )
    for item in builtins:
        if item.id not in _DEFAULT_BUILTINS:
            raise ConfigError(f"unknown built-in tool {item.id!r}; register custom tools as plugins")
    for item in (*builtins, *plugins):
        _validate_tool_schema(item.schema, f"tools.{item.id}.schema")
        if item.side_effect_policy not in {"none", "read_only", "workspace", "external"}:
            raise ConfigError(f"tools.{item.id}.side_effect_policy is unsupported")
    ids = [item.id for item in (*builtins, *plugins)]
    _unique(ids, "tools")
    return ToolsSpec(builtins=tuple(builtins), plugins=tuple(plugins))


def _parse_skills(value: Any, label: str) -> dict[str, float]:
    if value is _MISSING or value is None:
        return {}
    if isinstance(value, list):
        names = _string_list(value, label)
        return {name: 1.0 for name in names}
    skills = _mapping(value, label)
    result: dict[str, float] = {}
    for raw_name, score in skills.items():
        name = _text(raw_name, f"{label} key")
        result[name] = _number(score, f"{label}.{name}", minimum=0.0, maximum=1.0)
    return result


def _parse_agent(value: Any, label: str, providers: Mapping[str, ProviderSpec]) -> AgentSpec:
    agent = _mapping(value, label)
    _only_keys(
        agent,
        {
            "id",
            "display_name",
            "name",
            "role",
            "role_mandate",
            "mandate",
            "provider",
            "model",
            "reasoning",
            "reasoning_parameters",
            "generation",
            "generation_parameters",
            "skills",
            "profile",
            "profile_dimensions",
            "failure_modes",
            "communication_style",
            "work_schedule",
            "schedule",
            "work_rhythm",
            "tools",
            "permissions",
            "private_workspace",
            "workspace",
            "initial_memory",
            "initial_context",
            "context",
            "ownership",
            "initial_ownership",
            "initial_tasks",
            "owned_tasks",
            "metadata",
        },
        label,
    )
    agent_id = _identifier(agent.get("id"), f"{label}.id")
    display_name = _pick(agent, ("display_name", "name"), f"{label}.display_name", default=agent_id)
    role = _text(agent.get("role", "agent"), f"{label}.role")
    provider_value = agent.get("provider", _MISSING)
    provider = _optional_text(provider_value, f"{label}.provider")
    if provider is not None and provider not in providers:
        raise ConfigError(f"{label}.provider references unknown provider {provider!r}")
    if provider is None and len(providers) == 1:
        provider = next(iter(providers))
    if provider is None:
        raise ConfigError(f"{label}.provider is required when multiple providers are configured")
    model_value = agent.get("model", _MISSING)
    model = _optional_text(model_value, f"{label}.model")
    if model is None and provider is not None:
        model = providers[provider].default_model
    reasoning = _pick(agent, ("reasoning", "reasoning_parameters"), f"{label}.reasoning", default={})
    generation = _pick(
        agent,
        ("generation", "generation_parameters"),
        f"{label}.generation",
        default={},
    )
    profile = _pick(agent, ("profile", "profile_dimensions"), f"{label}.profile", default={})
    schedule = _pick(
        agent,
        ("work_schedule", "schedule", "work_rhythm"),
        f"{label}.work_schedule",
        default={},
    )
    workspace = _pick(
        agent,
        ("private_workspace", "workspace"),
        f"{label}.private_workspace",
        default={},
    )
    context = _pick(
        agent,
        ("initial_context", "initial_memory", "context"),
        f"{label}.initial_context",
        default={},
    )
    ownership = _pick(
        agent,
        ("ownership", "initial_ownership", "initial_tasks", "owned_tasks"),
        f"{label}.ownership",
        default=(),
    )
    communication_style = agent.get("communication_style", "")
    if communication_style is None:
        communication_style = ""
    return AgentSpec(
        id=agent_id,
        display_name=_text(display_name, f"{label}.display_name"),
        role=role,
        role_mandate=_optional_text(
            _pick(agent, ("role_mandate", "mandate"), f"{label}.role_mandate", default=""),
            f"{label}.role_mandate",
            default="",
        )
        or "",
        provider=provider,
        model=model,
        reasoning=_json_mapping(reasoning, f"{label}.reasoning"),
        generation=_json_mapping(generation, f"{label}.generation"),
        skills=_parse_skills(agent.get("skills", _MISSING), f"{label}.skills"),
        profile=_json_mapping(profile, f"{label}.profile"),
        failure_modes=_string_list(agent.get("failure_modes", _MISSING), f"{label}.failure_modes"),
        communication_style=_json_value(communication_style, f"{label}.communication_style"),
        work_schedule=_json_value(schedule, f"{label}.work_schedule"),
        tools=_string_list(agent.get("tools", _MISSING), f"{label}.tools"),
        permissions=_string_list(agent.get("permissions", _MISSING), f"{label}.permissions"),
        private_workspace=_json_value(workspace, f"{label}.private_workspace"),
        initial_context=_json_value(context, f"{label}.initial_context"),
        ownership=_string_list(ownership, f"{label}.ownership"),
        metadata=_json_mapping(agent.get("metadata", _MISSING), f"{label}.metadata"),
    )


def _parse_agents(value: Any, providers: Mapping[str, ProviderSpec]) -> tuple[AgentSpec, ...]:
    if not isinstance(value, list):
        raise ConfigError("agents must be a list")
    if not value:
        raise ConfigError("agents must contain at least one agent")
    agents = tuple(
        _parse_agent(item, f"agents[{index}]", providers) for index, item in enumerate(value)
    )
    _unique(tuple(item.id for item in agents), "agents")
    return agents


def _parse_priority(value: Any, label: str) -> int | str:
    if isinstance(value, bool):
        raise ConfigError(f"{label} must be an integer or non-empty string")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ConfigError(f"{label} must be an integer or non-empty string")


def _parse_task(value: Any, label: str) -> TaskSpec:
    task = _mapping(value, label)
    _only_keys(
        task,
        {
            "id",
            "title",
            "description",
            "priority",
            "owner",
            "collaborators",
            "dependencies",
            "input_artifacts",
            "expected_deliverables",
            "acceptance_criteria",
            "deadline",
            "metadata",
        },
        label,
    )
    return TaskSpec(
        id=_identifier(task.get("id"), f"{label}.id"),
        title=_text(task.get("title"), f"{label}.title"),
        description=_optional_text(task.get("description", _MISSING), f"{label}.description", default="") or "",
        priority=_parse_priority(task.get("priority", 0), f"{label}.priority"),
        owner=_optional_text(task.get("owner", _MISSING), f"{label}.owner"),
        collaborators=_string_list(task.get("collaborators", _MISSING), f"{label}.collaborators"),
        dependencies=_string_list(task.get("dependencies", _MISSING), f"{label}.dependencies"),
        input_artifacts=_json_list(task.get("input_artifacts", _MISSING), f"{label}.input_artifacts"),
        expected_deliverables=_json_list(
            task.get("expected_deliverables", _MISSING), f"{label}.expected_deliverables"
        ),
        acceptance_criteria=_json_list(
            task.get("acceptance_criteria", _MISSING), f"{label}.acceptance_criteria"
        ),
        deadline=_optional_text(task.get("deadline", _MISSING), f"{label}.deadline"),
        metadata=_json_mapping(task.get("metadata", _MISSING), f"{label}.metadata"),
    )


def _parse_tasks(value: Any) -> tuple[TaskSpec, ...]:
    if value is _MISSING or value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError("tasks must be a list")
    tasks = tuple(_parse_task(item, f"tasks[{index}]") for index, item in enumerate(value))
    _unique(tuple(item.id for item in tasks), "tasks")
    task_ids = {item.id for item in tasks}
    for task in tasks:
        for dependency in task.dependencies:
            if dependency not in task_ids:
                raise ConfigError(
                    f"tasks.{task.id}.dependencies references unknown task {dependency!r}"
                )
            if dependency == task.id:
                raise ConfigError(f"tasks.{task.id}.dependencies cannot reference itself")
    pending = {task.id: set(task.dependencies) for task in tasks}
    while pending:
        ready = {key for key, dependencies in pending.items() if not dependencies}
        if not ready:
            raise ConfigError("tasks.dependencies contains a cycle")
        pending = {key: dependencies - ready for key, dependencies in pending.items() if key not in ready}
    return tasks


def _parse_governance(value: Any, agents: Sequence[AgentSpec]) -> GovernanceSpec:
    governance = _mapping(value if value is not _MISSING else {}, "governance")
    _only_keys(
        governance,
        {
            "approval_mode",
            "approver_roles",
            "approver_members",
            "approvers",
            "quorum",
            "protocol_quorum",
            "proposal_review_delay_ticks",
            "review_delay_ticks",
            "deadlock_behavior",
            "role_permissions",
            "permissions",
            "decision_visibility",
            "public_private_decision_visibility",
            "visibility",
            "protocol_adoption_threshold",
            "adoption_threshold",
            "amendment_threshold",
            "retirement_behavior",
            "retirement_obsolescence_behavior",
            "obsolescence_behavior",
        },
        "governance",
    )
    agent_ids = {agent.id for agent in agents}
    roles = {agent.role for agent in agents}
    approver_roles = _string_list(governance.get("approver_roles", _MISSING), "governance.approver_roles")
    approver_members = _string_list(
        governance.get("approver_members", _MISSING), "governance.approver_members"
    )
    if "approvers" in governance:
        generic_approvers = _string_list(governance["approvers"], "governance.approvers")
        for approver in generic_approvers:
            if approver in agent_ids:
                if approver not in approver_members:
                    approver_members += (approver,)
            elif approver in roles:
                if approver not in approver_roles:
                    approver_roles += (approver,)
            else:
                raise ConfigError(
                    f"governance.approvers references unknown member or role {approver!r}"
                )
    unknown_roles = sorted(set(approver_roles) - roles)
    if unknown_roles:
        raise ConfigError(
            "governance.approver_roles references unknown roles: " + ", ".join(unknown_roles)
        )
    unknown_members = sorted(set(approver_members) - agent_ids)
    if unknown_members:
        raise ConfigError(
            "governance.approver_members references unknown agents: " + ", ".join(unknown_members)
        )
    role_permissions_value = _pick(
        governance,
        ("role_permissions", "permissions"),
        "governance.role_permissions",
        default={},
    )
    raw_role_permissions = _mapping(role_permissions_value, "governance.role_permissions")
    role_permissions: dict[str, tuple[str, ...]] = {}
    for role, permissions in raw_role_permissions.items():
        role_name = _text(role, "governance.role_permissions role")
        if role_name not in roles:
            raise ConfigError(
                f"governance.role_permissions references unknown role {role_name!r}"
            )
        role_permissions[role_name] = _string_list(
            permissions, f"governance.role_permissions.{role_name}"
        )
    if not approver_roles and not approver_members:
        approver_members = tuple(agent.id for agent in agents)
    quorum_value = _pick(
        governance,
        ("quorum", "protocol_quorum"),
        "governance.quorum",
        default=1,
    )
    quorum = _integer(quorum_value, "governance.quorum", minimum=1)
    approver_pool = set(approver_members)
    approver_pool.update(agent.id for agent in agents if agent.role in approver_roles)
    if quorum > len(approver_pool):
        raise ConfigError(
            f"governance.quorum ({quorum}) exceeds the configured approver pool ({len(approver_pool)})"
        )
    review_delay = _pick(
        governance,
        ("proposal_review_delay_ticks", "review_delay_ticks"),
        "governance.proposal_review_delay_ticks",
        default=0,
    )
    visibility = _pick(
        governance,
        ("decision_visibility", "public_private_decision_visibility", "visibility"),
        "governance.decision_visibility",
        default="public",
    )
    visibility_text = _text(visibility, "governance.decision_visibility").lower()
    if visibility_text not in {"public", "private", "members", "roles", "organization", "both"}:
        raise ConfigError(
            "governance.decision_visibility must be public, private, members, roles, organization, or both"
        )
    retirement = _pick(
        governance,
        ("retirement_behavior", "retirement_obsolescence_behavior", "obsolescence_behavior"),
        "governance.retirement_behavior",
        default="review",
    )
    return GovernanceSpec(
        approval_mode=_text(governance.get("approval_mode", "agent"), "governance.approval_mode"),
        approver_roles=approver_roles,
        approver_members=approver_members,
        quorum=quorum,
        proposal_review_delay_ticks=_integer(
            review_delay, "governance.proposal_review_delay_ticks", minimum=0
        ),
        deadlock_behavior=_text(
            governance.get("deadlock_behavior", "escalate"), "governance.deadlock_behavior"
        ),
        role_permissions=role_permissions,
        decision_visibility=visibility_text,
        protocol_adoption_threshold=_number(
            _pick(
                governance,
                ("protocol_adoption_threshold", "adoption_threshold"),
                "governance.protocol_adoption_threshold",
                default=0.5,
            ),
            "governance.protocol_adoption_threshold",
            minimum=0.0,
            maximum=1.0,
        ),
        amendment_threshold=_number(
            governance.get("amendment_threshold", 0.5),
            "governance.amendment_threshold",
            minimum=0.0,
            maximum=1.0,
        ),
        retirement_behavior=_text(retirement, "governance.retirement_behavior"),
    )


_PROTOCOL_ITEM_KEYS = {
    "id",
    "name",
    "description",
    "source",
    "package",
    "trigger",
    "action",
    "goal",
    "evidence",
    "enforcement",
    "visibility",
    "owner",
    "participants",
    "rules",
    "parameters",
    "metadata",
    "definition",
}


def _parse_protocol(value: Any, label: str) -> ProtocolSpec:
    if isinstance(value, str):
        protocol_id = _identifier(value, f"{label}.id")
        return ProtocolSpec(id=protocol_id, name=protocol_id)
    protocol = _mapping(value, label)
    _only_keys(protocol, _PROTOCOL_ITEM_KEYS, label)
    protocol_id = _identifier(protocol.get("id"), f"{label}.id")
    source = _text(protocol.get("source", "initial"), f"{label}.source").lower()
    if source not in {"initial", "loaded", "emergent"}:
        raise ConfigError(f"{label}.source must be initial, loaded, or emergent")
    if source == "emergent":
        raise ConfigError(f"{label}.source cannot be emergent in protocols.initial")
    definition: dict[str, Any] = {}
    for key in (
        "trigger",
        "action",
        "goal",
        "evidence",
        "enforcement",
        "visibility",
        "owner",
        "participants",
        "rules",
        "parameters",
        "metadata",
        "definition",
    ):
        if key in protocol:
            definition[key] = _json_value(protocol[key], f"{label}.{key}")
    return ProtocolSpec(
        id=protocol_id,
        name=_text(protocol.get("name", protocol_id), f"{label}.name"),
        description=_optional_text(protocol.get("description", _MISSING), f"{label}.description", default="") or "",
        source=source,
        package=_optional_text(protocol.get("package", _MISSING), f"{label}.package"),
        definition=definition,
    )


def _parse_protocol_packages(value: Any, label: str, base_dir: Path | None) -> tuple[Any, ...]:
    if value is _MISSING or value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{label} must be a list")
    packages = []
    for index, item in enumerate(value):
        item_label = f"{label}[{index}]"
        if isinstance(item, str):
            ref = _text(item, item_label)
            if ref.endswith(".yaml") or ref.endswith(".yml") or ref.endswith(".json") or "/" in ref or "\\" in ref:
                package_path = Path(ref)
                if not package_path.is_absolute() and base_dir is not None:
                    package_path = base_dir / package_path
                if not package_path.expanduser().resolve().is_file():
                    raise ConfigError(f"{item_label} package not found: {package_path}")
            packages.append(ref)
            continue
        package = _mapping(item, item_label)
        _only_keys(package, {"id", "name", "module", "path", "source", "metadata"}, item_label)
        module = _optional_text(package.get("module", _MISSING), f"{item_label}.module")
        path = _optional_text(package.get("path", _MISSING), f"{item_label}.path")
        if module is None and path is None:
            raise ConfigError(f"{item_label} must define module or path")
        _import_plugin(module, path, item_label, base_dir)
        packages.append(_json_value(package, item_label))
    return tuple(packages)


def _parse_protocols(value: Any, *, base_dir: Path | None) -> ProtocolsSpec:
    protocols = _mapping(value if value is not _MISSING else {}, "protocols")
    _only_keys(
        protocols,
        {
            "initial",
            "packages",
            "allow_proposals",
            "allow_adoption",
            "allow_revision",
            "allow_retirement",
            "adoption_threshold",
            "amendment_threshold",
            "review_delay_ticks",
            "dedup",
            "retirement_behavior",
        },
        "protocols",
    )
    initial_value = protocols.get("initial", _MISSING)
    if initial_value is _MISSING or initial_value is None:
        initial: tuple[ProtocolSpec, ...] = ()
    elif isinstance(initial_value, list):
        initial = tuple(
            _parse_protocol(item, f"protocols.initial[{index}]")
            for index, item in enumerate(initial_value)
        )
    else:
        raise ConfigError("protocols.initial must be a list")
    _unique(tuple(item.id for item in initial), "protocols.initial")
    return ProtocolsSpec(
        initial=initial,
        packages=_parse_protocol_packages(protocols.get("packages", _MISSING), "protocols.packages", base_dir),
        allow_proposals=_bool(protocols.get("allow_proposals", _MISSING), "protocols.allow_proposals", default=True),
        allow_adoption=_bool(protocols.get("allow_adoption", _MISSING), "protocols.allow_adoption", default=True),
        allow_revision=_bool(protocols.get("allow_revision", _MISSING), "protocols.allow_revision", default=True),
        allow_retirement=_bool(protocols.get("allow_retirement", _MISSING), "protocols.allow_retirement", default=True),
        adoption_threshold=_number(
            protocols.get("adoption_threshold", 0.5),
            "protocols.adoption_threshold",
            minimum=0.0,
            maximum=1.0,
        ),
        amendment_threshold=_number(
            protocols.get("amendment_threshold", 0.5),
            "protocols.amendment_threshold",
            minimum=0.0,
            maximum=1.0,
        ),
        review_delay_ticks=_integer(
            protocols.get("review_delay_ticks", 0), "protocols.review_delay_ticks", minimum=0
        ),
        dedup=_bool(protocols.get("dedup", _MISSING), "protocols.dedup", default=True),
        retirement_behavior=_text(
            protocols.get("retirement_behavior", "review"), "protocols.retirement_behavior"
        ),
    )


_SECTION_KEYS = {
    "learning.reflection": {"enabled", "cadence_ticks", "per_agent_cooldown", "cooldown_ticks", "salience_threshold"},
    "learning.wish": {"enabled", "cap", "wish_cap", "dedup", "wish_dedup", "salience_threshold"},
    "learning.proposal": {"enabled", "promotion_threshold", "proposal_promotion_threshold", "cap", "proposal_cap", "dedup"},
    "learning.protocol": {"enabled", "dedup", "review_delay_ticks", "lifecycle_review_delay", "adoption_threshold", "amendment_threshold", "retirement_enabled"},
}

def _learning_section(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"enabled": value}
    section = _mapping(value, label)
    _only_keys(section, _SECTION_KEYS[label], label)
    return section


def _section_number(section: Mapping[str, Any], names: Sequence[str], label: str, default: float) -> float:
    return _number(_pick(section, names, label, default=default), label, minimum=0.0, maximum=1.0)


def _section_int(section: Mapping[str, Any], names: Sequence[str], label: str, default: int, minimum: int = 0) -> int:
    return _integer(_pick(section, names, label, default=default), label, minimum=minimum)


def _parse_learning(value: Any) -> LearningSpec:
    learning = _mapping(value if value is not _MISSING else {}, "learning")
    _only_keys(
        learning,
        {
            "profile_conditioning",
            "capability_learning",
            "institutionalization",
            "reflection",
            "reflection_cadence_ticks",
            "per_agent_cooldown",
            "salience_threshold",
            "wish_extraction",
            "wish",
            "wish_cap",
            "wish_dedup",
            "proposal_generation",
            "proposal",
            "proposal_promotion_threshold",
            "proposal_cap",
            "protocol_formation",
            "protocol",
            "protocol_dedup",
            "lifecycle_review_delay",
            "company_skill_memory",
            "governance_approval",
            "executable_workflow",
            "external_signal_loop",
            "external_signals",
            "parameters",
        },
        "learning",
    )
    reflection_input = learning.get("reflection", _MISSING)
    reflection = _learning_section(
        {} if reflection_input is _MISSING else reflection_input, "learning.reflection"
    )
    if "reflection_cadence_ticks" in learning:
        reflection["cadence_ticks"] = learning["reflection_cadence_ticks"]
    if "per_agent_cooldown" in learning:
        reflection["per_agent_cooldown"] = learning["per_agent_cooldown"]
    if "salience_threshold" in learning:
        reflection["salience_threshold"] = learning["salience_threshold"]
    wish_input = learning.get("wish", _MISSING)
    wish = _learning_section({} if wish_input is _MISSING else wish_input, "learning.wish")
    if "wish_cap" in learning:
        wish["cap"] = learning["wish_cap"]
    if "wish_dedup" in learning:
        wish["dedup"] = learning["wish_dedup"]
    proposal_input = learning.get("proposal", _MISSING)
    proposal = _learning_section({} if proposal_input is _MISSING else proposal_input, "learning.proposal")
    if "proposal_promotion_threshold" in learning:
        proposal["promotion_threshold"] = learning["proposal_promotion_threshold"]
    if "proposal_cap" in learning:
        proposal["cap"] = learning["proposal_cap"]
    protocol_input = learning.get("protocol", _MISSING)
    protocol = _learning_section({} if protocol_input is _MISSING else protocol_input, "learning.protocol")
    if "protocol_dedup" in learning:
        protocol["dedup"] = learning["protocol_dedup"]
    if "lifecycle_review_delay" in learning:
        protocol["review_delay_ticks"] = learning["lifecycle_review_delay"]

    reflection_spec = ReflectionSpec(
        enabled=_bool(reflection.get("enabled", _MISSING), "learning.reflection.enabled", default=True),
        cadence_ticks=_section_int(reflection, ("cadence_ticks",), "learning.reflection.cadence_ticks", 6, 1),
        per_agent_cooldown=_section_int(
            reflection,
            ("per_agent_cooldown", "cooldown_ticks"),
            "learning.reflection.per_agent_cooldown",
            0,
        ),
        salience_threshold=_section_number(
            reflection,
            ("salience_threshold",),
            "learning.reflection.salience_threshold",
            0.0,
        ),
    )
    wish_spec = WishSpec(
        enabled=_bool(
            wish.get("enabled", learning.get("wish_extraction", _MISSING)),
            "learning.wish.enabled",
            default=True,
        ),
        cap=_section_int(wish, ("cap", "wish_cap"), "learning.wish.cap", 10),
        dedup=_bool(
            wish.get("dedup", wish.get("wish_dedup", _MISSING)), "learning.wish.dedup", default=True
        ),
        salience_threshold=_section_number(
            wish,
            ("salience_threshold",),
            "learning.wish.salience_threshold",
            0.0,
        ),
    )
    proposal_spec = ProposalLearningSpec(
        enabled=_bool(
            proposal.get("enabled", learning.get("proposal_generation", _MISSING)),
            "learning.proposal.enabled",
            default=True,
        ),
        promotion_threshold=_section_number(
            proposal,
            ("promotion_threshold", "proposal_promotion_threshold"),
            "learning.proposal.promotion_threshold",
            0.5,
        ),
        cap=_section_int(proposal, ("cap", "proposal_cap"), "learning.proposal.cap", 10),
        dedup=_bool(proposal.get("dedup", _MISSING), "learning.proposal.dedup", default=True),
    )
    protocol_spec = ProtocolLearningSpec(
        enabled=_bool(
            protocol.get("enabled", learning.get("protocol_formation", _MISSING)),
            "learning.protocol.enabled",
            default=True,
        ),
        dedup=_bool(protocol.get("dedup", _MISSING), "learning.protocol.dedup", default=True),
        review_delay_ticks=_section_int(
            protocol,
            ("review_delay_ticks", "lifecycle_review_delay"),
            "learning.protocol.review_delay_ticks",
            0,
        ),
        adoption_threshold=_section_number(
            protocol, ("adoption_threshold",), "learning.protocol.adoption_threshold", 0.5
        ),
        amendment_threshold=_section_number(
            protocol, ("amendment_threshold",), "learning.protocol.amendment_threshold", 0.5
        ),
        retirement_enabled=_bool(
            protocol.get("retirement_enabled", _MISSING),
            "learning.protocol.retirement_enabled",
            default=True,
        ),
    )
    return LearningSpec(
        profile_conditioning=_bool(
            learning.get("profile_conditioning", _MISSING), "learning.profile_conditioning", default=True
        ),
        capability_learning=_bool(
            learning.get("capability_learning", _MISSING), "learning.capability_learning", default=True
        ),
        institutionalization=_bool(
            learning.get("institutionalization", _MISSING), "learning.institutionalization", default=True
        ),
        reflection=reflection_spec,
        wish_extraction=_bool(
            learning.get("wish_extraction", _MISSING), "learning.wish_extraction", default=True
        ),
        wish=wish_spec,
        proposal_generation=_bool(
            learning.get("proposal_generation", _MISSING), "learning.proposal_generation", default=True
        ),
        proposal=proposal_spec,
        protocol_formation=_bool(
            learning.get("protocol_formation", _MISSING), "learning.protocol_formation", default=True
        ),
        protocol=protocol_spec,
        company_skill_memory=_bool(
            learning.get("company_skill_memory", _MISSING), "learning.company_skill_memory", default=True
        ),
        governance_approval=_bool(
            learning.get("governance_approval", _MISSING), "learning.governance_approval", default=True
        ),
        executable_workflow=_bool(
            learning.get("executable_workflow", _MISSING), "learning.executable_workflow", default=True
        ),
        external_signal_loop=_bool(
            _pick(
                learning,
                ("external_signal_loop", "external_signals"),
                "learning.external_signal_loop",
                default=True,
            ),
            "learning.external_signal_loop",
            default=True,
        ),
        parameters=_json_mapping(learning.get("parameters", _MISSING), "learning.parameters"),
    )


def _parse_prompts(value: Any, organization: OrganizationSpec) -> PromptSpec:
    prompts = _mapping(value if value is not _MISSING else {}, "prompts")
    _only_keys(
        prompts,
        {
            "organization_brief",
            "company_brief",
            "global_grounding_rules",
            "grounding_rules",
            "agent_instruction_suffix",
            "task_context_template",
            "custom_prompt_assets_path",
            "custom_prompt_asset_path",
            "assets_path",
            "metadata",
        },
        "prompts",
    )
    brief = _pick(
        prompts,
        ("organization_brief", "company_brief"),
        "prompts.organization_brief",
        default=organization.brief,
    )
    rules = _pick(
        prompts,
        ("global_grounding_rules", "grounding_rules"),
        "prompts.global_grounding_rules",
        default=(),
    )
    assets = _pick(
        prompts,
        ("custom_prompt_assets_path", "custom_prompt_asset_path", "assets_path"),
        "prompts.custom_prompt_assets_path",
        default=(),
    )
    if isinstance(assets, str):
        assets = (assets,)
    asset_paths = _string_list(assets, "prompts.custom_prompt_assets_path")
    return PromptSpec(
        organization_brief=_optional_text(brief, "prompts.organization_brief", default="") or "",
        global_grounding_rules=_string_list(rules, "prompts.global_grounding_rules"),
        agent_instruction_suffix=_optional_text(
            prompts.get("agent_instruction_suffix", _MISSING),
            "prompts.agent_instruction_suffix",
            default="",
        )
        or "",
        task_context_template=_optional_text(
            prompts.get("task_context_template", _MISSING), "prompts.task_context_template", default=""
        )
        or "",
        custom_prompt_assets_path=asset_paths,
        metadata=_json_mapping(prompts.get("metadata", _MISSING), "prompts.metadata"),
    )


def _parse_observability(value: Any) -> ObservabilitySpec:
    observability = _mapping(value if value is not _MISSING else {}, "observability")
    _only_keys(
        observability,
        {
            "public_trace",
            "inspector",
            "local_debug",
            "debug",
            "token_logging",
            "log_tokens",
            "cost_logging",
            "redact_secrets",
            "metadata",
        },
        "observability",
    )
    if observability.get("redact_secrets") is False:
        raise ConfigError("observability.redact_secrets cannot disable credential protection")
    local_debug = _pick(observability, ("local_debug", "debug"), "observability.local_debug", default=False)
    token_logging = _pick(observability, ("token_logging", "log_tokens"), "observability.token_logging", default=True)
    return ObservabilitySpec(
        public_trace=_bool(observability.get("public_trace", _MISSING), "observability.public_trace", default=True),
        inspector=_bool(observability.get("inspector", _MISSING), "observability.inspector", default=True),
        local_debug=_bool(local_debug, "observability.local_debug", default=False),
        token_logging=_bool(token_logging, "observability.token_logging", default=True),
        cost_logging=_bool(observability.get("cost_logging", _MISSING), "observability.cost_logging", default=True),
        redact_secrets=_bool(observability.get("redact_secrets", _MISSING), "observability.redact_secrets", default=True),
        metadata=_json_mapping(observability.get("metadata", _MISSING), "observability.metadata"),
    )


def _parse_runtime(value: Any, providers: Mapping[str, ProviderSpec]) -> RuntimeSpec:
    runtime = _mapping(value if value is not _MISSING else {}, "runtime")
    _only_keys(
        runtime,
        {
            "seed",
            "ticks",
            "decision_mode",
            "provider",
            "timeout",
            "timeout_seconds",
            "retries",
            "retry_count",
            "max_concurrent_agents",
        },
        "runtime",
    )
    provider_value = runtime.get("provider", _MISSING)
    provider = _optional_text(provider_value, "runtime.provider", default="generic")
    if provider not in {"generic", ""} and provider not in providers:
        raise ConfigError(f"runtime.provider references unknown provider {provider!r}")
    timeout = _pick(runtime, ("timeout_seconds", "timeout"), "runtime.timeout_seconds", default=60.0)
    retries = _pick(runtime, ("retry_count", "retries"), "runtime.retry_count", default=0)
    max_agents = runtime.get("max_concurrent_agents", _MISSING)
    return RuntimeSpec(
        seed=_integer(runtime.get("seed", _MISSING), "runtime.seed", default=42, minimum=0),
        ticks=_integer(runtime.get("ticks", _MISSING), "runtime.ticks", default=72, minimum=1),
        decision_mode=_text(runtime.get("decision_mode", "profile_policy"), "runtime.decision_mode"),
        provider=provider or "generic",
        timeout_seconds=_number(timeout, "runtime.timeout_seconds", minimum=0.001),
        retry_count=_integer(retries, "runtime.retry_count", minimum=0),
        max_concurrent_agents=(
            _integer(max_agents, "runtime.max_concurrent_agents", minimum=1)
            if max_agents is not _MISSING and max_agents is not None
            else None
        ),
    )


def _validate_task_agent_refs(tasks: Sequence[TaskSpec], agents: Sequence[AgentSpec]) -> None:
    agent_ids = {agent.id for agent in agents}
    for task in tasks:
        if task.owner is not None and task.owner not in agent_ids:
            raise ConfigError(f"tasks.{task.id}.owner references unknown agent {task.owner!r}")
        unknown = sorted(set(task.collaborators) - agent_ids)
        if unknown:
            raise ConfigError(
                f"tasks.{task.id}.collaborators references unknown agents: {', '.join(unknown)}"
            )
    task_ids = {task.id for task in tasks}
    for agent in agents:
        unknown = sorted(set(agent.ownership) - task_ids)
        if unknown:
            raise ConfigError(
                f"agents.{agent.id}.ownership references unknown tasks: {', '.join(unknown)}"
            )


def _normalized_data(
    *,
    organization: OrganizationSpec,
    providers: Mapping[str, ProviderSpec],
    agents: Sequence[AgentSpec],
    tasks: Sequence[TaskSpec],
    tools: ToolsSpec,
    governance: GovernanceSpec,
    protocols: ProtocolsSpec,
    learning: LearningSpec,
    runtime: RuntimeSpec,
    prompts: PromptSpec,
    observability: ObservabilitySpec,
) -> dict[str, Any]:
    data = {
        "schema_version": GENERIC_CONFIG_SCHEMA_VERSION,
        "organization": _as_data(organization),
        "providers": _as_data(providers),
        "agents": _as_data(agents),
        "tasks": _as_data(tasks),
        "tools": _as_data(tools),
        "governance": _as_data(governance),
        "protocols": _as_data(protocols),
        "learning": _as_data(learning),
        "runtime": _as_data(runtime),
        "prompts": _as_data(prompts),
        "observability": _as_data(observability),
    }
    # The runtime consumes tool IDs; retaining an explicit list beside the
    # detailed catalog makes the normalized shape convenient for lightweight
    # builders and Inspector code.
    data["tools"]["builtin_ids"] = [item.id for item in tools.builtins]
    data["tools"]["plugin_ids"] = [item.id for item in tools.plugins]
    return data


def parse_generic_config(
    payload: Mapping[str, Any], *, source_path: Path | None = None, digest: str = ""
) -> GenericConfig:
    """Validate and normalize a ``relic-agent-v2`` payload.

    The function does not inspect environment variables, contact a model
    provider, or execute a simulation.  Plugin modules are imported only to
    detect local import failures, as required by the plugin contract.
    """

    root = _mapping(payload, "config")
    _only_keys(
        root,
        {
            "schema_version",
            "organization",
            "providers",
            "agents",
            "tasks",
            "tools",
            "governance",
            "protocols",
            "learning",
            "runtime",
            "prompts",
            "observability",
        },
        "config",
    )
    schema_version = _text(root.get("schema_version"), "schema_version")
    if schema_version != GENERIC_CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"unsupported schema_version {schema_version!r}; expected {GENERIC_CONFIG_SCHEMA_VERSION!r}"
        )
    for required in ("organization", "providers", "agents"):
        if required not in root:
            raise ConfigError(f"config.{required} is required for {GENERIC_CONFIG_SCHEMA_VERSION}")
    base_dir = source_path.parent if source_path is not None else None
    organization = _parse_organization(root["organization"])
    providers = _parse_providers(root["providers"])
    tools = _parse_tools(root.get("tools", _MISSING), base_dir=base_dir)
    agents = _parse_agents(root["agents"], providers)
    tasks = _parse_tasks(root.get("tasks", _MISSING))
    _validate_task_agent_refs(tasks, agents)
    for agent in agents:
        unknown_tools = sorted(set(agent.tools) - set(tools.ids))
        if unknown_tools:
            raise ConfigError(
                f"agents.{agent.id}.tools references unknown tools: {', '.join(unknown_tools)}"
            )
    governance = _parse_governance(root.get("governance", _MISSING), agents)
    protocols = _parse_protocols(root.get("protocols", _MISSING), base_dir=base_dir)
    learning = _parse_learning(root.get("learning", _MISSING))
    runtime = _parse_runtime(root.get("runtime", _MISSING), providers)
    prompts = _parse_prompts(root.get("prompts", _MISSING), organization)
    observability = _parse_observability(root.get("observability", _MISSING))
    data = _normalized_data(
        organization=organization,
        providers=providers,
        agents=agents,
        tasks=tasks,
        tools=tools,
        governance=governance,
        protocols=protocols,
        learning=learning,
        runtime=runtime,
        prompts=prompts,
        observability=observability,
    )
    return GenericConfig(
        schema_version=schema_version,
        organization=organization,
        providers=providers,
        agents=agents,
        tasks=tasks,
        tools=tools,
        governance=governance,
        protocols=protocols,
        learning=learning,
        runtime=runtime,
        prompts=prompts,
        observability=observability,
        data=data,
        source_path=source_path,
        digest=digest,
    )


__all__ = [
    "GENERIC_CONFIG_SCHEMA_VERSION",
    "AgentSpec",
    "BuiltinToolSpec",
    "ConfigError",
    "GenericConfig",
    "GovernanceSpec",
    "LearningSpec",
    "ObservabilitySpec",
    "OrganizationSpec",
    "PromptSpec",
    "ProtocolLearningSpec",
    "ProtocolSpec",
    "ProtocolsSpec",
    "ProposalLearningSpec",
    "ProviderSpec",
    "ReflectionSpec",
    "RuntimeSpec",
    "TaskSpec",
    "ToolPluginSpec",
    "ToolsSpec",
    "WishSpec",
    "parse_generic_config",
]
