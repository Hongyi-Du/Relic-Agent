"""Event-driven organizational knowledge and institution formation.

This module owns shared organization records and the cadence that asks a host
harness for bounded synthesis.  It never calls a model provider and it never
executes repository tools.  Harness adapters expose the returned tool surface
inside their native agent loops.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from organization_core.contracts import OrganizationEvent, OrganizationEventType, _json_value
from organization_core.state import (
    OrganizationEpisodeState,
    OrganizationProposalState,
    OrganizationProtocolState,
    OrganizationReflectionState,
    OrganizationStateBundle,
    OrganizationToolState,
    OrganizationWishState,
)
from organization_core.synthesis import (
    OrganizationSynthesisKind,
    OrganizationSynthesisPort,
    OrganizationSynthesisRequest,
    OrganizationSynthesisResult,
    validate_synthesis_result,
)


@dataclass(frozen=True)
class OrganizationFormationPolicy:
    reflection_interval: int = 6
    proposal_interval: int = 6
    institution_interval: int = 24
    max_new_proposals_per_batch: int = 2
    max_open_proposals: int = 12
    stable_wish_urgency: float = 0.85
    stable_wish_support: int = 2
    stable_wish_episodes: int = 2

    def __post_init__(self) -> None:
        for name in (
            "reflection_interval",
            "proposal_interval",
            "institution_interval",
            "max_new_proposals_per_batch",
            "max_open_proposals",
            "stable_wish_support",
            "stable_wish_episodes",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"formation policy {name} must be positive")
        if not 0.0 <= float(self.stable_wish_urgency) <= 1.0:
            raise ValueError("stable_wish_urgency must be in [0, 1]")


DEFAULT_ORGANIZATION_FORMATION_POLICY = OrganizationFormationPolicy()


def wish_is_stable(
    wish: OrganizationWishState,
    policy: OrganizationFormationPolicy = DEFAULT_ORGANIZATION_FORMATION_POLICY,
) -> bool:
    attrs = wish.attributes
    urgency = float(attrs.get("urgency", 0.0) or 0.0)
    support = int(attrs.get("support_count", 1) or 1)
    episode_refs = {
        ref
        for key, refs in wish.source_refs.items()
        if "episode" in key
        for ref in refs
    }
    return bool(
        urgency >= policy.stable_wish_urgency
        or support >= policy.stable_wish_support
        or len(episode_refs) >= policy.stable_wish_episodes
        or attrs.get("founder_endorsed") is True
    )


OrganizationToolHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class OrganizationToolDefinition:
    """Harness-neutral function exposed by the organization module."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    handler: OrganizationToolHandler = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not str(self.name or "").strip():
            raise ValueError("organization tool name is required")
        if not str(self.description or "").strip():
            raise ValueError("organization tool description is required")
        if not callable(self.handler):
            raise TypeError("organization tool handler must be callable")
        object.__setattr__(
            self,
            "input_schema",
            _json_value(self.input_schema, path="organization_tool.input_schema"),
        )


_SOURCE_REFS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        name: {"type": "array", "items": {"type": "string"}}
        for name in ("episode", "reflection", "wish", "protocol", "tool", "event")
    },
    "required": ["episode", "reflection", "wish", "protocol", "tool", "event"],
    "additionalProperties": False,
}

_REFLECTION_ATTRIBUTES_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "assessment": {"type": "string"},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "lessons": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["assessment", "blockers", "lessons"],
    "additionalProperties": False,
}

_WISH_ATTRIBUTES_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "urgency": {"type": "number"},
        "target_problem": {"type": "string"},
        "interpreted_need": {"type": "string"},
        "support_count": {"type": "integer"},
        "founder_endorsed": {"type": "boolean"},
    },
    "required": [
        "urgency",
        "target_problem",
        "interpreted_need",
        "support_count",
        "founder_endorsed",
    ],
    "additionalProperties": False,
}

_PROPOSAL_ATTRIBUTES_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "required_actions": {"type": "array", "items": {"type": "string"}},
        "required_capabilities": {
            "type": "array",
            "items": {"type": "string"},
        },
        "risk": {"type": "string"},
        "expected_benefit": {"type": "string"},
    },
    "required": [
        "title",
        "summary",
        "required_actions",
        "required_capabilities",
        "risk",
        "expected_benefit",
    ],
    "additionalProperties": False,
}

_REFLECTION_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "reflection": {
            "type": "object",
            "properties": {
                "reflection_id": {"type": "string"},
                "member_id": {"type": "string"},
                "step": {"type": "integer"},
                "source_refs": _SOURCE_REFS_OUTPUT_SCHEMA,
                "created_wish_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "attributes": _REFLECTION_ATTRIBUTES_OUTPUT_SCHEMA,
            },
            "required": [
                "reflection_id",
                "member_id",
                "step",
                "source_refs",
                "created_wish_ids",
                "attributes",
            ],
            "additionalProperties": False,
        },
        "wishes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "wish_id": {"type": "string"},
                    "member_id": {"type": "string"},
                    "wish_type": {"type": "string"},
                    "status": {"type": "string"},
                    "source_refs": _SOURCE_REFS_OUTPUT_SCHEMA,
                    "generated_proposal_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "attributes": _WISH_ATTRIBUTES_OUTPUT_SCHEMA,
                },
                "required": [
                    "wish_id",
                    "member_id",
                    "wish_type",
                    "status",
                    "source_refs",
                    "generated_proposal_ids",
                    "attributes",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["reflection", "wishes"],
    "additionalProperties": False,
}

_PROPOSAL_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "proposal": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "proposal_type": {"type": "string"},
                "status": {"type": "string"},
                "proposer_member_id": {"type": ["string", "null"]},
                "source_refs": _SOURCE_REFS_OUTPUT_SCHEMA,
                "created_step": {"type": "integer"},
                "updated_step": {"type": "integer"},
                "created_object_id": {"type": ["string", "null"]},
                "attributes": _PROPOSAL_ATTRIBUTES_OUTPUT_SCHEMA,
            },
            "required": [
                "proposal_id",
                "proposal_type",
                "status",
                "proposer_member_id",
                "source_refs",
                "created_step",
                "updated_step",
                "created_object_id",
                "attributes",
            ],
            "additionalProperties": False,
        }
    },
    "required": ["proposal"],
    "additionalProperties": False,
}


class OrganizationFormationRuntime:
    """Mutable, replayable owner of the shared organizational state slice."""

    def __init__(
        self,
        state: OrganizationStateBundle,
        *,
        run_id: str,
        policy: OrganizationFormationPolicy | None = None,
    ) -> None:
        if not str(run_id or "").strip():
            raise ValueError("formation run_id is required")
        self.organization_id = state.organization_id
        self.run_id = str(run_id)
        self.policy = policy or DEFAULT_ORGANIZATION_FORMATION_POLICY
        self._template = state
        self._episodes = {item.episode_id: item for item in state.episodes}
        self._reflections = {
            item.reflection_id: item for item in state.reflections
        }
        self._wishes = {item.wish_id: item for item in state.wishes}
        self._proposals = {item.proposal_id: item for item in state.proposals}
        self._tools = {item.tool_id: item for item in state.tools}
        self._protocols = {item.protocol_id: item for item in state.protocols}
        self._repair_signals: dict[str, tuple[int, Mapping[str, Any]]] = {}
        self._issued_synthesis: set[str] = set()
        self._applied_synthesis: set[str] = set()
        self._event_payloads: dict[str, dict[str, Any]] = {}
        self._last_step = state.source_step

    @property
    def last_step(self) -> int:
        return self._last_step

    def state_bundle(self, *, source_step: int | None = None) -> OrganizationStateBundle:
        step = self._last_step if source_step is None else int(source_step)
        return replace(
            self._template,
            source_run_id=self.run_id,
            source_step=max(step, self._template.source_step),
            episodes=tuple(self._episodes[key] for key in sorted(self._episodes)),
            reflections=tuple(
                self._reflections[key] for key in sorted(self._reflections)
            ),
            wishes=tuple(self._wishes[key] for key in sorted(self._wishes)),
            proposals=tuple(
                self._proposals[key] for key in sorted(self._proposals)
            ),
            tools=tuple(self._tools[key] for key in sorted(self._tools)),
            protocols=tuple(
                self._protocols[key] for key in sorted(self._protocols)
            ),
        )

    def reconcile_state(
        self,
        state: OrganizationStateBundle,
    ) -> dict[str, dict[str, int]]:
        """Reconcile a trusted host projection during compatibility migration.

        Harness tool calls still use the stricter record methods.  This method
        exists for legacy hosts whose mature managers remain authoritative
        while their lifecycle is compared with the extracted core.
        """
        if state.organization_id != self.organization_id:
            raise ValueError("reconciled state organization_id does not match runtime")
        stores = (
            ("episodes", self._episodes, state.episodes, "episode_id"),
            ("reflections", self._reflections, state.reflections, "reflection_id"),
            ("wishes", self._wishes, state.wishes, "wish_id"),
            ("proposals", self._proposals, state.proposals, "proposal_id"),
            ("tools", self._tools, state.tools, "tool_id"),
            ("protocols", self._protocols, state.protocols, "protocol_id"),
        )
        delta: dict[str, dict[str, int]] = {}
        for name, store, values, id_field in stores:
            incoming = {getattr(item, id_field): item for item in values}
            added = len(set(incoming) - set(store))
            updated = sum(
                key in store and store[key] != value
                for key, value in incoming.items()
            )
            removed = len(set(store) - set(incoming))
            store.clear()
            store.update(incoming)
            delta[name] = {
                "added": added,
                "updated": updated,
                "removed": removed,
            }
        self._template = replace(
            state,
            episodes=(),
            reflections=(),
            wishes=(),
            proposals=(),
            tools=(),
            protocols=(),
        )
        self._last_step = max(self._last_step, state.source_step)
        # Constructing the merged bundle exercises cross-record lineage checks.
        self.state_bundle(source_step=state.source_step)
        return delta

    def publish(self, event: OrganizationEvent) -> bool:
        if event.provenance.run_id != self.run_id:
            raise ValueError("formation event run_id does not match runtime")
        payload = event.as_dict()
        existing = self._event_payloads.get(event.event_id)
        if existing is not None:
            if existing != payload:
                raise ValueError(
                    f"formation event id reused with different content: {event.event_id}"
                )
            return False
        if event.step < self._last_step:
            raise ValueError("formation event step moved backwards")
        self._reduce_event(event)
        self._event_payloads[event.event_id] = payload
        self._last_step = event.step
        return True

    def _reduce_event(self, event: OrganizationEvent) -> None:
        event_type = event.event_type
        record = event.payload.get("record")
        if event_type == OrganizationEventType.EPISODE_RECORDED.value:
            self.record_episode(OrganizationEpisodeState.from_dict(_record(record)))
        elif event_type == OrganizationEventType.REFLECTION_RECORDED.value:
            wishes = tuple(
                OrganizationWishState.from_dict(row)
                for row in event.payload.get("wishes", ())
            )
            self.record_reflection(
                OrganizationReflectionState.from_dict(_record(record)),
                wishes=wishes,
            )
        elif event_type == OrganizationEventType.WISH_RECORDED.value:
            self.record_wish(OrganizationWishState.from_dict(_record(record)))
        elif event_type == OrganizationEventType.PROPOSAL_RECORDED.value:
            self.record_proposal(
                OrganizationProposalState.from_dict(_record(record)),
                wish_id=str(event.payload.get("wish_id") or "") or None,
            )
        elif event_type == OrganizationEventType.TOOL_ADOPTED.value:
            self.record_tool(OrganizationToolState.from_dict(_record(record)))
        elif event_type == OrganizationEventType.PROTOCOL_ADOPTED.value:
            self.record_protocol(
                OrganizationProtocolState.from_dict(_record(record))
            )
        elif event_type == OrganizationEventType.POLICY_HARM_DETECTED.value:
            protocol_id = str(event.payload.get("protocol_id") or "").strip()
            if not protocol_id:
                raise ValueError("policy harm event requires protocol_id")
            self._repair_signals[protocol_id] = (event.step, event.payload)

    def record_episode(self, episode: OrganizationEpisodeState) -> bool:
        return _insert_same_or_fail(self._episodes, episode.episode_id, episode)

    def record_wish(self, wish: OrganizationWishState) -> bool:
        reflection_ids = set(wish.source_refs.get("reflection", ())) | set(
            wish.source_refs.get("source_reflection_ids", ())
        )
        missing = reflection_ids - set(self._reflections)
        if missing:
            raise ValueError(
                "wish references missing reflections: " + ",".join(sorted(missing))
            )
        return _insert_same_or_fail(self._wishes, wish.wish_id, wish)

    def record_reflection(
        self,
        reflection: OrganizationReflectionState,
        *,
        wishes: tuple[OrganizationWishState, ...] = (),
    ) -> bool:
        wish_by_id = {wish.wish_id: wish for wish in wishes}
        if len(wish_by_id) != len(wishes):
            raise ValueError("duplicate wishes in reflection transaction")
        expected = set(reflection.created_wish_ids)
        missing = expected - (set(self._wishes) | set(wish_by_id))
        if missing:
            raise ValueError(
                "reflection references missing wishes: " + ",".join(sorted(missing))
            )
        inserted = _insert_same_or_fail(
            self._reflections,
            reflection.reflection_id,
            reflection,
        )
        for wish in wishes:
            _insert_same_or_fail(self._wishes, wish.wish_id, wish)
        return inserted

    def record_proposal(
        self,
        proposal: OrganizationProposalState,
        *,
        wish_id: str | None = None,
    ) -> bool:
        inserted = _insert_same_or_fail(
            self._proposals,
            proposal.proposal_id,
            proposal,
        )
        if wish_id:
            wish = self._wishes.get(wish_id)
            if wish is None:
                raise ValueError(f"proposal references missing wish: {wish_id}")
            linked = tuple(
                dict.fromkeys((*wish.generated_proposal_ids, proposal.proposal_id))
            )
            self._wishes[wish_id] = replace(
                wish,
                generated_proposal_ids=linked,
                status=("proposed" if wish.status == "open" else wish.status),
            )
        return inserted

    def record_tool(self, tool: OrganizationToolState) -> bool:
        return _insert_same_or_fail(self._tools, tool.tool_id, tool)

    def record_protocol(self, protocol: OrganizationProtocolState) -> bool:
        return _insert_same_or_fail(
            self._protocols,
            protocol.protocol_id,
            protocol,
        )

    def query_context(
        self,
        *,
        member_id: str | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        bounded = max(1, min(int(limit), 100))
        members = [
            member.as_dict()
            for member in self._template.members
            if member_id is None or member.member_id == member_id
        ]
        open_wishes = [
            wish.as_dict()
            for wish in self._wishes.values()
            if wish.status == "open"
            and (member_id is None or wish.member_id == member_id)
        ]
        open_proposals = [
            proposal.as_dict()
            for proposal in self._proposals.values()
            if proposal.status in {"draft", "under_review"}
        ]
        return {
            "organization_id": self.organization_id,
            "run_id": self.run_id,
            "step": self._last_step,
            "members": members[:bounded],
            "open_wishes": open_wishes[:bounded],
            "open_proposals": open_proposals[:bounded],
            "protocols": [
                item.as_dict()
                for item in list(self._protocols.values())[:bounded]
            ],
            "tools": [
                item.as_dict() for item in list(self._tools.values())[:bounded]
            ],
        }

    def due_synthesis_requests(
        self,
        *,
        step: int,
    ) -> tuple[OrganizationSynthesisRequest, ...]:
        step = int(step)
        if step < self._last_step:
            raise ValueError("synthesis step moved backwards")
        requests: list[OrganizationSynthesisRequest] = []
        for protocol_id, (detected_step, signal) in sorted(
            self._repair_signals.items()
        ):
            request_id = (
                f"formation:{self.run_id}:repair:{protocol_id}:{detected_step}"
            )
            if request_id in self._issued_synthesis:
                continue
            requests.append(
                OrganizationSynthesisRequest(
                    request_id=request_id,
                    organization_id=self.organization_id,
                    run_id=self.run_id,
                    kind=OrganizationSynthesisKind.REPAIR,
                    step=step,
                    source_refs=(protocol_id,),
                    instructions=(
                        "Draft one bounded policy-repair proposal for this harmful "
                        "adopted protocol. Preserve the rule id and evidence; do not "
                        "adopt or execute the repair."
                    ),
                    context={"harm_signal": signal},
                    output_schema=_PROPOSAL_OUTPUT_SCHEMA,
                )
            )
        if step > 0 and step % self.policy.reflection_interval == 0:
            reflected_episodes = {
                ref
                for reflection in self._reflections.values()
                for key, refs in reflection.source_refs.items()
                if "episode" in key
                for ref in refs
            }
            for episode in self._episodes.values():
                if episode.status not in {"closed", "completed", "resolved"}:
                    continue
                if episode.episode_id in reflected_episodes:
                    continue
                actor_id = episode.primary_member_id or (
                    episode.participant_ids[0] if episode.participant_ids else None
                )
                request_id = (
                    f"formation:{self.run_id}:reflection:{episode.episode_id}:{step}"
                )
                if request_id in self._issued_synthesis:
                    continue
                requests.append(
                    OrganizationSynthesisRequest(
                        request_id=request_id,
                        organization_id=self.organization_id,
                        run_id=self.run_id,
                        kind=OrganizationSynthesisKind.REFLECTION,
                        step=step,
                        actor_id=actor_id,
                        source_refs=(episode.episode_id,),
                        instructions=(
                            "Reflect on this closed organizational episode. Return "
                            "one portable reflection and zero or more grounded wishes."
                        ),
                        context={"episode": episode.as_dict()},
                        output_schema=_REFLECTION_OUTPUT_SCHEMA,
                    )
                )
        if step > 0 and step % self.policy.proposal_interval == 0:
            available = max(
                0,
                self.policy.max_open_proposals
                - sum(
                    proposal.status in {"draft", "under_review"}
                    for proposal in self._proposals.values()
                ),
            )
            budget = min(self.policy.max_new_proposals_per_batch, available)
            eligible = [
                wish
                for wish in self._wishes.values()
                if wish.status == "open"
                and not wish.generated_proposal_ids
                and self._wish_is_stable(wish)
            ]
            eligible.sort(
                key=lambda wish: (
                    -float(wish.attributes.get("urgency", 0.0) or 0.0),
                    wish.wish_id,
                )
            )
            for wish in eligible[:budget]:
                request_id = f"formation:{self.run_id}:proposal:{wish.wish_id}:{step}"
                if request_id in self._issued_synthesis:
                    continue
                requests.append(
                    OrganizationSynthesisRequest(
                        request_id=request_id,
                        organization_id=self.organization_id,
                        run_id=self.run_id,
                        kind=OrganizationSynthesisKind.PROPOSAL,
                        step=step,
                        actor_id=wish.member_id,
                        source_refs=(wish.wish_id,),
                        instructions=(
                            "Turn this stable wish into one reviewable organizational "
                            "proposal. Preserve evidence lineage and do not adopt it."
                        ),
                        context={"wish": wish.as_dict()},
                        output_schema=_PROPOSAL_OUTPUT_SCHEMA,
                    )
                )
        if step > 0 and step % self.policy.institution_interval == 0:
            open_wishes = [
                wish.as_dict()
                for wish in self._wishes.values()
                if wish.status == "open"
            ]
            if open_wishes:
                request_id = f"formation:{self.run_id}:institution:{step}"
                if request_id not in self._issued_synthesis:
                    requests.append(
                        OrganizationSynthesisRequest(
                            request_id=request_id,
                            organization_id=self.organization_id,
                            run_id=self.run_id,
                            kind=OrganizationSynthesisKind.INSTITUTION,
                            step=step,
                            source_refs=tuple(
                                wish["wish_id"] for wish in open_wishes
                            ),
                            instructions=(
                                "Cluster recurrent wishes and return at most one "
                                "evidence-grounded institutional proposal. Do not adopt it."
                            ),
                            context={"wishes": open_wishes},
                            output_schema=_PROPOSAL_OUTPUT_SCHEMA,
                        )
                    )
        for request in requests:
            self._issued_synthesis.add(request.request_id)
        return tuple(requests)

    def run_due_synthesis(
        self,
        port: OrganizationSynthesisPort,
        *,
        step: int,
    ) -> tuple[OrganizationSynthesisResult, ...]:
        results = []
        for request in self.due_synthesis_requests(step=step):
            result = validate_synthesis_result(request, port.synthesize(request))
            results.append(result)
            if result.completed:
                self.apply_synthesis_result(request, result)
        self._last_step = max(self._last_step, int(step))
        return tuple(results)

    def apply_synthesis_result(
        self,
        request: OrganizationSynthesisRequest,
        result: OrganizationSynthesisResult,
    ) -> bool:
        validate_synthesis_result(request, result)
        if not result.completed:
            return False
        if request.request_id in self._applied_synthesis:
            return False
        if request.kind is OrganizationSynthesisKind.REFLECTION:
            reflection = OrganizationReflectionState.from_dict(
                _record(result.output.get("reflection"))
            )
            wishes = tuple(
                OrganizationWishState.from_dict(row)
                for row in result.output.get("wishes", ())
            )
            self.record_reflection(reflection, wishes=wishes)
        elif request.kind in {
            OrganizationSynthesisKind.PROPOSAL,
            OrganizationSynthesisKind.INSTITUTION,
            OrganizationSynthesisKind.REPAIR,
        }:
            proposal = OrganizationProposalState.from_dict(
                _record(result.output.get("proposal"))
            )
            wish_id = (
                request.source_refs[0]
                if request.kind is OrganizationSynthesisKind.PROPOSAL
                and request.source_refs
                else None
            )
            self.record_proposal(proposal, wish_id=wish_id)
        else:
            raise ValueError(
                f"unsupported formation synthesis kind: {request.kind.value}"
            )
        self._applied_synthesis.add(request.request_id)
        self._last_step = max(self._last_step, request.step)
        return True

    def tool_definitions(self) -> tuple[OrganizationToolDefinition, ...]:
        return (
            OrganizationToolDefinition(
                name="query_context",
                description=(
                    "Read bounded shared organizational memory, open wishes, "
                    "proposals, adopted protocols, and tools."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "member_id": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    "additionalProperties": False,
                },
                handler=lambda args: self.query_context(
                    member_id=str(args.get("member_id") or "") or None,
                    limit=int(args.get("limit") or 12),
                ),
            ),
            OrganizationToolDefinition(
                name="record_episode",
                description=(
                    "Record one bounded organizational episode after relevant "
                    "work evidence is available."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"episode": {"type": "object"}},
                    "required": ["episode"],
                    "additionalProperties": False,
                },
                handler=self._tool_record_episode,
            ),
            OrganizationToolDefinition(
                name="record_reflection",
                description=(
                    "Atomically record one reflection and its grounded wishes in "
                    "shared organizational memory."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "reflection": {"type": "object"},
                        "wishes": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["reflection", "wishes"],
                    "additionalProperties": False,
                },
                handler=self._tool_record_reflection,
            ),
            OrganizationToolDefinition(
                name="submit_proposal",
                description=(
                    "Submit one organizational proposal for validation/review; "
                    "this does not adopt it."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "proposal": {"type": "object"},
                        "wish_id": {"type": "string"},
                    },
                    "required": ["proposal"],
                    "additionalProperties": False,
                },
                handler=self._tool_submit_proposal,
            ),
        )

    def _tool_record_episode(self, args: Mapping[str, Any]) -> Mapping[str, Any]:
        episode = OrganizationEpisodeState.from_dict(_record(args.get("episode")))
        inserted = self.record_episode(episode)
        return {"recorded": inserted, "episode_id": episode.episode_id}

    def _tool_record_reflection(
        self,
        args: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        reflection = OrganizationReflectionState.from_dict(
            _record(args.get("reflection"))
        )
        wishes = tuple(
            OrganizationWishState.from_dict(row)
            for row in args.get("wishes", ())
        )
        inserted = self.record_reflection(reflection, wishes=wishes)
        return {
            "recorded": inserted,
            "reflection_id": reflection.reflection_id,
            "wish_ids": [wish.wish_id for wish in wishes],
        }

    def _tool_submit_proposal(
        self,
        args: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        proposal = OrganizationProposalState.from_dict(
            _record(args.get("proposal"))
        )
        inserted = self.record_proposal(
            proposal,
            wish_id=str(args.get("wish_id") or "") or None,
        )
        return {"recorded": inserted, "proposal_id": proposal.proposal_id}

    def _wish_is_stable(self, wish: OrganizationWishState) -> bool:
        return wish_is_stable(wish, self.policy)

    def wish_is_stable(self, wish: OrganizationWishState) -> bool:
        """Public policy hook used by compatibility adapters."""
        return self._wish_is_stable(wish)


def _record(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("organization lifecycle record must be an object")
    return value


def _insert_same_or_fail(store: dict[str, Any], key: str, value: Any) -> bool:
    previous = store.get(key)
    if previous is None:
        store[key] = value
        return True
    if previous != value:
        raise ValueError(f"organization record id reused with different content: {key}")
    return False


__all__ = [
    "DEFAULT_ORGANIZATION_FORMATION_POLICY",
    "OrganizationFormationPolicy",
    "OrganizationFormationRuntime",
    "OrganizationToolDefinition",
    "OrganizationToolHandler",
    "wish_is_stable",
]
