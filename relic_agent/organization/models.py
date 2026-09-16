"""Legacy mock state shared by the compatibility runtime and replay exporter.

Portable state for new integrations lives in ``organization_core.state``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from relic_agent.organization.tasks import Task, TaskStatus


@dataclass
class AgentState:
    agent_id: str
    display_name: str
    role: str
    profile: dict[str, float] = field(default_factory=dict)
    skills: dict[str, float] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    active_task_ids: list[str] = field(default_factory=list)
    status: str = "available"
    work_rhythm: dict[str, float] = field(default_factory=dict)
    vitals: dict[str, float] = field(
        default_factory=lambda: {"attention": 1.0, "fatigue": 0.0, "stress": 0.0}
    )

    def skill(self, name: str, default: float = 0.0) -> float:
        return float(self.skills.get(name, default))

    def public_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "role": self.role,
            "tools": list(self.tools),
            "active_task_ids": list(self.active_task_ids),
            "status": self.status,
        }


@dataclass
class OrganizationState:
    organization_id: str
    name: str
    tick: int = 0
    agents: dict[str, AgentState] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)
    proposals: dict[str, Any] = field(default_factory=dict)
    protocols: dict[str, Any] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "name": self.name,
            "tick": self.tick,
            "agents": [self.agents[key].public_dict() for key in sorted(self.agents)],
            "tasks": [self._task_dict(self.tasks[key]) for key in sorted(self.tasks)],
            "proposals": [self.proposals[key].to_dict() for key in sorted(self.proposals)],
            "protocols": [asdict(self.protocols[key]) for key in sorted(self.protocols)],
        }

    @staticmethod
    def _task_dict(task: Task) -> dict[str, Any]:
        payload = asdict(task)
        status = task.status
        payload["status"] = status.value if isinstance(status, TaskStatus) else str(status)
        return payload
