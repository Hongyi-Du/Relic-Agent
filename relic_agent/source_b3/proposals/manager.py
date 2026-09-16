"""Source-pinned HCI proposal lifecycle with explicit host capability seams.

The lifecycle, object materialization, validation, review latency, family
folding, and protocol-revision rules are ported from
``environments/org_env/proposals/manager.py`` at ``dda36fb``.  The source
expects a complete OrgWorld; this module changes only those imports into small
capability checks so an absent OrgWorld, action registry, LLM, or
ProgramBench-only repair path cannot be replaced by compatibility-story logic.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from relic_agent.source_b3.proposals.capabilities import (
    institutionalization_enabled,
    protocol_materialization_enabled,
    registered_action_types,
)
from relic_agent.source_b3.proposals.families import classify_family
from relic_agent.source_b3.proposals.objects import (
    PROPOSAL_TYPES,
    Proposal,
    ProtocolSpec,
    ToolSpec,
    ensure_list,
)
from relic_agent.source_b3.proposals.semantic_dedup import equivalent


MIN_PROPOSAL_REVIEW_TICKS = 2
MIN_PROTOCOL_REVIEW_TICKS = 3
MIN_DISTINCT_PROTOCOL_APPROVERS = 2
MAX_ACTIVE_PROTOCOL_PROPOSALS = 1
MAX_QUEUED_PROTOCOL_PROPOSALS = 3
MAX_ACTIVE_TOOLS_PER_FAMILY = 2


def _tick_value(value: object, fallback: int) -> int:
    """Use a fallback only for ``None``; tick zero is a real birth tick."""

    return int(value) if value is not None else int(fallback)


def recurrence_behind(proposal: Any) -> int:
    """Count repeated source episodes or independent source wishes."""

    cluster = set(getattr(proposal, "source_episode_ids", []) or [])
    if getattr(proposal, "source_episode_id", None):
        cluster.add(proposal.source_episode_id)
    cluster |= set(getattr(proposal, "source_event_ids", []) or [])
    wishes = {
        wish
        for wish in (getattr(proposal, "source_wish_ids", None) or [])
        if wish
    }
    return max(len(cluster), len(wishes))


def protocol_depth_ok(proposal: Any) -> bool:
    """Apply the source cross-episode/cross-wish protocol depth gate."""

    if getattr(proposal, "amends_protocol_id", None):
        return True
    cluster = set(getattr(proposal, "source_episode_ids", []) or [])
    if getattr(proposal, "source_episode_id", None):
        cluster.add(proposal.source_episode_id)
    cluster |= set(getattr(proposal, "source_event_ids", []) or [])
    if not cluster and not (getattr(proposal, "source_wish_ids", None) or []):
        return True
    return recurrence_behind(proposal) >= 2


@dataclass
class ValidationResult:
    passed: bool
    reason: str = ""


class ProposalValidator:
    """Source reality constraints before a draft enters review."""

    def validate(self, proposal: Proposal, world: Any) -> ValidationResult:
        if proposal.proposal_type not in PROPOSAL_TYPES:
            return ValidationResult(
                False, f"illegal proposal_type '{proposal.proposal_type}'"
            )
        if not proposal.title or not proposal.summary:
            return ValidationResult(False, "missing title/summary")
        # This is a ProgramBench-specific source route.  Its evidence model and
        # registry profile are intentionally outside this release boundary, so
        # reject rather than treating arbitrary metadata as an equivalent repair.
        if getattr(proposal, "repair_target_registry_protocol_id", None) is not None:
            return ValidationResult(
                False, "source_programbench_registry_repair_unavailable"
            )
        known = set(registered_action_types(world))
        for action in proposal.required_actions:
            if known and (not isinstance(action, str) or action not in known):
                return ValidationResult(
                    False, f"required action '{action}' does not exist"
                )
        manager = getattr(world, "proposal_manager", None)
        if manager is not None:
            if any(
                tool.name == proposal.title and tool.status == "active"
                for tool in manager.tools.values()
            ):
                return ValidationResult(False, "duplicate of an active tool")
            if any(
                spec.name == proposal.title and spec.status == "adopted"
                for spec in manager.protocol_specs.values()
            ):
                return ValidationResult(False, "duplicate of an adopted protocol")
        return ValidationResult(True, "")


_APPROVERS = {
    "protocol_proposal": ("founder", "cofounder"),
    "policy_repair_proposal": ("founder", "cofounder"),
    "role_proposal": ("founder", "cofounder"),
    "tool_proposal": ("cofounder", "reliability"),
    "workflow_proposal": ("cofounder", "reliability"),
    "artifact_template_proposal": ("cofounder",),
    "task_proposal": ("founder", "cofounder"),
}


class ProposalManager:
    """The source's sole validate → review → approve → adopt object path."""

    _TOOL_MERGE_CRITERION = (
        "two tools automate the SAME workflow step toward the SAME goal (e.g. both check / "
        "assemble evidence for claims, or both run the release smoke gate), making a second "
        "tool redundant — treat them as the same tool even if wording or naming differ, and "
        "only answer false when they automate genuinely different work"
    )
    _PROTOCOL_MERGE_CRITERION = (
        "two governance protocols regulate the SAME activity/trigger toward the SAME goal "
        "(e.g. both require evidence / sources / traceability for claims or results before "
        "they ship), making it redundant to adopt both — treat them as the same protocol "
        "even if their scope, wording, or naming differ, and only answer false when they "
        "govern genuinely different concerns"
    )
    _DEFAULT_SUCCESS_METRIC = {
        "evidence_governance": "unsupported-claim rate in release candidates drops to ~0",
        "release_engineering": "release candidates pass the smoke/CI gate on first re-check more often",
        "debugging": "release smoke/CI blockers are closed via patch (not abandoned), with fewer CI iterations",
        "review_merge": "PRs reach mainline with review sign-off and no post-merge regressions",
        "customer": "every inbound customer issue gets an owner + triage record within a day",
        "budget_governance": "expensive runs are approved before spend; token cost per merged patch trends down",
        "ownership": "every critical task/artifact has an explicit owner",
        "experiment": "experiment results are reproducible + logged to the shared tracker",
    }

    def __init__(self) -> None:
        self.proposals: Dict[str, Proposal] = {}
        self.tools: Dict[str, ToolSpec] = {}
        self.protocol_specs: Dict[str, ProtocolSpec] = {}
        self.validator = ProposalValidator()
        self._seq = 0
        self._tseq = 0
        self._pseq = 0

    def create_proposal(self, proposal: Proposal, world: Any) -> Proposal:
        tick = int(getattr(world, "world_tick", 0) or 0)
        if not getattr(proposal, "created_at_tick", 0):
            proposal.created_at_tick = tick
        if not getattr(proposal, "updated_at_tick", 0):
            proposal.updated_at_tick = tick
        if getattr(proposal, "approval_required_from", None) is None:
            proposal.approval_required_from = []
        if getattr(proposal, "approved_by", None) is None:
            proposal.approved_by = []
        verdict = self.validator.validate(proposal, world)
        if not verdict.passed:
            proposal.status = "rejected"
            proposal.rejection_reason = verdict.reason
            self.proposals[proposal.proposal_id] = proposal
            self._event(world, "proposal_event", "rejected_invalid", proposal)
            return proposal

        if proposal.proposal_type == "protocol_proposal":
            grounded = recurrence_behind(proposal) >= 2
            if not grounded:
                active = sum(
                    1
                    for item in self.proposals.values()
                    if item.proposal_type == "protocol_proposal"
                    and item.status == "under_review"
                )
                queued = sum(
                    1
                    for item in self.proposals.values()
                    if item.proposal_type == "protocol_proposal"
                    and item.status in ("draft", "under_review")
                )
                if (
                    active >= MAX_ACTIVE_PROTOCOL_PROPOSALS
                    or queued >= MAX_QUEUED_PROTOCOL_PROPOSALS
                ):
                    proposal.status = "rejected"
                    proposal.rejection_reason = "too many protocol proposals in flight"
                    self.proposals[proposal.proposal_id] = proposal
                    self._event(world, "proposal_event", "rejected_invalid", proposal)
                    return proposal

        if not proposal.family:
            proposal.family = classify_family(proposal)
        if proposal.proposal_type in ("protocol_proposal", "policy_repair_proposal"):
            if proposal.proposal_type == "policy_repair_proposal":
                self._name_the_rule_being_repaired(proposal, world)
            target = getattr(proposal, "repair_target_protocol_id", None)
            covering = (
                self.protocol_specs.get(target)
                if target in self.protocol_specs
                else self._covering_protocol(proposal, world)
            )
            if covering is not None:
                proposal.amends_protocol_id = covering.protocol_id
            elif (
                proposal.proposal_type == "protocol_proposal"
                and not protocol_depth_ok(proposal)
            ):
                proposal.status = "rejected"
                proposal.rejection_reason = (
                    "protocol needs cross-episode friction "
                    "(single episode — keep as wish / local fix / tool)"
                )
                self.proposals[proposal.proposal_id] = proposal
                self._event(world, "proposal_event", "rejected_shallow", proposal)
                return proposal
        proposal.status = "draft"
        self.proposals[proposal.proposal_id] = proposal
        self._event(world, "proposal_event", "created", proposal)
        self._link_sources(world, proposal)
        return proposal

    def evaluate_proposal(
        self, proposal: Proposal, world: Any, evaluator: Any = None
    ) -> None:
        if evaluator is not None:
            scores = evaluator(proposal, world)
            proposal.feasibility_score = scores.get("feasibility_score")
            proposal.usefulness_score = scores.get("usefulness_score")
            proposal.risk_score = scores.get("risk_score")
            proposal.adoption_score = scores.get("adoption_score")
            proposal.suggested_revision = scores.get("suggested_revision", "")
        proposal.updated_at_tick = int(getattr(world, "world_tick", 0))

    def route_for_approval(self, proposal: Proposal, world: Any) -> None:
        roles = _APPROVERS.get(proposal.proposal_type, ("founder", "cofounder"))
        proposal.approval_required_from = [
            agent_id
            for agent_id, agent in getattr(world, "agents", {}).items()
            if getattr(agent, "role", "") in roles
        ]
        proposal.status = "under_review"
        proposal.updated_at_tick = int(getattr(world, "world_tick", 0))
        self._event(world, "proposal_event", "under_review", proposal)

    def approve_proposal(
        self, proposal_id: str, agent_id: str, world: Any
    ) -> Optional[Any]:
        proposal = self.proposals.get(proposal_id)
        if proposal is None or proposal.status not in ("draft", "under_review"):
            return None
        if agent_id not in proposal.approved_by:
            proposal.approved_by.append(agent_id)
        self._event(world, "proposal_event", "approved", proposal, extra={"approver": agent_id})
        if self._approver_threshold_met(proposal) and self._can_adopt(proposal, world):
            proposal.status = "approved"
            return self.adopt_proposal(proposal_id, world)
        return None

    @staticmethod
    def _approver_threshold_met(proposal: Proposal) -> bool:
        needed = set(proposal.approval_required_from)
        return (needed and needed <= set(proposal.approved_by)) or (
            not needed and len(set(proposal.approved_by)) >= 2
        )

    def _can_adopt(self, proposal: Proposal, world: Any) -> bool:
        tick = int(getattr(world, "world_tick", 0))
        review_entry = int(
            getattr(proposal, "updated_at_tick", 0)
            or getattr(proposal, "created_at_tick", 0)
            or 0
        )
        if proposal.proposal_type == "protocol_proposal":
            minimum = MIN_PROTOCOL_REVIEW_TICKS
        elif proposal.proposal_type == "task_proposal":
            minimum = 0
        else:
            minimum = MIN_PROPOSAL_REVIEW_TICKS
        if tick - review_entry < minimum:
            return False
        if (
            proposal.proposal_type == "protocol_proposal"
            and len(set(proposal.approved_by)) < MIN_DISTINCT_PROTOCOL_APPROVERS
        ):
            return False
        return True

    def process_pending_adoptions(self, world: Any) -> List[Any]:
        adopted = []
        for proposal in list(self.proposals.values()):
            if proposal.status != "under_review":
                continue
            if self._approver_threshold_met(proposal) and self._can_adopt(proposal, world):
                proposal.status = "approved"
                object_created = self.adopt_proposal(proposal.proposal_id, world)
                if object_created is not None:
                    adopted.append(object_created)
        return adopted

    def reject_proposal(
        self, proposal_id: str, agent_id: str, reason: str, world: Any
    ) -> None:
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            return
        proposal.rejected_by.append(agent_id)
        proposal.status = "rejected"
        proposal.rejection_reason = reason
        self._event(world, "proposal_event", "rejected", proposal, extra={"by": agent_id})

    def adopt_proposal(self, proposal_id: str, world: Any) -> Optional[Any]:
        proposal = self.proposals.get(proposal_id)
        if proposal is None or proposal.status not in ("approved",):
            return None
        if not institutionalization_enabled(world):
            proposal.status = "rejected"
            proposal.rejection_reason = "mechanism_ablation:institutionalization"
            proposal.updated_at_tick = int(getattr(world, "world_tick", 0))
            self._event(world, "mechanism_ablation_event", "institutionalization_blocked", proposal)
            return None
        tick = int(getattr(world, "world_tick", 0))
        object_created: ToolSpec | ProtocolSpec | None = None
        if proposal.proposal_type in (
            "tool_proposal",
            "workflow_proposal",
            "artifact_template_proposal",
        ):
            covering = self._covering_tool(proposal, world)
            if covering is not None:
                return self._fold_into_tool(covering, proposal, world, tick)
            self._tseq += 1
            object_created = ToolSpec(
                tool_id=f"tool_{self._tseq}",
                name=proposal.title,
                description=proposal.summary,
                created_from_proposal_id=proposal.proposal_id,
                creator_agent_id=proposal.proposer_agent_id,
                source_wish_id=proposal.source_wish_id,
                source_episode_id=proposal.source_episode_id,
                source_episode_ids=list(getattr(proposal, "source_episode_ids", []) or []),
                family=proposal.family or classify_family(proposal),
                tool_type=(
                    "workflow_tool"
                    if proposal.proposal_type == "workflow_proposal"
                    else (
                        "artifact_tool"
                        if proposal.proposal_type == "artifact_template_proposal"
                        else "composed_action_tool"
                    )
                ),
                required_actions=ensure_list(proposal.required_actions),
                required_capabilities=ensure_list(proposal.required_capabilities),
                callable_by_roles=[],
                status="active",
                created_at_tick=tick,
                adopted_at_tick=tick,
                updated_at_tick=tick,
            )
            self.tools[object_created.tool_id] = object_created
            proposal.object_created_id = object_created.tool_id
            self._event(
                world,
                "tool_event",
                "created",
                proposal,
                extra={"tool_id": object_created.tool_id, "family": object_created.family},
            )
        elif proposal.proposal_type in ("protocol_proposal", "policy_repair_proposal"):
            if getattr(proposal, "repair_target_registry_protocol_id", None):
                return self._reject_registry_repair(proposal, world)
            existing = self.protocol_specs.get(
                getattr(proposal, "amends_protocol_id", None) or ""
            )
            if existing is None or existing.status != "adopted":
                target = getattr(proposal, "repair_target_protocol_id", None)
                existing = (
                    self.protocol_specs.get(target)
                    if target in self.protocol_specs
                    else self._covering_protocol(proposal, world)
                )
            if existing is not None:
                return self._amend_protocol(existing, proposal, world, tick)
            if proposal.proposal_type == "policy_repair_proposal":
                # The source's final target-resolution fallback reads the
                # OrgWorld harm detector. That dependency is intentionally
                # absent here; silently marking an unbound repair implemented
                # would misrepresent a no-op as governance action.
                proposal.status = "rejected"
                proposal.rejection_reason = "source_policy_repair_target_unavailable"
                proposal.updated_at_tick = tick
                self._event(world, "proposal_event", "rejected_unavailable", proposal)
                return None
            if not protocol_materialization_enabled(world) or not self._has_live_registry(world):
                proposal.status = "rejected"
                proposal.rejection_reason = "source_protocol_materialization_unavailable"
                proposal.updated_at_tick = tick
                self._event(world, "proposal_event", "rejected_unavailable", proposal)
                return None
            self._pseq += 1
            object_created = ProtocolSpec(
                protocol_id=f"protospec_{self._pseq}",
                name=proposal.title,
                created_from_proposal_id=proposal.proposal_id,
                source_episode_id=proposal.source_episode_id,
                source_episode_ids=list(getattr(proposal, "source_episode_ids", []) or []),
                source_wish_id=proposal.source_wish_id,
                source_wish_ids=list(getattr(proposal, "source_wish_ids", []) or []),
                source_reflection_id=proposal.source_reflection_id,
                trigger_condition=proposal.target_problem,
                required_steps=ensure_list(proposal.required_actions),
                required_fields=ensure_list(proposal.required_artifacts),
                enforcement_rule=proposal.proposed_solution,
                affected_actions=ensure_list(proposal.required_actions),
                benefits=ensure_list(proposal.expected_benefits),
                costs=ensure_list(proposal.expected_costs),
                risks=ensure_list(proposal.risks),
                status="adopted",
                proposed_by=proposal.proposer_agent_id,
                adopted_by=list(proposal.approved_by),
                created_at_tick=_tick_value(getattr(proposal, "created_at_tick", None), tick),
                adopted_at_tick=tick,
            )
            self._fill_protocol_structure(object_created, proposal, world)
            self.protocol_specs[object_created.protocol_id] = object_created
            proposal.object_created_id = object_created.protocol_id
            try:
                self._register_live_protocol(world, object_created)
            except Exception:
                del self.protocol_specs[object_created.protocol_id]
                proposal.object_created_id = None
                proposal.status = "rejected"
                proposal.rejection_reason = "source_protocol_materialization_failed"
                proposal.updated_at_tick = tick
                self._event(world, "proposal_event", "rejected_unavailable", proposal)
                return None
            self._event(
                world,
                "protocol_spec_event",
                "adopted",
                proposal,
                extra={"protocol_id": object_created.protocol_id},
            )
        if object_created is not None:
            proposal.adopted_tick = tick
            proposal.supporters = sorted(
                set(list(proposal.supporters) + list(proposal.approved_by))
            )
        proposal.status = "adopted" if object_created is not None else "implemented"
        return object_created

    @staticmethod
    def _has_live_registry(world: Any) -> bool:
        registry = getattr(world, "protocol_registry", None)
        return all(callable(getattr(registry, name, None)) for name in ("propose", "support", "adopt"))

    def _reject_registry_repair(self, proposal: Proposal, world: Any) -> None:
        proposal.status = "rejected"
        proposal.rejection_reason = "source_programbench_registry_repair_unavailable"
        proposal.updated_at_tick = int(getattr(world, "world_tick", 0) or 0)
        self._event(world, "proposal_event", "rejected_unavailable", proposal)
        return None

    @staticmethod
    def _tool_proposal_texts(proposal: Proposal) -> List[str]:
        return [
            proposal.title or "",
            proposal.summary or "",
            proposal.target_problem or "",
            proposal.proposed_solution or "",
            " ".join(ensure_list(proposal.required_actions)),
        ]

    @staticmethod
    def _tool_texts(tool: ToolSpec) -> List[str]:
        return [tool.name or "", tool.description or "", " ".join(tool.required_actions or [])]

    def _covering_tool(self, proposal: Proposal, world: Any) -> ToolSpec | None:
        family = proposal.family or classify_family(proposal)
        same_family = [
            tool
            for tool in self.tools.values()
            if tool.status == "active"
            and (getattr(tool, "family", "") or "") == family
            and family != "other"
        ]
        if not same_family:
            return None
        texts = self._tool_proposal_texts(proposal)
        for tool in same_family:
            if equivalent(
                world,
                texts,
                self._tool_texts(tool),
                kind="workflow tool",
                client=getattr(world, "llm_client", None),
                lo=0.0,
                hi=0.6,
                criterion=self._TOOL_MERGE_CRITERION,
            ):
                return tool
        if len(same_family) >= MAX_ACTIVE_TOOLS_PER_FAMILY:
            return max(
                same_family,
                key=lambda tool: (
                    getattr(tool, "support_count", 0),
                    getattr(tool, "adopted_at_tick", 0),
                ),
            )
        return None

    def _fold_into_tool(
        self, tool: ToolSpec, proposal: Proposal, world: Any, tick: int
    ) -> ToolSpec:
        tool.support_count += 1
        if proposal.proposer_agent_id and proposal.proposer_agent_id not in tool.supporters:
            tool.supporters.append(proposal.proposer_agent_id)
        if proposal.proposal_id not in tool.folded_proposal_ids:
            tool.folded_proposal_ids.append(proposal.proposal_id)
        for episode_id in list(getattr(proposal, "source_episode_ids", []) or []) + (
            [proposal.source_episode_id] if proposal.source_episode_id else []
        ):
            if episode_id and episode_id not in tool.source_episode_ids:
                tool.source_episode_ids.append(episode_id)
        tool.updated_at_tick = tick
        proposal.object_created_id = tool.tool_id
        proposal.status = "adopted"
        proposal.adopted_tick = tick
        self._event(
            world,
            "tool_event",
            "folded",
            proposal,
            extra={
                "tool_id": tool.tool_id,
                "family": tool.family,
                "support_count": tool.support_count,
            },
        )
        return tool

    def _fill_protocol_structure(
        self, spec: ProtocolSpec, proposal: Proposal, world: Any
    ) -> None:
        spec.family = proposal.family or classify_family(proposal)
        spec.problem_evidence = list(
            dict.fromkeys(
                list(getattr(proposal, "source_episode_ids", []) or [])
                + ([proposal.source_episode_id] if proposal.source_episode_id else [])
                + list(getattr(proposal, "source_event_ids", []) or [])
                + list(getattr(proposal, "affected_objects", []) or [])
            )
        )
        roles_affected = sorted(
            {
                getattr(getattr(world, "agents", {}).get(agent_id), "role", "")
                for agent_id in spec.adopted_by
                if agent_id in getattr(world, "agents", {})
            }
            - {""}
        )
        scope_bits = []
        if spec.affected_actions:
            scope_bits.append("actions: " + ", ".join(spec.affected_actions[:4]))
        if proposal.required_artifacts:
            scope_bits.append(
                "artifacts: " + ", ".join(ensure_list(proposal.required_artifacts)[:4])
            )
        spec.scope = "; ".join(scope_bits) or f"{spec.family} workflow"
        proposer_role = (
            getattr(
                getattr(world, "agents", {}).get(spec.proposed_by), "role", ""
            )
            if spec.proposed_by
            else ""
        )
        spec.responsible_roles = {
            "executor": [proposer_role] if proposer_role else [],
            "reviewer": roles_affected,
            "approver": roles_affected or ["founder", "cofounder"],
        }
        spec.enforcement_action = spec.enforcement_rule or (
            "block the release candidate / request the missing artifact until the protocol is satisfied"
        )
        spec.violation_condition = spec.violation_condition or (
            f"a {spec.family} step is skipped (required artifact / step missing)"
        )
        spec.success_metric = self._DEFAULT_SUCCESS_METRIC.get(
            spec.family, "the targeted failure/friction rate drops on subsequent cycles"
        )
        spec.sunset_rule = (
            "review after 5 uses or 240 ticks; amend if friction persists, "
            "retire if unused for 120 ticks or superseded by a broader protocol"
        )

    @staticmethod
    def _protocol_proposal_texts(proposal: Proposal) -> List[str]:
        return [
            proposal.title or "",
            proposal.summary or "",
            proposal.target_problem or "",
            proposal.proposed_solution or "",
            " ".join(ensure_list(proposal.required_artifacts)),
            " ".join(ensure_list(proposal.required_actions)),
            " ".join(ensure_list(proposal.expected_benefits)),
        ]

    @staticmethod
    def _protocol_spec_texts(spec: ProtocolSpec) -> List[str]:
        return [
            spec.name or "",
            spec.trigger_condition or "",
            spec.enforcement_rule or "",
            " ".join(spec.required_fields or []),
            " ".join(spec.required_steps or []),
            " ".join(spec.benefits or []),
        ]

    def _covering_protocol(
        self, proposal: Proposal, world: Any
    ) -> ProtocolSpec | None:
        adopted = [spec for spec in self.protocol_specs.values() if spec.status == "adopted"]
        if not adopted:
            return None
        texts = self._protocol_proposal_texts(proposal)
        for spec in adopted:
            if equivalent(
                world,
                texts,
                self._protocol_spec_texts(spec),
                kind="governance protocol",
                client=getattr(world, "llm_client", None),
                lo=0.0,
                hi=0.6,
                criterion=self._PROTOCOL_MERGE_CRITERION,
            ):
                return spec
        return None

    def _name_the_rule_being_repaired(self, proposal: Proposal, world: Any) -> None:
        if getattr(proposal, "repair_target_protocol_id", None) in self.protocol_specs:
            return
        adopted = [
            spec for spec in self.protocol_specs.values() if getattr(spec, "status", "") == "adopted"
        ]
        if not adopted:
            return
        said = " ".join(
            str(getattr(proposal, name, "") or "")
            for name in ("title", "summary", "target_problem", "proposed_solution")
        ).lower()
        words = set(re.findall(r"[a-z]{4,}", said))

        def overlap(spec: ProtocolSpec) -> int:
            text = (
                f"{getattr(spec, 'name', '')} "
                f"{getattr(spec, 'enforcement_rule', '')} "
                f"{getattr(spec, 'trigger_condition', '')}"
            ).lower()
            return len(words & set(re.findall(r"[a-z]{4,}", text)))

        best = max(adopted, key=overlap)
        if overlap(best) >= 3:
            proposal.repair_target_protocol_id = best.protocol_id

    def _mirror_registry_rule(self, world: Any, spec: ProtocolSpec, rule: str) -> None:
        registry = getattr(world, "protocol_registry", None)
        if registry is None:
            return
        protocol_id = f"proto_spec_{str(spec.protocol_id).split('_')[-1]}"
        protocol = getattr(registry, "protocols", {}).get(protocol_id)
        if protocol is not None:
            protocol.rule_summary = rule

    def _mirror_registry_revision(
        self,
        world: Any,
        spec: ProtocolSpec,
        proposal: Proposal,
        *,
        tick: int,
        revision_kind: str,
    ) -> Any:
        registry = getattr(world, "protocol_registry", None)
        if registry is None:
            return None
        protocol_id = f"proto_spec_{str(spec.protocol_id).split('_')[-1]}"
        if protocol_id not in getattr(registry, "protocols", {}):
            return None
        actor = (
            (getattr(proposal, "approved_by", None) or [None])[0]
            or getattr(proposal, "proposer_agent_id", "")
            or "organizational_gate"
        )
        if revision_kind == "deprecate":
            return registry.obsolete(
                actor,
                protocol_id,
                tick=tick,
                source_proposal_id=proposal.proposal_id,
            )
        return registry.amend(
            actor,
            protocol_id,
            tick=tick,
            revision_kind=revision_kind,
            source_proposal_id=proposal.proposal_id,
        )

    @staticmethod
    def _protocol_revision_snapshot(
        world: Any, spec: ProtocolSpec, proposal: Proposal
    ) -> dict:
        registry = getattr(world, "protocol_registry", None)
        mirror_id = f"proto_spec_{str(spec.protocol_id).split('_')[-1]}"
        mirror = (
            getattr(registry, "protocols", {}).get(mirror_id)
            if registry is not None
            else None
        )
        world_events = getattr(world, "events", None)
        agent_log = getattr(world, "agent_log", None)
        return {
            "spec": copy.deepcopy(spec.__dict__),
            "proposal": copy.deepcopy(proposal.__dict__),
            "registry": registry,
            "mirror": mirror,
            "mirror_state": copy.deepcopy(mirror.__dict__) if mirror is not None else None,
            "registry_events": list(getattr(registry, "events", [])) if registry is not None else None,
            "registry_seq": getattr(registry, "_seq", None) if registry is not None else None,
            "world_events": world_events,
            "world_event_rows": list(world_events) if isinstance(world_events, list) else None,
            "agent_log": agent_log,
            "agent_log_rows": list(agent_log) if isinstance(agent_log, list) else None,
        }

    @staticmethod
    def _restore_protocol_revision_snapshot(
        snapshot: dict, spec: ProtocolSpec, proposal: Proposal
    ) -> None:
        spec.__dict__.clear()
        spec.__dict__.update(snapshot["spec"])
        proposal.__dict__.clear()
        proposal.__dict__.update(snapshot["proposal"])
        mirror = snapshot["mirror"]
        if mirror is not None:
            mirror.__dict__.clear()
            mirror.__dict__.update(snapshot["mirror_state"])
        registry = snapshot["registry"]
        if registry is not None and snapshot["registry_events"] is not None:
            registry.events[:] = snapshot["registry_events"]
            if snapshot["registry_seq"] is not None:
                registry._seq = snapshot["registry_seq"]
        if snapshot["world_event_rows"] is not None:
            snapshot["world_events"][:] = snapshot["world_event_rows"]
        if snapshot["agent_log_rows"] is not None:
            snapshot["agent_log"][:] = snapshot["agent_log_rows"]

    def _amend_protocol(
        self, spec: ProtocolSpec, proposal: Proposal, world: Any, tick: int
    ) -> ProtocolSpec:
        snapshot = self._protocol_revision_snapshot(world, spec, proposal)
        try:
            return self._amend_protocol_transaction(spec, proposal, world, tick)
        except Exception:
            self._restore_protocol_revision_snapshot(snapshot, spec, proposal)
            raise

    def _amend_protocol_transaction(
        self, spec: ProtocolSpec, proposal: Proposal, world: Any, tick: int
    ) -> ProtocolSpec:
        repair = (
            getattr(proposal, "repair_kind", None)
            if proposal.proposal_type == "policy_repair_proposal"
            else None
        )
        registry_revision_event = self._mirror_registry_revision(
            world,
            spec,
            proposal,
            tick=tick,
            revision_kind=repair or "extend",
        )
        spec.revision += 1
        spec.last_revised_tick = tick
        added_fields: list[str] = []
        added_steps: list[str] = []
        removed_fields: list[str] = []
        removed_steps: list[str] = []
        if repair == "deprecate":
            spec.status = "deprecated"
        elif repair == "relax":
            drop_fields = set(ensure_list(proposal.required_artifacts))
            drop_steps = set(ensure_list(proposal.required_actions))
            if not drop_fields and not drop_steps and spec.revisions:
                previous = spec.revisions[-1]
                drop_fields = set(previous.get("added_fields", []) or [])
                drop_steps = set(previous.get("added_steps", []) or [])
            removed_fields = [field for field in spec.required_fields if field in drop_fields]
            removed_steps = [step for step in spec.required_steps if step in drop_steps]
            spec.required_fields = [field for field in spec.required_fields if field not in drop_fields]
            spec.required_steps = [step for step in spec.required_steps if step not in drop_steps]
            eased = str(getattr(proposal, "proposed_solution", "") or "").strip()
            if eased and len(eased) > 20:
                spec.enforcement_rule = eased[:600]
                self._mirror_registry_rule(world, spec, eased[:600])
        else:
            added_fields = [
                field
                for field in ensure_list(proposal.required_artifacts)
                if field not in spec.required_fields
            ]
            added_steps = [
                step
                for step in ensure_list(proposal.required_actions)
                if step not in spec.required_steps
            ]
            spec.required_fields.extend(added_fields)
            spec.required_steps.extend(added_steps)
        for agent_id in list(proposal.approved_by):
            if agent_id not in spec.adopted_by:
                spec.adopted_by.append(agent_id)
        spec.revisions.append(
            {
                "revision": spec.revision,
                "from_proposal_id": proposal.proposal_id,
                "tick": tick,
                "title": proposal.title,
                "summary": proposal.summary,
                "kind": repair or "extend",
                "added_fields": added_fields,
                "added_steps": added_steps,
                "removed_fields": removed_fields,
                "removed_steps": removed_steps,
                "rationale": proposal.proposed_solution or proposal.target_problem,
            }
        )
        if proposal.proposal_id not in spec.superseded_proposal_ids:
            spec.superseded_proposal_ids.append(proposal.proposal_id)
        proposal.object_created_id = spec.protocol_id
        proposal.status = "adopted"
        subtype = (
            "deprecated" if repair == "deprecate" else "relaxed" if repair == "relax" else "amended"
        )
        self._event(
            world,
            "protocol_spec_event",
            subtype,
            proposal,
            extra={"protocol_id": spec.protocol_id, "revision": spec.revision},
        )
        revision_event = {
            "type": "protocol_revision_event",
            "protocol_id": spec.protocol_id,
            "revision": spec.revision,
            "kind": repair or "extend",
            "from_proposal_id": proposal.proposal_id,
            "agent_id": proposal.proposer_agent_id,
            "tick": int(getattr(world, "world_tick", 0)),
        }
        if repair == "deprecate" and registry_revision_event is not None:
            revision_event["registry_event_id"] = getattr(registry_revision_event, "event_id", None)
        getattr(world, "events", []).append(revision_event)
        return spec

    def _register_live_protocol(self, world: Any, spec: ProtocolSpec) -> None:
        registry = getattr(world, "protocol_registry", None)
        if registry is None:
            raise RuntimeError("source_protocol_registry_unavailable")
        slug = "_".join(
            word
            for word in "".join(
                character if character.isalnum() else "_" for character in spec.name.lower()
            ).split("_")
            if word
        )
        protocol_type = slug[:40].rsplit("_", 1)[0] if len(slug) > 40 else slug
        protocol_type = protocol_type or "spec_protocol"
        protocol_id = f"proto_spec_{spec.protocol_id.split('_')[-1]}"
        if protocol_id in getattr(registry, "protocols", {}):
            return
        tick = int(getattr(world, "world_tick", 0))
        created = _tick_value(getattr(spec, "created_at_tick", None), tick)
        adopted = _tick_value(getattr(spec, "adopted_at_tick", None), tick)
        approver = (
            spec.adopted_by[0]
            if getattr(spec, "adopted_by", None)
            else spec.proposed_by
        )
        registry.propose(
            proposer_id=spec.proposed_by,
            protocol_type=protocol_type,
            rule_summary=spec.enforcement_rule or spec.name,
            scope="org",
            target_process=str(getattr(spec, "trigger_condition", ""))[:160],
            tick=created,
            protocol_id=protocol_id,
        )
        registry_protocol = registry.protocols.get(protocol_id)
        if registry_protocol is not None:
            for agent_id in getattr(spec, "adopted_by", None) or []:
                if agent_id not in registry_protocol.supporters:
                    registry.support(agent_id, protocol_id, tick=adopted)
        if registry_protocol is not None and registry_protocol.adoption_status != "adopted":
            registry.adopt(protocol_id, adopted, approver_id=approver, force=True)
        spec.affected_agents = list(getattr(spec, "affected_agents", []))

    def next_id(self, prefix: str = "proposal") -> str:
        self._seq += 1
        return f"{prefix}_{self._seq}"

    def _link_sources(self, world: Any, proposal: Proposal) -> None:
        reflection_manager = getattr(world, "reflection_manager", None)
        wish = (
            reflection_manager.wishes.get(proposal.source_wish_id)
            if reflection_manager and proposal.source_wish_id
            else None
        )
        if wish is not None:
            if proposal.proposal_id not in wish.generated_proposal_ids:
                wish.generated_proposal_ids.append(proposal.proposal_id)
            wish.status = "converted_to_proposal"
        if not proposal.source_episode_id:
            return
        episode_manager = getattr(world, "episode_manager", None)
        episode = (
            episode_manager.episodes.get(proposal.source_episode_id)
            if episode_manager
            else None
        )
        if episode is not None and proposal.proposal_id not in episode.linked_proposal_ids:
            episode.linked_proposal_ids.append(proposal.proposal_id)

    def _event(
        self,
        world: Any,
        event_type: str,
        subtype: str,
        proposal: Proposal,
        extra: dict | None = None,
    ) -> None:
        event = {
            "type": event_type,
            "subtype": subtype,
            "agent_id": proposal.proposer_agent_id,
            "tick": int(getattr(world, "world_tick", 0)),
            "object_id": proposal.proposal_id,
            "proposal_type": proposal.proposal_type,
        }
        if extra:
            event.update(extra)
        getattr(world, "events", []).append(event)
        agent_log = getattr(world, "agent_log", None)
        if agent_log is not None and proposal.proposer_agent_id:
            from relic_agent.reflection.models import AgentLogEntry

            agent_log.append(
                AgentLogEntry(
                    log_id=f"log_{len(agent_log)}",
                    agent_id=proposal.proposer_agent_id,
                    tick=int(getattr(world, "world_tick", 0)),
                    entry_type="proposal_created",
                    summary=f"{subtype} proposal: {proposal.title}",
                    related_episode_ids=[proposal.source_episode_id]
                    if proposal.source_episode_id
                    else [],
                    raw_payload={
                        "proposal_id": proposal.proposal_id,
                        "status": proposal.status,
                    },
                )
            )

    def proposals_snapshot(self) -> Dict[str, Any]:
        items = [proposal.to_dict() for proposal in self.proposals.values()]
        items.sort(key=lambda item: item.get("created_at_tick", 0))
        by_status: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        for proposal in self.proposals.values():
            by_status[proposal.status] = by_status.get(proposal.status, 0) + 1
            by_type[proposal.proposal_type] = by_type.get(proposal.proposal_type, 0) + 1
        return {
            "items": items,
            "by_status": by_status,
            "by_type": by_type,
            "total": len(self.proposals),
        }

    def tools_snapshot(self) -> Dict[str, Any]:
        items = [tool.to_dict() for tool in self.tools.values()]
        return {
            "items": items,
            "active_count": sum(
                1 for tool in self.tools.values() if tool.status == "active"
            ),
            "total": len(self.tools),
        }

    def protocol_specs_snapshot(self) -> Dict[str, Any]:
        items = [spec.to_dict() for spec in self.protocol_specs.values()]
        return {
            "items": items,
            "active_count": sum(
                1 for spec in self.protocol_specs.values() if spec.status == "adopted"
            ),
            "total": len(self.protocol_specs),
        }


__all__ = [
    "MAX_ACTIVE_PROTOCOL_PROPOSALS",
    "MAX_ACTIVE_TOOLS_PER_FAMILY",
    "MAX_QUEUED_PROTOCOL_PROPOSALS",
    "MIN_DISTINCT_PROTOCOL_APPROVERS",
    "MIN_PROPOSAL_REVIEW_TICKS",
    "MIN_PROTOCOL_REVIEW_TICKS",
    "ProposalManager",
    "ProposalValidator",
    "ValidationResult",
    "protocol_depth_ok",
    "recurrence_behind",
]
