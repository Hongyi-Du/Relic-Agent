"""Privacy-preserving projection of source ``OrgWorld`` state.

The vendored source has a deep debugger snapshot which intentionally includes
private workspaces, raw reflections, prompts, and product contents.  The
release Inspector uses ``relic-trace-v1`` instead.  These functions select
already-observed, public lifecycle facts without translating them into a
second organization model or mutating the source world.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


_LOS_XI_AGENT_ID = "scarlett"
_LOS_XI_DISPLAY_NAME = "Los Xi"


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    return str(value)


def _unique_strings(values: Iterable[Any], *, allowed: set[str] | None = None) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _text(value).strip()
        if not text or text in result:
            continue
        if allowed is not None and text not in allowed:
            continue
        result.append(text)
    return result


def _enum_text(value: Any, fallback: str = "") -> str:
    return _text(getattr(value, "value", value), fallback)


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _agent_display_name(agent_id: str, agent: Any) -> str:
    # The source identity / id remains untouched; this is the handoff-required
    # public identity overlay only.
    if agent_id == _LOS_XI_AGENT_ID:
        return _LOS_XI_DISPLAY_NAME
    return _text(getattr(agent, "name", None), agent_id)


def _public_agents(world: Any) -> list[dict[str, Any]]:
    tasks = set((getattr(world, "tasks", {}) or {}).keys())
    rows: list[dict[str, Any]] = []
    for agent_id, agent in sorted((getattr(world, "agents", {}) or {}).items()):
        rows.append(
            {
                "agent_id": _text(agent_id),
                "display_name": _agent_display_name(_text(agent_id), agent),
                "role": _text(getattr(agent, "role", None), "member"),
                # The source does not expose a portable tool declaration in a
                # public world record.  An empty list is more accurate than a
                # reconstructed/guessed tool surface.
                "tools": [],
                "active_task_ids": _unique_strings(
                    getattr(agent, "active_tasks", ()) or (), allowed=tasks
                ),
                "status": _text(getattr(agent, "current_status", None), "unknown"),
            }
        )
    config = getattr(world, "generic_config", {}) or {}
    configured = {a["id"]: a for a in config.get("agents", [])}
    for row in rows:
        agent = configured.get(row["agent_id"])
        if agent:
            provider = config.get("providers", {}).get(agent.get("provider"), {})
            row.update(display_name=agent.get("display_name", row["display_name"]),
                       tools=list(agent.get("tools", [])),
                       permissions=sorted(set(agent.get("permissions", [])) | set(config.get("governance", {}).get("role_permissions", {}).get(agent.get("role"), []))),
                       provider=agent.get("provider", ""),
                       model=agent.get("model") or provider.get("default_model", ""))
    return rows


def _public_tasks(world: Any) -> list[dict[str, Any]]:
    agents = set((getattr(world, "agents", {}) or {}).keys())
    tasks = set((getattr(world, "tasks", {}) or {}).keys())
    rows: list[dict[str, Any]] = []
    for task_id, task in sorted((getattr(world, "tasks", {}) or {}).items()):
        row: dict[str, Any] = {
            "task_id": _text(task_id),
            "title": _text(getattr(task, "title", None), _text(task_id)),
            "description": _text(getattr(task, "description", None)),
            "status": _enum_text(getattr(task, "status", None), "open"),
            "priority": int(getattr(task, "priority", 0) or 0),
            "dependencies": _unique_strings(
                getattr(task, "dependencies", ()) or (), allowed=tasks
            ),
            "visibility": _enum_text(getattr(task, "visibility", None), "team"),
        }
        owner = _text(getattr(task, "owner_id", None)).strip()
        if owner in agents:
            row["owner_id"] = owner
        for key in ("deadline_tick", "estimated_effort", "actual_effort", "progress_score"):
            value = _number(getattr(task, key, None))
            if value is not None:
                row[key] = value
        rows.append(row)
    configured = {task["id"]: task for task in (getattr(world, "generic_config", {}) or {}).get("tasks", [])}
    for row in rows:
        spec = configured.get(row["task_id"], {})
        for key in ("collaborators", "expected_deliverables", "acceptance_criteria", "input_artifacts"):
            row[key] = _unique_strings(item for item in spec.get(key, []) if isinstance(item, str))
    return rows


def _private_governance(world: Any) -> bool:
    config = getattr(world, "generic_config", {}) or {}
    return config.get("governance", {}).get("decision_visibility") in {"private", "roles", "members"}


def _public_proposals(world: Any) -> list[dict[str, Any]]:
    agents = set((getattr(world, "agents", {}) or {}).keys())
    rows: list[dict[str, Any]] = []
    for proposal_id, proposal in sorted(
        (getattr(getattr(world, "proposal_manager", None), "proposals", {}) or {}).items()
    ):
        row: dict[str, Any] = {
            "proposal_id": _text(proposal_id),
            "proposal_type": _text(getattr(proposal, "proposal_type", None), "proposal"),
            "title": _text(getattr(proposal, "title", None), _text(proposal_id)),
            "summary": _text(getattr(proposal, "summary", None)),
            "status": _text(getattr(proposal, "status", None), "pending"),
            "required_actions": _unique_strings(
                getattr(proposal, "required_actions", ()) or ()
            ),
            "expected_benefits": _unique_strings(
                getattr(proposal, "expected_benefits", ()) or ()
            ),
            "expected_costs": _unique_strings(getattr(proposal, "expected_costs", ()) or ()),
            "risks": _unique_strings(getattr(proposal, "risks", ()) or ()),
            "failure_modes": _unique_strings(getattr(proposal, "failure_modes", ()) or ()),
            "approval_required_from": _unique_strings(
                getattr(proposal, "approval_required_from", ()) or (), allowed=agents
            ),
            "approved_by": _unique_strings(
                getattr(proposal, "approved_by", ()) or (), allowed=agents
            ),
            "supporters": _unique_strings(
                getattr(proposal, "supporters", ()) or (), allowed=agents
            ),
            "rejected_by": _unique_strings(
                getattr(proposal, "rejected_by", ()) or (), allowed=agents
            ),
            "opposers": _unique_strings(
                getattr(proposal, "opposers", ()) or (), allowed=agents
            ),
        }
        for key in ("source_wish_ids", "source_episode_ids", "source_event_ids"):
            row[key] = _unique_strings(getattr(proposal, key, ()) or ())
        proposer = _text(getattr(proposal, "proposer_agent_id", None)).strip()
        if proposer in agents:
            row["proposer_agent_id"] = proposer
        for key in (
            "target_problem",
            "proposed_solution",
            "rejection_reason",
            "suggested_revision",
            "family",
            "source_wish_id", "source_reflection_id", "source_episode_id",
            "object_created_id", "amends_protocol_id", "repair_kind",
            "repair_target_protocol_id",
        ):
            value = _text(getattr(proposal, key, None)).strip()
            if value:
                row[key] = value
        for key in (
            "feasibility_score",
            "usefulness_score",
            "risk_score",
            "adoption_score",
            "created_at_tick",
            "updated_at_tick",
            "adopted_tick",
        ):
            value = _number(getattr(proposal, key, None))
            if value is not None:
                row[key] = value
        rows.append(row)
    if _private_governance(world):
        public_keys = {"proposal_id", "proposal_type", "status", "source_wish_id", "source_wish_ids",
                       "source_reflection_id", "source_episode_id", "source_episode_ids",
                       "object_created_id", "created_at_tick", "updated_at_tick", "adopted_tick",
                       "amends_protocol_id", "repair_kind", "repair_target_protocol_id"}
        rows = [{key: value for key, value in row.items() if key in public_keys} for row in rows]
    return rows


def _public_protocols(world: Any) -> list[dict[str, Any]]:
    agents = set((getattr(world, "agents", {}) or {}).keys())
    registry = getattr(world, "protocol_registry", None)
    rows: list[dict[str, Any]] = []
    for protocol_id, protocol in sorted((getattr(registry, "protocols", {}) or {}).items()):
        row: dict[str, Any] = {
            "protocol_id": _text(protocol_id),
            "protocol_type": _text(getattr(protocol, "protocol_type", None), "protocol"),
            "rule_summary": _text(getattr(protocol, "rule_summary", None)),
            "scope": _text(getattr(protocol, "scope", None)),
            "target_process": _text(getattr(protocol, "target_process", None)),
            "supporters": _unique_strings(
                getattr(protocol, "supporters", ()) or (), allowed=agents
            ),
            "opposers": _unique_strings(
                getattr(protocol, "opposers", ()) or (), allowed=agents
            ),
            "adoption_status": _text(getattr(protocol, "adoption_status", None), "pending"),
            "usage_events": _unique_strings(getattr(protocol, "usage_events", ()) or ()),
            "violation_events": _unique_strings(
                getattr(protocol, "violation_events", ()) or ()
            ),
            "enforcement_events": _unique_strings(
                getattr(protocol, "enforcement_events", ()) or ()
            ),
            "status": _text(getattr(protocol, "status", None), "active"),
            "emergence_level": _enum_text(getattr(protocol, "emergence_level", None), "none"),
        }
        proposer = _text(getattr(protocol, "proposer_id", None)).strip()
        if proposer in agents:
            row["proposer_id"] = proposer
        specs = getattr(getattr(world, "proposal_manager", None), "protocol_specs", {}) or {}
        spec_id = next((key for key, value in getattr(world, "_generic_protocol_registry_ids", {}).items()
                        if value == protocol_id), protocol_id)
        spec = specs.get(spec_id)
        if spec is None and protocol_id.startswith("proto_spec_"):
            spec = specs.get("protospec_" + protocol_id.removeprefix("proto_spec_"))
        if spec is not None:
            row["spec_id"] = spec.protocol_id
            for key in ("created_from_proposal_id", "trigger_condition", "enforcement_rule", "sunset_rule"):
                value = getattr(spec, key, None)
                if value:
                    row[key] = str(value)
            for key in ("required_steps", "required_fields", "affected_agents",
                        "affected_actions", "affected_artifacts"):
                row[key] = _unique_strings(getattr(spec, key, ()) or ())
            row["enforcement_action"] = _text(getattr(spec, "enforcement_action", None), "block")
            row["version"] = int(getattr(spec, "revision", 0)) + 1
            for key in ("source_episode_ids", "source_wish_ids"):
                row[key] = _unique_strings(getattr(spec, key, []) or [])
            for key in ("source_episode_id", "source_wish_id", "source_reflection_id"):
                if getattr(spec, key, None):
                    row[key] = str(getattr(spec, key))
        origins = getattr(world, "protocol_origins", {}) or {}
        row["origin"] = origins.get(protocol_id, getattr(protocol, "origin", "emergent"))
        for key in ("created_from_proposal_id", "revision"):
            value = getattr(protocol, key, None)
            if value is not None:
                row[key] = str(value)
        row["revisions"] = []
        for event in getattr(registry, "events", ()) or ():
            if getattr(event, "protocol_id", None) != protocol_id:
                continue
            kind = str(getattr(event, "event_type", ""))
            data = getattr(event, "data", {}) or {}
            if "amend" in kind or "revis" in kind:
                revision = {"event_id": str(event.event_id), "tick": int(event.tick)}
                for key in ("revision_kind", "source_proposal_id"):
                    if data.get(key):
                        revision[key] = str(data[key])
                row["revisions"].append(revision)
            if "retir" in kind or "deprecat" in kind or "obsolet" in kind:
                row["retired_tick"] = int(event.tick)
        for key in ("first_tick", "last_active_tick", "persistence_ticks", "retired_tick"):
            value = _number(getattr(protocol, key, None))
            if value is not None:
                row[key] = value
        metrics = getattr(protocol, "impact_metrics", None)
        if isinstance(metrics, Mapping):
            row["impact_metrics"] = {
                _text(key): value
                for key, value in metrics.items()
                if _number(value) is not None
            }
        rows.append(row)
    if _private_governance(world):
        for row in rows:
            for key in ("proposer_id", "supporters", "opposers"):
                row.pop(key, None)
    return rows


def _public_episodes(world: Any) -> list[dict[str, Any]]:
    agents = set((getattr(world, "agents", {}) or {}).keys())
    rows: list[dict[str, Any]] = []
    manager = getattr(world, "episode_manager", None)
    for episode_id, episode in sorted((getattr(manager, "episodes", {}) or {}).items()):
        timeline: list[dict[str, Any]] = []
        for item in getattr(episode, "timeline", ()) or ():
            if not isinstance(item, Mapping):
                continue
            tick = _number(item.get("tick"))
            if not isinstance(tick, int):
                continue
            timeline.append(
                {
                    "tick": tick,
                    "event_id": _text(item.get("event_id"), f"{episode_id}@{tick}"),
                    "event_type": _text(item.get("family"), "source_event"),
                    "actor_id": _text(item.get("actor_id")),
                }
            )
        row: dict[str, Any] = {
            "episode_id": _text(episode_id),
            "episode_type": _text(getattr(episode, "episode_type", None), "episode"),
            "title": _text(getattr(episode, "title", None), _text(episode_id)),
            "status": _text(getattr(episode, "status", None), "open"),
            "start_tick": int(getattr(episode, "start_tick", 0) or 0),
            "participants": _unique_strings(
                getattr(episode, "participants", ()) or (), allowed=agents
            ),
            "linked_event_ids": _unique_strings(
                getattr(episode, "linked_event_ids", ()) or ()
            ),
            "linked_task_ids": _unique_strings(
                getattr(episode, "linked_task_ids", ()) or ()
            ),
            "linked_protocol_ids": _unique_strings(
                getattr(episode, "linked_protocol_ids", ()) or ()
            ),
            "produced_protocols": _unique_strings(
                getattr(episode, "produced_protocols", ()) or ()
            ),
            "timeline": timeline,
        }
        end_tick = _number(getattr(episode, "end_tick", None))
        if isinstance(end_tick, int):
            row["end_tick"] = end_tick
        for key in ("problem_statement", "decision_summary", "outcome_summary"):
            value = _text(getattr(episode, key, None)).strip()
            if value:
                row[key] = value
        rows.append(row)
    return rows


def _public_decisions(world: Any, *, action_start: int) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    actions = list(getattr(world, "action_log", ()) or ())
    for offset, action in enumerate(actions[action_start:], start=action_start):
        if not isinstance(action, Mapping):
            continue
        agent_id = _text(action.get("agent_id")).strip()
        if agent_id not in (getattr(world, "agents", {}) or {}):
            continue
        tick = action.get("tick")
        if not isinstance(tick, int):
            continue
        decisions.append(
            {
                "decision_id": f"source-action-{offset:06d}",
                "tick": tick,
                "agent_id": agent_id,
                "chosen_action_id": _text(action.get("action_type"), "source_action"),
                "chosen_object_id": None,
            }
        )
    return decisions


def _public_governance_events(world: Any, *, event_start: int) -> list[dict[str, Any]]:
    agents = set((getattr(world, "agents", {}) or {}).keys())
    registry = getattr(world, "protocol_registry", None)
    protocols = set((getattr(registry, "protocols", {}) or {}).keys())
    projected: list[dict[str, Any]] = []
    for event in list(getattr(registry, "events", ()) or ())[event_start:]:
        protocol_id = _text(getattr(event, "protocol_id", None)).strip()
        if protocol_id not in protocols:
            continue
        tick = _number(getattr(event, "tick", None))
        if not isinstance(tick, int):
            continue
        row: dict[str, Any] = {
            "event_id": _text(getattr(event, "event_id", None), f"protocol@{tick}"),
            "event_type": _text(getattr(event, "event_type", None), "source_protocol_event"),
            "protocol_id": protocol_id,
            "tick": tick,
            # Source event data can refer to private workspace / evaluator
            # identifiers.  The fact and kind of lifecycle transition are the
            # public projection; detailed data stays in the source ledger.
            "data": {},
        }
        data = getattr(event, "data", {}) or {}
        if isinstance(data, Mapping):
            for key in ("amendment_id", "context_id", "episode_id", "task_id",
                        "violation_event_id", "revision_kind", "source_proposal_id"):
                if isinstance(data.get(key), str):
                    row["data"][key] = data[key]
        actor_id = _text(getattr(event, "actor_id", None)).strip()
        if actor_id in agents:
            row["actor_id"] = actor_id
        projected.append(row)
    if _private_governance(world):
        for row in projected:
            row.pop("actor_id", None)
            row["data"] = {}
    return projected


def _public_lineage(world: Any) -> list[dict[str, Any]]:
    """Publish identifiers and relationships, never reflection or wish text."""
    manager = getattr(world, "reflection_manager", None)
    rows = []
    for kind, collection, id_key in (("reflection", "reflections", "reflection_id"),
                                      ("wish", "wishes", "wish_id")):
        for key, item in (getattr(manager, collection, {}) or {}).items():
            row = {"lineage_id": str(key), "kind": kind,
                   "agent_id": str(getattr(item, "agent_id", "")),
                   "tick": int(getattr(item, "tick", getattr(item, "created_at_tick", 0)))}
            for field in ("source_episode_ids", "source_event_ids", "created_wish_ids",
                          "source_reflection_ids", "generated_proposal_ids", "related_episode_ids"):
                values = getattr(item, field, None)
                if values:
                    row[field] = _unique_strings(values)
            for field in ("source_reflection_id", "source_episode_id", "status"):
                value = getattr(item, field, None)
                if value:
                    row[field] = str(value)
            rows.append(row)
    if getattr(world, "generic_config", None):
        for key, tool in getattr(world.proposal_manager, "tools", {}).items():
            row = {"lineage_id": str(key), "kind": "tool",
                   "tick": int(getattr(tool, "adopted_at_tick", 0)),
                   "status": str(getattr(tool, "status", "active")),
                   "source_episode_ids": _unique_strings(getattr(tool, "source_episode_ids", []))}
            for field in ("created_from_proposal_id", "source_wish_id", "source_episode_id"):
                if value := getattr(tool, field, None):
                    row[field] = str(value)
            if creator := getattr(tool, "creator_agent_id", None):
                row["agent_id"] = str(creator)
            rows.append(row)
    return rows


def _public_config(world: Any) -> dict[str, Any] | None:
    config = getattr(world, "generic_config", None)
    if not config:
        return None
    org = config.get("organization", {})
    governance = config.get("governance", {})
    learning = config.get("learning", {})
    lifecycle = getattr(world, "generic_lifecycle", None)
    # Deliberate allowlists: provider endpoints, prompt text, plugin arguments,
    # memory, workspace paths, and secret environment values stay local.
    observability = config.get("observability", {})
    if not isinstance(observability, Mapping):
        observability = {}
    runtime_learning = getattr(lifecycle, "learning", None)
    if isinstance(runtime_learning, Mapping):
        reflection = getattr(lifecycle, "reflection", {})
        wish = getattr(lifecycle, "wish", {})
        proposal = getattr(lifecycle, "proposal", {})
        protocol_learning = getattr(lifecycle, "protocol_learning", {})
        # These are the same names exposed by the generic configuration, but
        # their values come from GenericLifecycle after its switch merge.
        feature_flags = {
            key: runtime_learning.get(key, True)
            for key in (
                "profile_conditioning",
                "capability_learning",
                "institutionalization",
                "company_skill_memory",
                "governance_approval",
                "executable_workflow",
                "external_signal_loop",
            )
        }
        feature_flags.update(
            reflection=reflection.get("enabled", True),
            wish_extraction=wish.get("enabled", True),
            proposal_generation=proposal.get("enabled", True),
            protocol_formation=protocol_learning.get("enabled", True),
            retirement=protocol_learning.get("retirement_enabled", True),
        )
        manager = getattr(world, "proposal_manager", None)
        allow_retirement = getattr(manager, "protocol_allow_retirement", True)
        feature_flags["retirement"] = (
            feature_flags["retirement"] is True and allow_retirement is True
        )
    else:
        # Keep compatibility with older lightweight projection fixtures that
        # have no mounted generic lifecycle.
        feature_flags = {
            key: value for key, value in learning.items() if isinstance(value, bool)
        }
        reflection = learning.get("reflection", {})
    summary = {"inspector_enabled": observability.get("inspector", True),
               "description": org.get("description", ""),
               "channels": list(org.get("channels", [])),
               "decision_mode": config.get("runtime", {}).get("decision_mode", "profile_policy"),
               "features_enabled": sorted(k for k, v in feature_flags.items() if v is True),
               "features_disabled": sorted(k for k, v in feature_flags.items() if v is False)}
    for key in ("approval_mode", "deadlock_behavior", "decision_visibility"):
        if isinstance(governance.get(key), str):
            summary[key] = governance[key]
    for key in ("protocol_quorum", "quorum", "proposal_review_delay_ticks",
                "protocol_adoption_threshold", "amendment_threshold"):
        if isinstance(governance.get(key), (int, float)):
            summary[key] = governance[key]
    for key in ("approver_roles", "approver_members"):
        if key in governance:
            summary[key] = list(governance[key])
    summary["role_permissions"] = {role: list(values) for role, values in governance.get("role_permissions", {}).items()}
    if isinstance(reflection, Mapping):
        summary["reflection_enabled"] = reflection.get("enabled", True)
        summary["reflection_cadence_ticks"] = reflection.get("cadence_ticks", 6)
    if isinstance(runtime_learning, Mapping):
        summary["retirement_behavior"] = getattr(
            lifecycle, "retirement_behavior", "review"
        )
        summary["retirement_enabled"] = feature_flags["retirement"]
        for key, default in (
            ("public_trace", True),
            ("local_debug", False),
            ("token_logging", True),
            ("cost_logging", True),
            ("redact_secrets", True),
        ):
            value = observability.get(key, default)
            summary[key] = value if isinstance(value, bool) else default
    return summary


def project_public_frame(
    world: Any,
    *,
    sequence: int,
    organization_id: str,
    organization_name: str,
    action_start: int,
    protocol_event_start: int,
) -> dict[str, Any]:
    """Create one strict public frame from a source world at its current tick."""

    tick = int(getattr(world, "world_tick", 0) or 0)
    frame = {
        "frame_id": f"frame_{sequence:06d}",
        "sequence": sequence,
        "tick": tick,
        "organization": {
            "organization_id": organization_id,
            "name": organization_name,
            "tick": tick,
            "agents": _public_agents(world),
            "tasks": _public_tasks(world),
            "proposals": _public_proposals(world),
            "protocols": _public_protocols(world),
        },
        # Source event payloads may contain private data.  Real selected and
        # executed actions are exposed through ``decisions`` below instead of
        # inventing synthetic event rows.
        "events": [],
        "episodes": _public_episodes(world),
        "decisions": _public_decisions(world, action_start=action_start),
        "governance_events": _public_governance_events(
            world, event_start=protocol_event_start
        ),
    }
    frame["tool_events"] = [
        {"event_id": str(event.get("event_id", f"tool-use-{index}")),
         "event_type": "tool_use" if event.get("type") == "tool_use_event" else "tool_execution",
         "tick": tick, "actor_id": str(event.get("actor_id", event.get("agent_id", ""))),
         "tool_id": str(event["tool_id"]), "status": str(event.get("status", "completed")),
         **({"error_type": str(event["error_type"])} if "error_type" in event else {})}
        for index, event in enumerate(getattr(world, "events", []))
        if isinstance(event, dict) and event.get("type") in {"tool_execution", "tool_use_event"}
        and event.get("tick") == tick and "tool_id" in event
    ]
    frame["lineage"] = _public_lineage(world)
    if getattr(world, "generic_config", None):
        # Structural public records only. Shared file bodies are available in
        # the local run workspace, never in a portable public trace.
        frame["artifacts"] = [
            {"artifact_id": f"artifact:{file.object_id}", "artifact_type": _text(file.file_type, "doc"),
             "title": _text(file.title), "version": int(file.version),
             "status": "available", "related_task_ids": _unique_strings(file.linked_task_ids)}
            for file in (getattr(getattr(world, "company", None), "files", {}) or {}).values()
            if _enum_text(getattr(file, "visibility", None)) in {"team", "public"}
        ]
    summary = _public_config(world)
    if summary is not None:
        frame["organization"]["config_summary"] = summary
    return frame


__all__ = ["project_public_frame"]
