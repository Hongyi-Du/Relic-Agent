"""Portable organizational state independent of any harness or environment."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Mapping

from organization_core.contracts import _json_value
from organization_core.gates import TypedGateRule


ORGANIZATION_STATE_SCHEMA_VERSION = "organization-state/v1"
LEGACY_CAPABILITY_BUNDLE_SCHEMA_VERSION = "org_capability_bundle_v1"


def _mapping(value: Mapping[str, Any], *, path: str) -> Mapping[str, Any]:
    return _json_value(dict(value), path=path)


def _refs(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value or "")))


@dataclass(frozen=True)
class OrganizationMemberState:
    member_id: str
    role: str = ""
    profile: Mapping[str, Any] = field(default_factory=dict)
    skills: Mapping[str, Any] = field(default_factory=dict)
    failure_modes: tuple[str, ...] = ()
    communication_style: Mapping[str, Any] = field(default_factory=dict)
    reputation: Mapping[str, Any] = field(default_factory=dict)
    authority: Mapping[str, Any] = field(default_factory=dict)
    go_to_tags: tuple[str, ...] = ()
    memory: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.member_id or "").strip():
            raise ValueError("member_id is required")
        for name in (
            "profile",
            "skills",
            "communication_style",
            "reputation",
            "authority",
            "memory",
        ):
            object.__setattr__(
                self,
                name,
                _mapping(getattr(self, name), path=f"member.{name}"),
            )
        object.__setattr__(self, "failure_modes", _refs(self.failure_modes))
        object.__setattr__(self, "go_to_tags", _refs(self.go_to_tags))

    def as_dict(self) -> dict[str, Any]:
        return {
            "member_id": self.member_id,
            "role": self.role,
            "profile": _json_value(self.profile, path="member.profile"),
            "skills": _json_value(self.skills, path="member.skills"),
            "failure_modes": list(self.failure_modes),
            "communication_style": _json_value(
                self.communication_style,
                path="member.communication_style",
            ),
            "reputation": _json_value(self.reputation, path="member.reputation"),
            "authority": _json_value(self.authority, path="member.authority"),
            "go_to_tags": list(self.go_to_tags),
            "memory": _json_value(self.memory, path="member.memory"),
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "OrganizationMemberState":
        return cls(
            member_id=str(row.get("member_id") or row.get("agent_id") or ""),
            role=str(row.get("role") or ""),
            profile=row.get("profile") or {},
            skills=row.get("skills") or {},
            failure_modes=tuple(row.get("failure_modes") or ()),
            communication_style=row.get("communication_style") or {},
            reputation=row.get("reputation") or {},
            authority=row.get("authority") or {},
            go_to_tags=tuple(row.get("go_to_tags") or ()),
            memory=row.get("memory") or {},
        )

    def as_legacy_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.member_id,
            "role": self.role,
            "profile": _json_value(self.profile, path="member.profile"),
            "skills": _json_value(self.skills, path="member.skills"),
            "failure_modes": list(self.failure_modes),
            "communication_style": _json_value(
                self.communication_style,
                path="member.communication_style",
            ),
            "reputation": _json_value(self.reputation, path="member.reputation"),
            "go_to_tags": list(self.go_to_tags),
        }


def _lineage(value: Mapping[str, Any]) -> Mapping[str, tuple[str, ...]]:
    return {
        str(kind): _refs(refs)
        for kind, refs in value.items()
        if str(kind or "")
    }


@dataclass(frozen=True)
class OrganizationEpisodeState:
    episode_id: str
    episode_type: str
    status: str
    start_step: int
    end_step: int | None = None
    participant_ids: tuple[str, ...] = ()
    primary_member_id: str | None = None
    lineage_refs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("episode_id", "episode_type", "status"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        object.__setattr__(self, "start_step", int(self.start_step))
        if self.end_step is not None:
            object.__setattr__(self, "end_step", int(self.end_step))
        object.__setattr__(self, "participant_ids", _refs(self.participant_ids))
        object.__setattr__(self, "lineage_refs", _lineage(self.lineage_refs))
        object.__setattr__(
            self, "attributes", _mapping(self.attributes, path="episode.attributes")
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "episode_type": self.episode_type,
            "status": self.status,
            "start_step": self.start_step,
            "end_step": self.end_step,
            "participant_ids": list(self.participant_ids),
            "primary_member_id": self.primary_member_id,
            "lineage_refs": {
                kind: list(refs) for kind, refs in sorted(self.lineage_refs.items())
            },
            "attributes": _json_value(self.attributes, path="episode.attributes"),
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "OrganizationEpisodeState":
        return cls(
            episode_id=str(row.get("episode_id") or ""),
            episode_type=str(row.get("episode_type") or ""),
            status=str(row.get("status") or ""),
            start_step=int(row.get("start_step") or 0),
            end_step=(
                int(row["end_step"]) if row.get("end_step") is not None else None
            ),
            participant_ids=tuple(row.get("participant_ids") or ()),
            primary_member_id=(
                str(row["primary_member_id"])
                if row.get("primary_member_id") is not None
                else None
            ),
            lineage_refs=row.get("lineage_refs") or {},
            attributes=row.get("attributes") or {},
        )


@dataclass(frozen=True)
class OrganizationReflectionState:
    reflection_id: str
    member_id: str
    step: int
    source_refs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    created_wish_ids: tuple[str, ...] = ()
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("reflection_id", "member_id"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        object.__setattr__(self, "step", int(self.step))
        object.__setattr__(self, "source_refs", _lineage(self.source_refs))
        object.__setattr__(self, "created_wish_ids", _refs(self.created_wish_ids))
        object.__setattr__(
            self,
            "attributes",
            _mapping(self.attributes, path="reflection.attributes"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "reflection_id": self.reflection_id,
            "member_id": self.member_id,
            "step": self.step,
            "source_refs": {
                kind: list(refs) for kind, refs in sorted(self.source_refs.items())
            },
            "created_wish_ids": list(self.created_wish_ids),
            "attributes": _json_value(
                self.attributes, path="reflection.attributes"
            ),
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "OrganizationReflectionState":
        return cls(
            reflection_id=str(row.get("reflection_id") or ""),
            member_id=str(row.get("member_id") or ""),
            step=int(row.get("step") or 0),
            source_refs=row.get("source_refs") or {},
            created_wish_ids=tuple(row.get("created_wish_ids") or ()),
            attributes=row.get("attributes") or {},
        )


@dataclass(frozen=True)
class OrganizationWishState:
    wish_id: str
    member_id: str
    wish_type: str
    status: str
    source_refs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    generated_proposal_ids: tuple[str, ...] = ()
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("wish_id", "member_id", "wish_type", "status"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        object.__setattr__(self, "source_refs", _lineage(self.source_refs))
        object.__setattr__(
            self, "generated_proposal_ids", _refs(self.generated_proposal_ids)
        )
        object.__setattr__(
            self, "attributes", _mapping(self.attributes, path="wish.attributes")
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "wish_id": self.wish_id,
            "member_id": self.member_id,
            "wish_type": self.wish_type,
            "status": self.status,
            "source_refs": {
                kind: list(refs) for kind, refs in sorted(self.source_refs.items())
            },
            "generated_proposal_ids": list(self.generated_proposal_ids),
            "attributes": _json_value(self.attributes, path="wish.attributes"),
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "OrganizationWishState":
        return cls(
            wish_id=str(row.get("wish_id") or ""),
            member_id=str(row.get("member_id") or ""),
            wish_type=str(row.get("wish_type") or ""),
            status=str(row.get("status") or ""),
            source_refs=row.get("source_refs") or {},
            generated_proposal_ids=tuple(row.get("generated_proposal_ids") or ()),
            attributes=row.get("attributes") or {},
        )


@dataclass(frozen=True)
class OrganizationProposalState:
    proposal_id: str
    proposal_type: str
    status: str
    proposer_member_id: str | None = None
    source_refs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    created_step: int = 0
    updated_step: int = 0
    created_object_id: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("proposal_id", "proposal_type", "status"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        object.__setattr__(self, "source_refs", _lineage(self.source_refs))
        object.__setattr__(self, "created_step", int(self.created_step))
        object.__setattr__(self, "updated_step", int(self.updated_step))
        object.__setattr__(
            self,
            "attributes",
            _mapping(self.attributes, path="proposal.attributes"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "proposal_type": self.proposal_type,
            "status": self.status,
            "proposer_member_id": self.proposer_member_id,
            "source_refs": {
                kind: list(refs) for kind, refs in sorted(self.source_refs.items())
            },
            "created_step": self.created_step,
            "updated_step": self.updated_step,
            "created_object_id": self.created_object_id,
            "attributes": _json_value(
                self.attributes, path="proposal.attributes"
            ),
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "OrganizationProposalState":
        return cls(
            proposal_id=str(row.get("proposal_id") or ""),
            proposal_type=str(row.get("proposal_type") or ""),
            status=str(row.get("status") or ""),
            proposer_member_id=(
                str(row["proposer_member_id"])
                if row.get("proposer_member_id") is not None
                else None
            ),
            source_refs=row.get("source_refs") or {},
            created_step=int(row.get("created_step") or 0),
            updated_step=int(row.get("updated_step") or 0),
            created_object_id=(
                str(row["created_object_id"])
                if row.get("created_object_id") is not None
                else None
            ),
            attributes=row.get("attributes") or {},
        )


@dataclass(frozen=True)
class OrganizationToolState:
    """Portable definition of an adopted organizational tool or workflow.

    A workflow is represented by ``tool_type='workflow_tool'`` because hosts
    execute both forms through the same composed-action boundary.  The core
    stores the definition and lineage; a host adapter decides how those action
    names become executable in its own harness.
    """

    tool_id: str
    name: str
    tool_type: str = "composed_action_tool"
    status: str = "active"
    description: str = ""
    creator_member_id: str | None = None
    source_refs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    output_schema: Mapping[str, Any] = field(default_factory=dict)
    required_actions: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = ()
    risk_tags: tuple[str, ...] = ()
    validation_rules: tuple[str, ...] = ()
    callable_by_roles: tuple[str, ...] = ()
    callable_by_members: tuple[str, ...] = ()
    family: str = ""
    supporters: tuple[str, ...] = ()
    support_count: int = 0
    folded_proposal_ids: tuple[str, ...] = ()
    created_step: int = 0
    adopted_step: int = 0
    updated_step: int = 0
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("tool_id", "name", "tool_type", "status"):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"{field_name} is required")
        object.__setattr__(
            self, "input_schema", _mapping(self.input_schema, path="tool.input_schema")
        )
        object.__setattr__(
            self,
            "output_schema",
            _mapping(self.output_schema, path="tool.output_schema"),
        )
        object.__setattr__(self, "source_refs", _lineage(self.source_refs))
        for field_name in (
            "required_actions",
            "required_capabilities",
            "required_permissions",
            "risk_tags",
            "validation_rules",
            "callable_by_roles",
            "callable_by_members",
            "supporters",
            "folded_proposal_ids",
        ):
            object.__setattr__(self, field_name, _refs(getattr(self, field_name)))
        for field_name in (
            "support_count",
            "created_step",
            "adopted_step",
            "updated_step",
        ):
            value = int(getattr(self, field_name))
            if value < 0:
                raise ValueError(f"{field_name} must be non-negative")
            object.__setattr__(self, field_name, value)
        object.__setattr__(
            self, "attributes", _mapping(self.attributes, path="tool.attributes")
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "name": self.name,
            "tool_type": self.tool_type,
            "status": self.status,
            "description": self.description,
            "creator_member_id": self.creator_member_id,
            "source_refs": {
                kind: list(refs) for kind, refs in sorted(self.source_refs.items())
            },
            "input_schema": _json_value(self.input_schema, path="tool.input_schema"),
            "output_schema": _json_value(
                self.output_schema, path="tool.output_schema"
            ),
            "required_actions": list(self.required_actions),
            "required_capabilities": list(self.required_capabilities),
            "required_permissions": list(self.required_permissions),
            "risk_tags": list(self.risk_tags),
            "validation_rules": list(self.validation_rules),
            "callable_by_roles": list(self.callable_by_roles),
            "callable_by_members": list(self.callable_by_members),
            "family": self.family,
            "supporters": list(self.supporters),
            "support_count": self.support_count,
            "folded_proposal_ids": list(self.folded_proposal_ids),
            "created_step": self.created_step,
            "adopted_step": self.adopted_step,
            "updated_step": self.updated_step,
            "attributes": _json_value(self.attributes, path="tool.attributes"),
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "OrganizationToolState":
        return cls(
            tool_id=str(row.get("tool_id") or ""),
            name=str(row.get("name") or ""),
            tool_type=str(row.get("tool_type") or "composed_action_tool"),
            status=str(row.get("status") or "active"),
            description=str(row.get("description") or ""),
            creator_member_id=(
                str(row["creator_member_id"])
                if row.get("creator_member_id") is not None
                else None
            ),
            source_refs=row.get("source_refs") or {},
            input_schema=row.get("input_schema") or {},
            output_schema=row.get("output_schema") or {},
            required_actions=tuple(row.get("required_actions") or ()),
            required_capabilities=tuple(row.get("required_capabilities") or ()),
            required_permissions=tuple(row.get("required_permissions") or ()),
            risk_tags=tuple(row.get("risk_tags") or ()),
            validation_rules=tuple(row.get("validation_rules") or ()),
            callable_by_roles=tuple(row.get("callable_by_roles") or ()),
            callable_by_members=tuple(row.get("callable_by_members") or ()),
            family=str(row.get("family") or ""),
            supporters=tuple(row.get("supporters") or ()),
            support_count=int(row.get("support_count") or 0),
            folded_proposal_ids=tuple(row.get("folded_proposal_ids") or ()),
            created_step=int(row.get("created_step") or 0),
            adopted_step=int(row.get("adopted_step") or 0),
            updated_step=int(row.get("updated_step") or 0),
            attributes=row.get("attributes") or {},
        )


@dataclass(frozen=True)
class OrganizationProtocolState:
    protocol_id: str
    protocol_type: str
    rule_summary: str = ""
    scope: str = "review"
    target_process: str = ""
    supporters: tuple[str, ...] = ()
    emergence_level: str = "none"
    capability: str = ""
    evidence_refs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    gate_rules: tuple[TypedGateRule, ...] = ()
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.protocol_id or "").strip():
            raise ValueError("protocol_id is required")
        if not str(self.protocol_type or "").strip():
            raise ValueError("protocol_type is required")
        object.__setattr__(self, "supporters", _refs(self.supporters))
        normalized = {
            str(kind): _refs(refs)
            for kind, refs in self.evidence_refs.items()
            if str(kind or "")
        }
        object.__setattr__(self, "evidence_refs", normalized)
        object.__setattr__(self, "gate_rules", tuple(self.gate_rules))
        if not all(isinstance(rule, TypedGateRule) for rule in self.gate_rules):
            raise TypeError("protocol gate_rules must contain TypedGateRule values")
        object.__setattr__(
            self,
            "attributes",
            _mapping(self.attributes, path="protocol.attributes"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "protocol_type": self.protocol_type,
            "rule_summary": self.rule_summary,
            "scope": self.scope,
            "target_process": self.target_process,
            "supporters": list(self.supporters),
            "emergence_level": self.emergence_level,
            "capability": self.capability,
            "evidence_refs": {
                kind: list(refs) for kind, refs in sorted(self.evidence_refs.items())
            },
            "gate_rules": [rule.as_dict() for rule in self.gate_rules],
            "attributes": _json_value(
                self.attributes,
                path="protocol.attributes",
            ),
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "OrganizationProtocolState":
        return cls(
            protocol_id=str(row.get("protocol_id") or ""),
            protocol_type=str(row.get("protocol_type") or ""),
            rule_summary=str(row.get("rule_summary") or ""),
            scope=str(row.get("scope") or "review"),
            target_process=str(row.get("target_process") or ""),
            supporters=tuple(row.get("supporters") or ()),
            emergence_level=str(row.get("emergence_level") or "none"),
            capability=str(row.get("capability") or ""),
            evidence_refs={
                str(kind): tuple(refs or ())
                for kind, refs in (row.get("evidence_refs") or {}).items()
            },
            gate_rules=tuple(
                TypedGateRule.from_dict(rule)
                for rule in (row.get("gate_rules") or ())
            ),
            attributes=row.get("attributes") or {},
        )

    def as_legacy_dict(self) -> dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "protocol_type": self.protocol_type,
            "rule_summary": self.rule_summary,
            "scope": self.scope,
            "target_process": self.target_process,
            "supporters": list(self.supporters),
            "emergence_level": self.emergence_level,
            "capability": self.capability,
        }


@dataclass(frozen=True)
class OrganizationCarrierState:
    carrier_id: str
    carrier_type: str
    title: str = ""
    content_summary: str = ""
    capability: str = ""
    linked_task_refs: tuple[str, ...] = ()
    linked_protocol_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.carrier_id or "").strip():
            raise ValueError("carrier_id is required")
        if not str(self.carrier_type or "").strip():
            raise ValueError("carrier_type is required")
        object.__setattr__(self, "linked_task_refs", _refs(self.linked_task_refs))
        object.__setattr__(self, "linked_protocol_refs", _refs(self.linked_protocol_refs))

    def as_dict(self) -> dict[str, Any]:
        return {
            "carrier_id": self.carrier_id,
            "carrier_type": self.carrier_type,
            "title": self.title,
            "content_summary": self.content_summary,
            "capability": self.capability,
            "linked_task_refs": list(self.linked_task_refs),
            "linked_protocol_refs": list(self.linked_protocol_refs),
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "OrganizationCarrierState":
        return cls(
            carrier_id=str(row.get("carrier_id") or row.get("doc_id") or ""),
            carrier_type=str(row.get("carrier_type") or row.get("doc_type") or ""),
            title=str(row.get("title") or ""),
            content_summary=str(row.get("content_summary") or ""),
            capability=str(row.get("capability") or ""),
            linked_task_refs=tuple(row.get("linked_task_refs") or ()),
            linked_protocol_refs=tuple(row.get("linked_protocol_refs") or ()),
        )

    def as_legacy_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.carrier_id,
            "doc_type": self.carrier_type,
            "title": self.title,
            "content_summary": self.content_summary,
            "capability": self.capability,
        }


@dataclass(frozen=True)
class OrganizationStateBundle:
    organization_id: str
    source_repository_id: str
    source_seed: int
    source_step: int
    members: tuple[OrganizationMemberState, ...] = ()
    protocols: tuple[OrganizationProtocolState, ...] = ()
    carriers: tuple[OrganizationCarrierState, ...] = ()
    episodes: tuple[OrganizationEpisodeState, ...] = ()
    reflections: tuple[OrganizationReflectionState, ...] = ()
    wishes: tuple[OrganizationWishState, ...] = ()
    proposals: tuple[OrganizationProposalState, ...] = ()
    tools: tuple[OrganizationToolState, ...] = ()
    capabilities: tuple[str, ...] = ()
    source_run_id: str = ""
    schema_version: str = ORGANIZATION_STATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("organization_id", "source_repository_id"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        if self.schema_version != ORGANIZATION_STATE_SCHEMA_VERSION:
            raise ValueError(f"unsupported organization state schema: {self.schema_version}")
        object.__setattr__(self, "source_seed", int(self.source_seed))
        object.__setattr__(self, "source_step", int(self.source_step))
        if self.source_step < 0:
            raise ValueError("source_step must be non-negative")
        for name, expected in (
            ("members", OrganizationMemberState),
            ("protocols", OrganizationProtocolState),
            ("carriers", OrganizationCarrierState),
            ("episodes", OrganizationEpisodeState),
            ("reflections", OrganizationReflectionState),
            ("wishes", OrganizationWishState),
            ("proposals", OrganizationProposalState),
            ("tools", OrganizationToolState),
        ):
            values = tuple(getattr(self, name))
            if not all(isinstance(item, expected) for item in values):
                raise TypeError(f"{name} contains an invalid state value")
            object.__setattr__(self, name, values)
        object.__setattr__(self, "capabilities", tuple(sorted(set(_refs(self.capabilities)))))
        for name, values in (
            ("member", [item.member_id for item in self.members]),
            ("protocol", [item.protocol_id for item in self.protocols]),
            ("carrier", [item.carrier_id for item in self.carriers]),
            ("episode", [item.episode_id for item in self.episodes]),
            ("reflection", [item.reflection_id for item in self.reflections]),
            ("wish", [item.wish_id for item in self.wishes]),
            ("proposal", [item.proposal_id for item in self.proposals]),
            ("tool", [item.tool_id for item in self.tools]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {name} ids in organization state")
        wish_ids = {item.wish_id for item in self.wishes}
        proposal_ids = {item.proposal_id for item in self.proposals}
        for reflection in self.reflections:
            missing = set(reflection.created_wish_ids) - wish_ids
            if missing:
                raise ValueError(
                    "reflection references missing wishes: "
                    + ",".join(sorted(missing))
                )
        for wish in self.wishes:
            missing = set(wish.generated_proposal_ids) - proposal_ids
            if missing:
                raise ValueError(
                    "wish references missing proposals: "
                    + ",".join(sorted(missing))
                )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "organization_id": self.organization_id,
            "source_repository_id": self.source_repository_id,
            "source_run_id": self.source_run_id,
            "source_seed": self.source_seed,
            "source_step": self.source_step,
            "members": [item.as_dict() for item in self.members],
            "protocols": [item.as_dict() for item in self.protocols],
            "carriers": [item.as_dict() for item in self.carriers],
            "episodes": [item.as_dict() for item in self.episodes],
            "reflections": [item.as_dict() for item in self.reflections],
            "wishes": [item.as_dict() for item in self.wishes],
            "proposals": [item.as_dict() for item in self.proposals],
            "tools": [item.as_dict() for item in self.tools],
            "capabilities": list(self.capabilities),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OrganizationStateBundle":
        return cls(
            schema_version=str(payload.get("schema_version") or ""),
            organization_id=str(payload.get("organization_id") or ""),
            source_repository_id=str(payload.get("source_repository_id") or ""),
            source_run_id=str(payload.get("source_run_id") or ""),
            source_seed=int(payload.get("source_seed") or 0),
            source_step=int(payload.get("source_step") or 0),
            members=tuple(
                OrganizationMemberState.from_dict(row)
                for row in (payload.get("members") or ())
            ),
            protocols=tuple(
                OrganizationProtocolState.from_dict(row)
                for row in (payload.get("protocols") or ())
            ),
            carriers=tuple(
                OrganizationCarrierState.from_dict(row)
                for row in (payload.get("carriers") or ())
            ),
            episodes=tuple(
                OrganizationEpisodeState.from_dict(row)
                for row in (payload.get("episodes") or ())
            ),
            reflections=tuple(
                OrganizationReflectionState.from_dict(row)
                for row in (payload.get("reflections") or ())
            ),
            wishes=tuple(
                OrganizationWishState.from_dict(row)
                for row in (payload.get("wishes") or ())
            ),
            proposals=tuple(
                OrganizationProposalState.from_dict(row)
                for row in (payload.get("proposals") or ())
            ),
            tools=tuple(
                OrganizationToolState.from_dict(row)
                for row in (payload.get("tools") or ())
            ),
            capabilities=tuple(payload.get("capabilities") or ()),
        )

    @classmethod
    def from_legacy_capability_bundle(
        cls,
        payload: Mapping[str, Any],
    ) -> "OrganizationStateBundle":
        if payload.get("schema_version") != LEGACY_CAPABILITY_BUNDLE_SCHEMA_VERSION:
            raise ValueError(
                f"capability_bundle_schema_unsupported:{payload.get('schema_version')}"
            )
        source_repository_id = str(payload.get("source_repository_id") or "")
        if not source_repository_id:
            raise ValueError("capability_bundle_source_repository_required")
        for key in ("protocols", "documents", "roster"):
            if not isinstance(payload.get(key), list):
                raise ValueError(f"capability_bundle_{key}_must_be_a_list")
        return cls(
            organization_id=f"organization:{source_repository_id}",
            source_repository_id=source_repository_id,
            source_seed=int(payload.get("source_seed") or 0),
            source_step=int(payload.get("source_tick") or 0),
            members=tuple(
                OrganizationMemberState.from_dict(row) for row in payload["roster"]
            ),
            protocols=tuple(
                OrganizationProtocolState.from_dict(row)
                for row in payload["protocols"]
            ),
            carriers=tuple(
                OrganizationCarrierState.from_dict(row)
                for row in payload["documents"]
            ),
            capabilities=tuple(payload.get("capabilities") or ()),
        )

    def as_legacy_capability_bundle(self) -> dict[str, Any]:
        return {
            "schema_version": LEGACY_CAPABILITY_BUNDLE_SCHEMA_VERSION,
            "source_repository_id": self.source_repository_id,
            "source_seed": self.source_seed,
            "source_tick": self.source_step,
            "protocols": [item.as_legacy_dict() for item in self.protocols],
            "documents": [item.as_legacy_dict() for item in self.carriers],
            "roster": [item.as_legacy_dict() for item in self.members],
            "capabilities": list(self.capabilities),
        }

    def canonical_sha256(self) -> str:
        encoded = json.dumps(
            self.as_dict(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "LEGACY_CAPABILITY_BUNDLE_SCHEMA_VERSION",
    "ORGANIZATION_STATE_SCHEMA_VERSION",
    "OrganizationCarrierState",
    "OrganizationEpisodeState",
    "OrganizationMemberState",
    "OrganizationProposalState",
    "OrganizationProtocolState",
    "OrganizationReflectionState",
    "OrganizationStateBundle",
    "OrganizationToolState",
    "OrganizationWishState",
]
