"""Compatibility-only deterministic mock runtime.

This runner is retained while the source HCI host adapter is ported. It keeps
the public CLI, trace contract, Inspector, and launchers runnable, but it is
not an authoritative B3/HCI execution runtime. Its emitted envelopes are
validated by the vendored source core in observation-only mode.
"""

from __future__ import annotations

import json
import os
import random
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from relic_agent.config import OrganizationConfig
from relic_agent.decision import Candidate, DecisionTrace, OrganizationPolicy
from relic_agent.episodes import EpisodeManager, OrgEpisode
from relic_agent.events import Event, EventStore
from relic_agent.governance import GovernanceManager
from relic_agent.governance.manager import SourceProposalGenerationUnavailableError
from relic_agent.organization import AgentState, OrganizationState, Task, TaskStatus
from relic_agent.reflection import ReflectionManager
from relic_agent.replay.trace import build_trace
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
        for task in self.state.tasks.values():
            if task.owner_id:
                task.status = TaskStatus.IN_PROGRESS
                self.state.agents[task.owner_id].active_task_ids.append(task.task_id)
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
        self.policy = OrganizationPolicy(profile_conditioning=True)
        self.decision_traces: list[DecisionTrace] = []
        self.frames: list[dict[str, Any]] = []
        self._recent_events: dict[str, list[Event]] = {
            agent_id: [] for agent_id in self.state.agents
        }
        self._rngs = {
            agent_id: random.Random(f"{config.runtime.seed}:{agent_id}")
            for agent_id in self.state.agents
        }
        self._last_frame_event_index = 0
        self._last_frame_decision_index = 0
        self._last_frame_protocol_event_index = 0
        self._source_core_bridge: SourceCoreObservationBridge | None = None
        self._source_proposal_generation_unavailable = False

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
                for agent_id in sorted(self.state.agents):
                    self._step_agent(self.state.agents[agent_id])
                self._maybe_reflect_and_propose()
                self._adopt_ready_proposals()
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

    def _step_agent(self, agent: AgentState) -> None:
        candidates = self._candidates(agent)
        chosen, trace = self.policy.select(
            tick=self.state.tick,
            agent=agent,
            candidates=candidates,
            rng=self._rngs[agent.agent_id],
        )
        self.decision_traces.append(trace)
        if chosen is None:
            return
        if chosen.action_id == "approve_proposal":
            self._approve(agent, chosen.object_id)
        elif chosen.action_id == "claim_task":
            self._claim_task(agent, chosen.object_id)
        elif chosen.action_id == "work_task":
            self._work_task(agent, chosen.object_id)
        elif chosen.action_id == "use_protocol":
            self._use_protocol(agent, chosen.object_id)

    def _candidates(self, agent: AgentState) -> list[Candidate]:
        approval_candidates = []
        for proposal in self.governance.proposals.values():
            if (
                proposal.status == "under_review"
                and agent.agent_id in proposal.approval_required_from
                and agent.agent_id not in proposal.approved_by
            ):
                approval_candidates.append(
                    Candidate(
                        action_id="approve_proposal",
                        object_id=proposal.proposal_id,
                        features={
                            "proposal_endorsement": float(proposal.usefulness_score or 0.0),
                            "proposal_skepticism": float(proposal.risk_score or 0.0),
                            "institutional_memory_gain": 0.8,
                        },
                    )
                )
        if approval_candidates:
            return approval_candidates

        candidates: list[Candidate] = []
        for task in self.state.tasks.values():
            status = task.status.value if isinstance(task.status, TaskStatus) else str(task.status)
            if task.owner_id == agent.agent_id and status not in {
                "done",
                "merged",
                "released",
                "abandoned",
            }:
                match = self._skill_match(agent, task)
                candidates.append(
                    Candidate(
                        action_id="work_task",
                        object_id=task.task_id,
                        features={
                            "progress_gain": 0.9,
                            "task_priority": task.priority / 5.0,
                            "skill_match": match,
                            "failure_risk_from_low_skill": 1.0 - match,
                            "attention_cost": 0.15,
                        },
                    )
                )
            elif task.owner_id is None and status == "open":
                match = self._skill_match(agent, task)
                candidates.append(
                    Candidate(
                        action_id="claim_task",
                        object_id=task.task_id,
                        features={
                            "dependency_unlock": 0.9,
                            "task_priority": task.priority / 5.0,
                            "skill_match": match,
                            "role_affinity": match,
                            "coordination_gain": 0.4,
                        },
                    )
                )
        if candidates:
            return candidates
        for protocol in self.governance.protocol_registry.protocols.values():
            if protocol.adoption_status == "adopted":
                candidates.append(
                    Candidate(
                        action_id="use_protocol",
                        object_id=protocol.protocol_id,
                        features={
                            "protocol_use_potential": 0.8,
                            "institutional_memory_gain": 0.5,
                            "coordination_gain": 0.3,
                        },
                    )
                )
        return candidates

    @staticmethod
    def _skill_match(agent: AgentState, task: Task) -> float:
        if not task.required_skills:
            return 0.5
        return sum(agent.skill(skill) for skill in task.required_skills) / len(task.required_skills)

    def _approve(self, agent: AgentState, proposal_id: str) -> None:
        proposal = self.governance.approve(proposal_id, agent.agent_id, tick=self.state.tick)
        self._emit(
            event_type="proposal_approved",
            actor_id=agent.agent_id,
            object_ids=(proposal_id,),
            payload={"summary": f"{agent.display_name} approved {proposal.title}."},
        )

    def _claim_task(self, agent: AgentState, task_id: str) -> None:
        task = self.state.tasks[task_id]
        if task.owner_id is not None:
            return
        task.owner_id = agent.agent_id
        task.status = TaskStatus.IN_PROGRESS
        task.history.append(
            {"tick": self.state.tick, "event": "claimed", "agent_id": agent.agent_id}
        )
        if task_id not in agent.active_task_ids:
            agent.active_task_ids.append(task_id)
        self._emit(
            event_type="task_started",
            actor_id=agent.agent_id,
            object_ids=(task_id,),
            payload={"summary": f"{agent.display_name} claimed {task.title}.", "title": task.title},
        )

    def _work_task(self, agent: AgentState, task_id: str) -> None:
        task = self.state.tasks[task_id]
        if task.owner_id != agent.agent_id:
            return
        if not any(
            task_id in episode.linked_object_ids for episode in self.episodes.episodes.values()
        ):
            self._emit(
                event_type="task_started",
                actor_id=agent.agent_id,
                object_ids=(task_id,),
                payload={
                    "summary": f"{agent.display_name} started {task.title}.",
                    "title": task.title,
                },
            )
        progress = min(1.0, task.progress_score + 0.5)
        adopted_protocol = self._adopted_protocol_id()
        if progress >= 1.0 and adopted_protocol is None:
            task.progress_score = 0.75
            task.status = TaskStatus.BLOCKED
            task.history.append(
                {"tick": self.state.tick, "event": "blocked_pending_review_protocol"}
            )
            self._emit(
                event_type="task_blocked",
                actor_id=agent.agent_id,
                object_ids=(task_id,),
                payload={"summary": f"{task.title} needs an accountable peer-review rule."},
            )
            return
        task.progress_score = progress
        task.status = TaskStatus.IN_PROGRESS
        task.actual_effort += 1.0
        self._emit(
            event_type="task_progressed",
            actor_id=agent.agent_id,
            object_ids=(task_id,),
            payload={
                "summary": f"{task.title} reached {progress:.0%} progress.",
                "progress": progress,
            },
        )
        if progress >= 1.0 and adopted_protocol is not None:
            self.governance.protocol_registry.use(
                agent.agent_id,
                adopted_protocol,
                tick=self.state.tick,
                context_id=f"task-completion:{task_id}",
                task_id=task_id,
            )
            self._emit(
                event_type="protocol_used",
                actor_id=agent.agent_id,
                object_ids=(adopted_protocol, task_id),
                payload={"summary": f"The adopted protocol governed completion of {task.title}."},
            )
            task.status = TaskStatus.DONE
            task.history.append(
                {"tick": self.state.tick, "event": "completed", "protocol_id": adopted_protocol}
            )
            if task_id in agent.active_task_ids:
                agent.active_task_ids.remove(task_id)
            self._emit(
                event_type="task_completed",
                actor_id=agent.agent_id,
                object_ids=(task_id, adopted_protocol),
                payload={"summary": f"{task.title} completed with peer-review governance."},
            )

    def _use_protocol(self, agent: AgentState, protocol_id: str) -> None:
        self.governance.protocol_registry.use(
            agent.agent_id,
            protocol_id,
            tick=self.state.tick,
            context_id=f"organization-cycle:{self.state.tick}",
        )
        self._emit(
            event_type="protocol_used",
            actor_id=agent.agent_id,
            object_ids=(protocol_id,),
            payload={"summary": f"{agent.display_name} used an adopted organization protocol."},
        )

    def _maybe_reflect_and_propose(self) -> None:
        if self._source_proposal_generation_unavailable:
            return
        if self.governance.proposals:
            return
        if self.state.tick % self.config.runtime.reflection_interval != 0:
            return
        candidates = [
            agent
            for agent in self.state.agents.values()
            if any(
                event.event_type == "task_blocked" for event in self._recent_events[agent.agent_id]
            )
        ]
        if not candidates:
            return
        agent = sorted(candidates, key=lambda item: item.agent_id)[0]
        reflection, wish = self.reflection.reflect(
            tick=self.state.tick,
            agent=agent,
            episodes=self.episodes.open_for_agent(agent.agent_id),
            recent_events=self._recent_events[agent.agent_id],
        )
        self._emit(
            event_type="reflection_completed",
            actor_id=agent.agent_id,
            object_ids=(reflection.reflection_id,),
            payload={"summary": "Private reflection completed."},
            visibility="private",
        )
        self._emit(
            event_type="wish_created",
            actor_id=agent.agent_id,
            object_ids=(wish.wish_id,),
            payload={"summary": "Private organization need recorded."},
            visibility="private",
        )
        try:
            proposal = self.governance.propose_from_wish(wish, tick=self.state.tick)
        except SourceProposalGenerationUnavailableError:
            # A mock reflection is not the HCI LLM proposal generator.  Keep
            # the release shell runnable but do not turn its private wish into
            # a fabricated governance object.
            self._source_proposal_generation_unavailable = True
            return
        self.state.proposals = self.governance.proposals
        self.state.proposal_object_id_projection = self.governance.public_proposal_object_ids()
        # Source proposals are descriptive until adoption; the registry mirror
        # appears only when the source manager materializes its ProtocolSpec.
        self.state.protocols = self.governance.protocol_registry.protocols
        for episode_id in proposal.source_episode_ids:
            episode = self.episodes.episodes.get(episode_id)
            if episode and proposal.proposal_id not in episode.linked_proposal_ids:
                episode.linked_proposal_ids.append(proposal.proposal_id)
        self._emit(
            event_type="proposal_created",
            actor_id=agent.agent_id,
            object_ids=(proposal.proposal_id,),
            payload={"summary": proposal.summary, "title": proposal.title},
        )

    def _adopt_ready_proposals(self) -> None:
        for proposal in list(self.governance.proposals.values()):
            if not self.governance.ready(proposal, tick=self.state.tick):
                continue
            protocol_id = self.governance.adopt(proposal.proposal_id, tick=self.state.tick)
            self.state.protocols = self.governance.protocol_registry.protocols
            self.state.proposal_object_id_projection = self.governance.public_proposal_object_ids()
            self._emit(
                event_type="protocol_adopted",
                actor_id=proposal.approved_by[0] if proposal.approved_by else "",
                object_ids=(protocol_id, proposal.proposal_id),
                payload={"summary": f"The organization adopted {proposal.title}."},
            )

    def _adopted_protocol_id(self) -> str | None:
        for protocol in self.governance.protocol_registry.protocols.values():
            if protocol.adoption_status == "adopted":
                return protocol.protocol_id
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
        episode = self.episodes.observe(event)
        if episode is not None and event.event_type == "protocol_adopted":
            for object_id in event.object_ids:
                if (
                    object_id in self.governance.protocol_registry.protocols
                    and object_id not in episode.produced_protocols
                ):
                    episode.produced_protocols.append(object_id)
        if actor_id in self._recent_events:
            self._recent_events[actor_id].append(event)
            self._recent_events[actor_id] = self._recent_events[actor_id][-12:]
        if self._source_core_bridge is not None:
            self._source_core_bridge.publish_legacy_event(event)
        if visibility in {"organization", "public"}:
            self._capture_frame()
        return event

    def _capture_frame(self, *, force: bool = False) -> None:
        events = self.events.public_since(self._last_frame_event_index)
        decisions = [
            {
                "decision_id": f"decision_{index:06d}",
                "tick": trace.tick,
                "agent_id": trace.agent_id,
                "chosen_action_id": trace.chosen_action_id,
                "chosen_object_id": trace.chosen_object_id,
            }
            for index, trace in enumerate(
                self.decision_traces[self._last_frame_decision_index :],
                start=self._last_frame_decision_index + 1,
            )
        ]
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
        self._last_frame_decision_index = len(self.decision_traces)
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
            "timeline": [dict(item) for item in episode.timeline],
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
                "authority": "legacy_compatibility_runtime",
            },
            "source_core": source_core.status().as_dict(),
            "source_proposal_lifecycle": self.governance.source_status(),
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
                "reflections": len(self.reflection.reflections),
                "wishes": len(self.reflection.wishes),
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
