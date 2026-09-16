"""Harness-agnostic evidence carried by an organizational capability.

These records are the common denominator between a host's evaluator-facing
event packet and a paper-facing capability ledger.  Hosts may retain richer
replay and metric data; adapters project the portable use, enforcement, and
held-out transfer facts into this contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class CapabilityUseEvidence:
    event_id: str
    actor_id: str
    tick: int
    shared_artifact_ref: str
    episode_id: str | None = None
    task_id: str | None = None
    support_refs: tuple[str, ...] = ()
    occurred_after_adoption: bool = True
    successful: bool = True


@dataclass(frozen=True)
class CapabilityEnforcementEvidence:
    event_id: str
    violation_event_id: str
    state_impact_ref: str
    support_refs: tuple[str, ...] = ()
    blocked_or_repaired: bool = True


@dataclass(frozen=True)
class CapabilityTransferEvidence:
    event_id: str
    held_out_target_id: str
    capability_invocation_ref: str
    baseline_outcome: float
    capability_outcome: float
    support_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilityEvidenceBundle:
    shared_artifact_refs: tuple[str, ...] = ()
    origin_episode_id: str | None = None
    origin_task_id: str | None = None
    use_events: tuple[CapabilityUseEvidence, ...] = ()
    enforcement_events: tuple[CapabilityEnforcementEvidence, ...] = ()
    transfer_events: tuple[CapabilityTransferEvidence, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        def row(item) -> dict[str, Any]:
            payload = dict(vars(item))
            payload["support_refs"] = list(item.support_refs)
            return payload

        return {
            "shared_artifact_refs": list(self.shared_artifact_refs),
            "origin_episode_id": self.origin_episode_id,
            "origin_task_id": self.origin_task_id,
            "use_events": [row(item) for item in self.use_events],
            "enforcement_events": [row(item) for item in self.enforcement_events],
            "transfer_events": [row(item) for item in self.transfer_events],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CapabilityEvidenceBundle":
        return cls(
            shared_artifact_refs=tuple(payload.get("shared_artifact_refs") or ()),
            origin_episode_id=payload.get("origin_episode_id"),
            origin_task_id=payload.get("origin_task_id"),
            use_events=tuple(
                CapabilityUseEvidence(
                    **{
                        **dict(row),
                        "support_refs": tuple(row.get("support_refs") or ()),
                    }
                )
                for row in (payload.get("use_events") or ())
            ),
            enforcement_events=tuple(
                CapabilityEnforcementEvidence(
                    **{
                        **dict(row),
                        "support_refs": tuple(row.get("support_refs") or ()),
                    }
                )
                for row in (payload.get("enforcement_events") or ())
            ),
            transfer_events=tuple(
                CapabilityTransferEvidence(
                    **{
                        **dict(row),
                        "support_refs": tuple(row.get("support_refs") or ()),
                    }
                )
                for row in (payload.get("transfer_events") or ())
            ),
        )


__all__ = [
    "CapabilityEnforcementEvidence",
    "CapabilityEvidenceBundle",
    "CapabilityTransferEvidence",
    "CapabilityUseEvidence",
]
