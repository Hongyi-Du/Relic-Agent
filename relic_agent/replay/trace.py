"""The fail-closed public ``relic-trace-v1`` contract."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from relic_agent.core.hashing import canonical_sha256

TRACE_SCHEMA_VERSION = "relic-trace-v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CREDENTIAL_VALUE_RE = re.compile(
    r"(?:\bBearer\s+[A-Za-z0-9._~+/=-]{12,}|\bsk-[A-Za-z0-9_-]{12,}|"
    r"\bgh[opusr]_[A-Za-z0-9_]{12,}|\bgithub_pat_[A-Za-z0-9_]{12,}|"
    r"\bAKIA[0-9A-Z]{16}\b)",
    re.IGNORECASE,
)
_LOCAL_PATH_RE = re.compile(
    r"(?:^|[\s\"'=:(])(?:/(?:home|root|Users|mnt/[A-Za-z])(?:/|\\)|"
    r"[A-Za-z]:\\(?:Users|Documents and Settings)\\|file://)",
    re.IGNORECASE,
)
_BLOCKED_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "cookie",
        "password",
        "private_memory",
        "private_memories",
        "provider_message",
        "provider_messages",
        "raw_reflection",
        "raw_reflection_excerpt",
        "reflection_text",
        "secret",
        "system_prompt",
    }
)
_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "organization_id",
        "config_sha256",
        "privacy",
        "frames",
        "evaluation_annotations",
        "run_metadata",
        "trace_sha256",
    }
)
_PRIVACY_KEYS = frozenset(
    {
        "private_reflections_included",
        "private_memories_included",
        "provider_messages_included",
    }
)
_FRAME_KEYS = frozenset(
    {
        "frame_id",
        "sequence",
        "tick",
        "organization",
        "events",
        "episodes",
        "decisions",
        "governance_events",
        "artifacts",
        "repo_state",
        "evaluation_annotations",
    }
)
_ORGANIZATION_KEYS = frozenset(
    {
        "organization_id",
        "name",
        "tick",
        "agents",
        "tasks",
        "proposals",
        "protocols",
        "artifacts",
        "repo_state",
        "evidence",
    }
)
_AGENT_KEYS = frozenset(
    {
        "agent_id",
        "display_name",
        "role",
        "tools",
        "active_task_ids",
        "current_task_id",
        "status",
        "local_state",
        "known_context",
        "recent_decisions",
        "obligations",
        "relevant_protocol_ids",
    }
)
_TASK_KEYS = frozenset(
    {
        "task_id",
        "title",
        "description",
        "status",
        "priority",
        "owner_id",
        "required_skills",
        "dependencies",
        "deadline_tick",
        "estimated_effort",
        "actual_effort",
        "visibility",
        "progress_score",
        "progress_evidence",
        "history",
        "blockers",
        "related_artifact_ids",
        "related_protocol_ids",
    }
)
_PROPOSAL_KEYS = frozenset(
    {
        "proposal_id",
        "proposal_type",
        "title",
        "summary",
        "proposer_agent_id",
        "source_wish_id",
        "source_wish_ids",
        "source_reflection_id",
        "source_episode_id",
        "source_episode_ids",
        "source_event_ids",
        "target_problem",
        "proposed_solution",
        "required_actions",
        "required_capabilities",
        "required_artifacts",
        "required_participants",
        "affected_agents",
        "affected_objects",
        "affected_protocols",
        "expected_benefits",
        "expected_costs",
        "risks",
        "failure_modes",
        "feasibility_score",
        "usefulness_score",
        "risk_score",
        "adoption_score",
        "suggested_revision",
        "family",
        "status",
        "approval_required_from",
        "approved_by",
        "supporters",
        "rejected_by",
        "opposers",
        "rejection_reason",
        "object_created_id",
        "adopted_tick",
        "impact",
        "created_at_tick",
        "updated_at_tick",
        "amends_protocol_id",
        "repair_kind",
        "repair_target_protocol_id",
    }
)
_PROTOCOL_KEYS = frozenset(
    {
        "protocol_id",
        "protocol_type",
        "name",
        "proposer_id",
        "created_from_proposal_id",
        "proposal_event_id",
        "rule_summary",
        "trigger_condition",
        "required_steps",
        "required_fields",
        "enforcement_rule",
        "violation_condition",
        "exception_rule",
        "scope",
        "affected_agents",
        "affected_actions",
        "affected_artifacts",
        "responsible_roles",
        "success_metric",
        "enforcement_action",
        "sunset_rule",
        "target_process",
        "supporters",
        "opposers",
        "adoption_status",
        "usage_events",
        "violation_events",
        "enforcement_events",
        "revisions",
        "revision",
        "version",
        "first_tick",
        "last_active_tick",
        "retired_tick",
        "persistence_ticks",
        "impact_metrics",
        "emergence_level",
        "status",
    }
)
_EPISODE_KEYS = frozenset(
    {
        "episode_id",
        "episode_type",
        "title",
        "status",
        "start_tick",
        "end_tick",
        "participants",
        "linked_event_ids",
        "linked_task_ids",
        "linked_protocol_ids",
        "linked_proposal_ids",
        "problem_statement",
        "decision_summary",
        "outcome_summary",
        "produced_protocols",
        "timeline",
    }
)
_EVENT_KEYS = frozenset(
    {"event_id", "tick", "event_type", "actor_id", "object_ids", "payload", "visibility"}
)
_DECISION_KEYS = frozenset(
    {"decision_id", "tick", "agent_id", "chosen_action_id", "chosen_object_id"}
)
_GOVERNANCE_EVENT_KEYS = frozenset(
    {"event_id", "event_type", "protocol_id", "actor_id", "tick", "data"}
)
_PRIVATE_EVENT_TYPES = frozenset({"reflection_completed", "wish_created"})


class TraceError(ValueError):
    """Raised when a public trace does not satisfy the release contract."""


def _bad_number(value: str) -> None:
    raise TraceError(f"trace contains non-finite number: {value}")


def _load_json_without_duplicate_keys(text: str) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise TraceError(f"trace contains duplicate key: {key}")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=object_pairs, parse_constant=_bad_number)


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TraceError(f"{label} must be an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise TraceError(f"{label} must be a list")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TraceError(f"{label} must be a non-empty string")
    return value


def _require_integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise TraceError(f"{label} must be an integer >= {minimum}")
    return value


def _reject_unknown_keys(value: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise TraceError(f"{label} contains unsupported fields: {', '.join(unknown)}")


def _validate_finite_numbers(value: Any, path: str = "trace") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise TraceError(f"{path} contains a non-finite number")
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_finite_numbers(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_finite_numbers(item, f"{path}[{index}]")


def _validate_public_values(value: Any, path: str = "trace") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.casefold().replace("-", "_")
            if normalized in _BLOCKED_KEYS:
                raise TraceError(f"public trace contains blocked field at {path}.{key}")
            _validate_public_values(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_public_values(item, f"{path}[{index}]")
        return
    if isinstance(value, str):
        if _CREDENTIAL_VALUE_RE.search(value):
            raise TraceError(f"public trace contains credential-like text at {path}")
        if _LOCAL_PATH_RE.search(value):
            raise TraceError(f"public trace contains a local filesystem path at {path}")


def _validate_object_collection(
    value: Any,
    *,
    label: str,
    identifier: str,
    allowed: frozenset[str],
) -> None:
    seen: set[str] = set()
    for index, item in enumerate(_require_list(value, label)):
        record = _require_mapping(item, f"{label}[{index}]")
        _reject_unknown_keys(record, allowed, f"{label}[{index}]")
        object_id = _require_string(record.get(identifier), f"{label}[{index}].{identifier}")
        if object_id in seen:
            raise TraceError(f"{label} contains duplicate {identifier}: {object_id}")
        seen.add(object_id)


def _validate_structure(payload: dict[str, Any]) -> None:
    _reject_unknown_keys(payload, _TOP_LEVEL_KEYS, "trace")
    if payload.get("schema_version") != TRACE_SCHEMA_VERSION:
        raise TraceError(f"trace must use {TRACE_SCHEMA_VERSION}")
    _require_string(payload.get("run_id"), "trace.run_id")
    organization_id = _require_string(payload.get("organization_id"), "trace.organization_id")
    config_sha256 = _require_string(payload.get("config_sha256"), "trace.config_sha256")
    if not _SHA256_RE.fullmatch(config_sha256):
        raise TraceError("trace.config_sha256 must be a lowercase SHA-256 digest")

    privacy = _require_mapping(payload.get("privacy"), "trace.privacy")
    _reject_unknown_keys(privacy, _PRIVACY_KEYS, "trace.privacy")
    if set(privacy) != _PRIVACY_KEYS:
        raise TraceError("trace.privacy must declare every public privacy boundary")
    for key in sorted(_PRIVACY_KEYS):
        if privacy[key] is not False:
            raise TraceError(f"trace.privacy.{key} must be false")

    frames = _require_list(payload.get("frames"), "trace.frames")
    if not frames:
        raise TraceError("trace.frames must be a non-empty list")
    last_tick = -1
    seen_events: set[str] = set()
    seen_governance_events: set[str] = set()
    seen_decisions: set[str] = set()
    for index, item in enumerate(frames):
        label = f"trace.frames[{index}]"
        frame = _require_mapping(item, label)
        _reject_unknown_keys(frame, _FRAME_KEYS, label)
        required = {
            "frame_id",
            "sequence",
            "tick",
            "organization",
            "events",
            "episodes",
            "decisions",
            "governance_events",
        }
        if not required <= set(frame):
            raise TraceError(f"{label} is missing required public snapshot fields")
        if frame["frame_id"] != f"frame_{index:06d}":
            raise TraceError(f"{label}.frame_id must match its sequence")
        if _require_integer(frame["sequence"], f"{label}.sequence") != index:
            raise TraceError(f"{label}.sequence must be contiguous from zero")
        tick = _require_integer(frame["tick"], f"{label}.tick")
        if tick < last_tick:
            raise TraceError("trace frame ticks must be non-decreasing")
        last_tick = tick

        organization = _require_mapping(frame["organization"], f"{label}.organization")
        _reject_unknown_keys(organization, _ORGANIZATION_KEYS, f"{label}.organization")
        if organization.get("organization_id") != organization_id:
            raise TraceError(f"{label}.organization_id does not match the trace")
        if organization.get("tick") != tick:
            raise TraceError(f"{label}.organization.tick does not match the frame")
        for key in ("agents", "tasks", "proposals", "protocols"):
            if key not in organization:
                raise TraceError(f"{label}.organization.{key} is required")
        _validate_object_collection(
            organization["agents"],
            label=f"{label}.organization.agents",
            identifier="agent_id",
            allowed=_AGENT_KEYS,
        )
        _validate_object_collection(
            organization["tasks"],
            label=f"{label}.organization.tasks",
            identifier="task_id",
            allowed=_TASK_KEYS,
        )
        _validate_object_collection(
            organization["proposals"],
            label=f"{label}.organization.proposals",
            identifier="proposal_id",
            allowed=_PROPOSAL_KEYS,
        )
        _validate_object_collection(
            organization["protocols"],
            label=f"{label}.organization.protocols",
            identifier="protocol_id",
            allowed=_PROTOCOL_KEYS,
        )

        for event_index, event_item in enumerate(_require_list(frame["events"], f"{label}.events")):
            event_label = f"{label}.events[{event_index}]"
            event = _require_mapping(event_item, event_label)
            _reject_unknown_keys(event, _EVENT_KEYS, event_label)
            event_id = _require_string(event.get("event_id"), f"{event_label}.event_id")
            if event_id in seen_events:
                raise TraceError(f"trace contains duplicate public event: {event_id}")
            seen_events.add(event_id)
            if _require_integer(event.get("tick"), f"{event_label}.tick") != tick:
                raise TraceError(f"{event_label}.tick must match its snapshot frame")
            event_type = _require_string(event.get("event_type"), f"{event_label}.event_type")
            if event_type in _PRIVATE_EVENT_TYPES:
                raise TraceError(f"{event_label} contains a private event type")
            if event.get("visibility") not in {"organization", "public"}:
                raise TraceError(f"{event_label} is not a public event")
            _require_list(event.get("object_ids"), f"{event_label}.object_ids")
            _require_mapping(event.get("payload"), f"{event_label}.payload")

        _validate_object_collection(
            frame["episodes"],
            label=f"{label}.episodes",
            identifier="episode_id",
            allowed=_EPISODE_KEYS,
        )
        for decision_index, decision_item in enumerate(
            _require_list(frame["decisions"], f"{label}.decisions")
        ):
            decision_label = f"{label}.decisions[{decision_index}]"
            decision = _require_mapping(decision_item, decision_label)
            _reject_unknown_keys(decision, _DECISION_KEYS, decision_label)
            decision_id = _require_string(
                decision.get("decision_id"), f"{decision_label}.decision_id"
            )
            if decision_id in seen_decisions:
                raise TraceError(f"trace contains duplicate public decision: {decision_id}")
            seen_decisions.add(decision_id)
            if _require_integer(decision.get("tick"), f"{decision_label}.tick") != tick:
                raise TraceError(f"{decision_label}.tick must match its snapshot frame")
            _require_string(decision.get("agent_id"), f"{decision_label}.agent_id")

        for event_index, event_item in enumerate(
            _require_list(frame["governance_events"], f"{label}.governance_events")
        ):
            event_label = f"{label}.governance_events[{event_index}]"
            event = _require_mapping(event_item, event_label)
            _reject_unknown_keys(event, _GOVERNANCE_EVENT_KEYS, event_label)
            event_id = _require_string(event.get("event_id"), f"{event_label}.event_id")
            if event_id in seen_governance_events:
                raise TraceError(f"trace contains duplicate governance event: {event_id}")
            seen_governance_events.add(event_id)
            if _require_integer(event.get("tick"), f"{event_label}.tick") > tick:
                raise TraceError(f"{event_label}.tick cannot be later than its snapshot frame")
            _require_string(event.get("event_type"), f"{event_label}.event_type")
            _require_string(event.get("protocol_id"), f"{event_label}.protocol_id")
            _require_mapping(event.get("data"), f"{event_label}.data")


def validate_trace(payload: Any, *, verify_digest: bool = True) -> dict[str, Any]:
    """Validate and return one public trace payload without mutating it."""

    trace = _require_mapping(payload, "trace")
    _validate_structure(trace)
    _validate_finite_numbers(trace)
    _validate_public_values(trace)
    if verify_digest:
        expected = trace.get("trace_sha256")
        if not isinstance(expected, str) or not _SHA256_RE.fullmatch(expected):
            raise TraceError("trace.trace_sha256 must be a lowercase SHA-256 digest")
        unhashed = dict(trace)
        unhashed.pop("trace_sha256", None)
        if expected != canonical_sha256(unhashed):
            raise TraceError("trace digest mismatch")
    return trace


def build_trace(
    *,
    run_id: str,
    organization_id: str,
    config_digest: str,
    frames: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build, validate, and digest a public trace from curated public frames."""

    trace = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "run_id": run_id,
        "organization_id": organization_id,
        "config_sha256": config_digest,
        "privacy": {
            "private_reflections_included": False,
            "private_memories_included": False,
            "provider_messages_included": False,
        },
        "frames": frames,
    }
    validate_trace(trace, verify_digest=False)
    trace["trace_sha256"] = canonical_sha256(trace)
    return validate_trace(trace)


def load_trace(path: str | Path) -> dict[str, Any]:
    """Load a digest-bound public trace and reject malformed or private content."""

    trace_path = Path(path).expanduser().resolve()
    try:
        payload = _load_json_without_duplicate_keys(trace_path.read_text(encoding="utf-8"))
    except TraceError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise TraceError(f"cannot load trace {trace_path}: {exc}") from exc
    return validate_trace(payload)


__all__ = [
    "TRACE_SCHEMA_VERSION",
    "TraceError",
    "build_trace",
    "load_trace",
    "validate_trace",
]
