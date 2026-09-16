"""Harness-agnostic utility selection for organizational decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Mapping, Protocol, runtime_checkable


class SelectionMode(str, Enum):
    ARGMAX = "argmax"
    SOFTMAX = "softmax"


@dataclass(frozen=True)
class SelectionOption:
    option_id: str
    utility: float
    allowed: bool = True

    def __post_init__(self) -> None:
        if not str(self.option_id or "").strip():
            raise ValueError("option_id is required")
        if not math.isfinite(float(self.utility)):
            raise ValueError("option utility must be finite")
        object.__setattr__(self, "utility", float(self.utility))
        object.__setattr__(self, "allowed", bool(self.allowed))


@dataclass(frozen=True)
class SelectionRequest:
    request_id: str
    options: tuple[SelectionOption, ...]
    mode: SelectionMode = SelectionMode.SOFTMAX
    temperature: float = 1.0
    jitter: float = 0.0

    def __post_init__(self) -> None:
        if not str(self.request_id or "").strip():
            raise ValueError("request_id is required")
        object.__setattr__(self, "options", tuple(self.options))
        if not all(isinstance(option, SelectionOption) for option in self.options):
            raise TypeError("options must contain SelectionOption values")
        object.__setattr__(self, "mode", SelectionMode(self.mode))
        if not math.isfinite(float(self.temperature)) or self.temperature <= 0:
            raise ValueError("temperature must be finite and positive")
        if not math.isfinite(float(self.jitter)) or self.jitter < 0:
            raise ValueError("jitter must be finite and non-negative")
        option_ids = [option.option_id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("selection option ids must be unique")
        object.__setattr__(self, "temperature", float(self.temperature))
        object.__setattr__(self, "jitter", float(self.jitter))


@dataclass(frozen=True)
class SelectionDecision:
    request_id: str
    selected_option_id: str
    selected_index: int
    selectable_indices: tuple[int, ...]
    jittered_utilities: tuple[float | None, ...]

    def __post_init__(self) -> None:
        if not self.request_id or not self.selected_option_id:
            raise ValueError("selection decision ids are required")
        object.__setattr__(self, "selectable_indices", tuple(self.selectable_indices))
        object.__setattr__(self, "jittered_utilities", tuple(self.jittered_utilities))
        if self.selected_index not in self.selectable_indices:
            raise ValueError("selected index must be selectable")
        if self.selected_index < 0 or self.selected_index >= len(self.jittered_utilities):
            raise ValueError("selected index is outside jittered utilities")
        if self.jittered_utilities[self.selected_index] is None:
            raise ValueError("selected option must have a jittered utility")


@runtime_checkable
class RandomSource(Protocol):
    def uniform(self, a: float, b: float) -> float: ...

    def random(self) -> float: ...


@runtime_checkable
class OrganizationSelectionEngine(Protocol):
    def select(
        self,
        request: SelectionRequest,
        *,
        rng: RandomSource,
    ) -> SelectionDecision | None: ...


class UtilitySelectionEngine:
    """Select an allowed option while preserving caller-owned RNG semantics."""

    def select(
        self,
        request: SelectionRequest,
        *,
        rng: RandomSource,
    ) -> SelectionDecision | None:
        selectable = tuple(
            index for index, option in enumerate(request.options) if option.allowed
        )
        if not selectable:
            return None

        jittered: list[float | None] = [None] * len(request.options)
        for index in selectable:
            utility = request.options[index].utility
            jittered[index] = utility + rng.uniform(-request.jitter, request.jitter)

        if request.mode is SelectionMode.ARGMAX:
            selected = max(
                selectable,
                key=lambda index: _present(jittered[index]),
            )
        else:
            maximum = max(_present(jittered[index]) for index in selectable)
            weights = {
                index: math.exp(
                    (_present(jittered[index]) - maximum) / request.temperature
                )
                for index in selectable
            }
            total = sum(weights.values()) or 1.0
            draw = rng.random() * total
            accumulated = 0.0
            selected = selectable[-1]
            for index in selectable:
                accumulated += weights[index]
                if draw <= accumulated:
                    selected = index
                    break

        return SelectionDecision(
            request_id=request.request_id,
            selected_option_id=request.options[selected].option_id,
            selected_index=selected,
            selectable_indices=selectable,
            jittered_utilities=tuple(jittered),
        )


class LinearUtilityModel:
    """Compute Σ feature_value × member/context-specific feature_weight."""

    def score(
        self,
        feature_values: Mapping[str, float],
        feature_weights: Mapping[str, float],
    ) -> float:
        return sum(
            float(feature_weights.get(feature, 0.0)) * float(value)
            for feature, value in feature_values.items()
        )


@dataclass(frozen=True)
class ProfileWeightRule:
    profile_key: str
    feature_key: str
    coefficient: float

    def __post_init__(self) -> None:
        for name in ("profile_key", "feature_key"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        coefficient = float(self.coefficient)
        if not math.isfinite(coefficient):
            raise ValueError("profile coefficient must be finite")
        object.__setattr__(self, "coefficient", coefficient)


@dataclass(frozen=True)
class ProfileUtilityRequest:
    feature_values: Mapping[str, float]
    base_weights: Mapping[str, float]
    profile_values: Mapping[str, float] = field(default_factory=dict)
    rules: tuple[ProfileWeightRule, ...] = ()
    feature_multipliers: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("feature_values", "base_weights", "profile_values"):
            values = {str(key): float(value) for key, value in getattr(self, name).items()}
            if not all(math.isfinite(value) for value in values.values()):
                raise ValueError(f"{name} values must be finite")
            object.__setattr__(self, name, values)
        multipliers = {
            str(key): float(value)
            for key, value in self.feature_multipliers.items()
        }
        if not all(math.isfinite(value) for value in multipliers.values()):
            raise ValueError("feature_multipliers values must be finite")
        object.__setattr__(self, "feature_multipliers", multipliers)
        object.__setattr__(self, "rules", tuple(self.rules))
        if not all(isinstance(rule, ProfileWeightRule) for rule in self.rules):
            raise TypeError("rules must contain ProfileWeightRule values")


@dataclass(frozen=True)
class ProfileUtilityEvaluation:
    utility: float
    effective_weights: Mapping[str, float]


class ProfileUtilityModel:
    """Apply ordered profile rules and host-provided context multipliers."""

    def evaluate(self, request: ProfileUtilityRequest) -> ProfileUtilityEvaluation:
        weights = {}
        for feature in request.feature_values:
            weight = float(request.base_weights.get(feature, 0.0))
            for rule in request.rules:
                if rule.feature_key == feature:
                    weight += (
                        float(request.profile_values.get(rule.profile_key, 0.0))
                        * rule.coefficient
                    )
            weight *= float(request.feature_multipliers.get(feature, 1.0))
            weights[feature] = weight
        return ProfileUtilityEvaluation(
            utility=LinearUtilityModel().score(request.feature_values, weights),
            effective_weights=weights,
        )


def _present(value: float | None) -> float:
    if value is None:
        raise ValueError("selectable option is missing a jittered utility")
    return value


__all__ = [
    "LinearUtilityModel",
    "OrganizationSelectionEngine",
    "ProfileUtilityEvaluation",
    "ProfileUtilityModel",
    "ProfileUtilityRequest",
    "ProfileWeightRule",
    "RandomSource",
    "SelectionDecision",
    "SelectionMode",
    "SelectionOption",
    "SelectionRequest",
    "UtilitySelectionEngine",
]
