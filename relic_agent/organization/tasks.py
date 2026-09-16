"""Task and ownership state for a Relic organization."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    REVIEW = "review"
    MERGED = "merged"
    RELEASED = "released"
    DONE = "done"
    ABANDONED = "abandoned"


COMPLETED_TASK_STATUSES = frozenset({TaskStatus.MERGED, TaskStatus.RELEASED, TaskStatus.DONE})


@dataclass
class Task:
    task_id: str
    title: str = ""
    description: str = ""
    status: TaskStatus = TaskStatus.OPEN
    priority: int = 3
    owner_id: str | None = None
    required_skills: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    deadline_tick: int | None = None
    estimated_effort: float = 0.0
    actual_effort: float = 0.0
    visibility: str = "organization"
    progress_score: float = 0.0
    progress_evidence: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)


__all__ = ["COMPLETED_TASK_STATUSES", "Task", "TaskStatus"]
