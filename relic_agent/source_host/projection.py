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
    return rows


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
        proposer = _text(getattr(proposal, "proposer_agent_id", None)).strip()
        if proposer in agents:
            row["proposer_agent_id"] = proposer
        for key in (
            "target_problem",
            "proposed_solution",
            "rejection_reason",
            "suggested_revision",
            "family",
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
        for key in ("first_tick", "last_active_tick", "persistence_ticks"):
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
        actor_id = _text(getattr(event, "actor_id", None)).strip()
        if actor_id in agents:
            row["actor_id"] = actor_id
        projected.append(row)
    return projected


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
    return {
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


__all__ = ["project_public_frame"]
