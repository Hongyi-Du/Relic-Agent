"""Explicit host boundary for HCI's source growth lifecycle.

The vendored appraiser and reconciler are the HCI implementations. Relic
Agent's release shell has neither their OrgWorld, product substrate, nor
ExecutionResult producer, so it must not derive skills, reputation, authority,
or profile-conditioned actions from shell task records.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from relic_agent.source_b3.growth.appraiser import GrowthAppraiser
from relic_agent.source_b3.growth.reconciler import GrowthReconciler
from relic_agent.source_b3.growth.provenance import source_b3_growth_provenance


class SourceB3GrowthHostUnavailableError(RuntimeError):
    """A growth operation was requested without its complete HCI source host."""


@dataclass(frozen=True)
class SourceB3GrowthLifecycleStatus:
    """Auditable capability statement for the bounded source port."""

    source_host_seen: bool
    collected_signal_count: int
    applied_growth_event_count: int
    last_reconcile_tick: int | None

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "relic-agent-source-b3-growth-status-v1",
            **source_b3_growth_provenance(),
            "activation": "explicit_hci_orgworld_and_execution_result_only",
            "scope": [
                "source_coding_profile",
                "source_growth_signal_appraisal",
                "source_skill_reputation_authority_reconciliation",
                "source_go_to_tag_refresh",
                "source_authority_helpers",
            ],
            "source_host_binding": (
                "explicit_source_orgworld_seen"
                if self.source_host_seen
                else "unbound_no_source_orgworld"
            ),
            "collected_signal_count": self.collected_signal_count,
            "applied_growth_event_count": self.applied_growth_event_count,
            "last_reconcile_tick": self.last_reconcile_tick,
            "unavailable_fail_closed": [
                "legacy_compatibility_task_to_growth_translation",
                "legacy_compatibility_profile_conditioned_action_selection",
                "source_orgworld_action_execution",
                "source_product_artifact_substrate",
                "source_runtime_adapter_candidate_generation",
                "source_capability_transfer_arms",
                "source_capability_evaluator",
                "hci_human_seat_host_adapter",
            ],
        }


class SourceB3GrowthLifecycleAdapter:
    """Forward source growth only for an already-mounted HCI execution host.

    The adapter has no method accepting a Relic Agent EventStore, Task, or
    compatibility AgentState. A caller must supply HCI's exact OrgWorld and
    ExecutionResult types; the HCI product helper is loaded before a source
    operation starts, preventing a partial compatibility translation.
    """

    _SOURCE_WORLD_MODULE = "environments.org_env.backend.simulation.world"
    _SOURCE_WORLD_CLASS = "OrgWorld"
    _SOURCE_RESULT_MODULE = "environments.org_env.runtime_adapter.execution"
    _SOURCE_RESULT_CLASS = "ExecutionResult"
    _SOURCE_WORLD_ATTRIBUTES = (
        "world_tick",
        "agents",
        "tasks",
        "product_artifacts",
        "repo_system",
        "growth_events",
    )
    _SOURCE_RESULT_ATTRIBUTES = (
        "action_id",
        "agent_id",
        "action_type",
        "success",
        "failure_reason",
        "created_objects",
        "modified_objects",
        "events",
        "cost_events",
        "messages",
        "state_delta",
        "memory_delta",
        "graph_edges",
    )

    def __init__(self) -> None:
        self._appraiser = GrowthAppraiser()
        self._reconciler = GrowthReconciler()
        self._source_host_seen = False
        self._collected_signal_count = 0
        self._applied_growth_event_count = 0
        self._last_reconcile_tick: int | None = None

    @property
    def appraiser(self) -> GrowthAppraiser:
        """The direct source appraiser for an explicitly mounted HCI host."""

        return self._appraiser

    @property
    def reconciler(self) -> GrowthReconciler:
        """The direct source reconciler for an explicitly mounted HCI host."""

        return self._reconciler

    def collect_source_result(
        self,
        result: Any,
        *,
        actor_id: str,
        world: Any,
    ) -> list[Any]:
        """Append source GrowthSignals for one source ExecutionResult."""

        self._require_source_world(world)
        self._require_source_result(result)
        self._require_product_host()
        normalized_actor_id = str(actor_id or "")
        if not normalized_actor_id or normalized_actor_id not in world.agents:
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_actor_not_owned_by_orgworld"
            )
        if str(getattr(result, "agent_id", "") or "") != normalized_actor_id:
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_execution_result_actor_mismatch"
            )
        if str(getattr(world.agents[normalized_actor_id], "id", "") or "") != normalized_actor_id:
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_orgworld_agent_identity_mismatch"
            )
        try:
            signals = self._appraiser.collect(result, world, normalized_actor_id)
        except Exception as exc:
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_appraisal_unavailable:" + type(exc).__name__
            ) from exc
        self._source_host_seen = True
        self._collected_signal_count += len(signals)
        return signals

    def reconcile_source_growth(
        self,
        *,
        world: Any,
        tick: int | None = None,
    ) -> dict[str, Any]:
        """Apply the source batch reconciler at the source world's current tick."""

        self._require_source_world(world)
        self._require_product_host()
        world_tick = self._non_negative_tick(getattr(world, "world_tick", None))
        requested_tick = world_tick if tick is None else self._non_negative_tick(tick)
        if requested_tick != world_tick:
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_tick_must_match_orgworld"
            )
        before = len(getattr(world, "growth_events"))
        try:
            outcome = self._reconciler.run(world, requested_tick)
        except Exception as exc:
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_reconciliation_unavailable:" + type(exc).__name__
            ) from exc
        after = len(getattr(world, "growth_events"))
        self._source_host_seen = True
        self._applied_growth_event_count += max(0, after - before)
        self._last_reconcile_tick = requested_tick
        return dict(outcome)

    def status(self) -> SourceB3GrowthLifecycleStatus:
        """Report only source-manager activity, never compatibility shell data."""

        return SourceB3GrowthLifecycleStatus(
            source_host_seen=self._source_host_seen,
            collected_signal_count=self._collected_signal_count,
            applied_growth_event_count=self._applied_growth_event_count,
            last_reconcile_tick=self._last_reconcile_tick,
        )

    @classmethod
    def _require_source_world(cls, world: Any) -> None:
        if world is None:
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_orgworld_required"
            )
        world_type = type(world)
        if (
            world_type.__module__ != cls._SOURCE_WORLD_MODULE
            or world_type.__name__ != cls._SOURCE_WORLD_CLASS
        ):
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_requires_hci_orgworld"
            )
        cls._require_mounted_source_type(
            world,
            module_name=cls._SOURCE_WORLD_MODULE,
            class_name=cls._SOURCE_WORLD_CLASS,
            unavailable_code="source_b3_growth_orgworld_source_type_not_mounted",
            mismatch_code="source_b3_growth_requires_hci_orgworld",
        )
        missing = [
            name for name in cls._SOURCE_WORLD_ATTRIBUTES if not hasattr(world, name)
        ]
        if missing:
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_orgworld_missing_fields:" + ",".join(missing)
            )
        if not isinstance(world.agents, Mapping):
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_orgworld_agents_must_be_mapping"
            )
        if not isinstance(world.tasks, Mapping):
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_orgworld_tasks_must_be_mapping"
            )
        if not isinstance(world.product_artifacts, Mapping):
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_orgworld_product_artifacts_must_be_mapping"
            )
        if not isinstance(world.growth_events, list):
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_orgworld_growth_events_must_be_list"
            )
        cls._non_negative_tick(getattr(world, "world_tick", None))

    @classmethod
    def _require_source_result(cls, result: Any) -> None:
        if result is None:
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_execution_result_required"
            )
        result_type = type(result)
        if (
            result_type.__module__ != cls._SOURCE_RESULT_MODULE
            or result_type.__name__ != cls._SOURCE_RESULT_CLASS
        ):
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_requires_hci_execution_result"
            )
        cls._require_mounted_source_type(
            result,
            module_name=cls._SOURCE_RESULT_MODULE,
            class_name=cls._SOURCE_RESULT_CLASS,
            unavailable_code="source_b3_growth_execution_result_source_type_not_mounted",
            mismatch_code="source_b3_growth_requires_hci_execution_result",
        )
        missing = [
            name for name in cls._SOURCE_RESULT_ATTRIBUTES if not hasattr(result, name)
        ]
        if missing:
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_execution_result_missing_fields:" + ",".join(missing)
            )
        if not isinstance(getattr(result, "action_id"), str) or not result.action_id:
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_execution_result_action_id_required"
            )
        if not isinstance(getattr(result, "agent_id"), str) or not result.agent_id:
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_execution_result_agent_id_required"
            )
        if not isinstance(getattr(result, "action_type"), str) or not result.action_type:
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_execution_result_action_type_required"
            )
        if not isinstance(getattr(result, "success"), bool):
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_execution_result_success_must_be_boolean"
            )
        if not isinstance(getattr(result, "failure_reason"), str):
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_execution_result_failure_reason_must_be_string"
            )
        for name in (
            "created_objects",
            "modified_objects",
            "events",
            "cost_events",
            "messages",
            "memory_delta",
            "graph_edges",
        ):
            if not isinstance(getattr(result, name), list):
                raise SourceB3GrowthHostUnavailableError(
                    "source_b3_growth_execution_result_" + name + "_must_be_list"
                )
        if not isinstance(getattr(result, "state_delta"), Mapping):
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_execution_result_state_delta_must_be_mapping"
            )

    @staticmethod
    def _require_mounted_source_type(
        value: Any,
        *,
        module_name: str,
        class_name: str,
        unavailable_code: str,
        mismatch_code: str,
    ) -> None:
        """Require exact identity with a class from a mounted HCI module.

        A matching ``__module__``/class-name pair alone can be assigned to a
        release-shell object.  A usable source host necessarily retains the
        module that created its classes in ``sys.modules``.  Requiring that
        module's exported class object prevents a compatibility dataclass or
        namespace from becoming an implicit HCI host.
        """

        source_module = sys.modules.get(module_name)
        if source_module is None:
            raise SourceB3GrowthHostUnavailableError(unavailable_code)
        source_class = getattr(source_module, class_name, None)
        if not isinstance(source_class, type) or type(value) is not source_class:
            raise SourceB3GrowthHostUnavailableError(mismatch_code)

    @staticmethod
    def _require_product_host() -> None:
        try:
            product_objects = import_module("environments.org_env.product.objects")
        except (ImportError, ModuleNotFoundError) as exc:
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_product_host_required"
            ) from exc
        if not callable(getattr(product_objects, "artifact_purpose", None)):
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_product_artifact_purpose_required"
            )

    @staticmethod
    def _non_negative_tick(value: Any) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_orgworld_tick_required"
            )
        tick = value
        if tick < 0:
            raise SourceB3GrowthHostUnavailableError(
                "source_b3_growth_orgworld_tick_required"
            )
        return tick


__all__ = [
    "SourceB3GrowthHostUnavailableError",
    "SourceB3GrowthLifecycleAdapter",
    "SourceB3GrowthLifecycleStatus",
]
