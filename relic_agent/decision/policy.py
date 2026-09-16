"""Extracted B3 profile-conditioned structured decision policy.

The scoring form and principal weights come from the locked B3 runtime. This
module removes experiment-arm switches and scenario-specific bonuses while retaining the
auditable rule:

    utility(candidate) = sum(weight(agent, feature) * feature_value)

Mock execution uses a seeded bounded softmax and records every candidate score.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

from relic_agent.organization.models import AgentState

BASE_WEIGHTS = {
    "progress_gain": 0.55,
    "deadline_urgency": 0.30,
    "task_priority": 0.30,
    "blocker_resolution": 0.35,
    "dependency_unlock": 0.20,
    "skill_match": 0.40,
    "role_affinity": 0.30,
    "learning_gain": 0.10,
    "failure_risk_from_low_skill": -0.30,
    "coordination_gain": 0.30,
    "clarity_gain": 0.20,
    "trust_gain": 0.25,
    "conflict_risk": -0.25,
    "attention_cost": -0.30,
    "fatigue_delta": -0.20,
    "stress_delta": -0.20,
    "protocol_creation_potential": 0.20,
    "protocol_use_potential": 0.20,
    "protocol_violation_risk": -0.40,
    "norm_enforcement_gain": 0.20,
    "institutional_memory_gain": 0.20,
    "proposal_endorsement": 0.45,
    "proposal_skepticism": 0.45,
}

PROFILE_COEFFS = (
    ("process_commitment", "protocol_use_potential", 0.5),
    ("process_commitment", "proposal_endorsement", 0.3),
    ("process_resistance", "protocol_use_potential", -0.4),
    ("process_resistance", "protocol_violation_risk", 0.5),
    ("process_resistance", "proposal_endorsement", -0.3),
    ("protocol_design", "protocol_creation_potential", 0.5),
    ("institutional_memory", "institutional_memory_gain", 0.5),
    ("conformity", "protocol_violation_risk", -0.4),
    ("conformity", "protocol_use_potential", 0.4),
    ("conformity", "proposal_endorsement", 0.5),
    ("group_loyalty", "proposal_endorsement", 0.35),
    ("long_termism", "proposal_endorsement", 0.3),
    ("long_termism", "institutional_memory_gain", 0.3),
    ("risk_aversion", "proposal_skepticism", 0.55),
    ("quality_bar", "proposal_skepticism", 0.4),
    ("curiosity", "learning_gain", 0.4),
    ("social_tact", "coordination_gain", 0.4),
    ("ownership_drive", "dependency_unlock", 0.30),
)

SOFTMAX_TEMPERATURE = 0.6


@dataclass(frozen=True)
class Candidate:
    action_id: str
    object_id: str = ""
    features: dict[str, float] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionTrace:
    tick: int
    agent_id: str
    chosen_action_id: str | None
    chosen_object_id: str | None
    candidates: tuple[dict[str, Any], ...]
    policy: str = "b3_profile_conditioned_bounded_softmax"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "agent_id": self.agent_id,
            "chosen_action_id": self.chosen_action_id,
            "chosen_object_id": self.chosen_object_id,
            "candidates": [dict(candidate) for candidate in self.candidates],
            "policy": self.policy,
        }


class OrganizationPolicy:
    def __init__(self, *, profile_conditioning: bool = True) -> None:
        self.profile_conditioning = profile_conditioning

    @staticmethod
    def _lookup(agent: AgentState, name: str) -> float:
        if name in agent.profile:
            return float(agent.profile[name])
        return float(agent.skills.get(name, 0.0))

    def weight(self, agent: AgentState, feature: str) -> float:
        weight = BASE_WEIGHTS.get(feature, 0.0)
        if self.profile_conditioning:
            for trait, target_feature, coefficient in PROFILE_COEFFS:
                if target_feature == feature:
                    weight += self._lookup(agent, trait) * coefficient
        if feature == "fatigue_delta":
            weight *= 1.0 + float(agent.vitals.get("fatigue", 0.0))
        if feature == "stress_delta":
            weight *= 1.0 + float(agent.vitals.get("stress", 0.0))
        return weight

    def score(self, agent: AgentState, candidate: Candidate) -> float:
        return sum(
            self.weight(agent, feature) * float(value)
            for feature, value in candidate.features.items()
        )

    def select(
        self,
        *,
        tick: int,
        agent: AgentState,
        candidates: list[Candidate],
        rng: random.Random,
    ) -> tuple[Candidate | None, DecisionTrace]:
        if not candidates:
            return None, DecisionTrace(tick, agent.agent_id, None, None, ())
        scored: list[tuple[Candidate, float, float]] = []
        for candidate in candidates:
            utility = self.score(agent, candidate)
            jittered = utility + rng.uniform(-0.05, 0.05)
            scored.append((candidate, utility, jittered))
        maximum = max(row[2] for row in scored)
        exponentials = [math.exp((row[2] - maximum) / SOFTMAX_TEMPERATURE) for row in scored]
        threshold = rng.random() * (sum(exponentials) or 1.0)
        cumulative = 0.0
        chosen_index = len(scored) - 1
        for index, value in enumerate(exponentials):
            cumulative += value
            if threshold <= cumulative:
                chosen_index = index
                break
        chosen = scored[chosen_index][0]
        trace = DecisionTrace(
            tick=tick,
            agent_id=agent.agent_id,
            chosen_action_id=chosen.action_id,
            chosen_object_id=chosen.object_id or None,
            candidates=tuple(
                {
                    "action_id": candidate.action_id,
                    "object_id": candidate.object_id or None,
                    "features": dict(candidate.features),
                    "utility": round(utility, 8),
                    "selected": index == chosen_index,
                }
                for index, (candidate, utility, _) in enumerate(scored)
            ),
        )
        return chosen, trace
