"""Generic configuration for the source organization lifecycle.

The generic runtime uses the same ``OrgWorld.step`` and the same lifecycle
managers as the source organization.  This module is deliberately a small
configuration seam around those managers.  It does not advance a clock, copy
``OrgWorld.step``, or keep a second set of protocol/proposal objects.

``configure_lifecycle`` is called after a generic world has been built.  It
installs configured subclasses, retaining any state already present on the
manager instances, and loads initial/package protocols into the existing
proposal manager and protocol registry.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import re
from types import MethodType
from typing import Any, Iterator, Mapping

import yaml

import environments.org_env.reflection.batch_manager as _batch_module
import environments.org_env.reflection.manager as _reflection_module
from environments.org_env.backend.protocol.registry import ProtocolRegistry
from environments.org_env.proposals.manager import ProposalManager
from environments.org_env.proposals.objects import Proposal, ProtocolSpec, ensure_list
from environments.org_env.reflection.batch_manager import ReflectionBatchManager
from environments.org_env.reflection.manager import ReflectionManager


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _section(config: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name, {})
    return dict(value) if isinstance(value, Mapping) else {}


def _bool(value: Any, default: bool = True) -> bool:
    return value if isinstance(value, bool) else default


def _int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, number)


def _float(value: Any, default: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _safe_slug(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-")
    return text or "protocol"


def _token(value: Any) -> str:
    """Return the action/id spelling used by the generic execution layer."""

    return re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().casefold()).strip("_")


def _values(value: Any) -> list[Any]:
    """Normalize a scalar or a list without splitting a scalar into characters."""

    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


@contextmanager
def _agent_provider(world: Any, agent_id: str | None) -> Iterator[None]:
    """Select a generic agent's configured provider for source helper calls."""

    client = getattr(world, "llm_client", None)
    use_agent = getattr(client, "use_agent", None)
    if callable(use_agent) and agent_id:
        with use_agent(agent_id):
            yield
        return
    yield


class _AgentPromptClient:
    """Add a generic agent's configured prompt context to a source call.

    The source cognitive modules accept an ``OrgLLMClient`` and build their own
    module instruction.  Generic worlds also expose ``world.agent_prompt`` as
    the public prompt surface (organization brief, grounding rules, mandate,
    suffix, and prompt assets).  This thin proxy keeps the source schemas and
    fallback behavior while ensuring those fields reach reflection/proposal
    calls as well as task actions.
    """

    def __init__(self, client: Any, world: Any, agent_id: str | None) -> None:
        self._client = client
        self._prompt = ""
        prompt = getattr(world, "agent_prompt", None)
        if callable(prompt) and agent_id:
            # A malformed generic configuration must surface at the cognitive
            # boundary.  Swallowing a missing prompt/config field here makes the
            # source manager silently switch to its template fallback, which is
            # especially misleading when only one agent's route is broken.
            value = prompt(agent_id)
            if isinstance(value, str):
                self._prompt = value.strip()

    def _system(self, module_system: Any) -> str:
        module_text = str(module_system or "").strip()
        if self._prompt and module_text:
            return f"{self._prompt}\n\n{module_text}"
        return self._prompt or module_text

    def generate_json(self, system_prompt: str, user_prompt: str, schema: Any, **kwargs: Any) -> Any:
        return self._client.generate_json(
            self._system(system_prompt), user_prompt, schema, **kwargs
        )

    def generate_text(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> Any:
        return self._client.generate_text(
            self._system(system_prompt), user_prompt, **kwargs
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


@contextmanager
def _temporary_values(module: Any, values: Mapping[str, Any]) -> Iterator[None]:
    """Temporarily tune constants used by the source manager implementation.

    The source managers intentionally keep their hot path compact and use
    module constants for hard spam/cost bounds.  A generic run needs those
    bounds to follow its validated configuration, while a source-native run in
    the same interpreter must retain its pinned defaults.  The context is
    short-lived and only surrounds one manager operation.
    """

    previous = {name: getattr(module, name) for name in values}
    try:
        for name, value in values.items():
            setattr(module, name, value)
        yield
    finally:
        for name, value in previous.items():
            setattr(module, name, value)


class GenericReflectionManager(ReflectionManager):
    """Configured view of the source reflection and wish manager."""

    def __init__(self, settings: Mapping[str, Any] | None = None) -> None:
        super().__init__()
        settings = dict(settings or {})
        self.reflection_settings = settings
        self.reflection_enabled = _bool(settings.get("enabled"), True)
        self.wish_settings = dict(settings.get("wish", {}))
        self.wish_enabled = _bool(self.wish_settings.get("enabled"), True)
        self.wish_cap = _int(self.wish_settings.get("cap"), 10)
        self.wish_dedup = _bool(self.wish_settings.get("dedup"), True)
        self.wish_salience_threshold = _float(
            self.wish_settings.get("salience_threshold"), 0.0
        )
        self.per_agent_cooldown = _int(settings.get("per_agent_cooldown"), 0)

    def maybe_trigger_reflection(
        self, agent_id: str, world: Any, *, reason: str = "", episode: Any = None
    ) -> bool:
        if not self.reflection_enabled:
            return False
        mem = self._memory(world, agent_id)
        tick = int(getattr(world, "world_tick", 0) or 0)
        last = getattr(mem, "last_reflection_tick", None)
        if last is not None and tick - int(last) < self.per_agent_cooldown:
            return False
        return True

    def reflect(
        self,
        agent_id: str,
        world: Any,
        *,
        episode: Any = None,
        reason: str = "",
        batch_budget: list[int] | None = None,
    ) -> Any:
        if not self.maybe_trigger_reflection(
            agent_id, world, reason=reason, episode=episode
        ):
            return None
        # ``generate_reflection`` itself is source-owned.  The temporary values
        # only alter source sparsity gates for this configured generic call.
        values = {
            "REFLECT_COOLDOWN": self.per_agent_cooldown,
            "MAX_OPEN_WISHES_PER_AGENT": self.wish_cap,
            "MAX_OPEN_WISHES_GLOBAL": self.wish_cap,
        }
        with _temporary_values(_reflection_module, values):
            with _agent_provider(world, agent_id):
                return super().reflect(
                    agent_id,
                    world,
                    episode=episode,
                    reason=reason,
                    batch_budget=batch_budget,
                )

    def _llm_client_reflect(
        self, client: Any, agent_id: str, context: dict[str, Any], world: Any = None
    ) -> Any:
        # Keep the source reflection schema/normalization while routing the
        # generic prompt through the public agent_prompt surface.
        prompted = _AgentPromptClient(client, world, agent_id) if world is not None else client
        return super()._llm_client_reflect(prompted, agent_id, context, world)

    def _find_similar_wish(
        self, wtype: str, target: str, related_objs: list[str], fp: str
    ) -> Any:
        if not self.wish_dedup:
            return None
        return super()._find_similar_wish(wtype, target, related_objs, fp)

    def integrate_wishes(
        self,
        reflection: Any,
        world: Any,
        episode: Any = None,
        *,
        batch_budget: list[int] | None = None,
    ) -> list[Any]:
        if not self.wish_enabled or self.wish_cap <= 0:
            return []
        values = {
            "MAX_OPEN_WISHES_PER_AGENT": self.wish_cap,
            "MAX_OPEN_WISHES_GLOBAL": self.wish_cap,
            "WISH_MIN_URGENCY_FOR_NEW": self.wish_salience_threshold,
        }
        with _temporary_values(_reflection_module, values):
            return super().integrate_wishes(
                reflection, world, episode, batch_budget=batch_budget
            )

    def _allow_new_wish(
        self,
        idea: dict[str, Any],
        urgency: float,
        related_issues: list[str],
        world: Any,
        agent_id: str = "",
    ) -> bool:
        """Apply the generic wish salience setting to object-free work.

        The source reflection manager requires a product issue or a relatively
        high urgency before it creates a wish.  Generic organizations have no
        product-artifact registry, so that source-only anchor would make every
        valid generic reflection disappear at the configuration boundary.  The
        generic schema exposes an explicit salience threshold; use it as the
        creation gate while retaining the source's basic content checks.
        """
        del related_issues, world, agent_id
        if not idea.get("description") or not (
            idea.get("risk_if_unaddressed") or idea.get("risk")
        ):
            return False
        return float(urgency) >= self.wish_salience_threshold


class GenericReflectionBatchManager(ReflectionBatchManager):
    """Configured cadence/cooldown/salience view of source batching."""

    def __init__(self, settings: Mapping[str, Any] | None = None) -> None:
        super().__init__()
        settings = dict(settings or {})
        self.reflection_settings = settings
        self.reflection_enabled = _bool(settings.get("enabled"), True)
        self.cadence_ticks = _int(settings.get("cadence_ticks"), 6, minimum=1)
        self.per_agent_cooldown = _int(settings.get("per_agent_cooldown"), 0)
        self.salience_threshold = _float(settings.get("salience_threshold"), 0.0)
        wish = settings.get("wish", {})
        self.wish_cap = _int(wish.get("cap"), 10) if isinstance(wish, Mapping) else 10

    def maybe_run_batch(self, world: Any, tick: int, llm_client: Any = None) -> list[Any]:
        if not self.reflection_enabled:
            return []
        if tick <= 0 or tick % self.cadence_ticks != 0:
            return []
        return self.run_batch(world, tick, llm_client)

    def _salience(self, aid: str, world: Any, window: list[dict[str, Any]], closed_eps: list[Any]):
        # Source salience is an unbounded score (closed episode=2, product
        # change/challenge=1.5, plus work-state terms).  Generic config uses a
        # validated [0, 1] threshold, so normalize the source score to a small
        # stable range before applying it.
        score, episode = super()._salience(aid, world, window, closed_eps)
        return min(1.0, max(0.0, float(score) / 3.0)), episode

    def run_batch(self, world: Any, tick: int, llm_client: Any = None) -> list[Any]:
        values = {
            "REFLECTION_BATCH_INTERVAL_TICKS": self.cadence_ticks,
            "MIN_REFLECTION_GAP_PER_AGENT": self.per_agent_cooldown,
            # The source's once-per-day cap is a default policy.  A configured
            # cooldown is the generic policy, so permit the number of daily
            # slots that the cooldown can actually express.
            "MAX_REFLECTIONS_PER_AGENT_PER_DAY": max(
                1, 24 // max(1, self.per_agent_cooldown)
            ),
            "MIN_REFLECTION_SALIENCE": max(0.0001, self.salience_threshold),
            "MAX_NEW_WISHES_PER_BATCH": min(2, self.wish_cap),
        }
        with _temporary_values(_batch_module, values), _temporary_values(
            _reflection_module, {"REFLECT_COOLDOWN": self.per_agent_cooldown}
        ):
            return super().run_batch(world, tick, llm_client)


class GenericProtocolRegistry(ProtocolRegistry):
    """Source registry with validated generic protocol lifecycle knobs."""

    def __init__(
        self,
        *,
        min_supporters: int = 1,
        allow_adoption: bool = True,
        allow_revision: bool = True,
        allow_retirement: bool = True,
        review_delay_ticks: int = 0,
        dedup: bool = True,
        retirement_enabled: bool = True,
    ) -> None:
        super().__init__(min_supporters=min_supporters)
        self.allow_adoption = bool(allow_adoption)
        self.allow_revision = bool(allow_revision)
        self.allow_retirement = bool(allow_retirement)
        self.review_delay_ticks = max(0, int(review_delay_ticks))
        self.dedup = bool(dedup)
        self.retirement_enabled = bool(retirement_enabled)
        self._loading_protocol = False

    def propose(self, *, proposer_id: str, protocol_type: str, rule_summary: str,
                scope: str = "review", target_process: str = "", tick: int = 0,
                protocol_id: str | None = None):
        pid = protocol_id or f"proto_{protocol_type}"
        if pid in self.protocols:
            existing = self.protocols[pid]
            if self.dedup and getattr(existing, "status", "active") != "obsolete":
                return existing
            # A retired protocol may be proposed again, and a non-deduplicating
            # registry must retain both proposals.  Registry ids are the live
            # mirror ids, so make the collision explicit instead of overwriting
            # the earlier object and its event history.
            base = pid
            suffix = 2
            while pid in self.protocols:
                pid = f"{base}_{suffix}"
                suffix += 1
        if self.dedup:
            for protocol in self.protocols.values():
                if (
                    str(getattr(protocol, "rule_summary", "")).strip().casefold()
                    == str(rule_summary).strip().casefold()
                    and str(getattr(protocol, "target_process", "")).strip().casefold()
                    == str(target_process).strip().casefold()
                    and getattr(protocol, "status", "active") != "obsolete"
                ):
                    return protocol
        return super().propose(
            proposer_id=proposer_id,
            protocol_type=protocol_type,
            rule_summary=rule_summary,
            scope=scope,
            target_process=target_process,
            tick=tick,
            # ``pid`` may have been suffixed above after an obsolete or
            # non-deduplicating collision.  Passing the original id here
            # would make the source registry overwrite the older row and
            # silently sever its event history.
            protocol_id=pid,
        )

    def _can_adopt(self, protocol: Any, tick: int) -> bool:
        return (
            getattr(protocol, "adoption_status", "") == "proposed"
            and len(set(getattr(protocol, "supporters", []) or [])) >= self.min_supporters
            and int(tick) - int(getattr(protocol, "first_tick", 0) or 0)
            >= self.review_delay_ticks
            and len(set(getattr(protocol, "opposers", []) or []))
            < len(set(getattr(protocol, "supporters", []) or []))
        )

    def adopt(self, protocol_id: str, tick: int = 0, *, approver_id: str | None = None,
              force: bool = False) -> bool:
        if not self.allow_adoption and not self._loading_protocol:
            return False
        return super().adopt(
            protocol_id, tick, approver_id=approver_id, force=force
        )

    def amend(self, agent_id: str, protocol_id: str, tick: int = 0, *,
              revision_kind: str = "extend", source_proposal_id: str | None = None):
        if not self.allow_revision:
            raise ValueError("protocol_revision_disabled")
        if not source_proposal_id and not self._loading_protocol:
            raise ValueError("protocol_change_requires_proposal")
        protocol = self.protocols.get(protocol_id)
        if protocol is not None and getattr(protocol, "adoption_status", "") != "adopted":
            raise ValueError("protocol_change_requires_adopted_protocol")
        return super().amend(
            agent_id,
            protocol_id,
            tick,
            revision_kind=revision_kind,
            source_proposal_id=source_proposal_id,
        )

    def obsolete(self, agent_id: str, protocol_id: str, tick: int = 0, *,
                 source_proposal_id: str | None = None):
        if not self.allow_retirement or not self.retirement_enabled:
            raise ValueError("protocol_retirement_disabled")
        if not source_proposal_id and not self._loading_protocol:
            raise ValueError("protocol_change_requires_proposal")
        protocol = self.protocols.get(protocol_id)
        if protocol is not None and getattr(protocol, "adoption_status", "") != "adopted":
            raise ValueError("protocol_change_requires_adopted_protocol")
        return super().obsolete(
            agent_id, protocol_id, tick, source_proposal_id=source_proposal_id
        )


class GenericProposalManager(ProposalManager):
    """Source proposal manager with generic governance and promotion settings."""

    def __init__(self, settings: Mapping[str, Any] | None = None,
                 governance: Mapping[str, Any] | None = None,
                 protocol_settings: Mapping[str, Any] | None = None) -> None:
        super().__init__()
        self.learning_settings = dict(settings or {})
        self.governance_settings = dict(governance or {})
        self.protocol_settings = dict(protocol_settings or {})
        self.proposal_enabled = _bool(self.learning_settings.get("enabled"), True)
        self.proposal_cap = _int(self.learning_settings.get("cap"), 10)
        self.proposal_dedup = _bool(self.learning_settings.get("dedup"), True)
        self.promotion_threshold = _float(
            self.learning_settings.get("promotion_threshold"), 0.5
        )
        self.governance_enabled = _bool(
            self.governance_settings.get("enabled"), True
        )
        self.governance_quorum = _int(
            self.governance_settings.get("quorum"), 1, minimum=1
        )
        self.governance_review_delay = _int(
            self.governance_settings.get("proposal_review_delay_ticks"), 0
        )
        self.deadlock_behavior = str(
            self.governance_settings.get("deadlock_behavior", "escalate")
        ).strip().lower()
        if self.deadlock_behavior not in {"wait", "escalate", "reject"}:
            # Unknown governance values must fail closed.  The schema rejects
            # them for normal configs; this guard also covers integrations
            # constructing the manager directly from a mapping.
            self.deadlock_behavior = "wait"
        parameters = self.governance_settings.get("parameters", {})
        if not isinstance(parameters, Mapping):
            parameters = {}
        self.deadlock_ticks = _int(
            parameters.get("deadlock_ticks", 24),
            24,
        )
        self.approver_members = tuple(
            str(item) for item in self.governance_settings.get("approver_members", ())
        )
        self.approver_roles = tuple(
            str(item) for item in self.governance_settings.get("approver_roles", ())
        )
        # An omitted approver selector means "the whole roster".  An
        # explicitly empty selector means there is no approver pool and must
        # stay empty; silently widening that restriction to every agent would
        # turn a governance configuration typo into an unauthorized vote.
        self._approver_selector_explicit = any(
            key in self.governance_settings
            for key in ("approver_members", "approver_roles", "approvers")
        )
        self.protocol_allow_proposals = _bool(
            self.protocol_settings.get("allow_proposals"), True
        )
        self.protocol_allow_adoption = _bool(
            self.protocol_settings.get("allow_adoption"), True
        )
        self.protocol_allow_revision = _bool(
            self.protocol_settings.get("allow_revision"), True
        )
        self.protocol_allow_retirement = _bool(
            self.protocol_settings.get("allow_retirement"), True
        )
        self.protocol_retirement_enabled = _bool(
            self.protocol_settings.get(
                "retirement_enabled", self.learning_settings.get("retirement_enabled")
            ),
            True,
        )
        self.protocol_review_delay = max(
            _int(self.protocol_settings.get("review_delay_ticks"), 0),
            _int(self.learning_settings.get("review_delay_ticks"), 0),
        )
        # Config schema normalization gives equivalent aliases the same value.
        # For direct integrations, however, a missing nested section must not
        # replace an explicitly configured top-level threshold with its 0.5
        # default.  Use the first explicitly supplied alias and keep the
        # ordinary default only when no section supplied one.
        self.adoption_threshold = self._threshold(
            (self.governance_settings, "protocol_adoption_threshold"),
            (self.governance_settings, "adoption_threshold"),
            (self.protocol_settings, "adoption_threshold"),
            default=0.5,
        )
        self.amendment_threshold = self._threshold(
            (self.governance_settings, "amendment_threshold"),
            (self.protocol_settings, "amendment_threshold"),
            default=0.5,
        )
        self._generic_registry_ids: dict[str, str] = {}

    @staticmethod
    def _threshold(*entries: tuple[Mapping[str, Any], str], default: float) -> float:
        for section, key in entries:
            if key in section:
                return _float(section.get(key), default)
        return default

    def _approver_pool(self, world: Any) -> list[str]:
        agents = getattr(world, "agents", {}) or {}
        pool = [aid for aid in self.approver_members if aid in agents]
        pool.extend(
            aid
            for aid, agent in agents.items()
            if getattr(agent, "role", "") in self.approver_roles and aid not in pool
        )
        if not pool and not self._approver_selector_explicit:
            pool = list(agents)
        return pool

    @staticmethod
    def _agent_permissions(world: Any, agent_id: str) -> set[str]:
        """Return the effective permissions without making the tool registry mandatory."""

        registry = getattr(world, "tool_registry", None)
        permissions = getattr(registry, "permissions", None)
        if callable(permissions):
            try:
                return {str(value) for value in permissions(agent_id)}
            except (KeyError, TypeError, ValueError):
                pass
        config = getattr(world, "agent_config", {}) or {}
        raw = config.get(agent_id, {}) if isinstance(config, Mapping) else {}
        if not isinstance(raw, Mapping):
            raw = {}
        values = set(str(value) for value in raw.get("permissions", ()) or ())
        agent = getattr(world, "agents", {}).get(agent_id)
        values.update(
            str(value) for value in (getattr(agent, "permissions", ()) or ())
        )
        role = raw.get("role", getattr(agent, "role", ""))
        governance = getattr(world, "generic_config", {}) or {}
        if isinstance(governance, Mapping):
            role_permissions = governance.get("governance", {}).get("role_permissions", {})
            if isinstance(role_permissions, Mapping):
                values.update(str(value) for value in role_permissions.get(role, ()) or ())
        return values

    @staticmethod
    def _disabled_proposal(world: Any, proposal: Proposal, reason: str) -> Proposal:
        """Close a disabled draft without exposing it as a proposal object."""

        proposal.status = "rejected"
        proposal.rejection_reason = reason
        # Events are useful for a run audit, while the manager's object map is
        # reserved for proposals that entered the configured lifecycle.
        event = getattr(world, "events", None)
        if event is not None:
            event.append(
                {
                    "type": "proposal_event",
                    "subtype": "rejected_disabled",
                    "agent_id": proposal.proposer_agent_id,
                    "tick": int(getattr(world, "world_tick", 0) or 0),
                    "object_id": proposal.proposal_id,
                    "proposal_type": proposal.proposal_type,
                    "reason": reason,
                }
            )
        return proposal

    @staticmethod
    def _proposal_signature(proposal: Any) -> tuple[str, ...]:
        return tuple(
            " ".join(str(getattr(proposal, key, "") or "").casefold().split())
            for key in ("proposal_type", "title", "target_problem", "proposed_solution")
        )

    def create_proposal(self, proposal: Proposal, world: Any) -> Proposal:
        if not self.proposal_enabled:
            return self._disabled_proposal(world, proposal, "learning.proposal.disabled")
        if (
            proposal.proposal_type in {"protocol_proposal", "policy_repair_proposal"}
            and not self.protocol_allow_proposals
        ):
            return self._disabled_proposal(world, proposal, "protocol_proposals_disabled")
        open_count = sum(
            item.status in ("draft", "under_review")
            for item in self.proposals.values()
        )
        if self.proposal_cap <= 0 or open_count >= self.proposal_cap:
            proposal.status = "rejected"
            proposal.rejection_reason = "proposal_cap_reached"
            self.proposals[proposal.proposal_id] = proposal
            self._event(world, "proposal_event", "rejected_cap", proposal)
            return proposal
        if self.proposal_dedup:
            signature = self._proposal_signature(proposal)
            duplicate = next(
                (
                    item
                    for item in self.proposals.values()
                    if item.status in ("draft", "under_review", "approved", "adopted")
                    and self._proposal_signature(item) == signature
                ),
                None,
            )
            if duplicate is not None:
                proposal.status = "rejected"
                proposal.rejection_reason = f"duplicate_of:{duplicate.proposal_id}"
                self.proposals[proposal.proposal_id] = proposal
                self._event(world, "proposal_event", "rejected_duplicate", proposal)
                return proposal
        return super().create_proposal(proposal, world)

    def route_for_approval(self, proposal: Proposal, world: Any) -> None:
        if not self.governance_enabled:
            proposal.status = "rejected"
            proposal.rejection_reason = "governance_approval_disabled"
            self._event(world, "proposal_event", "rejected_disabled", proposal)
            return
        proposal.approval_required_from = self._approver_pool(world)
        proposal.status = "under_review"
        proposal.updated_at_tick = int(getattr(world, "world_tick", 0) or 0)
        self._event(world, "proposal_event", "under_review", proposal)

    def _approver_threshold_met(self, proposal: Proposal) -> bool:
        approved = set(getattr(proposal, "approved_by", []) or [])
        required = set(getattr(proposal, "approval_required_from", []) or [])
        if required:
            # Quorum is exact: if a configuration requests two approvals but
            # names only one eligible approver, one vote cannot satisfy it.
            return len(approved & required) >= self.governance_quorum
        return len(approved) >= self.governance_quorum

    def _can_adopt(self, proposal: Proposal, world: Any) -> bool:
        tick = int(getattr(world, "world_tick", 0) or 0)
        entry = int(
            getattr(proposal, "updated_at_tick", 0)
            or getattr(proposal, "created_at_tick", 0)
            or 0
        )
        delay = self.governance_review_delay
        if proposal.proposal_type in ("protocol_proposal", "policy_repair_proposal"):
            delay = max(delay, self.protocol_review_delay)
        elif proposal.proposal_type == "task_proposal":
            delay = 0
        if tick - entry < delay:
            return False
        score = getattr(proposal, "adoption_score", None)
        threshold = (
            self.amendment_threshold
            if getattr(proposal, "amends_protocol_id", None)
            else self.adoption_threshold
        )
        if score is not None and float(score) < threshold:
            return False
        return True

    def approve_proposal(self, proposal_id: str, agent_id: str, world: Any) -> Any:
        proposal = self.proposals.get(proposal_id)
        if proposal is None or agent_id not in self._approver_pool(world):
            return None
        if "approve_protocol" not in self._agent_permissions(world, agent_id):
            return None
        return super().approve_proposal(proposal_id, agent_id, world)

    def process_pending_adoptions(self, world: Any) -> list[Any]:
        # The source step retains a historical literal ``adoption_score >=
        # 0.6`` in its auto branch.  Cast the same normal governance votes here
        # using the configured threshold so a lower configured threshold can
        # actually take effect; ``approve_proposal`` and the source sweep
        # still own quorum, delay, and object creation.
        if (
            getattr(world, "approval_mode", "agent") == "auto"
            and getattr(world, "auto_approve", False)
            and self.governance_enabled
        ):
            for proposal in list(self.proposals.values()):
                if proposal.status != "under_review":
                    continue
                threshold = (
                    self.amendment_threshold
                    if getattr(proposal, "amends_protocol_id", None)
                    else self.adoption_threshold
                )
                score = getattr(proposal, "adoption_score", None)
                if score is None or float(score) < threshold:
                    continue
                for approver in list(getattr(proposal, "approval_required_from", ()) or ()):
                    if approver in getattr(proposal, "approved_by", ()):
                        continue
                    self.approve_proposal(proposal.proposal_id, approver, world)

        # Keep the source sweep as the authority for adoption.  The small
        # deadlock policy below only records/rejects stale review objects and
        # never manufactures an adopted object.
        adopted = super().process_pending_adoptions(world)
        tick = int(getattr(world, "world_tick", 0) or 0)
        if self.deadlock_behavior in {"reject", "rejected", "close"}:
            for proposal in list(self.proposals.values()):
                if proposal.status != "under_review":
                    continue
                age = tick - int(
                    getattr(proposal, "updated_at_tick", 0)
                    or getattr(proposal, "created_at_tick", 0)
                    or 0
                )
                if age >= self.deadlock_ticks:
                    self.reject_proposal(
                        proposal.proposal_id,
                        "governance",
                        "governance_deadlock",
                        world,
                    )
            return adopted
        if self.deadlock_behavior in {"escalate", "escalation"}:
            for proposal in self.proposals.values():
                if proposal.status != "under_review":
                    continue
                age = tick - int(
                    getattr(proposal, "updated_at_tick", 0)
                    or getattr(proposal, "created_at_tick", 0)
                    or 0
                )
                if age >= self.deadlock_ticks and not getattr(
                    proposal, "_deadlock_reported", False
                ):
                    setattr(proposal, "_deadlock_reported", True)
                    getattr(world, "events", []).append(
                        {
                            "type": "governance_event",
                            "subtype": "proposal_deadlock",
                            "proposal_id": proposal.proposal_id,
                            "tick": tick,
                        }
                    )
        return adopted

    def adopt_proposal(self, proposal_id: str, world: Any) -> Any:
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            return None
        if proposal.proposal_type == "protocol_proposal" and not self.protocol_allow_adoption:
            proposal.status = "rejected"
            proposal.rejection_reason = "protocol_adoption_disabled"
            self._event(world, "proposal_event", "rejected_disabled", proposal)
            return None
        if proposal.amends_protocol_id and not self.protocol_allow_revision:
            proposal.status = "rejected"
            proposal.rejection_reason = "protocol_revision_disabled"
            self._event(world, "proposal_event", "rejected_disabled", proposal)
            return None
        if (
            proposal.proposal_type == "policy_repair_proposal"
            and getattr(proposal, "repair_kind", "") == "deprecate"
            and (not self.protocol_allow_retirement or not self.protocol_retirement_enabled)
        ):
            proposal.status = "rejected"
            proposal.rejection_reason = "protocol_retirement_disabled"
            self._event(world, "proposal_event", "rejected_disabled", proposal)
            return None
        obj = super().adopt_proposal(proposal_id, world)
        if obj is not None:
            origin = getattr(world, "protocol_origins", {})
            if hasattr(obj, "protocol_id"):
                origin.setdefault(obj.protocol_id, "emergent")
                registry_id = getattr(world, "_generic_protocol_registry_ids", {}).get(
                    obj.protocol_id
                ) or f"proto_spec_{str(obj.protocol_id).split('_')[-1]}"
                getattr(world, "_generic_protocol_registry_ids", {})[
                    obj.protocol_id
                ] = registry_id
                if registry_id:
                    origin.setdefault(registry_id, "emergent")
            world.protocol_origins = origin
        return obj

    # Initial config protocols use their public ids in the registry.  The
    # source manager normally derives ``proto_spec_N``; these small overrides
    # keep amendment/retirement events linked to configured ids too.
    def _registry_id(self, world: Any, spec: Any) -> str:
        return getattr(world, "_generic_protocol_registry_ids", {}).get(
            getattr(spec, "protocol_id", ""),
            f"proto_spec_{str(getattr(spec, 'protocol_id', '')).split('_')[-1]}",
        )

    def _mirror_registry_status(self, world: Any, spec: Any, status: str) -> None:
        reg = getattr(world, "protocol_registry", None)
        protocol = getattr(reg, "protocols", {}).get(self._registry_id(world, spec)) if reg else None
        if protocol is not None:
            # The source amendment path marks the ProtocolSpec deprecated
            # before it calls ``registry.obsolete``.  Keep the live mirror's
            # adoption status intact until that proposal-backed registry call,
            # while exposing the pending status for readers that inspect the
            # object during the transition.
            if status in {"deprecated", "retired", "obsolete"}:
                protocol.status = status
            else:
                protocol.adoption_status = status

    def _mirror_registry_rule(self, world: Any, spec: Any, rule: str) -> None:
        reg = getattr(world, "protocol_registry", None)
        protocol = getattr(reg, "protocols", {}).get(self._registry_id(world, spec)) if reg else None
        if protocol is not None:
            protocol.rule_summary = rule

    def _mirror_registry_revision(self, world: Any, spec: Any, proposal: Any, *, tick: int,
                                  revision_kind: str) -> None:
        reg = getattr(world, "protocol_registry", None)
        pid = self._registry_id(world, spec)
        if not reg or pid not in getattr(reg, "protocols", {}):
            return
        actor = (getattr(proposal, "approved_by", None) or [None])[0] or getattr(
            proposal, "proposer_agent_id", ""
        ) or "organizational_gate"
        if revision_kind == "deprecate":
            reg.obsolete(actor, pid, tick=tick, source_proposal_id=proposal.proposal_id)
        else:
            reg.amend(
                actor,
                pid,
                tick=tick,
                revision_kind=revision_kind,
                source_proposal_id=proposal.proposal_id,
            )


class GenericLifecycle:
    """The configured hooks mounted on one generic ``OrgWorld``."""

    def __init__(self, world: Any, config_data: Mapping[str, Any], base_dir: Path | None = None):
        self.world = world
        self.config = config_data
        self.base_dir = Path(base_dir or ".").expanduser().resolve()
        self.learning = _section(config_data, "learning")
        self.governance = _section(config_data, "governance")
        self.protocols = _section(config_data, "protocols")
        raw_reflection = self.learning.get("reflection", {})
        raw_wish = self.learning.get("wish", {})
        raw_proposal = self.learning.get("proposal", {})
        raw_protocol = self.learning.get("protocol", {})
        self.reflection = (
            dict(raw_reflection) if isinstance(raw_reflection, Mapping) else {}
        )
        self.wish = dict(raw_wish) if isinstance(raw_wish, Mapping) else {}
        self.proposal = (
            dict(raw_proposal) if isinstance(raw_proposal, Mapping) else {}
        )
        self.protocol_learning = (
            dict(raw_protocol) if isinstance(raw_protocol, Mapping) else {}
        )
        reflection_switch = _bool(self.learning.get("reflection_enabled"), True)
        if isinstance(raw_reflection, bool):
            reflection_switch = reflection_switch and raw_reflection
        wish_switch = _bool(self.learning.get("wish_extraction"), True)
        if isinstance(raw_wish, bool):
            wish_switch = wish_switch and raw_wish
        proposal_switch = _bool(self.learning.get("proposal_generation"), True)
        if isinstance(raw_proposal, bool):
            proposal_switch = proposal_switch and raw_proposal
        protocol_switch = _bool(self.learning.get("protocol_formation"), True)
        if isinstance(raw_protocol, bool):
            protocol_switch = protocol_switch and raw_protocol
        self.reflection["wish"] = self.wish
        self.reflection["enabled"] = _bool(
            self.reflection.get("enabled"), True
        ) and reflection_switch
        self.wish["enabled"] = _bool(
            self.wish.get("enabled"), True
        ) and wish_switch
        self.proposal["enabled"] = _bool(
            self.proposal.get("enabled"), True
        ) and proposal_switch
        self.protocol_learning["enabled"] = _bool(
            self.protocol_learning.get("enabled"), True
        ) and protocol_switch
        self.protocol_learning["retirement_enabled"] = _bool(
            self.protocol_learning.get("retirement_enabled"), True
        ) and protocol_switch
        # ``review`` is the normal, proposal-backed retirement path.  ``keep``
        # makes retirement unavailable while leaving amendments enabled.  The
        # normalized schema supplies both governance and protocol spellings;
        # accepting ``keep`` from either keeps direct mapping integrations
        # consistent with the public config surface.  Unknown values fail
        # closed and are treated as ``keep``.
        retirement_values = (
            self.governance.get("retirement_behavior"),
            self.protocols.get("retirement_behavior"),
        )
        self.retirement_behavior = (
            "keep"
            if "keep" in {str(value or "").strip().lower() for value in retirement_values}
            else "review"
        )
        if any(
            value is not None
            and str(value).strip().lower() not in {"review", "keep"}
            for value in retirement_values
        ):
            self.retirement_behavior = "keep"
        self.learning["reflection_enabled"] = self.reflection["enabled"]
        self.learning["wish_extraction"] = self.wish["enabled"]
        self.learning["proposal_generation"] = self.proposal["enabled"]
        self.learning["protocol_formation"] = self.protocol_learning["enabled"]
        self.learning["institutionalization"] = _bool(
            self.learning.get("institutionalization"), True
        )
        self.learning["governance_approval"] = _bool(
            self.learning.get("governance_approval"), True
        )
        self.protocol_learning["retirement_enabled"] = (
            self.protocol_learning["retirement_enabled"]
            and self.retirement_behavior == "review"
        )
        self._install_managers()
        self._install_world_hooks()
        self._load_protocols()

    def _install_managers(self) -> None:
        world = self.world
        old_reflection = getattr(world, "reflection_manager", None)
        reflection = GenericReflectionManager(self.reflection)
        if old_reflection is not None:
            reflection.__dict__.update(getattr(old_reflection, "__dict__", {}))
        # Settings must win over copied state from a previously configured
        # manager, while the source counters/maps are retained.
        reflection.reflection_settings = self.reflection
        reflection.reflection_enabled = self.reflection["enabled"]
        reflection.wish_settings = self.wish
        reflection.wish_enabled = _bool(self.wish.get("enabled"), True)
        reflection.wish_cap = _int(self.wish.get("cap"), 10)
        reflection.wish_dedup = _bool(self.wish.get("dedup"), True)
        reflection.wish_salience_threshold = _float(
            self.wish.get("salience_threshold"), 0.0
        )
        reflection.per_agent_cooldown = _int(self.reflection.get("per_agent_cooldown"), 0)
        world.reflection_manager = reflection

        old_batch = getattr(world, "reflection_batch_manager", None)
        batch = GenericReflectionBatchManager(self.reflection)
        if old_batch is not None:
            batch.__dict__.update(getattr(old_batch, "__dict__", {}))
        batch.reflection_settings = self.reflection
        batch.reflection_enabled = self.reflection["enabled"]
        batch.cadence_ticks = _int(self.reflection.get("cadence_ticks"), 6, minimum=1)
        batch.per_agent_cooldown = _int(self.reflection.get("per_agent_cooldown"), 0)
        batch.salience_threshold = _float(self.reflection.get("salience_threshold"), 0.0)
        batch.wish_cap = reflection.wish_cap
        world.reflection_batch_manager = batch

        old_proposals = getattr(world, "proposal_manager", None)
        governance_settings = {
            **self.governance,
            "enabled": self.learning["governance_approval"],
            "parameters": self.learning.get("parameters", {}),
        }
        adoption_threshold = next(
            (
                value
                for section, key in (
                    (self.governance, "protocol_adoption_threshold"),
                    (self.governance, "adoption_threshold"),
                    (self.protocols, "adoption_threshold"),
                    (self.protocol_learning, "adoption_threshold"),
                )
                if (value := section.get(key)) is not None
            ),
            0.5,
        )
        amendment_threshold = next(
            (
                value
                for section, key in (
                    (self.governance, "amendment_threshold"),
                    (self.protocols, "amendment_threshold"),
                    (self.protocol_learning, "amendment_threshold"),
                )
                if (value := section.get(key)) is not None
            ),
            0.5,
        )
        protocol_settings = {
            **self.protocols,
            **self.protocol_learning,
            # Every relevant switch is an AND.  A nested ``enabled: true``
            # cannot re-enable a disabled top-level protocol formation switch.
            "allow_proposals": _bool(self.protocols.get("allow_proposals"), True)
            and self.protocol_learning["enabled"],
            "allow_adoption": _bool(self.protocols.get("allow_adoption"), True)
            and self.protocol_learning["enabled"],
            "allow_revision": _bool(self.protocols.get("allow_revision"), True)
            and self.protocol_learning["enabled"],
            "allow_retirement": _bool(self.protocols.get("allow_retirement"), True)
            and self.protocol_learning["enabled"],
            "review_delay_ticks": max(
                _int(self.protocols.get("review_delay_ticks"), 0),
                _int(self.protocol_learning.get("review_delay_ticks"), 0),
                _int(self.governance.get("proposal_review_delay_ticks"), 0),
            ),
            "adoption_threshold": _float(adoption_threshold, 0.5),
            "amendment_threshold": _float(amendment_threshold, 0.5),
            "dedup": _bool(self.protocols.get("dedup"), True)
            and _bool(self.protocol_learning.get("dedup"), True),
            "retirement_enabled": (
                _bool(self.protocols.get("retirement_enabled"), True)
                and _bool(self.protocol_learning.get("retirement_enabled"), True)
                and self.retirement_behavior == "review"
            ),
        }
        manager = GenericProposalManager(
            self.proposal,
            governance_settings,
            protocol_settings,
        )
        if old_proposals is not None:
            # Keep the source manager's durable object stores and counters while
            # allowing the generic constructor to own every policy setting.
            for name in (
                "proposals",
                "tools",
                "protocol_specs",
                "validator",
                "_seq",
                "_tseq",
                "_pseq",
            ):
                if hasattr(old_proposals, name):
                    setattr(manager, name, getattr(old_proposals, name))
        world.proposal_manager = manager

        governance = self.governance
        min_supporters = _int(
            governance.get("protocol_quorum", governance.get("quorum")),
            1,
            minimum=1,
        )
        old_registry = getattr(world, "protocol_registry", None)
        registry = GenericProtocolRegistry(
            min_supporters=min_supporters,
            allow_adoption=protocol_settings["allow_adoption"],
            allow_revision=protocol_settings["allow_revision"],
            allow_retirement=protocol_settings["allow_retirement"],
            review_delay_ticks=_int(protocol_settings.get("review_delay_ticks"), 0),
            dedup=protocol_settings["dedup"],
            retirement_enabled=protocol_settings["retirement_enabled"],
        )
        if old_registry is not None:
            for name in ("protocols", "events", "_seq", "compilation_frozen"):
                if hasattr(old_registry, name):
                    setattr(registry, name, getattr(old_registry, name))
        world.protocol_registry = registry
        manager._generic_registry_ids = getattr(world, "_generic_protocol_registry_ids", {})

    def _proposal_generation_enabled(self) -> bool:
        return bool(
            self.learning.get("institutionalization", True)
            and self.proposal.get("enabled", True)
        )

    def _proposal_disabled_reason(self, proposal: Any) -> str | None:
        """Return the feature switch that prevents a proposal entering the map."""

        if not self._proposal_generation_enabled():
            if not self.learning.get("institutionalization", True):
                return "learning.institutionalization.disabled"
            return "learning.proposal.disabled"
        proposal_type = str(getattr(proposal, "proposal_type", "") or "")
        if proposal_type in {"protocol_proposal", "policy_repair_proposal"}:
            if not self.protocol_learning.get("enabled", True):
                return "learning.protocol.disabled"
            if proposal_type == "protocol_proposal" and not getattr(
                self.world.proposal_manager, "protocol_allow_proposals", True
            ):
                return "protocol_proposals_disabled"
        return None

    def _enrich_proposal_lineage(self, proposal: Any) -> None:
        """Attach real wishes that contributed the proposal's episode cluster.

        The fixed institution detector is episode based, while wishes are a
        second legitimate route into the same protocol.  Wishes retain the
        episode/event references they reflected on, so linking intersections
        here closes that provenance edge without inventing a wish for an
        unrelated detector result.
        """

        wish_ids = [
            str(value)
            for value in (getattr(proposal, "source_wish_ids", []) or [])
            if value
        ]
        source_wish = getattr(proposal, "source_wish_id", None)
        if source_wish and str(source_wish) not in wish_ids:
            wish_ids.append(str(source_wish))
        if not wish_ids:
            episode_refs = {
                str(value)
                for value in (
                    list(getattr(proposal, "source_episode_ids", []) or [])
                    + ([getattr(proposal, "source_episode_id", None)]
                       if getattr(proposal, "source_episode_id", None)
                       else [])
                    + list(getattr(proposal, "source_event_ids", []) or [])
                )
                if value
            }
            wishes = getattr(
                getattr(self.world, "reflection_manager", None), "wishes", {}
            ) or {}
            matches: list[Any] = []
            for wish in wishes.values():
                refs = {
                    str(value)
                    for value in (
                        list(getattr(wish, "related_episode_ids", []) or [])
                        + ([getattr(wish, "source_episode_id", None)]
                           if getattr(wish, "source_episode_id", None)
                           else [])
                        + list(getattr(wish, "source_event_ids", []) or [])
                    )
                    if value
                }
                if episode_refs.intersection(refs):
                    matches.append(wish)
            # Preserve reflection insertion order, preferring an open wish as
            # the singular source so ProposalManager._link_sources can close
            # the remaining ask while retaining all contributing ids.
            matches.sort(
                key=lambda wish: (
                    getattr(wish, "status", "") != "open",
                    int(getattr(wish, "created_at_tick", 0) or 0),
                    str(getattr(wish, "wish_id", "")),
                )
            )
            for wish in matches:
                wid = str(getattr(wish, "wish_id", "") or "")
                if wid and wid not in wish_ids:
                    wish_ids.append(wid)
        if wish_ids:
            proposal.source_wish_ids = wish_ids
            if not getattr(proposal, "source_wish_id", None):
                proposal.source_wish_id = wish_ids[-1]
            if not getattr(proposal, "source_reflection_id", None):
                wishes = getattr(
                    getattr(self.world, "reflection_manager", None), "wishes", {}
                ) or {}
                selected = wishes.get(proposal.source_wish_id)
                if selected is not None:
                    proposal.source_reflection_id = getattr(
                        selected, "source_reflection_id", None
                    )

    def _install_world_hooks(self) -> None:
        world = self.world
        world.generic_lifecycle = self
        world.generic_configured = True
        world.generic_deadlock_ticks = getattr(
            world.proposal_manager, "deadlock_ticks", 24
        )
        world.auto_propose = bool(
            self.learning.get("institutionalization", True)
            and self.proposal.get("enabled", True)
        )
        world.institutionalization_enabled = bool(
            self.learning.get("institutionalization", True)
        )
        world.governance_approval_enabled = bool(
            self.learning.get("governance_approval", True)
        )
        world.generic_retirement_behavior = self.retirement_behavior
        # Source step reads these two bounds as instance attributes.  Keeping
        # them on the world makes proposal caps affect the existing step.
        world.MAX_OPEN_PROPOSALS_GLOBAL = _int(self.proposal.get("cap"), 10)
        world.MAX_NEW_PROPOSALS_PER_BATCH = min(
            2, max(0, world.MAX_OPEN_PROPOSALS_GLOBAL)
        )
        world._generic_protocol_registry_ids = dict(
            getattr(world, "_generic_protocol_registry_ids", {})
        )
        # The source step's predicate is a small policy seam; bind the generic
        # threshold while leaving all orchestration in ``OrgWorld.step``.
        world._wish_ready_for_proposal = MethodType(
            GenericLifecycle.wish_ready_for_proposal, world
        )
        # OrgWorld.step owns all orchestration, but its two proposal entry
        # points are the narrow seams needed to apply generic feature switches,
        # route wish calls through the originating agent, and preserve source
        # wish lineage for detector-created protocol proposals.
        source_submit = getattr(world, "_generic_source_submit_proposal", None)
        if source_submit is None:
            source_submit = getattr(world, "_submit_proposal", None)
            if callable(source_submit):
                world._generic_source_submit_proposal = source_submit
        source_propose_from_wish = getattr(
            world, "_generic_source_propose_from_wish", None
        )
        if source_propose_from_wish is None:
            source_propose_from_wish = getattr(world, "propose_from_wish", None)
            if callable(source_propose_from_wish):
                world._generic_source_propose_from_wish = source_propose_from_wish

        def submit_proposal(instance: Any, proposal: Proposal) -> Any:
            lifecycle = getattr(instance, "generic_lifecycle", None)
            if lifecycle is None:
                return source_submit(proposal) if callable(source_submit) else proposal
            reason = lifecycle._proposal_disabled_reason(proposal)
            if reason:
                return GenericProposalManager._disabled_proposal(
                    instance, proposal, reason
                )
            lifecycle._enrich_proposal_lineage(proposal)
            return source_submit(proposal) if callable(source_submit) else proposal

        if callable(source_submit):
            world._submit_proposal = MethodType(submit_proposal, world)

        def propose_from_wish(
            instance: Any,
            wish_id: str,
            *,
            evaluate: bool = True,
            route: bool = True,
        ) -> Any:
            lifecycle = getattr(instance, "generic_lifecycle", None)
            if lifecycle is None or not lifecycle._proposal_generation_enabled():
                return None
            if not callable(source_propose_from_wish):
                return None
            wish = getattr(getattr(instance, "reflection_manager", None), "wishes", {}).get(
                wish_id
            )
            agent_id = getattr(wish, "agent_id", None)
            with _agent_provider(instance, agent_id):
                return source_propose_from_wish(
                    wish_id, evaluate=evaluate, route=route
                )

        if callable(source_propose_from_wish):
            world.propose_from_wish = MethodType(propose_from_wish, world)
        # Keep company-skill memory switch effective at reconciliation time.
        original_reconcile = getattr(world, "_generic_source_reconcile", None)
        if original_reconcile is None:
            original_reconcile = getattr(world, "reconcile", None)
            if callable(original_reconcile):
                world._generic_source_reconcile = original_reconcile
        if callable(original_reconcile):
            def reconcile_with_learning(instance, reason: str = ""):
                value = original_reconcile(reason=reason)
                if not self.learning.get("company_skill_memory", True):
                    instance.company_skills = []
                return value
            world.reconcile = MethodType(reconcile_with_learning, world)

    @staticmethod
    def wish_ready_for_proposal(world: Any, wish: Any) -> bool:
        threshold = _float(
            getattr(getattr(world, "generic_lifecycle", None), "proposal", {}).get(
                "promotion_threshold", 0.5
            ),
            0.5,
        )
        urgency = float(getattr(wish, "urgency", 0.0) or 0.0)
        # The configured promotion threshold is a hard lower bound.  Support,
        # recurrence, and founder endorsement can explain *why* a sufficiently
        # urgent wish is stable, but cannot silently promote an explicitly
        # below-threshold wish.
        if urgency < threshold:
            return False
        if getattr(wish, "support_count", 1) >= 2:
            return True
        if len(getattr(wish, "related_episode_ids", []) or []) >= 2:
            return True
        founders = {
            aid
            for aid, agent in getattr(world, "agents", {}).items()
            if getattr(agent, "is_founder", False)
            or str(getattr(agent, "role", "")).casefold() in {"founder", "cofounder"}
        }
        return bool(
            getattr(wish, "agent_id", None) in founders
            or founders.intersection(getattr(wish, "supporting_agent_ids", []) or [])
        )

    def _protocol_definition(self, item: Mapping[str, Any]) -> dict[str, Any]:
        # ``parse_generic_config`` deliberately keeps the compact aliases in
        # ``definition``.  Package exports use the canonical field names there,
        # while hand-written configs may put aliases beside ``id``/``name``.
        # Merge both forms before constructing the source ProtocolSpec so no
        # execution or lineage field disappears at the configuration boundary.
        nested = item.get("definition", {})
        definition = dict(nested) if isinstance(nested, Mapping) else {}
        aliases = (
            ("trigger", "trigger_condition"),
            ("action", "affected_actions"),
            ("rules", "required_steps"),
            ("evidence", "required_fields"),
            ("enforcement", "enforcement_rule"),
            ("artifacts", "affected_artifacts"),
            ("actions", "affected_actions"),
        )
        for source, target in aliases:
            if target not in definition and source in definition:
                definition[target] = definition[source]
            if target not in definition and source in item:
                definition[target] = item[source]
        for key in (
            "family",
            "problem_evidence",
            "scope",
            "responsible_roles",
            "success_metric",
            "enforcement_action",
            "sunset_rule",
            "exception_rule",
            "affected_agents",
            "affected_artifacts",
            "benefits",
            "costs",
            "risks",
            "trigger_condition",
            "required_steps",
            "required_fields",
            "enforcement_rule",
            "affected_actions",
        ):
            if key not in definition and key in item:
                definition[key] = item[key]
        return definition

    def _protocol_item(self, item: Any, *, origin: str) -> Mapping[str, Any] | None:
        if isinstance(item, str):
            return {"id": item, "name": item, "source": origin}
        # A module package may export the same ProtocolSpec dataclass used by
        # the source runtime.  Accepting its safe structural representation
        # keeps package loading a data operation and avoids requiring callers to
        # rewrite an exported protocols.json file.
        if not isinstance(item, Mapping) and hasattr(item, "to_dict"):
            try:
                item = item.to_dict()
            except Exception:
                return None
        if not isinstance(item, Mapping) and hasattr(item, "__dict__"):
            item = dict(item.__dict__)
        if not isinstance(item, Mapping):
            return None
        result = dict(item)
        result["source"] = origin
        return result

    def _load_protocol_file(self, path: Path) -> list[Any]:
        try:
            payload = (
                json.loads(path.read_text(encoding="utf-8"))
                if path.suffix.lower() == ".json"
                else yaml.safe_load(path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError, yaml.YAMLError):
            return []
        if isinstance(payload, Mapping):
            if any(key in payload for key in ("protocols", "initial", "protocol")):
                payload = payload.get(
                    "protocols", payload.get("initial", payload.get("protocol", []))
                )
            elif payload.get("id"):
                payload = [payload]
            else:
                payload = []
        if isinstance(payload, Mapping):
            payload = [payload]
        return list(payload or []) if isinstance(payload, (list, tuple)) else []

    def _load_package(self, package: Any) -> list[Any]:
        # Protocol packages are data-only JSON/YAML files.  In particular, do
        # not import a module named by an old compatibility mapping: importing
        # executable package code at configuration time would make a protocol
        # package an implicit plugin boundary.
        if isinstance(package, str):
            path = Path(package)
            path = path if path.is_absolute() else self.base_dir / path
            return self._load_protocol_file(path) if path.is_file() else []
        if not isinstance(package, Mapping):
            return []
        if package.get("path"):
            path = Path(str(package["path"]))
            path = path if path.is_absolute() else self.base_dir / path
            return self._load_protocol_file(path) if path.is_file() else []
        return []

    def _load_protocols(self) -> None:
        initial = self.protocols.get("initial", [])
        package_items: list[Any] = []
        for package in self.protocols.get("packages", []) or []:
            package_items.extend(self._load_package(package))
        seen: set[str] = set()
        dedup = _bool(self.protocols.get("dedup"), True) and _bool(
            self.protocol_learning.get("dedup"), True
        )
        for origin, values in (("initial", initial), ("loaded", package_items)):
            for raw in values or []:
                item = self._protocol_item(raw, origin=origin)
                if item is None or not item.get("id"):
                    continue
                pid = str(item["id"])
                if pid in seen and dedup:
                    continue
                if pid in self.world.proposal_manager.protocol_specs and dedup:
                    continue
                if not dedup:
                    base = pid
                    suffix = 2
                    while pid in seen or pid in self.world.proposal_manager.protocol_specs:
                        pid = f"{base}_{suffix}"
                        suffix += 1
                seen.add(pid)
                self._install_protocol(pid, item, origin)
        self.world._generic_protocol_registry_ids = dict(
            getattr(self.world, "_generic_protocol_registry_ids", {})
        )
        self.world.proposal_manager._generic_registry_ids = self.world._generic_protocol_registry_ids

    def _install_protocol(self, pid: str, item: Mapping[str, Any], origin: str) -> None:
        world = self.world
        definition = self._protocol_definition(item)
        approvers = list(
            getattr(
                world.proposal_manager,
                "_approver_pool",
                lambda _w: list(world.agents),
            )(world)
        )
        if not approvers:
            approvers = [next(iter(world.agents), "system")]

        def first(*keys: str, default: Any = None) -> Any:
            for key in keys:
                if key in definition:
                    return definition[key]
                if key in item:
                    return item[key]
            return default

        trigger = str(first("trigger_condition", "trigger", default="") or "").strip()
        supported_trigger = _token(trigger) in self._KNOWN_ACTIONS | set(self._ACTION_GROUPS) | {
            "before_completing_a_task", "before_completing_task", "before_task_completion",
            "before_reviewing_a_document", "before_reviewing_a_pr",
        }
        if trigger and not supported_trigger:
            raise ValueError(f"protocol {pid}: unsupported trigger_condition {trigger!r}")
        scope = str(first("scope", default="org") or "org").strip()
        if scope not in {"org", "organization", "shared tasks"}:
            if not scope.startswith("task:") or scope[5:] not in world.tasks:
                raise ValueError(f"protocol {pid}: unsupported scope {scope!r}")
        enforcement = str(first("enforcement_action", default="") or "").strip().lower()
        if enforcement not in {"", "block", "notify"}:
            raise ValueError(f"protocol {pid}: unsupported enforcement_action {enforcement!r}")
        affected = ensure_list(first("affected_agents", default=[]))
        if any(aid not in world.agents and
               not (aid.startswith("role:") and aid[5:] in
                    {str(agent.role) for agent in world.agents.values()}) for aid in affected):
            raise ValueError(f"protocol {pid}: unknown affected_agents")

        responsible = first("responsible_roles", default={})
        if not isinstance(responsible, Mapping):
            responsible = {}
        responsible_roles = {
            str(role): ensure_list(members)
            for role, members in responsible.items()
        }
        quorum = getattr(world.proposal_manager, "governance_quorum", 1)
        quorum = max(1, int(quorum or 1))
        # Initial protocols are already declared by configuration, but their
        # approval metadata must still satisfy the configured quorum.  Never
        # shrink the quorum to the available list and claim a stronger review
        # than actually happened.
        adopted_by = approvers[:quorum] if len(approvers) >= quorum else []
        initial_status = "adopted" if len(adopted_by) >= quorum else "proposed"
        spec = ProtocolSpec(
            protocol_id=pid,
            name=str(item.get("name", pid)),
            trigger_condition=trigger,
            required_steps=ensure_list(first("required_steps", "rules", default=[])),
            required_fields=ensure_list(first("required_fields", "evidence", default=[])),
            enforcement_rule=str(
                first(
                    "enforcement_rule",
                    "enforcement",
                    default=item.get("description", ""),
                )
                or ""
            ),
            violation_condition=str(first("violation_condition", default="") or ""),
            exception_rule=first("exception_rule", default=None),
            family=str(first("family", default="") or ""),
            problem_evidence=ensure_list(first("problem_evidence", default=[])),
            scope=scope,
            responsible_roles=responsible_roles,
            success_metric=str(first("success_metric", default="") or ""),
            enforcement_action=enforcement or "block",
            sunset_rule=str(first("sunset_rule", default="") or ""),
            affected_agents=affected,
            affected_actions=ensure_list(
                first("affected_actions", "action", "actions", default=[])
            ),
            affected_artifacts=ensure_list(
                first("affected_artifacts", "artifacts", default=[])
            ),
            benefits=ensure_list(first("benefits", default=[])),
            costs=ensure_list(first("costs", default=[])),
            risks=ensure_list(first("risks", default=[])),
            status=initial_status,
            proposed_by=approvers[0] if approvers else (next(iter(world.agents), "system")),
            adopted_by=adopted_by,
            created_at_tick=0,
            adopted_at_tick=0 if initial_status == "adopted" else None,
        )
        # Keep package metadata available to inspectors without inventing a
        # parallel protocol object.  ``ProtocolSpec.to_dict`` includes these
        # safe structural attributes in the generic artifact.
        spec.origin = origin
        spec.source = origin
        if "metadata" in definition:
            spec.metadata = deepcopy(definition["metadata"])
        if "visibility" in definition:
            spec.visibility = definition["visibility"]
        world.proposal_manager.protocol_specs[pid] = spec
        world._generic_protocol_registry_ids[pid] = pid
        registry = world.protocol_registry
        registry._loading_protocol = True
        try:
            protocol_type = _safe_slug(spec.name)
            protocol = registry.propose(
                proposer_id=spec.proposed_by or "system",
                protocol_type=protocol_type,
                rule_summary=spec.enforcement_rule or spec.name,
                scope=spec.scope or "org",
                target_process=spec.trigger_condition,
                tick=0,
                protocol_id=pid,
            )
            registry_id = str(getattr(protocol, "protocol_id", pid))
            world._generic_protocol_registry_ids[pid] = registry_id
            protocol.supporters = list(dict.fromkeys(spec.adopted_by or approvers))
            if initial_status == "adopted":
                registry.adopt(
                    registry_id,
                    0,
                    approver_id=spec.adopted_by[0],
                    force=True,
                )
            protocol.origin = origin
            protocol.source = origin
        finally:
            registry._loading_protocol = False
        world.protocol_origins[pid] = origin
        world.protocol_origins[world._generic_protocol_registry_ids.get(pid, pid)] = origin

    _ACTION_GROUPS = {
        "require_evidence": {"complete_task", "review_doc", "review_pr"},
        "evidence": {"complete_task", "review_doc", "review_pr"},
        "review": {"review_doc", "review_pr"},
        "approve": {"approve_proposal"},
        "adopt": {"approve_proposal", "adopt_protocol"},
        "complete": {"complete_task"},
        "completion": {"complete_task"},
        "task_completion": {"complete_task"},
        "release": {"release", "publish", "post_company_update"},
        "publish": {"release", "publish", "post_company_update"},
    }
    _KNOWN_ACTIONS = {
        "claim_task",
        "work_on_task",
        "complete_task",
        "review_doc",
        "review_pr",
        "approve_proposal",
        "adopt_protocol",
        "use_tool",
        "send_message",
    }

    @classmethod
    def _expand_action(cls, value: Any) -> set[str]:
        token = _token(value)
        return set(cls._ACTION_GROUPS.get(token, {token})) if token else set()

    @classmethod
    def _field_action_match(cls, value: Any, action: str) -> bool:
        """Match compact action aliases and prose trigger conditions."""

        wanted = _token(action)
        if not wanted:
            return False
        text = _token(value)
        if not text:
            return False
        if wanted in cls._expand_action(text):
            return True
        words = text.replace("_", " ")
        # Trigger conditions often read ``before completing a task`` rather
        # than carrying a machine action id.  Keep this fallback deliberately
        # narrow so a generic word such as ``work`` does not govern every act.
        if wanted == "complete_task" and re.search(
            r"\b(complete|completing|completion|finish|finished)\b", words
        ):
            return True
        if wanted in {"review_doc", "review_pr"} and re.search(
            r"\b(review|reviewing|reviewer|signoff|sign off)\b", words
        ):
            return True
        if wanted == "approve_proposal" and re.search(
            r"\b(approve|approval|adopt|adoption)\b", words
        ):
            return True
        if wanted in {"complete_task", "review_doc", "review_pr"} and re.search(
            r"\b(evidence|deliverable|artifact|trace|source)\b", words
        ):
            return True
        return False

    def _protocol_applies(self, spec: Any, action: str) -> bool:
        action = _token(action)
        declared_values = list(getattr(spec, "affected_actions", []) or [])
        # ``required_steps`` can contain action ids in exported packages; prose
        # checklist items are ignored except for the textual fallback below.
        declared_values.extend(getattr(spec, "required_steps", []) or [])
        if any(action in self._expand_action(value) for value in declared_values):
            return True
        trigger = getattr(spec, "trigger_condition", "")
        if self._field_action_match(trigger, action):
            return True
        # A protocol with an evidence/review rule but no explicit action still
        # governs the generic completion/review gate.  This is useful for an
        # exported protocol package whose canonical action list was omitted.
        rule = " ".join(
            _text(getattr(spec, key, ""))
            for key in ("enforcement_rule", "violation_condition", "enforcement_action")
        )
        if not declared_values and not _text(trigger):
            return self._field_action_match(rule, action)
        return False

    def _matching_protocols(
        self, action: str, task_id: str | None = None, agent_id: str | None = None
    ) -> list[tuple[str, Any]]:
        pm = self.world.proposal_manager
        result = []
        for pid, spec in pm.protocol_specs.items():
            if getattr(spec, "status", "") != "adopted":
                continue
            registry_id = self.world._generic_protocol_registry_ids.get(pid, pid)
            protocol = self.world.protocol_registry.protocols.get(registry_id)
            if protocol is None or getattr(protocol, "adoption_status", "") != "adopted":
                continue
            scope = str(getattr(spec, "scope", "org") or "org")
            if scope.startswith("task:") and task_id != scope[5:]:
                continue
            if scope == "shared tasks":
                task = (getattr(self.world, "tasks", {}) or {}).get(task_id)
                if _text(getattr(task, "visibility", "")).lower() not in {"team", "public"}:
                    continue
            affected = set(getattr(spec, "affected_agents", []) or [])
            if agent_id and affected:
                agent = self.world.agents.get(agent_id)
                role = str(getattr(agent, "role", ""))
                if agent_id not in affected and f"role:{role}" not in affected:
                    continue
            if self._protocol_applies(spec, action):
                result.append((pid, spec))
        return result

    @staticmethod
    def _artifact_present(world: Any, requirement: Any) -> bool:
        value = str(requirement or "").strip()
        if not value:
            return True
        files = getattr(getattr(world, "company", None), "files", {}) or {}
        if value in files:
            return True
        wanted = value.casefold()
        return any(
            str(getattr(file, "title", "") or "").strip().casefold() == wanted
            for file in files.values()
        )

    def _required_artifact_values(self, spec: Any) -> list[str]:
        values = [str(item).strip() for item in getattr(spec, "affected_artifacts", []) or []]
        # Some package producers call this field ``required_artifacts``.  The
        # source ProtocolSpec names the same structural channel
        # ``affected_artifacts``; loader preserves both into this one field.
        values.extend(
            str(item).strip()
            for item in getattr(spec, "required_artifacts", []) or []
        )
        files = getattr(getattr(self.world, "company", None), "files", {}) or {}
        configured_artifacts: set[str] = set(files)
        for task_spec in (getattr(self.world, "task_specs", {}) or {}).values():
            if isinstance(task_spec, Mapping):
                artifact_values = list(task_spec.get("expected_deliverables", []) or [])
                artifact_values.extend(task_spec.get("input_artifacts", []) or [])
                for artifact in artifact_values:
                    if isinstance(artifact, Mapping):
                        artifact = artifact.get("id", artifact.get("name", ""))
                    if artifact:
                        configured_artifacts.add(str(artifact).strip())
        semantic_fields = {
            "evidence",
            "supporting_evidence",
            "supporting-evidence",
            "proof",
            "source",
            "trace",
            "raw_trace",
            "deliverable",
            "reviewer_signoff",
            "reviewer-signoff",
            "review_done",
            "review-done",
            "tests_passed",
            "tests-passed",
            "docs_ready",
            "docs-ready",
            "status",
            "owner",
            "config",
            "seed",
            "cost",
        }
        # ``required_fields`` normally names evidence fields.  Treat one as a
        # file requirement only when it resolves to a known file or is clearly
        # written as a path/file reference; a semantic field such as
        # ``supporting-evidence`` must go through the evidence gate instead.
        for item in getattr(spec, "required_fields", []) or []:
            text = str(item).strip()
            lower = text.casefold()
            explicit_file_prefix = lower.startswith(
                ("artifact:", "file:", "document:", "doc:", "path:")
            )
            path_like = bool(
                re.search(r"[/\\]", text)
                or re.search(r"\.[a-z0-9]{1,12}$", lower)
            )
            known_file = text in files or any(
                str(getattr(file, "title", "") or "").strip().casefold() == lower
                for file in files.values()
            )
            configured_id = text in configured_artifacts
            identifier_like = bool(
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,119}", text)
                and lower not in semantic_fields
            )
            if text and (
                known_file
                or configured_id
                or explicit_file_prefix
                or path_like
                or identifier_like
            ):
                values.append(
                    re.sub(
                        r"^(?:artifact|file|document|doc|path):\s*",
                        "",
                        text,
                        flags=re.IGNORECASE,
                    ).strip()
                )
        return list(dict.fromkeys(value for value in values if value))

    def _protocol_gate_failure(
        self, spec: Any, action: str, task_id: str | None
    ) -> str | None:
        if not task_id:
            return None
        task = (getattr(self.world, "tasks", {}) or {}).get(task_id)
        if task is None:
            return None
        action = _token(action)
        missing = [
            value
            for value in self._required_artifact_values(spec)
            if not self._artifact_present(self.world, value)
        ]
        if missing:
            return "missing_required_artifact"
        text = " ".join(
            _text(getattr(spec, key, ""))
            for key in (
                "trigger_condition",
                "enforcement_rule",
                "enforcement_action",
                "violation_condition",
            )
        ).casefold()
        fields = " ".join(str(value) for value in getattr(spec, "required_fields", []) or [])
        requires_evidence = bool(
            re.search(r"\b(evidence|deliverable|artifact|trace|source|proof)\b", text + " " + fields.casefold())
        ) or bool(getattr(spec, "required_fields", []) or [])
        requires_review = bool(
            re.search(r"\b(review|reviewer|signoff|sign_off|approval)\b", text)
        ) or any(
            _token(value) in {"review", "review_doc", "review_pr"}
            for value in getattr(spec, "required_steps", []) or []
        )
        if action == "complete_task":
            if requires_evidence:
                ready = getattr(self.world, "deliverables_ready", None)
                if callable(ready) and not ready(task_id):
                    return "missing_evidence"
            if requires_review:
                collaborators = set(
                    getattr(self.world, "task_specs", {})
                    .get(task_id, {})
                    .get("collaborators", getattr(task, "collaborators", []))
                    or []
                )
                current_reviews = getattr(self.world, "current_task_reviews", None)
                reviewed = (current_reviews(task_id) if callable(current_reviews) else set(
                    (getattr(self.world, "task_reviews", {}) or {}).get(task_id, set())))
                if collaborators - reviewed:
                    return "missing_review"
        elif action in {"review_doc", "review_pr"} and requires_evidence:
            ready = getattr(self.world, "deliverables_ready", None)
            if callable(ready) and not ready(task_id):
                return "missing_evidence"
        return None

    def before_action(self, agent_id: str, action: str, task_id: str | None = None) -> bool:
        if not self.learning.get("executable_workflow", True):
            return True
        for pid, spec in self._matching_protocols(action, task_id, agent_id):
            if getattr(spec, "status", "") in {"deprecated", "retired", "obsolete"}:
                return False
            failure = self._protocol_gate_failure(spec, action, task_id)
            if failure is not None:
                if getattr(spec, "enforcement_action", "block") == "notify":
                    self._record_protocol_violation(agent_id, pid, spec, task_id,
                                                    int(self.world.world_tick), reason=failure,
                                                    blocked=False)
                    continue
                return False
        return True

    def _record_protocol_use(
        self,
        agent_id: str,
        pid: str,
        spec: Any,
        task_id: str | None,
        tick: int,
        result: Any = None,
    ) -> None:
        registry_id = self.world._generic_protocol_registry_ids.get(pid, pid)
        try:
            event = self.world.protocol_registry.use(
                agent_id,
                registry_id,
                tick,
                context_id=f"action@{tick}",
                task_id=task_id,
            )
        except (KeyError, ValueError):
            return
        spec.use_count = int(getattr(spec, "use_count", 0) or 0) + 1
        spec.last_used_tick = tick
        if event.event_id not in spec.use_event_ids:
            spec.use_event_ids.append(event.event_id)
        raw = {
            "type": "protocol_use_event",
            "protocol_id": pid,
            "registry_event_id": event.event_id,
            "agent_id": agent_id,
            "tick": tick,
            "object_id": task_id,
        }
        self.world.events.append(raw)
        if result is not None:
            result.events.append(dict(raw))

    def _record_protocol_violation(
        self,
        agent_id: str,
        pid: str,
        spec: Any,
        task_id: str | None,
        tick: int,
        result: Any = None,
        reason: str = "protocol_violation",
        blocked: bool = True,
    ) -> None:
        registry_id = self.world._generic_protocol_registry_ids.get(pid, pid)
        try:
            event = self.world.protocol_registry.violate(
                agent_id, registry_id, tick, context_id=f"action@{tick}"
            )
        except (KeyError, ValueError):
            return
        spec.violation_count = int(getattr(spec, "violation_count", 0) or 0) + 1
        if event.event_id not in spec.violation_event_ids:
            spec.violation_event_ids.append(event.event_id)
        raw = {
            "type": "protocol_violation_event",
            "protocol_id": pid,
            "registry_event_id": event.event_id,
            "agent_id": agent_id,
            "tick": tick,
            "object_id": task_id,
            "reason_code": reason,
        }
        self.world.events.append(raw)
        if result is not None:
            result.events.append(dict(raw))
        # A gate catches the non-compliance at the same moment it records the
        # violation.  The registry event keeps the paired enforcement evidence
        # used by the source emergence classifier.
        try:
            enforcement = self.world.protocol_registry.enforce(
                "organizational_gate",
                registry_id,
                tick,
                violation_event_id=event.event_id,
                blocked=blocked,
                context_id=f"action@{tick}",
            )
        except (KeyError, ValueError):
            enforcement = None
        if enforcement is not None:
            spec.enforcement_count = int(getattr(spec, "enforcement_count", 0) or 0) + 1
            if enforcement.event_id not in spec.enforcement_event_ids:
                spec.enforcement_event_ids.append(enforcement.event_id)
            enforcement_raw = {
                "type": "protocol_enforcement_event",
                "protocol_id": pid,
                "registry_event_id": enforcement.event_id,
                "agent_id": "organizational_gate",
                "tick": tick,
                "object_id": task_id,
                "blocked": blocked,
                "reason_code": reason,
            }
            self.world.events.append(enforcement_raw)
            if result is not None:
                result.events.append(dict(enforcement_raw))

    def after_action(self, agent_id: str, action: str, task_id: str | None, result: Any) -> None:
        tick = int(getattr(self.world, "world_tick", 0) or 0)
        failure = str(getattr(result, "failure_reason", "") or "")
        failure_key = failure.rsplit(".", 1)[-1]
        friction = {
            "PendingReview": "missing_review",
            "MissingEvidence": "missing_evidence",
            "MissingDeliverables": "missing_evidence",
            "ChangesRequested": "changes_requested",
        }.get(failure_key)
        protocol_blocked = failure_key in {"PermissionError", "ProtocolBlocked", "protocol_blocked"}
        # A generic task has no source product claim event.  A completion that
        # reaches the real review/evidence gate is nevertheless genuine
        # organizational friction, so give the source episode manager its
        # canonical dispute event family and payload.
        if friction:
            for event in getattr(result, "events", []) or []:
                if event.get("type") == "task_progress_event" and not result.success:
                    event.update(
                        {
                            "type": "claim_dispute_event",
                            "subtype": "claim_dispute",
                            "reason_code": friction,
                            "target": task_id,
                            "task_id": task_id,
                            "summary": f"{action}: {friction}",
                        }
                    )
                    break
        if not self.learning.get("executable_workflow", True):
            return
        for pid, spec in self._matching_protocols(action, task_id, agent_id):
            if result.success:
                self._record_protocol_use(agent_id, pid, spec, task_id, tick, result)
            elif friction or protocol_blocked:
                self._record_protocol_violation(
                    agent_id,
                    pid,
                    spec,
                    task_id,
                    tick,
                    result,
                    reason=friction or "protocol_blocked",
                )

    def amend_protocol(
        self,
        protocol_id: str,
        *,
        agent_id: str | None = None,
        tick: int | None = None,
        revision_kind: str = "extend",
        title: str = "",
        summary: str = "",
        required_actions: list[str] | None = None,
        required_artifacts: list[str] | None = None,
    ) -> Any:
        manager = self.world.proposal_manager
        if not getattr(manager, "protocol_allow_revision", True):
            return None
        world = self.world
        tick = int(world.world_tick if tick is None else tick)
        spec = manager.protocol_specs.get(protocol_id)
        if spec is None:
            return None
        agent = agent_id or getattr(spec, "proposed_by", None) or next(iter(world.agents), "system")
        proposal = Proposal(
            proposal_id=manager.next_id("proposal"),
            proposal_type="protocol_proposal",
            title=title or f"Amend {spec.name}",
            summary=summary or f"Revise {spec.name}",
            proposer_agent_id=agent,
            source_episode_id=getattr(spec, "source_episode_id", None),
            source_episode_ids=list(getattr(spec, "source_episode_ids", []) or []),
            source_wish_id=getattr(spec, "source_wish_id", None),
            source_wish_ids=list(getattr(spec, "source_wish_ids", []) or []),
            target_problem=spec.trigger_condition,
            proposed_solution=summary or f"Amend {spec.name}",
            required_actions=ensure_list(required_actions),
            required_artifacts=ensure_list(required_artifacts),
            amends_protocol_id=protocol_id,
            affected_protocols=[protocol_id],
            created_at_tick=tick,
            updated_at_tick=tick,
        )
        return self._submit_protocol_change(proposal)

    def retire_protocol(
        self,
        protocol_id: str,
        *,
        agent_id: str | None = None,
        tick: int | None = None,
        reason: str = "protocol retired",
    ) -> Any:
        manager = self.world.proposal_manager
        if (
            not getattr(manager, "protocol_allow_retirement", True)
            or not getattr(manager, "protocol_retirement_enabled", True)
        ):
            return None
        world = self.world
        tick = int(world.world_tick if tick is None else tick)
        spec = manager.protocol_specs.get(protocol_id)
        if spec is None:
            return None
        agent = agent_id or getattr(spec, "proposed_by", None) or next(iter(world.agents), "system")
        proposal = Proposal(
            proposal_id=manager.next_id("proposal"),
            proposal_type="policy_repair_proposal",
            title=f"Retire {spec.name}",
            summary=reason,
            proposer_agent_id=agent,
            source_episode_id=getattr(spec, "source_episode_id", None),
            source_episode_ids=list(getattr(spec, "source_episode_ids", []) or []),
            source_wish_id=getattr(spec, "source_wish_id", None),
            source_wish_ids=list(getattr(spec, "source_wish_ids", []) or []),
            target_problem=spec.trigger_condition,
            proposed_solution=reason,
            affected_protocols=[protocol_id],
            repair_target_protocol_id=protocol_id,
            repair_kind="deprecate",
            created_at_tick=tick,
            updated_at_tick=tick,
        )
        return self._submit_protocol_change(proposal)

    def _submit_protocol_change(self, proposal: Proposal) -> Any:
        """Submit a protocol amendment/retirement through the normal proposal path.

        Protocol changes are ordinary proposals: validation, evaluation, review
        routing, approval, and finally adoption.  Automatic governance may cast
        the configured approvers' votes, but the proposal manager still enforces
        quorum, score, and review latency.  Agent governance leaves the proposal
        under review for explicit approver actions.
        """
        world = self.world
        manager = world.proposal_manager
        proposal = manager.create_proposal(proposal, world)
        if proposal.status == "rejected":
            return proposal
        cog = getattr(world, "_cog", None) or {}
        evaluator_obj = cog.get("proposal_eval") if isinstance(cog, Mapping) else None
        evaluate = getattr(evaluator_obj, "evaluate", None)
        if callable(evaluate):
            proposer = getattr(proposal, "proposer_agent_id", None)
            # Keep the source evaluator's scoring and fallback behavior, while
            # binding its model call to the agent who proposed the change.
            prompted = _AgentPromptClient(
                getattr(world, "llm_client", None), world, proposer
            )
            with _agent_provider(world, proposer):
                manager.evaluate_proposal(
                    proposal,
                    world,
                    lambda candidate, current_world: evaluate(
                        candidate, current_world, prompted
                    ),
                )
        else:
            # A small direct integration may not wire the cognitive modules;
            # leave the proposal unevaluated rather than manufacturing a
            # passing score.  The normal source manager will then keep it in
            # review until a real evaluator is attached.
            manager.evaluate_proposal(proposal, world)
        manager.route_for_approval(proposal, world)
        if proposal.status == "rejected":
            return proposal

        if (
            getattr(world, "approval_mode", "agent") == "auto"
            and getattr(world, "auto_approve", False)
        ):
            for approver in list(getattr(proposal, "approval_required_from", []) or []):
                if approver in getattr(proposal, "approved_by", []):
                    continue
                adopted = manager.approve_proposal(proposal.proposal_id, approver, world)
                if adopted is not None:
                    return adopted
        return proposal


def _copy_manager_state(target: Any, source: Any, names: tuple[str, ...]) -> None:
    for name in names:
        if hasattr(source, name):
            setattr(target, name, getattr(source, name))


def configure_lifecycle(
    world: Any,
    config_data: Mapping[str, Any] | Any,
    base_dir: str | Path | None = None,
) -> GenericLifecycle:
    """Configure the existing source lifecycle managers for one generic world.

    ``config_data`` may be the normalized dictionary consumed by the builder or
    a ``GenericConfig`` object exposing ``data``.  The returned lifecycle is
    also mounted as ``world.generic_lifecycle`` so generic execution hooks can
    call ``before_action`` and ``after_action``.
    """

    source_path = getattr(config_data, "source_path", None)
    if hasattr(config_data, "data"):
        config_data = config_data.data
    if not isinstance(config_data, Mapping):
        raise TypeError("generic lifecycle config must be a mapping")
    if base_dir is None:
        base_dir = Path(source_path).parent if source_path else Path(".")
    return GenericLifecycle(world, config_data, Path(base_dir))


__all__ = [
    "GenericLifecycle",
    "GenericProposalManager",
    "GenericProtocolRegistry",
    "GenericReflectionBatchManager",
    "GenericReflectionManager",
    "configure_lifecycle",
]
