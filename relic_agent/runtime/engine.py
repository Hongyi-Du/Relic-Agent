"""Source-native execution host for a B3 Relic organization.

The release shell previously advanced a clock beside a bespoke state model.
This host builds and steps the vendored Relic B3 ``OrgWorld`` directly.
All public output is a privacy-filtered projection of that real world.
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

from environments.org_env.backend.simulation import OrgWorld
from environments.org_env.config.scenarios import default_scenario

from relic_agent.config import OrganizationConfig
from relic_agent.replay.trace import build_trace
from relic_agent.source_host import (
    archived_compat_modules_loaded,
    assert_no_forbidden_loaded_modules,
    project_public_frame,
    source_host_provenance,
    structural_conformance,
    verify_critical_vendor_blobs,
)
from relic_agent.source_host.provenance import DISABLED_CAPABILITIES


RUN_SCHEMA_VERSION = "relic-agent-run-v2"
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# Publish every completed source tick.  Frames are append-only and the public
# trace is small for the shipped 72-tick workflow, so this gives live Inspector
# clients a faithful current source-world projection rather than a shell clock
# or a delayed batch checkpoint.
_TRACE_CHECKPOINT_TICKS = 1


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
    """Build and step one source-native B3 ``OrgWorld`` instance."""

    def __init__(self, config: OrganizationConfig) -> None:
        self.config = config
        self.world: OrgWorld | None = None
        self.frames: list[dict[str, Any]] = []
        self._action_mark = 0
        self._protocol_event_mark = 0

    def run(
        self,
        *,
        output_root: str | Path,
        ticks: int | None = None,
        run_id: str | None = None,
    ) -> RunResult:
        total_ticks = ticks if ticks is not None else self.config.runtime.ticks
        if not isinstance(total_ticks, int) or isinstance(total_ticks, bool) or total_ticks < 1:
            raise ValueError("ticks must be a positive integer")
        actual_run_id = run_id or str(uuid.uuid4())
        if not _RUN_ID_RE.fullmatch(actual_run_id) or ".." in actual_run_id:
            raise ValueError("run_id must be a safe 1-128 character identifier")

        blob_verification = verify_critical_vendor_blobs()
        if not blob_verification["verified"]:
            raise RuntimeError("source_vendor_blob_verification_failed")

        started_at = datetime.now(UTC).isoformat()
        output_directory = Path(output_root).expanduser().resolve() / actual_run_id
        output_directory.mkdir(parents=True, exist_ok=False)
        config_snapshot_path = output_directory / "config.yaml"
        trace_path = output_directory / "trace.json"
        status_path = output_directory / "status.json"
        manifest_path = output_directory / "run.json"
        self._atomic_text(config_snapshot_path, self.config.source_path.read_text(encoding="utf-8"))
        self._write_status(status_path, actual_run_id, "running", tick=0)

        try:
            # ``default_scenario`` is intentionally called without an explicit
            # condition override.  Its source default is B3, which preserves
            # the source's normal run id and the pinned 336-tick structures.
            self.world = OrgWorld(default_scenario(seed=self.config.runtime.seed)).build()
            self._capture_frame()
            trace = self._write_public_trace(trace_path, actual_run_id)
            for _ in range(total_ticks):
                self.world.step()
                self._capture_frame()
                if self.world.world_tick % _TRACE_CHECKPOINT_TICKS == 0:
                    trace = self._write_public_trace(trace_path, actual_run_id)
                self._write_status(
                    status_path,
                    actual_run_id,
                    "running",
                    tick=self.world.world_tick,
                )
            module_boundary = assert_no_forbidden_loaded_modules()
            module_boundary["archived_compat_modules_loaded"] = archived_compat_modules_loaded()
        except BaseException as exc:
            current_tick = int(getattr(self.world, "world_tick", 0) or 0)
            self._write_status(
                status_path,
                actual_run_id,
                "failed",
                tick=current_tick,
                error_type=type(exc).__name__,
            )
            raise

        manifest = self._manifest(
            run_id=actual_run_id,
            started_at=started_at,
            finished_at=datetime.now(UTC).isoformat(),
            trace=trace,
            blob_verification=blob_verification,
            module_boundary=module_boundary,
        )
        self._atomic_json(manifest_path, manifest)
        self._write_status(
            status_path,
            actual_run_id,
            "completed",
            tick=self._world().world_tick,
        )
        world = self._world()
        return RunResult(
            run_id=actual_run_id,
            run_directory=output_directory,
            manifest_path=manifest_path,
            trace_path=trace_path,
            status="completed",
            ticks=total_ticks,
            event_count=len(world.events),
            completed_task_count=self._completed_task_count(world),
            adopted_protocol_count=self._adopted_protocol_count(world),
        )

    def _world(self) -> OrgWorld:
        if self.world is None:
            raise RuntimeError("source_orgworld_not_built")
        return self.world

    def _capture_frame(self) -> None:
        world = self._world()
        self.frames.append(
            project_public_frame(
                world,
                sequence=len(self.frames),
                organization_id=self.config.organization_id,
                organization_name=self.config.name,
                action_start=self._action_mark,
                protocol_event_start=self._protocol_event_mark,
            )
        )
        self._action_mark = len(world.action_log)
        self._protocol_event_mark = len(world.protocol_registry.events)

    def _write_public_trace(self, path: Path, run_id: str) -> dict[str, Any]:
        trace = build_trace(
            run_id=run_id,
            organization_id=self.config.organization_id,
            config_digest=self.config.digest,
            frames=self.frames,
        )
        self._atomic_json(path, trace)
        return trace

    def _manifest(
        self,
        *,
        run_id: str,
        started_at: str,
        finished_at: str,
        trace: dict[str, Any],
        blob_verification: dict[str, object],
        module_boundary: dict[str, object],
    ) -> dict[str, Any]:
        world = self._world()
        conformance = structural_conformance(world)
        evidence = self._workflow_evidence(world)
        workflow_acceptance = "passed" if evidence["complete"] else "unavailable_fail_closed"
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
                "ticks": world.world_tick,
                "provider_calls_made": 0,
                "authority": "source_native_orgworld",
                "action_selection": "source_profile_policy",
                "action_execution": "source_orgworld_step",
                "workflow_acceptance": workflow_acceptance,
                "workflow_acceptance_reason": evidence["reason"],
                "paper_result_evidence": "not_produced_by_standalone_org_host",
            },
            "source": {
                **source_host_provenance(),
                "critical_blob_verification": blob_verification,
                "loaded_module_boundary": module_boundary,
            },
            "adapters": {
                "baseline": {
                    "requested": self.config.runtime.baseline,
                    "effective": "source_default_b3",
                    "accepted_values": ["", "b3", "full", "sociogenesis"],
                },
                "profile_causality": {
                    "mode": "source_recorded_no_op",
                    "source_record_count": len(
                        getattr(world, "profile_causality_records", ()) or ()
                    ),
                },
                "capability_transfer": {
                    "mode": "empty_no_op",
                    "arguments": "rejected_fail_closed",
                },
                "public_identity": {
                    "source_agent_id": "scarlett",
                    "public_display_name": "Los Xi",
                    "mutation": "projection_only",
                },
            },
            "disabled_capabilities": list(DISABLED_CAPABILITIES),
            "conformance": {
                "source_native_world_built": True,
                "source_native_steps": world.world_tick,
                "structures": conformance,
            },
            "outputs": {
                "config_snapshot": "config.yaml",
                "status": "status.json",
                "trace": "trace.json",
                "trace_sha256": trace["trace_sha256"],
            },
            "summary": {
                "agents": len(world.agents),
                "tasks": len(world.tasks),
                "completed_tasks": self._completed_task_count(world),
                "events": len(world.events),
                "episodes": len(world.episode_manager.episodes),
                "reflections": len(world.reflection_manager.reflections),
                "wishes": len(world.reflection_manager.wishes),
                "proposals": len(world.proposal_manager.proposals),
                "protocol_specs": len(world.proposal_manager.protocol_specs),
                "adopted_protocols": self._adopted_protocol_count(world),
            },
        }

    @staticmethod
    def _workflow_evidence(world: OrgWorld) -> dict[str, object]:
        checks = {
            "source_actions": len(world.action_log) > 0,
            "source_episodes": len(world.episode_manager.episodes) > 0,
            "source_reflections": len(world.reflection_manager.reflections) > 0,
            "source_proposals": len(world.proposal_manager.proposals) > 0,
            "source_protocol_specs": len(world.proposal_manager.protocol_specs) > 0,
        }
        missing = [name for name, present in checks.items() if not present]
        return {
            "complete": not missing,
            "checks": checks,
            "reason": (
                "real_source_action_episode_reflection_proposal_protocol_evidence_present"
                if not missing
                else "missing_real_source_evidence:" + ",".join(missing)
            ),
        }

    @staticmethod
    def _completed_task_count(world: OrgWorld) -> int:
        terminal = {"done", "merged", "released", "completed"}
        return sum(
            _text(getattr(task, "status", "")) in terminal
            for task in world.tasks.values()
        )

    @staticmethod
    def _adopted_protocol_count(world: OrgWorld) -> int:
        return sum(
            _text(getattr(protocol, "adoption_status", "")) == "adopted"
            for protocol in world.protocol_registry.protocols.values()
        )

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

    @classmethod
    def _write_status(
        cls,
        path: Path,
        run_id: str,
        status: str,
        *,
        tick: int,
        error_type: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "schema_version": "relic-agent-status-v2",
            "run_id": run_id,
            "status": status,
            "tick": tick,
            "authority": "source_native_orgworld",
        }
        if error_type:
            payload["error_type"] = error_type
        cls._atomic_json(path, payload)


def _text(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


__all__ = ["OrganizationRuntime", "RUN_SCHEMA_VERSION", "RunResult"]
