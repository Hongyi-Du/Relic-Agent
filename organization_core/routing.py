"""Portable organization-member routing policies."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class RoutingCandidate:
    member_id: str
    authority: Mapping[str, float] = field(default_factory=dict)
    availability: float = 1.0

    def __post_init__(self) -> None:
        if not str(self.member_id or "").strip():
            raise ValueError("member_id is required")
        authority = {str(key): float(value) for key, value in self.authority.items()}
        if not all(math.isfinite(value) for value in authority.values()):
            raise ValueError("authority values must be finite")
        availability = float(self.availability)
        if not math.isfinite(availability) or availability < 0:
            raise ValueError("availability must be finite and non-negative")
        object.__setattr__(self, "authority", authority)
        object.__setattr__(self, "availability", availability)


@dataclass(frozen=True)
class RoutingRequest:
    request_id: str
    domain: str
    candidates: tuple[RoutingCandidate, ...]
    count: int = 1
    concentration: float = 1.0

    def __post_init__(self) -> None:
        for name in ("request_id", "domain"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        object.__setattr__(self, "candidates", tuple(self.candidates))
        if not all(isinstance(item, RoutingCandidate) for item in self.candidates):
            raise TypeError("candidates must contain RoutingCandidate values")
        ids = [item.member_id for item in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("routing candidate ids must be unique")
        count = int(self.count)
        concentration = float(self.concentration)
        if count < 0:
            raise ValueError("routing count must be non-negative")
        if not math.isfinite(concentration) or concentration < 0:
            raise ValueError("routing concentration must be finite and non-negative")
        object.__setattr__(self, "count", count)
        object.__setattr__(self, "concentration", concentration)


@dataclass(frozen=True)
class RoutingDecision:
    request_id: str
    assignee_ids: tuple[str, ...]
    scores: tuple[float, ...]

    def __post_init__(self) -> None:
        if not str(self.request_id or "").strip():
            raise ValueError("request_id is required")
        object.__setattr__(self, "assignee_ids", tuple(self.assignee_ids))
        object.__setattr__(self, "scores", tuple(float(item) for item in self.scores))


@runtime_checkable
class OrganizationRoutingEngine(Protocol):
    def route(self, request: RoutingRequest) -> RoutingDecision: ...


class AuthorityRoutingEngine:
    """Rank members by exp(concentration × authority) × availability."""

    def route(self, request: RoutingRequest) -> RoutingDecision:
        scores = tuple(
            max(
                1e-6,
                math.exp(
                    request.concentration
                    * float(candidate.authority.get(request.domain, 0.0))
                )
                * candidate.availability,
            )
            for candidate in request.candidates
        )
        order = sorted(range(len(request.candidates)), key=lambda index: -scores[index])
        assignees = tuple(
            request.candidates[index].member_id
            for index in order[: request.count]
        )
        return RoutingDecision(
            request_id=request.request_id,
            assignee_ids=assignees,
            scores=scores,
        )


__all__ = [
    "AuthorityRoutingEngine",
    "OrganizationRoutingEngine",
    "RoutingCandidate",
    "RoutingDecision",
    "RoutingRequest",
]
