"""Harness-owned synthesis contracts for organizational cognition.

The organization core decides *when* a reflection, proposal, or institution
needs synthesis and validates the returned record.  A mounted harness owns the
model conversation, tool loop, retries, compaction, and provider credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable

from organization_core.contracts import _json_value


SYNTHESIS_SCHEMA_VERSION = "organization-synthesis/v1"


class OrganizationSynthesisKind(str, Enum):
    EPISODE_SUMMARY = "episode_summary"
    REFLECTION = "reflection"
    WISH = "wish"
    PROPOSAL = "proposal"
    PROPOSAL_EVALUATION = "proposal_evaluation"
    INSTITUTION = "institution"
    TOOL = "tool"
    REPAIR = "repair"


class OrganizationSynthesisStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    DECLINED = "declined"


@dataclass(frozen=True)
class OrganizationSynthesisRequest:
    """A portable cognition request, not an LLM API request.

    ``instructions`` describe the organizational judgment to perform, while
    ``context`` and ``output_schema`` are detached JSON values.  They are
    intentionally provider-neutral so an adapter may use a Codex turn, a
    Cursor agent, a DeepSeek harness session, or a deterministic test double.
    """

    request_id: str
    organization_id: str
    run_id: str
    kind: OrganizationSynthesisKind | str
    step: int
    instructions: str
    actor_id: str | None = None
    source_refs: tuple[str, ...] = ()
    context: Mapping[str, Any] = field(default_factory=dict)
    output_schema: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SYNTHESIS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "request_id",
            "organization_id",
            "run_id",
            "instructions",
            "schema_version",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"synthesis {name} is required")
        if self.schema_version != SYNTHESIS_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported synthesis schema: {self.schema_version}"
            )
        object.__setattr__(self, "kind", OrganizationSynthesisKind(self.kind))
        object.__setattr__(self, "step", int(self.step))
        if self.step < 0:
            raise ValueError("synthesis step must be non-negative")
        object.__setattr__(
            self,
            "source_refs",
            tuple(dict.fromkeys(str(ref) for ref in self.source_refs if str(ref))),
        )
        object.__setattr__(
            self,
            "context",
            _json_value(self.context, path="synthesis.context"),
        )
        object.__setattr__(
            self,
            "output_schema",
            _json_value(self.output_schema, path="synthesis.output_schema"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "organization_id": self.organization_id,
            "run_id": self.run_id,
            "kind": self.kind.value,
            "step": self.step,
            "actor_id": self.actor_id,
            "source_refs": list(self.source_refs),
            "instructions": self.instructions,
            "context": _json_value(self.context, path="synthesis.context"),
            "output_schema": _json_value(
                self.output_schema,
                path="synthesis.output_schema",
            ),
        }


@dataclass(frozen=True)
class OrganizationSynthesisResult:
    """Result and provenance returned by a harness synthesis session."""

    request_id: str
    status: OrganizationSynthesisStatus | str
    host: str
    output: Mapping[str, Any] = field(default_factory=dict)
    model: str = ""
    thread_id: str | None = None
    turn_id: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    error: str = ""

    def __post_init__(self) -> None:
        for name in ("request_id", "host"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"synthesis result {name} is required")
        object.__setattr__(self, "status", OrganizationSynthesisStatus(self.status))
        object.__setattr__(
            self,
            "output",
            _json_value(self.output, path="synthesis.result.output"),
        )
        object.__setattr__(
            self,
            "usage",
            _json_value(self.usage, path="synthesis.result.usage"),
        )
        if self.status is OrganizationSynthesisStatus.COMPLETED and self.error:
            raise ValueError("completed synthesis cannot contain an error")

    @property
    def completed(self) -> bool:
        return self.status is OrganizationSynthesisStatus.COMPLETED

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status.value,
            "host": self.host,
            "model": self.model,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "output": _json_value(
                self.output,
                path="synthesis.result.output",
            ),
            "usage": _json_value(self.usage, path="synthesis.result.usage"),
            "error": self.error,
        }


@runtime_checkable
class OrganizationSynthesisPort(Protocol):
    """The only outbound cognition dependency allowed in organization_core."""

    def synthesize(
        self,
        request: OrganizationSynthesisRequest,
    ) -> OrganizationSynthesisResult: ...


def validate_synthesis_result(
    request: OrganizationSynthesisRequest,
    result: OrganizationSynthesisResult,
) -> OrganizationSynthesisResult:
    if result.request_id != request.request_id:
        raise ValueError("synthesis result request_id does not match request")
    if result.completed and not isinstance(result.output, Mapping):
        raise TypeError("completed synthesis output must be an object")
    return result


__all__ = [
    "OrganizationSynthesisKind",
    "OrganizationSynthesisPort",
    "OrganizationSynthesisRequest",
    "OrganizationSynthesisResult",
    "OrganizationSynthesisStatus",
    "SYNTHESIS_SCHEMA_VERSION",
    "validate_synthesis_result",
]
