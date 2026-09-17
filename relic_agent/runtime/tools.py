"""Small tool registry shared by generic organization action adapters.

Plugins are trusted Python functions ``execute(arguments, context)``. Context is
an isolated snapshot; returned artifacts are applied only after successful
completion. This is error isolation, not a Python sandbox.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from copy import deepcopy
from importlib import import_module
from pathlib import Path
from typing import Any

from environments.org_env.backend.entities.work import TaskStatus
from environments.org_env.backend.workspace.objects import FileObject, Visibility
from relic_agent.config_schema import _plugin_module_from_path


def validate_arguments(value: Any, schema: dict, label: str = "arguments") -> None:
    """Validate the documented compact JSON-schema subset used by tools."""
    types = {"object": dict, "array": list, "string": str, "number": (int, float),
             "integer": int, "boolean": bool, "null": type(None)}
    kind = schema.get("type")
    if kind and (kind not in types or not isinstance(value, types[kind]) or
                 (kind in {"number", "integer"} and isinstance(value, bool))):
        raise ValueError(f"{label} must have type {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{label} is not an allowed value")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{label}.{key} is required")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ValueError(f"{label} contains unknown fields")
        for key, item in value.items():
            if key in properties:
                validate_arguments(item, properties[key], f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            validate_arguments(item, schema.get("items", {}), f"{label}[{index}]")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{label} is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{label} is above maximum")


class GenericToolRegistry:
    def __init__(self, config, world):
        self.world = world
        self.config = config.data if hasattr(config, "data") else config
        self.base_dir = Path(getattr(config, "source_path", None) or ".").parent
        self.agents = {a["id"]: a for a in self.config["agents"]}
        self.specs = {}
        self.functions = {}
        for item in self.config.get("tools", {}).get("builtins", []):
            spec = {"id": item} if isinstance(item, str) else item
            self.specs[spec["id"]] = spec
        for spec in self.config.get("tools", {}).get("plugins", []):
            if spec.get("module"):
                module = import_module(spec["module"])
            else:
                path = Path(spec["path"])
                module = _plugin_module_from_path(
                    path if path.is_absolute() else self.base_dir / path, f"tool {spec['id']}")
            function = getattr(module, spec.get("entrypoint") or "execute", None)
            if not callable(function):
                raise ValueError(f"tool {spec['id']} must export a callable execute(arguments, context)")
            self.specs[spec["id"]] = spec
            self.functions[spec["id"]] = function

    def permissions(self, agent_id: str) -> set[str]:
        agent = self.agents[agent_id]
        return set(agent.get("permissions", [])) | set(
            self.config.get("governance", {}).get("role_permissions", {}).get(agent["role"], []))

    def learned_tool(self, tool_id: str):
        """Return an adopted tool composed by the live proposal manager.

        Generic tools configured in YAML and tools learned at runtime deliberately
        share this lookup surface.  The latter are data-only ``ToolSpec`` objects;
        execution is handled by :class:`GenericExecution`, which can only invoke
        the small set of existing generic actions.
        """
        manager = getattr(self.world, "proposal_manager", None)
        tools = getattr(manager, "tools", {}) if manager is not None else {}
        tool = tools.get(tool_id) if isinstance(tools, dict) else None
        return tool if getattr(tool, "status", None) == "active" else None

    def can_use_learned_tool(self, agent_id: str, tool) -> bool:
        """Check a learned ToolSpec's explicit call and permission grants."""
        if tool is None or getattr(tool, "status", None) != "active":
            return False
        agent = self.agents.get(agent_id)
        if agent is None:
            return False
        callable_agents = set(getattr(tool, "callable_by_agents", []) or [])
        if callable_agents and agent_id not in callable_agents:
            return False
        callable_roles = set(getattr(tool, "callable_by_roles", []) or [])
        if callable_roles and agent.get("role") not in callable_roles:
            return False
        required = set(getattr(tool, "required_permissions", []) or [])
        return required <= self.permissions(agent_id)

    def learned_tools_for(self, agent_id: str) -> list[Any]:
        """List active learned tools this agent is allowed to call."""
        manager = getattr(self.world, "proposal_manager", None)
        tools = getattr(manager, "tools", {}) if manager is not None else {}
        if not isinstance(tools, dict):
            return []
        return [tool for tool in tools.values() if self.can_use_learned_tool(agent_id, tool)]

    def catalog(self, agent_id: str) -> list[dict]:
        agent = self.agents[agent_id]
        catalog = [{"id": name, "description": self.specs[name].get("description", ""),
                    "schema": self.specs[name].get("schema", {})}
                   for name in agent.get("tools", [])]
        # A learned tool is exposed only after its ToolSpec grants pass.  It is
        # intentionally represented by its declared input schema; no arbitrary
        # callable or code path is surfaced through the catalog.
        configured = {item["id"] for item in catalog}
        for tool in self.learned_tools_for(agent_id):
            if tool.tool_id in configured:
                continue
            catalog.append({"id": tool.tool_id, "description": getattr(tool, "description", ""),
                            "schema": getattr(tool, "input_schema", {}) or {}})
        return catalog

    def execute(self, agent_id: str, tool_id: str, arguments: dict | None = None) -> dict:
        arguments = {} if arguments is None else arguments
        event = {"type": "tool_execution", "event_id": f"tool-{len(self.world.events)}",
                 "tick": self.world.world_tick, "actor_id": agent_id, "tool_id": tool_id}
        try:
            agent = self.agents[agent_id]
            if tool_id not in agent.get("tools", []):
                raise PermissionError("tool is not granted to this agent")
            spec = self.specs[tool_id]
            permissions = self.permissions(agent_id)
            if not set(spec.get("permissions", [])) <= permissions:
                raise PermissionError("required tool permissions are missing")
            policy = spec.get("side_effect_policy", "none")
            if policy == "external" and "external" not in permissions:
                raise PermissionError("external side effects require external permission")
            if policy not in {"none", "read_only", "workspace", "external"}:
                raise ValueError("unsupported tool side_effect_policy")
            validate_arguments(arguments, spec.get("schema", {}))
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be an object")
            if tool_id in self.functions:
                context = {"agent_id": agent_id, "tick": self.world.world_tick,
                           "organization_id": self.config["organization"]["id"],
                           "config": deepcopy(spec.get("config", {})),
                           "tasks": self._tasks(),
                           "files": self._visible_files(agent_id)}
                pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="relic-tool")
                future = pool.submit(self.functions[tool_id], deepcopy(arguments), context)
                try:
                    result = future.result(timeout=spec.get("timeout_seconds", 30))
                finally:
                    pool.shutdown(wait=False, cancel_futures=True)
                if not isinstance(result, dict):
                    raise ValueError("plugin result must be an object")
                artifacts = result.get("artifacts", [])
                if artifacts and policy not in {"workspace", "external"}:
                    raise PermissionError("artifact writes require workspace side effects")
                if result.get("status", "completed") == "completed":
                    prepared = [self._prepare_file(agent_id, artifact) for artifact in artifacts]
                    if len({file.object_id for file in prepared}) != len(prepared):
                        raise ValueError("duplicate returned artifact ids")
                    for record in prepared:
                        self._apply_file(record)
            else:
                writes = (tool_id == "messaging" or
                          (tool_id == "files" and arguments.get("operation") == "write") or
                          (tool_id == "task_board" and arguments.get("operation", "list") != "list"))
                if writes and policy in {"none", "read_only"}:
                    raise PermissionError("tool side effects are disabled")
                result = self._builtin(agent_id, tool_id, arguments)
            status = result.get("status", "completed")
            if status not in {"completed", "pending", "failed"}:
                raise ValueError("unsupported tool result status")
            event["status"] = status
            output = {"status": status, "result": result}
            if status != "completed":
                # Only a stable error category is public, never plugin text.
                output["error_type"] = result.get("error_type", "RequirementsPending") if tool_id not in self.functions else "PluginRequirementsPending"
        except TimeoutError:
            event["status"] = "timeout"
            output = {"status": "timeout", "error_type": "TimeoutError"}
        except Exception as exc:
            # Plugin/provider exceptions may include secrets. Publish types only.
            event["status"] = "failed"
            event["error_type"] = type(exc).__name__
            output = {"status": "failed", "error_type": type(exc).__name__}
        self.world.events.append(event)
        return output

    def _tasks(self):
        return [{"id": t.task_id, "title": t.title, "owner": t.owner_id,
                 "priority": getattr(t, "priority", 3),
                 "deadline": getattr(t, "deadline_tick", None),
                 "status": str(getattr(t.status, "value", t.status))}
                for t in self.world.tasks.values()]

    def _visible_files(self, agent_id):
        return [{"id": f.object_id, "title": f.title, "content": f.raw_payload}
                for f in self.world.company.visible_files_for(agent_id)]

    def _write_file(self, agent_id, data):
        return self._apply_file(self._prepare_file(agent_id, data))

    def _prepare_file(self, agent_id, data):
        if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not data["id"].strip():
            raise ValueError("artifact requires a nonempty string id")
        file_id = data["id"]
        existing = self.world.company.files.get(file_id)
        task_ids = data.get(
            "task_ids",
            list(getattr(existing, "linked_task_ids", []) or []) if existing else [],
        )
        if not isinstance(task_ids, list) or any(not isinstance(key, str) or key not in self.world.tasks for key in task_ids):
            raise ValueError("artifact task_ids must reference existing tasks")
        if existing and not existing.visible_to(agent_id, members=self.world.company.members):
            raise PermissionError("file is not visible to this agent")

        # An update is a new FileObject instance, so all omitted access fields
        # must be copied from the old object.  In particular, defaulting an
        # existing private file to TEAM silently published it on overwrite.
        if "visibility" in data:
            requested_visibility = data["visibility"]
            try:
                requested_visibility = Visibility(
                    getattr(requested_visibility, "value", requested_visibility)
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("artifact visibility is invalid") from exc
        elif existing is not None:
            requested_visibility = Visibility(
                getattr(existing.visibility, "value", existing.visibility)
            )
        else:
            requested_visibility = Visibility.TEAM

        if existing is not None:
            old_visibility = Visibility(
                getattr(existing.visibility, "value", existing.visibility)
            )
            if requested_visibility != old_visibility and existing.owner_id != agent_id:
                # Visibility changes on another owner's file are an explicit
                # publication/share operation.  Merely being able to read a
                # team-visible file does not grant that authority.
                permissions = self.permissions(agent_id)
                if not permissions.intersection({"publish_file", "publish", "manage_workspace"}):
                    raise PermissionError("only the file owner or a publish permission may change visibility")

        owner_id = existing.owner_id if existing is not None else agent_id
        creator_id = existing.creator_id if existing is not None else agent_id
        if existing is not None and owner_id is None:
            # FileObject.__post_init__ derives owner_id from creator_id.  Keep a
            # legacy owner-less file owner-less when it is overwritten.
            creator_id = None
        record = FileObject(object_id=file_id, title=str(data.get("title", file_id)),
                            owner_id=owner_id, creator_id=creator_id,
                            visibility=requested_visibility,
                            raw_payload=data.get("content", ""),
                            content_summary=str(data.get("summary", "")),
                            linked_task_ids=task_ids,
                            granted_to=set(getattr(existing, "granted_to", set()) or ()) if existing else set(),
                            file_type=getattr(existing, "file_type", "doc") if existing else "doc",
                            created_tick=self.world.world_tick,
                            last_modified_tick=self.world.world_tick)
        record.provenance.add(tick=self.world.world_tick, actor_id=agent_id,
                              action="tool_write", note="generic tool artifact")
        if existing:
            record.version = existing.version + 1
            record.recompute_hash({"content": record.raw_payload, "version": record.version})
        return record

    def _apply_file(self, record):
        file_id = record.object_id
        self.world.company.register_file(record)
        for task_id in record.linked_task_ids:
            task = self.world.tasks.get(task_id)
            if task is not None and file_id not in task.linked_artifacts:
                task.linked_artifacts.append(file_id)
        return {"artifact_id": file_id}

    def _builtin(self, agent_id, name, args):
        operation = args.get("operation", "list")
        if name == "files":
            if operation not in {"list", "read", "write"}:
                raise ValueError("unknown file operation")
            if operation == "write":
                return self._write_file(agent_id, args)
            files = self._visible_files(agent_id)
            if operation == "read":
                return next(f for f in files if f["id"] == args["id"])
            return {"files": files}
        if name == "search":
            query = str(args.get("query", "")).casefold()
            return {"files": [f for f in self._visible_files(agent_id)
                              if query in (str(f["title"]) + str(f["content"])).casefold()]}
        if name == "messaging":
            channel = args.get("channel", "general")
            if channel not in self.world.comm.channels:
                raise ValueError("unknown channel")
            if agent_id not in self.world.comm.channels[channel].members:
                raise PermissionError("agent is not a channel member")
            message = self.world.comm.send_message(sender_id=agent_id, channel_id=channel,
                text=str(args.get("text", "")), tick=self.world.world_tick)
            return {"message_id": message.message_id}
        if name == "task_board":
            if operation == "list":
                return {"tasks": self._tasks()}
            task = self.world.tasks[args["task_id"]]
            if operation in {"claim", "assign"}:
                owner = args.get("owner", agent_id)
                if owner not in self.world.agents:
                    raise ValueError("unknown owner")
                if owner in getattr(self.world, "task_specs", {}).get(task.task_id, {}).get("collaborators", []):
                    raise ValueError("task owner must be distinct from its reviewers")
                if owner != agent_id and "assign_task" not in self.permissions(agent_id):
                    raise PermissionError("assign_task permission required")
                if task.owner_id not in {None, agent_id, owner}:
                    raise PermissionError("task already has another owner")
                previous_owner = task.owner_id
                if previous_owner and previous_owner != owner:
                    active = self.world.agents[previous_owner].active_tasks
                    if task.task_id in active:
                        active.remove(task.task_id)
                task.owner_id = owner
                self.world.board.owners[task.task_id] = owner
                task.status = TaskStatus.IN_PROGRESS
                if task.task_id not in self.world.agents[owner].active_tasks:
                    self.world.agents[owner].active_tasks.append(task.task_id)
            elif operation == "complete":
                if task.owner_id != agent_id:
                    raise PermissionError("only the task owner may complete it")
                if any(str(getattr(self.world.tasks[d].status, "value", self.world.tasks[d].status))
                       not in {"done", "completed", "merged", "released"} for d in task.dependencies):
                    raise ValueError("task dependencies are incomplete")
                if hasattr(self.world, "complete_generic_task"):
                    # Metadata tool calls can reach this low-level builtin
                    # directly.  Route completion through the same lifecycle
                    # seam as the explicit GenericExecution action so a
                    # protocol cannot be bypassed by spelling completion as
                    # ``task_board.complete``.
                    lifecycle = getattr(self.world, "generic_lifecycle", None)
                    completion = None
                    if (
                        lifecycle
                        and hasattr(lifecycle, "before_action")
                        and not getattr(self.world, "_generic_completion_lifecycle_active", False)
                    ):
                        if lifecycle.before_action(agent_id, "complete_task", task.task_id) is False:
                            raise PermissionError("protocol blocked action")
                    try:
                        outcome = self.world.complete_generic_task(agent_id, task.task_id, args)
                        completion = type("CompletionResult", (), {
                            "success": outcome.get("status") == "completed",
                            "failure_reason": "" if outcome.get("status") == "completed" else outcome.get("error_type", "requirements_pending"),
                            "events": [],
                            "modified_objects": [task.task_id] if outcome.get("status") == "completed" else [],
                            "created_objects": [],
                        })()
                    except Exception as exc:
                        completion = type("CompletionResult", (), {
                            "success": False,
                            "failure_reason": type(exc).__name__,
                            "events": [],
                            "modified_objects": [],
                            "created_objects": [],
                        })()
                        raise
                    finally:
                        if (
                            lifecycle
                            and hasattr(lifecycle, "after_action")
                            and completion is not None
                            and not getattr(self.world, "_generic_completion_lifecycle_active", False)
                        ):
                            lifecycle.after_action(agent_id, "complete_task", task.task_id, completion)
                    return outcome
                raise ValueError("runtime does not expose generic task completion")
            else:
                raise ValueError("unknown task operation")
            task.history.append({"tick": self.world.world_tick, "event": operation, "agent_id": agent_id})
            return {"task_id": task.task_id, "status": str(getattr(task.status, "value", task.status))}
        raise ValueError("unknown builtin tool")
