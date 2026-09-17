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

    def catalog(self, agent_id: str) -> list[dict]:
        agent = self.agents[agent_id]
        return [{"id": name, "description": self.specs[name].get("description", ""),
                 "schema": self.specs[name].get("schema", {})}
                for name in agent.get("tools", [])]

    def execute(self, agent_id: str, tool_id: str, arguments: dict | None = None) -> dict:
        arguments = arguments or {}
        event = {"type": "tool_execution", "event_id": f"tool-{len(self.world.events)}",
                 "tick": self.world.world_tick, "actor_id": agent_id, "tool_id": tool_id}
        try:
            agent = self.agents[agent_id]
            if tool_id not in agent.get("tools", []):
                raise PermissionError("tool is not granted to this agent")
            spec = self.specs[tool_id]
            permissions = set(agent.get("permissions", [])) | set(
                self.config.get("governance", {}).get("role_permissions", {}).get(agent["role"], []))
            if not set(spec.get("permissions", [])) <= permissions:
                raise PermissionError("required tool permissions are missing")
            policy = spec.get("side_effect_policy", "none")
            if policy == "external" and "external" not in permissions:
                raise PermissionError("external side effects require external permission")
            if policy not in {"none", "read_only", "workspace", "external"}:
                raise ValueError("unsupported tool side_effect_policy")
            validate_arguments(arguments, spec.get("schema", {}))
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
                for artifact in artifacts:
                    self._write_file(agent_id, artifact)
            else:
                writes = (tool_id == "messaging" or
                          (tool_id == "files" and arguments.get("operation") == "write") or
                          (tool_id == "task_board" and arguments.get("operation", "list") != "list"))
                if writes and policy in {"none", "read_only"}:
                    raise PermissionError("tool side effects are disabled")
                result = self._builtin(agent_id, tool_id, arguments)
            event["status"] = "completed"
            output = {"status": "completed", "result": result}
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
                 "status": str(getattr(t.status, "value", t.status))}
                for t in self.world.tasks.values()]

    def _visible_files(self, agent_id):
        return [{"id": f.object_id, "title": f.title, "content": f.raw_payload}
                for f in self.world.company.visible_files_for(agent_id)]

    def _write_file(self, agent_id, data):
        file_id = str(data["id"])
        existing = self.world.company.files.get(file_id)
        if existing and not existing.visible_to(agent_id, members=self.world.company.members):
            raise PermissionError("file is not visible to this agent")
        record = FileObject(object_id=file_id, title=str(data.get("title", file_id)),
                            owner_id=agent_id, creator_id=agent_id,
                            visibility=Visibility(data.get("visibility", "team")),
                            raw_payload=data.get("content", ""),
                            content_summary=str(data.get("summary", "")),
                            linked_task_ids=list(data.get("task_ids", [])),
                            created_tick=self.world.world_tick,
                            last_modified_tick=self.world.world_tick)
        record.provenance.add(tick=self.world.world_tick, actor_id=agent_id,
                              action="tool_write", note="generic tool artifact")
        self.world.company.register_file(record)
        return {"artifact_id": file_id}

    def _builtin(self, agent_id, name, args):
        operation = args.get("operation", "list")
        if name == "files":
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
                if owner != agent_id and "assign_task" not in self.agents[agent_id].get("permissions", []):
                    raise PermissionError("assign_task permission required")
                if task.owner_id not in {None, agent_id, owner}:
                    raise PermissionError("task already has another owner")
                task.owner_id = owner
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
                    return self.world.complete_generic_task(agent_id, task.task_id, args)
                raise ValueError("runtime does not expose generic task completion")
            else:
                raise ValueError("unknown task operation")
            task.history.append({"tick": self.world.world_tick, "event": operation, "agent_id": agent_id})
            return {"task_id": task.task_id, "status": str(getattr(task.status, "value", task.status))}
        raise ValueError("unknown builtin tool")
