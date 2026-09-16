"""Explicit host boundary for HCI's reflection-to-wish lifecycle.

The vendored manager is the source implementation.  The Relic Agent release
shell has neither an HCI ``OrgWorld`` nor its OpenAI-compatible cognition
provider, so it must never use the source manager's template fallback to turn
mock task events into private cognitive records.  This adapter therefore only
forwards an already-mounted source world, a closed source episode, and a real
OpenAI-compatible provider boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from relic_agent.source_b3.reflection.batch_manager import ReflectionBatchManager
from relic_agent.source_b3.reflection.manager import ReflectionManager
from relic_agent.source_b3.reflection.objects import AgentReflection, Wish
from relic_agent.source_b3.reflection.provenance import source_b3_reflection_provenance


class SourceB3ReflectionHostUnavailableError(RuntimeError):
    """Raised instead of manufacturing a reflection or wish from shell state."""


@dataclass(frozen=True)
class SourceB3ReflectionLifecycleStatus:
    """Auditable capability statement for the bounded source port."""

    reflection_count: int
    wish_count: int
    batches_run: int
    source_host_seen: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "relic-agent-source-b3-reflection-status-v1",
            **source_b3_reflection_provenance(),
            "activation": (
                "explicit_mounted_hci_orgworld_closed_episode_and_"
                "openai_compatible_provider_only"
            ),
            "scope": [
                "source_reflection_objects",
                "source_closed_episode_reflection_trigger",
                "source_wish_mapping_deduplication",
                "source_memory_log_and_event_linkage",
                "source_reflection_batch_selection",
            ],
            "source_host_binding": (
                "explicit_source_orgworld_seen" if self.source_host_seen
                else "unbound_no_source_orgworld"
            ),
            "reflection_count": self.reflection_count,
            "wish_count": self.wish_count,
            "batches_run": self.batches_run,
            "unavailable_fail_closed": [
                "legacy_compatibility_event_to_reflection_translation",
                "legacy_compatibility_episode_to_reflection_translation",
                "template_reflection_without_source_provider",
                "source_provider_empty_or_failed_result",
                "mock_provider_reflection",
                "native_anthropic_provider",
                "source_llm_prompt_assets_without_hci_host",
                "source_wish_to_proposal_generation",
                "source_growth_policy_execution",
                "hci_human_seat_host_adapter",
            ],
        }


class SourceB3ReflectionLifecycleAdapter:
    """Forward source reflection only when all source-owned inputs are present.

    No ``reflect`` compatibility method is provided.  Calling the full source
    manager requires a real ``OrgWorld`` that has explicitly mounted this exact
    manager, a terminal episode owned by that world's episode manager, and an
    OpenAI-compatible ``generate_json`` provider.  Those conditions prevent the
    old release-shell heuristics or source template fallback from becoming
    evidence of HCI cognition.
    """

    _SOURCE_WORLD_MODULE = "environments.org_env.backend.simulation.world"
    _SOURCE_WORLD_CLASS = "OrgWorld"
    _SOURCE_WORLD_ATTRIBUTES = (
        "world_tick",
        "agents",
        "agent_memories",
        "events",
        "action_log",
        "episode_manager",
        "product_artifacts",
        "agent_log",
        "institutionalization_enabled",
    )
    _OPENAI_COMPATIBLE_PROVIDERS = frozenset({"openai", "http"})

    def __init__(self) -> None:
        self._manager = ReflectionManager()
        self._batch_manager = ReflectionBatchManager()
        self._source_host_seen = False

    @property
    def manager(self) -> ReflectionManager:
        """The unmodified source manager for explicitly mounted HCI hosts."""

        return self._manager

    @property
    def reflections(self) -> Mapping[str, AgentReflection]:
        """Private source objects; the public trace never reads this mapping."""

        return self._manager.reflections

    @property
    def wishes(self) -> Mapping[str, Wish]:
        """Private source objects; the public trace never reads this mapping."""

        return self._manager.wishes

    def reflect_on_closed_source_episode(
        self,
        episode: Any,
        *,
        world: Any,
    ) -> list[AgentReflection]:
        """Run the source close trigger, never a compatibility conversion."""

        self._require_source_world(world)
        self._require_mounted_source_manager(world)
        self._require_closed_owned_episode(episode, world)
        self._require_openai_compatible_provider(world)
        reflections = self._call_source_without_template_fallback(
            lambda: self._manager.on_episode_closed(episode, world)
        )
        self._source_host_seen = True
        return reflections

    def run_source_batch(self, *, world: Any) -> list[AgentReflection]:
        """Run the source batch selector against an explicitly mounted HCI host."""

        self._require_source_world(world)
        self._require_mounted_source_manager(world)
        self._require_openai_compatible_provider(world)
        reflections = self._call_source_without_template_fallback(
            lambda: self._batch_manager.maybe_run_batch(
                world,
                int(getattr(world, "world_tick")),
                getattr(world, "llm_client"),
            )
        )
        self._source_host_seen = True
        return reflections

    def status(self) -> SourceB3ReflectionLifecycleStatus:
        """Report only source-manager records, never compatibility shell data."""

        return SourceB3ReflectionLifecycleStatus(
            reflection_count=len(self._manager.reflections),
            wish_count=len(self._manager.wishes),
            batches_run=self._batch_manager.batches_run,
            source_host_seen=self._source_host_seen,
        )

    @classmethod
    def _require_source_world(cls, world: Any) -> None:
        if world is None:
            raise SourceB3ReflectionHostUnavailableError(
                "source_b3_reflection_orgworld_required"
            )
        world_type = type(world)
        if (
            world_type.__module__ != cls._SOURCE_WORLD_MODULE
            or world_type.__name__ != cls._SOURCE_WORLD_CLASS
        ):
            raise SourceB3ReflectionHostUnavailableError(
                "source_b3_reflection_requires_hci_orgworld"
            )
        missing = [name for name in cls._SOURCE_WORLD_ATTRIBUTES if not hasattr(world, name)]
        if missing:
            raise SourceB3ReflectionHostUnavailableError(
                "source_b3_reflection_orgworld_missing_fields:" + ",".join(missing)
            )
        if not isinstance(world.agents, Mapping):
            raise SourceB3ReflectionHostUnavailableError(
                "source_b3_reflection_orgworld_agents_must_be_mapping"
            )

    def _require_mounted_source_manager(self, world: Any) -> None:
        if getattr(world, "reflection_manager", None) is not self._manager:
            raise SourceB3ReflectionHostUnavailableError(
                "source_b3_reflection_manager_not_explicitly_mounted"
            )

    @staticmethod
    def _require_closed_owned_episode(episode: Any, world: Any) -> None:
        if episode is None:
            raise SourceB3ReflectionHostUnavailableError(
                "source_b3_reflection_closed_episode_required"
            )
        episode_id = str(getattr(episode, "episode_id", "") or "")
        if not episode_id:
            raise SourceB3ReflectionHostUnavailableError(
                "source_b3_reflection_episode_id_required"
            )
        if str(getattr(episode, "status", "open")) == "open":
            raise SourceB3ReflectionHostUnavailableError(
                "source_b3_reflection_terminal_episode_required"
            )
        manager = getattr(world, "episode_manager")
        episodes = getattr(manager, "episodes", None)
        if not isinstance(episodes, Mapping) or episodes.get(episode_id) is not episode:
            raise SourceB3ReflectionHostUnavailableError(
                "source_b3_reflection_episode_not_owned_by_orgworld"
            )

    @classmethod
    def _require_openai_compatible_provider(cls, world: Any) -> None:
        client = getattr(world, "llm_client", None)
        if client is None:
            raise SourceB3ReflectionHostUnavailableError(
                "source_b3_reflection_openai_compatible_provider_required"
            )
        provider = str(getattr(client, "provider", "") or "").strip().lower()
        if provider not in cls._OPENAI_COMPATIBLE_PROVIDERS:
            raise SourceB3ReflectionHostUnavailableError(
                "source_b3_reflection_provider_not_openai_compatible:"
                + (provider or "missing")
            )
        if not callable(getattr(client, "generate_json", None)):
            raise SourceB3ReflectionHostUnavailableError(
                "source_b3_reflection_provider_generate_json_required"
            )
        if getattr(world, "text_engine", None) is not None:
            raise SourceB3ReflectionHostUnavailableError(
                "source_b3_reflection_text_engine_fallback_not_supported"
            )

    def _call_source_without_template_fallback(
        self,
        operation: Any,
    ) -> list[AgentReflection]:
        """Run source code but reject its template fallback before any record exists."""

        try:
            with self._forbid_template_fallback():
                return operation()
        except SourceB3ReflectionHostUnavailableError:
            raise
        except Exception as exc:
            raise SourceB3ReflectionHostUnavailableError(
                "source_b3_reflection_execution_unavailable:"
                + type(exc).__name__
            ) from exc

    @contextmanager
    def _forbid_template_fallback(self) -> Iterator[None]:
        """Turn the source manager's LLM/template fallback into fail-closed behavior.

        The upstream manager deliberately falls back to a deterministic template
        to keep an HCI simulation running after a provider error. That is valid
        upstream behavior, but not evidence that a Relic-Agent shell recreated
        live cognition. This narrow temporary guard leaves the source manager's
        LLM result processing intact and raises before it writes a template
        reflection, wish, memory, log, or event.
        """

        original = self._manager._template_reflect

        def reject_template(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise SourceB3ReflectionHostUnavailableError(
                "source_b3_reflection_provider_result_required"
            )

        self._manager._template_reflect = reject_template
        try:
            yield
        finally:
            self._manager._template_reflect = original


__all__ = [
    "SourceB3ReflectionHostUnavailableError",
    "SourceB3ReflectionLifecycleAdapter",
    "SourceB3ReflectionLifecycleStatus",
]
