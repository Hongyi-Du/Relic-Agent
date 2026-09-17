"""Compatibility trace shell with no active HCI action execution.

This runner is retained while the source HCI host adapter is ported. It keeps
the public CLI, trace contract, Inspector, and launchers runnable, but it
does not select or execute organization actions. Its tick envelopes are
validated by the vendored source core in observation-only mode.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from relic_agent.config import OrganizationConfig
from relic_agent.episodes import EpisodeManager, OrgEpisode
from relic_agent.events import Event, EventStore
from relic_agent.governance import GovernanceManager
from relic_agent.organization import AgentState, OrganizationState, Task, TaskStatus
from relic_agent.reflection import ReflectionManager
from relic_agent.replay.trace import build_trace
from relic_agent.source_b3.growth.lifecycle import SourceB3GrowthLifecycleAdapter
from relic_agent.source_b3.policy import SourceB3PolicyLifecycleAdapter
from relic_agent.source_core import SourceCoreObservationBridge

RUN_SCHEMA_VERSION = "relic-agent-run-v1"
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class RunResult:
    run_id: str
    run_directory: Path
    manifest_path: Path
    trace_path: Path
    status: str
    ticks: int
    event_count: int
    completed_task_count: int
    adopted_protocol_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_directory": str(self.run_directory),
            "manifest_path": str(self.manifest_path),
            "trace_path": str(self.trace_path),
            "status": self.status,
            "ticks": self.ticks,
            "event_count": self.event_count,
            "completed_task_count": self.completed_task_count,
            "adopted_protocol_count": self.adopted_protocol_count,
        }


class OrganizationRuntime:
    """Legacy compatibility entry point kept for the release shell.

    New integrations must mount ``organization_core.OrganizationModule`` on a
    source-compatible host. This class cannot activate that path and records
    the limitation in every generated ``run.json``.
    """

    def __init__(self, config: OrganizationConfig) -> None:
        self.config = config
        self.state = OrganizationState(config.organization_id, config.name)
        self.state.agents = {
            item.agent_id: AgentState(
                agent_id=item.agent_id,
                display_name=item.display_name,
                role=item.role,
                profile=dict(item.profile),
                skills=dict(item.skills),
                tools=list(item.tools),
            )
            for item in config.agents
        }
        self.state.tasks = {
            item.task_id: Task(
                task_id=item.task_id,
                title=item.title,
                description=item.description,
                priority=item.priority,
                owner_id=item.owner_id,
                required_skills=list(item.required_skills),
            )
            for item in config.tasks
        }
        # An owner in the release config is static input, not evidence that a
        # source action has claimed or started the task.  The compatibility
        # shell must preserve that distinction until a real HCI host executes
        # an action, so it leaves the source-neutral task state untouched.
        self.events = EventStore()
        self.episodes = EpisodeManager()
        self.reflection = ReflectionManager()
        self.governance = GovernanceManager(
            agent_ids=tuple(sorted(self.state.agents)),
            min_approvers=config.governance.min_approvers,
            review_ticks=config.governance.review_ticks,
            agent_roles={agent_id: agent.role for agent_id, agent in self.state.agents.items()},
            known_actions=(
                "approve_proposal",
                "claim_task",
                "work_task",
                "use_protocol",
            ),
        )
        self.governance.bind_host_context(
            agents=self.state.agents,
            reflection_manager=self.reflection,
            episode_manager=self.episodes,
        )
        # These adapters are intentionally unbound in the release shell. They
        # make the precise source capabilities and host requirements visible in
        # every run manifest without turning configured tasks into fake HCI
        # actions, growth signals, or policy decisions.
        self.growth = SourceB3GrowthLifecycleAdapter()
        self.policy = SourceB3PolicyLifecycleAdapter()
        self.frames: list[dict[str, Any]] = []
        self._last_frame_event_index = 0
        self._last_frame_protocol_event_index = 0
        self._source_core_bridge: SourceCoreObservationBridge | None = None

    def run(
        self,
        *,
        output_root: str | Path,
        ticks: int | None = None,
        run_id: str | None = None,
    ) -> RunResult:
        total_ticks = ticks if ticks is not None else self.config.runtime.ticks
        if total_ticks < 1:
            raise ValueError("ticks must be positive")
        actual_run_id = run_id or str(uuid.uuid4())
        if not _RUN_ID_RE.fullmatch(actual_run_id) or ".." in actual_run_id:
            raise ValueError("run_id must be a safe 1-128 character identifier")
        started_at = datetime.now(UTC).isoformat()
        output_directory = Path(output_root).expanduser().resolve() / actual_run_id
        output_directory.mkdir(parents=True, exist_ok=False)
        self._source_core_bridge = SourceCoreObservationBridge.from_config(
            self.config,
            run_id=actual_run_id,
        )
        self.governance.bind_source_core_bridge(self._source_core_bridge)
        config_snapshot_path = output_directory / "config.yaml"
        self._atomic_text(
            config_snapshot_path,
            self.config.source_path.read_text(encoding="utf-8"),
        )
        trace_path = output_directory / "trace.json"
        status_path = output_directory / "status.json"
        self._capture_frame(force=True)
        self._write_status(status_path, actual_run_id, "running")
        try:
            trace = self._write_public_trace(trace_path, actual_run_id)
            for tick in range(1, total_ticks + 1):
                self.state.tick = tick
                self._advance_trace_tick()
                self._source_core_bridge.complete_tick(self.state.tick)
                self._capture_frame(force=self.frames[-1]["tick"] != self.state.tick)
                trace = self._write_public_trace(trace_path, actual_run_id)
                self._write_status(status_path, actual_run_id, "running")
        except BaseException as exc:
            if self.frames[-1]["tick"] != self.state.tick:
                self._capture_frame(force=True)
            try:
                self._write_public_trace(trace_path, actual_run_id)
            except Exception:
                pass
            self._write_status(
                status_path,
                actual_run_id,
                "failed",
                error_type=type(exc).__name__,
            )
            raise

        manifest = self._manifest(
            run_id=actual_run_id,
            started_at=started_at,
            finished_at=datetime.now(UTC).isoformat(),
            trace=trace,
        )
        manifest_path = output_directory / "run.json"
        self._atomic_json(manifest_path, manifest)
        self._write_status(status_path, actual_run_id, "completed")
        completed = sum(
            1
            for task in self.state.tasks.values()
            if task.status in {TaskStatus.DONE, TaskStatus.MERGED, TaskStatus.RELEASED}
        )
        adopted = sum(
            1
            for protocol in self.governance.protocol_registry.protocols.values()
            if protocol.adoption_status == "adopted"
        )
        return RunResult(
            run_id=actual_run_id,
            run_directory=output_directory,
            manifest_path=manifest_path,
            trace_path=trace_path,
            status="completed",
            ticks=total_ticks,
            event_count=len(self.events.events),
            completed_task_count=completed,
            adopted_protocol_count=adopted,
        )

    def _advance_trace_tick(self) -> None:
        """Advance the trace clock without selecting or executing an action.

        This deliberately replaces the former per-agent compatibility action
        loop.  HCI selection needs a source candidate pool, OrgWorld, work
        rhythm, attractor guard, and execution adapter.  A config task or a
        shell tick cannot stand in for any of those inputs.  The only state
        transition here is the already-recorded clock value; source-core then
        records its append-only tick envelope separately.
        """

        return None

    def _emit(
        self,
        *,
        event_type: str,
        actor_id: str = "",
        object_ids: tuple[str, ...] = (),
        payload: dict[str, Any] | None = None,
        visibility: str = "organization",
    ) -> Event:
        event = self.events.emit(
            tick=self.state.tick,
            event_type=event_type,
            actor_id=actor_id,
            object_ids=object_ids,
            payload=payload,
            visibility=visibility,
        )
        if self._source_core_bridge is not None:
            self._source_core_bridge.publish_legacy_event(event)
        if visibility in {"organization", "public"}:
            self._capture_frame()
        return event

    def _capture_frame(self, *, force: bool = False) -> None:
        events = self.events.public_since(self._last_frame_event_index)
        # The compatibility shell never publishes an action decision. Source
        # decisions can only come from a future mounted HCI action host.
        decisions: list[dict[str, Any]] = []
        governance_events = [
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "protocol_id": event.protocol_id,
                "actor_id": event.actor_id,
                "tick": event.tick,
                "data": dict(event.data),
            }
            for event in self.governance.protocol_registry.events[
                self._last_frame_protocol_event_index :
            ]
        ]
        if not force and not events and not decisions and not governance_events:
            return
        self._last_frame_event_index = len(self.events.events)
        self._last_frame_protocol_event_index = len(self.governance.protocol_registry.events)
        sequence = len(self.frames)
        self.frames.append(
            {
                "frame_id": f"frame_{sequence:06d}",
                "sequence": sequence,
                "tick": self.state.tick,
                "organization": self.state.public_dict(),
                "events": events,
                "episodes": [
                    self._public_episode(episode)
                    for episode in sorted(
                        self.episodes.episodes.values(), key=lambda item: item.episode_id
                    )
                ],
                "decisions": decisions,
                "governance_events": governance_events,
            }
        )

    @staticmethod
    def _public_episode(episode: OrgEpisode) -> dict[str, Any]:
        return {
            "episode_id": episode.episode_id,
            "episode_type": episode.episode_type,
            "title": episode.title,
            "status": episode.status,
            "start_tick": episode.start_tick,
            "end_tick": episode.end_tick,
            "participants": list(episode.participants),
            "linked_event_ids": list(episode.linked_event_ids),
            "linked_task_ids": list(episode.linked_task_ids),
            "linked_protocol_ids": list(episode.linked_protocol_ids),
            "problem_statement": episode.problem_statement,
            "decision_summary": episode.decision_summary,
            "outcome_summary": episode.outcome_summary,
            "produced_protocols": list(episode.produced_protocols),
            # HCI's source event view calls this field ``family``.  The public
            # trace contract calls the same normalized category ``event_type``;
            # this is a shape-only projection of an already source-observed
            # episode, never a conversion from a legacy compatibility event.
            "timeline": [
                {
                    "tick": item["tick"],
                    "event_id": item["event_id"],
                    "event_type": item["family"],
                    "actor_id": item.get("actor_id") or "",
                }
                for item in episode.timeline
            ],
        }

    def _write_public_trace(self, path: Path, run_id: str) -> dict[str, Any]:
        trace = build_trace(
            run_id=run_id,
            organization_id=self.state.organization_id,
            config_digest=self.config.digest,
            frames=self.frames,
        )
        self._atomic_json(path, trace)
        return trace

    def _write_status(
        self,
        path: Path,
        run_id: str,
        status: str,
        *,
        error_type: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "schema_version": "relic-agent-status-v1",
            "run_id": run_id,
            "status": status,
            "tick": self.state.tick,
        }
        if error_type:
            payload["error_type"] = error_type
        self._atomic_json(path, payload)

    def _manifest(
        self,
        *,
        run_id: str,
        started_at: str,
        finished_at: str,
        trace: dict[str, Any],
    ) -> dict[str, Any]:
        source_core = self._source_core_bridge
        if source_core is None:
            raise RuntimeError("source-core observation bridge was not initialized")
        return {
            "schema_version": RUN_SCHEMA_VERSION,
            "run_id": run_id,
            "status": "completed",
            "started_at": started_at,
            "finished_at": finished_at,
            "config": {
                "schema_version": self.config.schema_version,
                "sha256": self.config.digest,
                "file_name": self.config.source_path.name,
            },
            "runtime": {
                "provider": self.config.runtime.provider,
                "seed": self.config.runtime.seed,
                "ticks": self.state.tick,
                "provider_calls_made": 0,
                "authority": "compatibility_trace_shell_unbound",
                "action_selection": "unbound_no_source_orgworld",
                "action_execution": "unavailable_fail_closed",
                "workflow_acceptance": "unavailable_fail_closed",
                "workflow_acceptance_reason": (
                    "source_orgworld_action_host_not_mounted"
                ),
                "paper_result_evidence": "not_produced_by_compatibility_shell",
            },
            "source_core": source_core.status().as_dict(),
            "source_episode_lifecycle": self.episodes.status().as_dict(),
            "source_reflection_lifecycle": self.reflection.status().as_dict(),
            "source_proposal_lifecycle": self.governance.source_status(),
            "source_growth_lifecycle": self.growth.status().as_dict(),
            "source_policy_lifecycle": self.policy.status().as_dict(),
            "outputs": {
                "config_snapshot": "config.yaml",
                "status": "status.json",
                "trace": "trace.json",
                "trace_sha256": trace["trace_sha256"],
            },
            "summary": {
                "agents": len(self.state.agents),
                "tasks": len(self.state.tasks),
                "completed_tasks": sum(
                    1
                    for task in self.state.tasks.values()
                    if task.status in {TaskStatus.DONE, TaskStatus.MERGED, TaskStatus.RELEASED}
                ),
                "events": len(self.events.events),
                "episodes": len(self.episodes.episodes),
                "reflections": self.reflection.status().reflection_count,
                "wishes": self.reflection.status().wish_count,
                "proposals": len(self.governance.proposals),
                "adopted_protocols": sum(
                    1
                    for protocol in self.governance.protocol_registry.protocols.values()
                    if protocol.adoption_status == "adopted"
                ),
            },
        }

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _atomic_text(path: Path, payload: str) -> None:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                if payload and not payload.endswith("\n"):
                    handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
