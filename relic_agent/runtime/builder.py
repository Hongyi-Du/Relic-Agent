"""Initial-world builders and generic domain adapters for the shared OrgWorld.

Generic mode replaces seed data and the domain action menu. It inherits
OrgWorld.step unchanged, including appraisal, memory, episodes, reflection,
proposals, governance, protocol detectors, and capability learning.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path

from agent_sdk.lived.core.contracts import ActionCandidate
from environments.org_env.backend.agents import OrgAgent
from environments.org_env.backend.entities.work import Task, TaskStatus, Document
from environments.org_env.backend.simulation import OrgWorld
from environments.org_env.backend.workspace.personal import PersonalWorkspace
from environments.org_env.backend.workspace.objects import FileObject, Visibility
from environments.org_env.config.scenarios import default_scenario
from environments.org_env.experiments.ablations import EXTERNAL_BRIDGE, resolve_mechanism_ablations
from environments.org_env.runtime_adapter.execution import ExecutionResult
from environments.org_env.runtime_adapter.feature_extractor import OrgFeatureExtractor
from relic_agent.config_schema import TaskSpec, _import_plugin
from relic_agent.runtime.providers import ProviderRegistry, ProviderError
from relic_agent.runtime.tools import GenericToolRegistry

_TERMINAL = {"done", "completed", "merged", "released"}

# Learned tools are intentionally small compositions of already-supported
# generic actions.  Keep this allow-list local to the generic adapter: a
# ToolSpec cannot name an arbitrary Python method or source-product action.
_LEARNED_TASK_ACTIONS = {"claim_task", "work_on_task", "review_doc", "complete_task"}
_LEARNED_BUILTIN_ACTIONS = {
    "files": "files",
    "files.read": "files",
    "files.write": "files",
    "task_board": "task_board",
    "task_board.claim": "task_board",
    "task_board.complete": "task_board",
    "messaging": "messaging",
    "search": "search",
    "send_message": "messaging",
}


def _status(task):
    return str(getattr(task.status, "value", task.status))


def _artifact_id(value):
    return str(value.get("id", value.get("name", "deliverable"))) if isinstance(value, dict) else str(value)


class GenericActionMapper:
    def to_core_candidates(self, perception, world):
        aid = perception.agent_id
        configured = world.agent_config[aid]
        tools = set(configured["tools"])
        learned = list(getattr(world.tool_registry, "learned_tools_for", lambda _aid: [])(aid))
        candidates = []
        for task_id, task in world.tasks.items():
            if _status(task) in _TERMINAL:
                continue
            spec = world.task_specs[task_id]
            ready = all(_status(world.tasks[key]) in _TERMINAL for key in task.dependencies)
            ready = ready and all(_artifact_id(item) in world.company.files and
                                  world.company.files[_artifact_id(item)].visible_to(aid, members=world.company.members)
                                  for item in spec["input_artifacts"])
            if not ready:
                continue
            required_tools = {call["tool"] for call in spec["metadata"].get("tool_calls", [])}
            if task.owner_id is None and aid not in spec["collaborators"] and "task_board" in tools and required_tools <= tools:
                candidates.append(ActionCandidate("claim_task", {"task_id": task_id}))
            if task.owner_id == aid:
                for index, call in enumerate(spec["metadata"].get("tool_calls", [])):
                    if (task_id, index) not in world.completed_tool_calls and call["tool"] in tools:
                        candidates.append(ActionCandidate("use_tool", {
                            "task_id": task_id, "tool": call["tool"],
                            "arguments": call.get("arguments", {}), "call_index": index}))
                if world.action_selection_mode == "llm_direct":
                    for tool_id in sorted(tools & set(world.tool_registry.functions)):
                        candidates.append(ActionCandidate("use_tool", {"task_id": task_id, "tool": tool_id}))
                for tool in learned:
                    if tool.required_actions and _learned_tool_needs_task(tool):
                        candidates.append(ActionCandidate("use_tool", {
                            "task_id": task_id, "tool_id": tool.tool_id}))
                if "files" in tools and (not world.deliverables_ready(task_id) or task_id in world.task_revision_requests):
                    candidates.append(ActionCandidate("work_on_task", {"task_id": task_id}))
                elif "task_board" in tools:
                    candidates.append(ActionCandidate("complete_task", {"task_id": task_id}))
            elif (aid in spec["collaborators"] and "files" in tools
                  and "review" in world.tool_registry.permissions(aid) and world.deliverables_ready(task_id)):
                if aid not in world.current_task_reviews(task_id):
                    candidates.append(ActionCandidate("review_doc", {"task_id": task_id}))
        permissions = world.tool_registry.permissions(aid)
        for proposal in world.proposal_manager.proposals.values():
            if (world.approval_mode != "auto" and proposal.status == "under_review" and aid in proposal.approval_required_from
                    and aid not in proposal.approved_by and aid not in proposal.rejected_by
                    and "approve_protocol" in permissions):
                candidates.append(ActionCandidate("approve_proposal", {"proposal_id": proposal.proposal_id}))
                candidates.append(ActionCandidate("reject_proposal", {"proposal_id": proposal.proposal_id}))
        # A composed tool that only uses task-independent generic operations can
        # be called without an owned task.  Task-bound compositions are offered
        # in the owner branch above, where their task context is explicit.
        for tool in learned:
            if tool.required_actions and not _learned_tool_needs_task(tool):
                candidates.append(ActionCandidate("use_tool", {"tool_id": tool.tool_id}))
        if not candidates and "messaging" in tools and world.comm.channels:
            channel = next((name for name, value in world.comm.channels.items() if aid in value.members), None)
            if channel is not None:
                candidates.append(ActionCandidate("send_message", {"channel": channel}))
        return candidates


def _learned_tool_needs_task(tool) -> bool:
    """Whether a ToolSpec composition needs a task context to execute."""
    return any(
        str(action).strip() in _LEARNED_TASK_ACTIONS
        or str(action).strip() in {"files.write", "task_board.complete", "task_board.claim"}
        for action in (getattr(tool, "required_actions", []) or [])
    )


class GenericFeatures(OrgFeatureExtractor):
    def extract(self, candidate, perception, world):
        features = super().extract(candidate, perception, world)
        agent = world.agents[perception.agent_id]
        # Unknown role names retain skill-based selection without a closed role enum.
        features.skill_match = max(agent.skills.values(), default=0.5)
        task_id = (candidate.parameters or {}).get("task_id")
        task = world.tasks.get(task_id) if task_id else None
        if task is not None:
            # ``Task.deadline_tick`` is an absolute simulation tick.  The
            # source extractor historically filled deadline_urgency from task
            # priority, which made two otherwise identical deadlines
            # indistinguishable.  Urgency now rises as the due tick approaches
            # and remains maximal after it passes.
            deadline = getattr(task, "deadline_tick", None)
            if deadline is not None:
                remaining = int(deadline) - int(getattr(world, "world_tick", 0))
                features.deadline_urgency = (
                    1.0 if remaining <= 0 else 1.0 / (float(remaining) + 1.0)
                )
            features.task_priority = float(getattr(task, "priority", 3)) / 5.0
        if candidate.action_type in {"claim_task", "complete_task", "work_on_task", "review_doc", "use_tool"}:
            features.progress_gain = 0.8
        return features


class GenericExecution:
    def execute(self, aid, candidate, world):
        action = candidate.action_type
        params = candidate.parameters
        task_id = params.get("task_id")
        result = ExecutionResult(action_id=f"generic-{world.world_tick}-{aid}-{len(world.action_log)}",
                                 agent_id=aid, action_type=action)
        try:
            lifecycle = getattr(world, "generic_lifecycle", None)
            if lifecycle and hasattr(lifecycle, "before_action"):
                if lifecycle.before_action(aid, action, task_id) is False:
                    raise PermissionError("protocol blocked action")
            if action == "claim_task":
                outcome = world.tool_registry.execute(aid, "task_board", {"operation": "claim", "task_id": task_id})
            elif action == "work_on_task":
                outcome = world.work_on_generic_task(aid, task_id)
            elif action == "review_doc":
                outcome = world.review_generic_task(aid, task_id)
            elif action == "complete_task":
                if "task_board" not in set(world.agent_config[aid].get("tools", [])):
                    outcome = {"status": "failed", "error_type": "PermissionError"}
                else:
                    # GenericExecution owns the lifecycle boundary for this
                    # explicit action; calling the low-level builtin here
                    # would invoke the completion hook a second time.
                    had_marker = getattr(world, "_generic_completion_lifecycle_active", False)
                    world._generic_completion_lifecycle_active = True
                    try:
                        outcome = world.tool_registry.execute(
                            aid, "task_board", {"operation": "complete", "task_id": task_id}
                        )
                    finally:
                        if had_marker:
                            world._generic_completion_lifecycle_active = had_marker
                        else:
                            del world._generic_completion_lifecycle_active
            elif action == "use_tool":
                learned_id = params.get("tool_id")
                if learned_id:
                    outcome = self._execute_learned_tool(aid, learned_id, params, task_id, world, result)
                else:
                    tool_id = params["tool"]
                    arguments = params.get("arguments")
                    if arguments is None:
                        arguments = world.provider_registry.generate_json_for_agent(
                            aid, world.agent_prompt(aid, task_id), f"Choose arguments for tool {tool_id}",
                            world.tool_registry.specs[tool_id].get("schema", {"type": "object"}))
                    outcome = world.tool_registry.execute(aid, tool_id, arguments)
                    if outcome["status"] == "completed" and "call_index" in params:
                        world.completed_tool_calls.add((task_id, params["call_index"]))
            elif action in {"approve_proposal", "reject_proposal"}:
                proposal = world.proposal_manager.proposals[params["proposal_id"]]
                if aid not in proposal.approval_required_from or "approve_protocol" not in world.tool_registry.permissions(aid):
                    raise PermissionError("agent cannot review this proposal")
                if action == "approve_proposal":
                    world.proposal_manager.approve_proposal(proposal.proposal_id, aid, world)
                else:
                    world.proposal_manager.reject_proposal(proposal.proposal_id, aid, "agent_rejected", world)
                outcome = {"status": "completed"}
            elif action == "send_message":
                outcome = world.tool_registry.execute(aid, "messaging", {
                    "channel": params["channel"],
                    "text": params.get("text") or
                    f"{world.agents[aid].name} is available to coordinate shared work."})
            else:
                raise ValueError("unsupported generic action")
            result.success = outcome.get("status") == "completed"
            result.failure_reason = "" if result.success else outcome.get("error_type", "requirements_pending")
            if task_id and task_id not in result.modified_objects and task_id not in result.created_objects:
                result.modified_objects.append(task_id)
            event = {"type": "task_progress_event" if task_id else "communication_event",
                     "event_id": result.action_id, "agent_id": aid, "tick": world.world_tick,
                     "task_id": task_id, "action": action, "success": result.success,
                     "summary": f"{action}: {'completed' if result.success else 'requirements pending'}"}
            result.events.append(event)
        except ProviderError:
            raise
        except Exception as exc:
            result.success = False
            result.failure_reason = type(exc).__name__
            result.events.append({"type": "conflict_event", "event_id": result.action_id,
                                  "agent_id": aid, "tick": world.world_tick,
                                  "task_id": task_id, "summary": f"{action}: {type(exc).__name__}"})
        if lifecycle and hasattr(lifecycle, "after_action"):
            lifecycle.after_action(aid, action, task_id, result)
        return result

    @staticmethod
    def _work_snapshot(world):
        """Capture only generic work state used to decide tool-use evidence."""
        tasks = tuple(sorted(
            (
                str(task_id),
                str(getattr(task.status, "value", task.status)),
                getattr(task, "owner_id", None),
                float(getattr(task, "progress_score", 0.0) or 0.0),
                len(getattr(task, "history", []) or []),
                len(getattr(task, "progress_evidence", []) or []),
            )
            for task_id, task in getattr(world, "tasks", {}).items()
        ))
        files = tuple(sorted(
            (
                str(file_id),
                int(getattr(file, "version", 0) or 0),
                str(getattr(file, "content_hash", "") or ""),
            )
            for file_id, file in getattr(getattr(world, "company", None), "files", {}).items()
        ))
        return tasks, files

    def _execute_learned_tool(self, aid, tool_id, params, task_id, world, result):
        """Run an adopted ToolSpec as a bounded composition of generic actions.

        ToolSpec metadata controls who may call the tool and what permissions it
        requires.  Each composed step then goes through the existing generic
        builtin/action grant checks; no source-product handler or dynamic Python
        callable is reachable from this path.
        """
        tool = world.tool_registry.learned_tool(tool_id)
        if tool is None or not world.tool_registry.can_use_learned_tool(aid, tool):
            raise PermissionError("learned tool is not granted to this agent")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        schema = getattr(tool, "input_schema", {}) or {}
        from relic_agent.runtime.tools import validate_arguments
        validate_arguments(arguments, schema)
        if not isinstance(arguments, dict):
            raise ValueError("learned tool arguments must be an object")

        before = self._work_snapshot(world)
        step_results = []
        for raw_action in list(getattr(tool, "required_actions", []) or []):
            action = str(raw_action).strip()
            if not action or action == "use_tool":
                raise ValueError("learned tool has an unsupported composed action")
            outcome = self._run_learned_step(aid, action, arguments, task_id, world, result)
            step_results.append(outcome)
            if outcome.get("status") != "completed":
                break
        if not step_results:
            return {"status": "failed", "error_type": "tool_declares_no_steps"}
        final = step_results[-1]
        after = self._work_snapshot(world)
        changed = before != after
        if changed:
            before_tasks, before_files = before
            after_tasks, after_files = after
            before_task_map = {row[0]: row for row in before_tasks}
            before_file_map = {row[0]: row for row in before_files}
            for row in after_tasks:
                if before_task_map.get(row[0]) != row and row[0] not in result.modified_objects:
                    result.modified_objects.append(row[0])
            for row in after_files:
                if row[0] not in before_file_map:
                    result.created_objects.append(row[0])
                elif before_file_map[row[0]] != row and row[0] not in result.modified_objects:
                    result.modified_objects.append(row[0])
        # Read-only operations and messages are real uses too. Preserve the
        # final status when a composition makes progress but cannot finish.
        if changed or any(step.get("status") == "completed" for step in step_results):
            touched = list(dict.fromkeys(result.modified_objects + result.created_objects))
            result.events.append({
                "type": "tool_use_event",
                "subtype": getattr(tool, "tool_type", "composed_action_tool"),
                "tool_id": tool.tool_id,
                "agent_id": aid,
                "tick": world.world_tick,
                "task_id": task_id,
                "object_id": touched[0] if touched else task_id,
                "affected": touched[:5],
                "status": final["status"],
                **({"error_type": final["error_type"]} if final.get("error_type") and final["status"] != "completed" else {}),
            })
            result.graph_edges.append((aid, "used_tool", tool.tool_id))
        return final

    def _run_learned_step(self, aid, action, arguments, task_id, world, result):
        lifecycle = getattr(world, "generic_lifecycle", None)
        normalized = action.casefold()
        # Completion already crosses the task-board lifecycle boundary. Other
        # composed task actions must obey the same protocol gates as direct work.
        if lifecycle is None or normalized not in {"claim_task", "work_on_task", "review_doc"}:
            return self._run_learned_operation(aid, action, arguments, task_id, world)
        permitted = lifecycle.before_action(aid, normalized, task_id)
        outcome = (self._run_learned_operation(aid, action, arguments, task_id, world)
                   if permitted else {"status": "failed", "error_type": "ProtocolBlocked"})
        step = ExecutionResult(action_id=result.action_id, agent_id=aid, action_type=normalized,
                               success=outcome.get("status") == "completed",
                               failure_reason=outcome.get("error_type", ""))
        lifecycle.after_action(aid, normalized, task_id, step)
        result.events.extend(step.events)
        result.graph_edges.extend(step.graph_edges)
        return outcome

    def _run_learned_operation(self, aid, action, arguments, task_id, world):
        """Execute one allow-listed generic step and preserve its public status."""
        normalized = action.casefold()
        if normalized in _LEARNED_TASK_ACTIONS:
            if not task_id:
                return {"status": "failed", "error_type": "TaskContextRequired"}
            required_tool = "task_board" if normalized in {"claim_task", "complete_task"} else "files"
            if required_tool not in set(world.agent_config[aid].get("tools", [])):
                return {"status": "failed", "error_type": "PermissionError"}
            if normalized == "review_doc" and "review" not in world.tool_registry.permissions(aid):
                return {"status": "failed", "error_type": "PermissionError"}
            if normalized == "claim_task":
                return world.tool_registry.execute(aid, "task_board", {
                    "operation": "claim", "task_id": task_id,
                    **({key: value for key, value in arguments.items() if key in {"owner"}}),
                })
            if normalized == "work_on_task":
                return world.work_on_generic_task(aid, task_id)
            if normalized == "review_doc":
                return world.review_generic_task(aid, task_id)
            return world.tool_registry.execute(aid, "task_board", {
                "operation": "complete", "task_id": task_id,
            })

        builtin = _LEARNED_BUILTIN_ACTIONS.get(normalized)
        operation = None
        if builtin is None and "." in normalized:
            base, operation = normalized.split(".", 1)
            builtin = _LEARNED_BUILTIN_ACTIONS.get(base)
        if builtin is not None:
            configured = set(world.agent_config[aid].get("tools", []))
            if builtin not in configured:
                return {"status": "failed", "error_type": "PermissionError"}
            payload = deepcopy(arguments)
            if operation and "operation" not in payload:
                payload["operation"] = operation
            if builtin == "messaging" and "channel" not in payload:
                payload["channel"] = "general"
            return world.tool_registry.execute(aid, builtin, payload)

        # A configured plugin is still a valid generic action.  It is resolved
        # through the existing registry, never by importing a ToolSpec field.
        configured = set(world.agent_config[aid].get("tools", []))
        if action in configured and action in world.tool_registry.functions:
            return world.tool_registry.execute(aid, action, deepcopy(arguments))
        return {"status": "failed", "error_type": "UnsupportedLearnedAction"}


class GenericOrgWorld(OrgWorld):
    """A configurable domain with the original shared simulation step."""

    def _record_deadline_events(self, tick: int) -> None:
        """Record each missed task deadline once without blocking late work."""
        seen = self.__dict__.setdefault("_generic_overdue_deadlines", set())
        for task_id, task in self.tasks.items():
            deadline = getattr(task, "deadline_tick", None)
            if deadline is None or int(tick) <= int(deadline) or task_id in seen:
                continue
            completion_ticks = [
                int(entry.get("tick", tick))
                for entry in (getattr(task, "history", []) or [])
                if entry.get("event") == "completed"
            ]
            # A task completed on or before its due tick was never overdue,
            # even though the next tick may now be past that historical date.
            if completion_ticks and min(completion_ticks) <= int(deadline):
                continue
            overdue = int(tick) - int(deadline)
            event = {
                "type": "deadline_event",
                "subtype": "overdue",
                "event_id": f"deadline-{task_id}-{deadline}",
                "task_id": task_id,
                "deadline_tick": int(deadline),
                "tick": int(tick),
                "overdue_ticks": overdue,
                "completed_late": bool(completion_ticks),
            }
            self.events.append(event)
            seen.add(task_id)

    @property
    def action_selection_mode(self):
        return self.generic_config.get("runtime", {}).get("decision_mode", "profile_policy")

    def can_agent_act(self, agent, clock):
        schedule = self.agent_config[agent.id].get("work_schedule") or {}
        if not isinstance(schedule, dict):
            return True
        if "active_ticks" in schedule and self.world_tick not in schedule["active_ticks"]:
            return False
        hours = schedule.get("hours", schedule.get("working_hours"))
        if isinstance(hours, list) and clock.hour_in_day not in hours:
            return False
        if "weekdays" in schedule and clock.day_of_week not in schedule["weekdays"]:
            return False
        every = schedule.get("interval_ticks", 1)
        if self.world_tick % max(1, int(every)):
            return False
        if any(key in schedule for key in ("after_hours_responsiveness", "weekend_work_tendency")):
            return super().can_agent_act(agent, clock)
        return True

    def _constrain_candidates(self, agent, candidates, clock):
        # The generic mapper already enforces tool grants; schedules are checked
        # by can_agent_act. Product-only action/category restrictions do not apply.
        return candidates

    def _llm_decide(self, aid, agent, perception, candidates):
        # Read only the newest messages in channels this member can currently
        # access.  Mark them read after the provider actually receives them;
        # a failed request must not silently consume the inbox.
        unread = [message for message in self.comm.perceivable_messages(aid)
                  if aid not in message.read_by and message.sender_id != aid][-10:]
        context = {
            "tools": self.tool_registry.catalog(aid),
            "tasks": [{**spec, "owner": self.tasks[key].owner_id, "status": _status(self.tasks[key])}
                      for key, spec in self.task_specs.items()],
            "proposals": [{"id": proposal.proposal_id, "title": proposal.title,
                           "summary": proposal.summary, "adoption_score": proposal.adoption_score}
                          for proposal in self.proposal_manager.proposals.values()
                          if proposal.status == "under_review" and aid in proposal.approval_required_from],
            "available_actions": [{"index": i, "action": candidate.action_type, "arguments": candidate.parameters}
                                  for i, candidate in enumerate(candidates)],
            "new_messages": [{"id": message.message_id, "from": message.sender_id,
                              "channel": message.channel_id, "text": message.full_text[:1000],
                              "linked_objects": message.linked_objects[:10]}
                             for message in unread],
        }
        response = self.provider_registry.generate_json_for_agent(
            aid, self.agent_prompt(aid),
            json.dumps(context),
            {"type": "object", "properties": {"index": {"type": "integer"}, "arguments": {"type": "object"}}, "required": ["index"]})
        for message in unread:
            self.comm.mark_read(aid, message.message_id)
        index = response.get("index")
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(candidates):
            candidate = candidates[index]
            if candidate.action_type == "use_tool" and isinstance(response.get("arguments"), dict):
                candidate.parameters["arguments"] = response["arguments"]
            if candidate.action_type == "send_message" and isinstance(response.get("arguments"), dict):
                text = response["arguments"].get("text")
                if isinstance(text, str) and text.strip():
                    candidate.parameters["text"] = text[:2000]
            return candidate
        return None

    def agent_prompt(self, aid, task_id=None):
        config = self.generic_config
        agent = self.agent_config[aid]
        prompts = config["prompts"]
        context = {key: agent[key] for key in ("role", "skills", "initial_context", "metadata")}
        if self.profile_conditioning_enabled:
            context.update({key: agent[key] for key in ("profile", "failure_modes", "communication_style")})
        sections = [config["organization"]["name"], prompts["organization_brief"],
                    config["organization"]["brief"], config["organization"]["description"], json.dumps(config["organization"]["metadata"]), agent["role_mandate"],
                    json.dumps({key: config["organization"][key] for key in ("shared_goals", "topology")}),
                    json.dumps(context),
                    *prompts["global_grounding_rules"], prompts["agent_instruction_suffix"], *self.prompt_assets]
        if task_id:
            task = self.task_specs[task_id]
            template = prompts["task_context_template"]
            sections.append(template.format_map({**task, "task_id": task_id}) if template else json.dumps(task))
            sections.extend(note for (tid, _), note in self.private_review_notes.items() if tid == task_id)
            for reference in task["input_artifacts"]:
                file = self.company.files.get(_artifact_id(reference))
                if file and file.visible_to(aid, members=self.company.members):
                    sections.append(json.dumps({"input_artifact": file.object_id, "content": file.raw_payload}))
        return "\n".join(str(section) for section in sections if section)

    def deliverables_ready(self, task_id):
        spec = self.task_specs[task_id]
        expected = spec["expected_deliverables"] or [f"{task_id}-deliverable"]
        readers = set(spec["collaborators"])
        if self.tasks[task_id].owner_id:
            readers.add(self.tasks[task_id].owner_id)
        return all((file := self.company.files.get(_artifact_id(item))) is not None and
                   all(file.visible_to(aid, members=self.company.members) for aid in readers)
                   for item in expected)

    def work_on_generic_task(self, aid, task_id):
        task = self.tasks[task_id]
        if task.owner_id != aid:
            raise PermissionError("only owner can write task deliverables")
        spec = self.task_specs[task_id]
        task.status = TaskStatus.IN_PROGRESS
        client = self.provider_registry.client_for_agent(aid)
        route = self.provider_registry.resolve(aid)
        provider = self.generic_config["providers"][route.provider]
        expected = spec["expected_deliverables"] or [f"{task_id}-deliverable"]
        for item in expected:
            artifact_id = _artifact_id(item)
            if artifact_id in self.company.files and task_id not in self.task_revision_requests:
                continue
            if provider["type"] in {"mock", "deterministic"}:
                # A deterministic fixture still travels through its configured
                # model route, so mixed-provider routing is exercised offline.
                client.generate_text(self.agent_prompt(aid, task_id), f"Draft {artifact_id}")
                content = f"Deterministic draft: {task.title}\n{task.description}\nCriteria: {spec['acceptance_criteria']}"
            else:
                content = client.generate_text(self.agent_prompt(aid, task_id), f"Produce deliverable {artifact_id}")
            outcome = self.tool_registry.execute(aid, "files", {
                "operation": "write", "id": artifact_id, "title": artifact_id,
                "content": content, "task_ids": [task_id]})
            if outcome["status"] != "completed":
                return outcome
        self.task_revision_requests.discard(task_id)
        task.progress_score = 0.7
        task.progress_evidence.append({"kind": "deliverables", "tick": self.world_tick, "agent_id": aid})
        return {"status": "completed"}

    def review_generic_task(self, aid, task_id):
        spec = self.task_specs[task_id]
        if aid not in spec["collaborators"]:
            raise PermissionError("reviewer is not a collaborator")
        if "review" not in self.tool_registry.permissions(aid):
            raise PermissionError("review permission is required")
        return self._assess_generic_task(aid, task_id)

    def _assess_generic_task(self, aid, task_id):
        """Assess criteria through the selected reviewer or the sole owner's route."""
        client = self.provider_registry.client_for_agent(aid)
        route = self.provider_registry.resolve(aid)
        provider = self.generic_config["providers"][route.provider]
        prompt = "Review deliverables against every acceptance criterion; approve only when satisfied: " + json.dumps(
            [file.raw_payload for file in self.company.files.values() if task_id in file.linked_task_ids
             and file.visible_to(aid, members=self.company.members)])
        if provider["type"] in {"mock", "deterministic"}:
            feedback = client.generate_text(self.agent_prompt(aid, task_id), prompt)
            approved = True  # Explicit deterministic example review, not a model quality claim.
        else:
            review = client.generate_json(self.agent_prompt(aid, task_id), prompt,
                {"type": "object", "properties": {"approved": {"type": "boolean"}, "feedback": {"type": "string"}},
                 "required": ["approved", "feedback"]})
            approved, feedback = review["approved"] is True, str(review["feedback"])
        self.private_review_notes[(task_id, aid)] = feedback
        if approved:
            self.task_reviews.setdefault(task_id, {})[aid] = self._reviewed_artifacts(task_id)
        else:
            self.task_revision_requests.add(task_id)
            self.task_reviews[task_id] = {}
        self.tasks[task_id].progress_evidence.append({"kind": "review", "agent_id": aid, "tick": self.world_tick,
                                                    "approved": approved,
                                                    "artifact_versions": self.task_reviews.get(task_id, {}).get(aid, {})})
        return {"status": "completed" if approved else "pending", "error_type": "ChangesRequested"}

    def _reviewed_artifacts(self, task_id):
        """Version receipt for exactly the artifacts linked to this task."""
        return {file.object_id: (file.version, file.content_hash)
                for file in self.company.files.values() if task_id in file.linked_task_ids}

    def current_task_reviews(self, task_id):
        current = self._reviewed_artifacts(task_id)
        return {aid for aid, receipt in self.task_reviews.get(task_id, {}).items()
                if receipt == current}

    def complete_generic_task(self, aid, task_id, arguments):
        task = self.tasks[task_id]
        if task.owner_id != aid:
            raise PermissionError("only owner may complete task")
        calls = self.task_specs[task_id]["metadata"].get("tool_calls", [])
        if any((task_id, index) not in self.completed_tool_calls for index in range(len(calls))):
            return {"status": "pending", "error_type": "PendingTools"}
        if task_id in self.task_revision_requests:
            return {"status": "pending", "error_type": "ChangesRequested"}
        if not self.deliverables_ready(task_id):
            return {"status": "pending", "error_type": "MissingDeliverables"}
        if any(_status(self.tasks[key]) not in _TERMINAL for key in task.dependencies):
            return {"status": "pending", "error_type": "PendingDependencies"}
        if not set(self.task_specs[task_id]["collaborators"]) <= self.current_task_reviews(task_id):
            return {"status": "pending", "error_type": "PendingReview"}
        spec = self.task_specs[task_id]
        if not spec["collaborators"] and spec["acceptance_criteria"]:
            route = self.provider_registry.resolve(aid)
            provider = self.generic_config["providers"][route.provider]
            if provider["type"] not in {"mock", "deterministic"}:
                assessment = self._assess_generic_task(aid, task_id)
                if assessment["status"] != "completed":
                    return assessment
        task.status = TaskStatus.DONE
        task.progress_score = 1.0
        task.history.append({"tick": self.world_tick, "event": "completed", "agent_id": aid})
        if task_id in self.agents[aid].active_tasks:
            self.agents[aid].active_tasks.remove(task_id)
        return {"status": "completed", "task_id": task_id}

    def _close_tasks_that_meet_their_gate(self, tick):
        # Generic completion is gated by configured deliverables, dependencies,
        # and collaborators, not the product-specific patch/merge gate.
        return None


def task_spec_from_source(task):
    """Expose a source/product task through the same public TaskSpec shape."""
    return TaskSpec(id=task.task_id, title=task.title, description=task.description,
                    priority=task.priority, owner=task.owner_id,
                    dependencies=tuple(task.dependencies), input_artifacts=tuple(task.linked_artifacts),
                    acceptance_criteria=tuple(task.completion_requirements), deadline=task.deadline_tick,
                    metadata={"preset": "source_b3", "visibility": task.visibility})


def build_source_b3_world(config):
    world = OrgWorld(default_scenario(seed=config.runtime.seed)).build()
    world.task_specs = {key: asdict(task_spec_from_source(task)) for key, task in world.tasks.items()}
    return world


def build_generic_world(config):
    data = deepcopy(config.data)
    scenario = default_scenario(seed=config.runtime.seed)
    disabled = set(resolve_mechanism_ablations(scenario.params.get("mechanism_ablations")).disabled)
    # The public configuration controls the existing shared external-signal
    # gate, including when the source compatibility environment says otherwise.
    if data["learning"]["external_signal_loop"]:
        disabled.discard(EXTERNAL_BRIDGE)
    else:
        disabled.add(EXTERNAL_BRIDGE)
    scenario.params["mechanism_ablations"] = sorted(disabled)
    world = GenericOrgWorld(scenario)
    world.generic_config = data
    world.agent_config = {agent["id"]: agent for agent in data["agents"]}
    world.task_specs = {task["id"]: task for task in data["tasks"]}
    world.task_reviews = {}
    world.private_review_notes = {}
    world.task_revision_requests = set()
    world.completed_tool_calls = set()
    world.protocol_origins = {}
    world.prompt_assets = []
    for name in data["prompts"]["custom_prompt_assets_path"]:
        path = Path(name)
        path = path if path.is_absolute() else config.source_path.parent / path
        paths = sorted(path.glob("*")) if path.is_dir() else [path]
        world.prompt_assets.extend(p.read_text(encoding="utf-8") for p in paths if p.is_file())
    world.company.workspace_id = data["organization"]["id"]
    world.company.company_name = data["organization"]["name"]
    organization = data["organization"]
    world.company_config = {
        "company_name": organization["name"], "product_name": organization["name"],
        "product_purpose": organization["brief"] or "Complete the configured shared tasks.",
        "company_framing": organization["description"] or "a configurable agent organization",
        "product_stage": "shared task execution",
        "discovery_narrative": organization["brief"] or "Coordinate the configured shared work.",
        "work_narrative": "Work on the organization's task board and shared artifacts.",
        "decision_narrative": "Follow the configured roles, permissions, and governance.",
        "product_context_files_heading": "Shared organization artifacts",
        "product_context_gaps_heading": "Outstanding task requirements",
        "product_context_issues_heading": "Shared task issues",
    }
    from environments.org_env.backend.budget import CompensationProfile
    from environments.org_env.backend.clock import AgentAvailability
    for spec in data["agents"]:
        aid = spec["id"]
        style = spec["communication_style"]
        if isinstance(style, str):
            style = {"description": style}
        schedule = spec["work_schedule"] if isinstance(spec["work_schedule"], dict) else {}
        agent = OrgAgent(agent_id=aid, name=spec["display_name"], role=spec["role"],
                         initial_identity=spec["role_mandate"], skills=spec["skills"], profile=spec["profile"],
                         failure_modes=spec["failure_modes"], communication_style=style,
                         work_rhythm=schedule, permissions=spec["permissions"])
        world.agents[aid] = agent
        workspace = spec["private_workspace"]
        workspace = {"root": workspace} if isinstance(workspace, str) else workspace
        personal = PersonalWorkspace(agent_id=aid, sandbox_id=f"sandbox_{aid}",
                                     personal_workspace_id=workspace.get("root", f"pw_{aid}"))
        personal.personal_notes.append(json.dumps(spec["initial_context"]))
        for index, item in enumerate(workspace.get("files", [])):
            item = {"id": f"{aid}-private-{index}", "content": item} if isinstance(item, str) else item
            record = FileObject(object_id=item["id"], title=item.get("title", item["id"]),
                                owner_id=aid, visibility=Visibility.PRIVATE, raw_payload=item.get("content", ""))
            personal.add_local_file(record)
            world.company.register_file(record)
        world.personal[aid] = personal
        world.company.add_member(aid)
        world.budget_system.register_agent(CompensationProfile(agent_id=aid))
        world.time.register_agent(AgentAvailability(agent_id=aid,
            after_hours_responsiveness=schedule.get("after_hours_responsiveness", 0.4),
            weekend_work_tendency=schedule.get("weekend_work_tendency", 0.3),
            deep_work_preference=schedule.get("deep_work_preference", 0.5),
            meeting_tolerance=schedule.get("meeting_tolerance", 0.5)))
        world.sandbox_system.ensure_sandbox(aid)
        world.memory[aid] = []
    world.teams = data["organization"]["topology"].get("teams", {})
    for channel in data["organization"]["channels"] or ["general"]:
        world.comm.create_channel(channel, members=set(world.teams.get(channel, world.agents)))
    for name, members in world.teams.items():
        if name not in world.comm.channels:
            world.comm.create_channel(name, members=set(members))
    for spec in data["tasks"]:
        priority = spec["priority"]
        if isinstance(priority, str):
            priority = {"low": 1, "medium": 3, "high": 5, "critical": 10}.get(priority, 3)
        deadline_tick = spec["deadline"]
        owner = spec["owner"] or next((agent["id"] for agent in data["agents"] if spec["id"] in agent["ownership"]), None)
        task = Task(task_id=spec["id"], title=spec["title"], description=spec["description"],
                    owner_id=owner, priority=priority, dependencies=spec["dependencies"],
                    deadline_tick=deadline_tick, completion_requirements=[])
        world.tasks[task.task_id] = task
        world.board.tasks.append(task.task_id)
        if owner:
            world.agents[owner].active_tasks.append(task.task_id)
            world.board.owners[task.task_id] = owner
    shared = data["organization"]["shared_workspace"]
    shared = {"root": shared} if isinstance(shared, str) else shared
    if shared.get("root"):
        world.company.workspace_id = shared["root"]
    initial_files = data["organization"]["initial_documents"] + data["organization"]["initial_artifacts"] + shared.get("files", [])
    for index, item in enumerate(initial_files):
        item = {"id": f"initial-{index}", "content": item} if isinstance(item, str) else item
        file_id = str(item.get("id", f"initial-{index}"))
        owner = item.get("owner", next(iter(world.agents)))
        world.company.register_file(FileObject(object_id=file_id, title=item.get("title", file_id),
            owner_id=owner, visibility=Visibility.TEAM, raw_payload=item.get("content", ""),
            content_summary=item.get("summary", "")))
        world.documents[file_id] = Document(doc_id=file_id, title=item.get("title", file_id), owner_id=owner)
    world.provider_registry = ProviderRegistry(config)
    world.llm_client = world.provider_registry
    world.tool_registry = GenericToolRegistry(config, world)
    learning = data["learning"]
    world.profile_conditioning_enabled = learning["profile_conditioning"]
    world.capability_learning_enabled = learning["capability_learning"]
    world.institutionalization_enabled = learning["institutionalization"]
    world.auto_propose = learning["proposal_generation"]
    world.auto_approve = data["governance"]["approval_mode"] == "auto"
    world.approval_mode = data["governance"]["approval_mode"]
    world._wire_loop()
    world._loop.update(mapper=GenericActionMapper(), execution=GenericExecution(), features=GenericFeatures())
    # SDL customization belongs to generic applications only. The source B3
    # builder never reads this section, preserving its published policy.
    sdl = data["runtime"]["sdl"]
    if (sdl["base_weights"] or sdl["profile_coefficients"] or sdl["scorer"]
            or sdl["temperature"] != 0.6 or sdl["jitter"] != 0.05):
        from relic_agent.runtime.sdl import ConfigurableOrgPolicy
        scorer = None
        if scorer_spec := sdl["scorer"]:
            module = _import_plugin(scorer_spec["module"], scorer_spec["path"],
                                    "runtime.sdl.scorer", config.source_path.parent)
            scorer = getattr(module, scorer_spec["entrypoint"] or "execute")
        world._loop["policy"] = ConfigurableOrgPolicy(world._loop["policy"], sdl, scorer)
    from relic_agent.runtime.lifecycle import configure_lifecycle
    configure_lifecycle(world, config, config.source_path.parent)
    if data["runtime"]["decision_mode"] == "flat_deterministic":
        world._loop["policy"].use_profile_conditioning = False
        world._loop["policy"].mode = "argmax"
    # OrgAgent already initializes skills, reputation and authority. Do not
    # derive learned authority or emit growth events when learning is disabled.
    if world.capability_learning_enabled:
        world._growth_reconciler.run(world, 0)
    world._authority_t0 = {aid: dict(agent.authority) for aid, agent in world.agents.items()}

    # Keep the shared class-level step intact (generic mode uses the same
    # OrgWorld simulation) while adding one small instance hook for deadline
    # observability after each tick.
    def step_with_deadline_evidence():
        # Resolve the shared class method at call time so test/integration
        # instrumentation that wraps ``OrgWorld.step`` still observes generic
        # runs.  Capturing the bound method here would bypass that hook.
        snapshot = OrgWorld.step(world)
        world._record_deadline_events(world.world_tick)
        return snapshot
    world.step = step_with_deadline_evidence
    return world
