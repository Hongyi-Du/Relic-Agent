"""Explicit boundary for HCI's structural protocol-affordance policy.

The source module can remove a candidate that violates a machine-checkable
adopted protocol. It is not a replacement action selector: source candidate
generation, profile scoring, attractor guarding, and execution remain owned by
the full HCI runtime adapter.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from relic_agent.source_b3.policy import protocol_affordance
from relic_agent.source_b3.policy.provenance import source_b3_policy_provenance


class SourceB3PolicyHostUnavailableError(RuntimeError):
    """A structural protocol mask was requested outside the HCI action host."""


@dataclass(frozen=True)
class SourceB3PolicyLifecycleStatus:
    """Auditable capability statement for the bounded source policy port."""

    source_host_seen: bool
    filter_calls: int
    prevented_action_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "relic-agent-source-b3-policy-status-v1",
            **source_b3_policy_provenance(),
            "activation": "explicit_hci_orgworld_and_action_candidates_only",
            "scope": [
                "source_adopted_protocol_liveness_check",
                "source_structural_review_before_merge_mask",
                "source_prevented_action_ledger",
            ],
            "source_host_binding": (
                "explicit_source_orgworld_seen"
                if self.source_host_seen
                else "unbound_no_source_orgworld"
            ),
            "filter_calls": self.filter_calls,
            "prevented_action_count": self.prevented_action_count,
            "unavailable_fail_closed": [
                "legacy_compatibility_candidate_to_source_action_translation",
                "legacy_compatibility_profile_conditioned_action_selection",
                "source_runtime_adapter_candidate_generation",
                "source_runtime_adapter_action_execution",
                "source_attractor_guard",
                "source_full_profile_selector",
                "hci_human_seat_host_adapter",
            ],
        }


class SourceB3PolicyLifecycleAdapter:
    """Apply only source structural masks to caller-provided HCI candidates."""

    _SOURCE_WORLD_MODULE = "environments.org_env.backend.simulation.world"
    _SOURCE_WORLD_CLASS = "OrgWorld"
    _SOURCE_CANDIDATE_MODULE = "agent_sdk.lived.core.contracts"
    _SOURCE_CANDIDATE_CLASS = "ActionCandidate"
    _SOURCE_WORLD_ATTRIBUTES = (
        "world_tick",
        "agents",
        "proposal_manager",
        "repo_system",
        "condition_spec",
        "events",
    )

    def __init__(self) -> None:
        self._source_host_seen = False
        self._filter_calls = 0
        self._prevented_action_count = 0

    def filter_source_candidates(
        self,
        candidates: Sequence[Any],
        *,
        agent_id: str,
        world: Any,
        tick: int | None = None,
    ) -> tuple[list[Any], list[tuple[Any, str]]]:
        """Run the exact source mask and source prevented-action ledger.

        This returns the same kept and blocked collections as the source helper.
        It deliberately does not select from the kept candidates or execute one.
        """

        self._require_source_world(world)
        normalized_agent_id = str(agent_id or "")
        if not normalized_agent_id or normalized_agent_id not in world.agents:
            raise SourceB3PolicyHostUnavailableError(
                "source_b3_policy_agent_not_owned_by_orgworld"
            )
        if str(getattr(world.agents[normalized_agent_id], "id", "") or "") != normalized_agent_id:
            raise SourceB3PolicyHostUnavailableError(
                "source_b3_policy_orgworld_agent_identity_mismatch"
            )
        if isinstance(candidates, (str, bytes, Mapping)):
            raise SourceB3PolicyHostUnavailableError(
                "source_b3_policy_candidates_must_be_sequence"
            )
        pool = list(candidates)
        for candidate in pool:
            self._require_source_candidate(candidate)
        world_tick = self._non_negative_tick(getattr(world, "world_tick", None))
        requested_tick = world_tick if tick is None else self._non_negative_tick(tick)
        if requested_tick != world_tick:
            raise SourceB3PolicyHostUnavailableError(
                "source_b3_policy_tick_must_match_orgworld"
            )
        try:
            kept, blocked = protocol_affordance.filter_candidates(
                pool,
                normalized_agent_id,
                world,
            )
            protocol_affordance.record_prevented(
                world,
                normalized_agent_id,
                blocked,
                requested_tick,
            )
        except Exception as exc:
            raise SourceB3PolicyHostUnavailableError(
                "source_b3_policy_structural_mask_unavailable:" + type(exc).__name__
            ) from exc
        self._source_host_seen = True
        self._filter_calls += 1
        self._prevented_action_count += len(blocked)
        return list(kept), list(blocked)

    def status(self) -> SourceB3PolicyLifecycleStatus:
        """Report source mask activity and no invented selection activity."""

        return SourceB3PolicyLifecycleStatus(
            source_host_seen=self._source_host_seen,
            filter_calls=self._filter_calls,
            prevented_action_count=self._prevented_action_count,
        )

    @classmethod
    def _require_source_world(cls, world: Any) -> None:
        if world is None:
            raise SourceB3PolicyHostUnavailableError(
                "source_b3_policy_orgworld_required"
            )
        world_type = type(world)
        if (
            world_type.__module__ != cls._SOURCE_WORLD_MODULE
            or world_type.__name__ != cls._SOURCE_WORLD_CLASS
        ):
            raise SourceB3PolicyHostUnavailableError(
                "source_b3_policy_requires_hci_orgworld"
            )
        cls._require_mounted_source_type(
            world,
            module_name=cls._SOURCE_WORLD_MODULE,
            class_name=cls._SOURCE_WORLD_CLASS,
            unavailable_code="source_b3_policy_orgworld_source_type_not_mounted",
            mismatch_code="source_b3_policy_requires_hci_orgworld",
        )
        missing = [
            name for name in cls._SOURCE_WORLD_ATTRIBUTES if not hasattr(world, name)
        ]
        if missing:
            raise SourceB3PolicyHostUnavailableError(
                "source_b3_policy_orgworld_missing_fields:" + ",".join(missing)
            )
        if not isinstance(world.agents, Mapping):
            raise SourceB3PolicyHostUnavailableError(
                "source_b3_policy_orgworld_agents_must_be_mapping"
            )
        if not isinstance(world.events, list):
            raise SourceB3PolicyHostUnavailableError(
                "source_b3_policy_orgworld_events_must_be_list"
            )
        protocol_specs = getattr(world.proposal_manager, "protocol_specs", None)
        if not isinstance(protocol_specs, Mapping):
            raise SourceB3PolicyHostUnavailableError(
                "source_b3_policy_orgworld_protocol_specs_must_be_mapping"
            )
        pull_requests = getattr(getattr(world.repo_system, "repo", None), "pull_requests", None)
        if not isinstance(pull_requests, Mapping):
            raise SourceB3PolicyHostUnavailableError(
                "source_b3_policy_orgworld_pull_requests_must_be_mapping"
            )
        cls._non_negative_tick(getattr(world, "world_tick", None))

    @classmethod
    def _require_source_candidate(cls, candidate: Any) -> None:
        candidate_type = type(candidate)
        if (
            candidate_type.__module__ != cls._SOURCE_CANDIDATE_MODULE
            or candidate_type.__name__ != cls._SOURCE_CANDIDATE_CLASS
        ):
            raise SourceB3PolicyHostUnavailableError(
                "source_b3_policy_requires_hci_action_candidate"
            )
        cls._require_mounted_source_type(
            candidate,
            module_name=cls._SOURCE_CANDIDATE_MODULE,
            class_name=cls._SOURCE_CANDIDATE_CLASS,
            unavailable_code="source_b3_policy_action_candidate_source_type_not_mounted",
            mismatch_code="source_b3_policy_requires_hci_action_candidate",
        )
        action_type = getattr(candidate, "action_type", None)
        if not isinstance(action_type, str) or not action_type:
            raise SourceB3PolicyHostUnavailableError(
                "source_b3_policy_candidate_action_type_required"
            )
        parameters = getattr(candidate, "parameters", None)
        if parameters is not None and not isinstance(parameters, Mapping):
            raise SourceB3PolicyHostUnavailableError(
                "source_b3_policy_candidate_parameters_must_be_mapping"
            )
        for name in ("source", "rationale", "target_uid"):
            if not hasattr(candidate, name):
                raise SourceB3PolicyHostUnavailableError(
                    "source_b3_policy_action_candidate_missing_fields:" + name
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
        """Require a mounted HCI module's exact class, not a lookalike.

        The compatibility shell can manufacture attributes but cannot satisfy
        this identity gate: an active source object must come from the module
        that is still mounted in the source process.
        """

        source_module = sys.modules.get(module_name)
        if source_module is None:
            raise SourceB3PolicyHostUnavailableError(unavailable_code)
        source_class = getattr(source_module, class_name, None)
        if not isinstance(source_class, type) or type(value) is not source_class:
            raise SourceB3PolicyHostUnavailableError(mismatch_code)

    @staticmethod
    def _non_negative_tick(value: Any) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise SourceB3PolicyHostUnavailableError(
                "source_b3_policy_orgworld_tick_required"
            )
        tick = value
        if tick < 0:
            raise SourceB3PolicyHostUnavailableError(
                "source_b3_policy_orgworld_tick_required"
            )
        return tick


__all__ = [
    "SourceB3PolicyHostUnavailableError",
    "SourceB3PolicyLifecycleAdapter",
    "SourceB3PolicyLifecycleStatus",
]
