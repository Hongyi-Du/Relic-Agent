"""Generic-only SDL customization; the hash-pinned source policy stays untouched."""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from environments.org_env.runtime_adapter.policy import BASE_WEIGHTS, PROFILE_COEFFS, OrgPolicy


class _CandidateFeatures:
    def __init__(self, candidate: Any, features: Any):
        self.candidate = candidate
        self.features = features

    def to_dict(self) -> dict[str, float]:
        return (self.features.to_dict() if hasattr(self.features, "to_dict")
                else dict(self.features))


class ConfigurableOrgPolicy(OrgPolicy):
    """Score generic candidates with app-defined weights or a trusted scorer.

    The source policy owns canonical experiment behavior; this class is wired
    only by ``build_generic_world`` when an SDL override is configured.
    """

    def __init__(self, source_policy: OrgPolicy, spec: dict, scorer=None):
        super().__init__(mode=source_policy.mode,
                         use_profile_conditioning=source_policy.use_profile_conditioning)
        self.base_weights = {**BASE_WEIGHTS, **spec["base_weights"]}
        coefficients = {(trait, feature): value for trait, feature, value in PROFILE_COEFFS}
        coefficients.update({(row["trait"], row["feature"]): row["coefficient"]
                             for row in spec["profile_coefficients"]})
        self.profile_coeffs = [(trait, feature, value)
                               for (trait, feature), value in coefficients.items()]
        self.temperature = spec["temperature"]
        self.jitter = spec["jitter"]
        self.scorer = scorer
        self.scorer_config = deepcopy((spec.get("scorer") or {}).get("config", {}))

    def weight(self, agent, feature: str, world: Any) -> float:
        weight = self.base_weights.get(feature, 0.0)
        if self.use_profile_conditioning:
            for trait, target, coefficient in self.profile_coeffs:
                if target == feature:
                    weight += self._lookup(agent, trait) * coefficient
        if feature in ("api_cost", "compute_cost", "budget_cost", "runway_risk_delta"):
            weight *= 1.0 + world.budget_system.budget.budget_pressure
        if feature == "overtime_penalty":
            weight *= 1.0 - 0.7 * float(agent.work_rhythm.get("late_night_bias", 0.3))
        if feature == "weekend_penalty":
            weight *= 1.0 - 0.7 * float(agent.work_rhythm.get("weekend_work_tendency", 0.3))
        if feature in ("fatigue_delta", "burnout_risk_delta"):
            weight *= 1.0 + agent.vitals.get("fatigue", 0.0)
        if feature == "stress_delta":
            weight *= 1.0 + agent.vitals.get("stress", 0.0)
        return weight

    def score(self, agent, features, world, *, candidate=None) -> float:
        values = features.to_dict() if hasattr(features, "to_dict") else dict(features)
        if self.scorer is None:
            return sum(self.weight(agent, name, world) * value
                       for name, value in values.items())
        candidate = candidate or getattr(features, "candidate", None)
        context = {
            "agent_id": agent.id,
            "role": agent.role,
            "profile": deepcopy(agent.profile),
            "skills": deepcopy(agent.skills),
            "tick": world.world_tick,
            "action": getattr(candidate, "action_type", None),
            "parameters": deepcopy(getattr(candidate, "parameters", None) or {}),
            "config": deepcopy(self.scorer_config),
        }
        value = self.scorer(deepcopy(values), context)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("SDL scorer must return a finite number")
        return float(value)

    def _sampling_choice(self, utilities: list[float], allowed: list[int], rng) -> int:
        jittered = {i: utilities[i] + rng.uniform(-self.jitter, self.jitter) for i in allowed}
        if self.mode == "argmax":
            return max(allowed, key=lambda i: jittered[i])
        maximum = max(jittered.values())
        exponentials = {i: math.exp((jittered[i] - maximum) / self.temperature) for i in allowed}
        threshold = rng.random() * (sum(exponentials.values()) or 1.0)
        accumulated = 0.0
        chosen = allowed[-1]
        for index in allowed:
            accumulated += exponentials[index]
            if threshold <= accumulated:
                chosen = index
                break
        return chosen

    def select(self, agent, perception, scored_candidates, world, *, rng):
        if not scored_candidates:
            return None
        scored = [(candidate, _CandidateFeatures(candidate, features))
                  for candidate, features in scored_candidates]
        tick = int(getattr(world, "world_tick", 0))
        profile_decisions = self._programbench_candidate_decisions(world, scored)
        raw = [self.score(agent, features, world) for _, features in scored]
        if self.use_profile_conditioning:
            authority = [self._authority_bonus(agent, c, world) for c, _ in scored]
            reputation = [self._reputation_bonus(agent, c, world) for c, _ in scored]
            coding = [self._coding_bonus(agent, c, world) for c, _ in scored]
        else:
            authority = reputation = coding = [0.0] * len(scored)
        base = [r + a + rep + code for r, a, rep, code
                in zip(raw, authority, reputation, coding)]
        penalties = [self._attractor_penalties(c, agent, world, tick) for c, _ in scored]
        utilities = [value + sum(penalty.values())
                     for value, penalty in zip(base, penalties)]
        if profile_decisions is not None:
            utilities = [value + decision.bonus
                         for value, decision in zip(utilities, profile_decisions)]
            allowed = [i for i, decision in enumerate(profile_decisions) if decision.allowed]
        else:
            allowed = list(range(len(scored)))
        chosen = self._sampling_choice(utilities, allowed, rng) if allowed else None
        decision = self._decision_for(world, agent.id, tick)
        self._record_trace(agent, world, tick, scored, base, penalties, utilities, chosen,
                           abonus=authority, rbonus=reputation,
                           decision_id=getattr(decision, "decision_id", None),
                           source=getattr(decision, "decision_source", "rule"),
                           profile_decisions=profile_decisions)
        return scored[chosen][0] if chosen is not None else None

    def trace_choice(self, agent, world, tick, scored_candidates, chosen_candidate, *,
                     decision_id=None, source="llm") -> None:
        scored = [(candidate, _CandidateFeatures(candidate, features))
                  for candidate, features in scored_candidates]
        super().trace_choice(agent, world, tick, scored, chosen_candidate,
                             decision_id=decision_id, source=source)

    def _record_trace(self, agent, world, tick, scored, base, penalties, utilities, chosen,
                      *, abonus=None, rbonus=None, decision_id=None, source="rule",
                      profile_decisions=None) -> None:
        # The source counterfactual assumes its frozen linear weights and TAU.
        # A customized scorer/temperature must not publish that metric.
        sink = getattr(world, "policy_trace", None)
        if sink is None:
            return
        order = sorted(range(len(utilities)), key=lambda i: -utilities[i])[:4]
        rows = []
        for index in order:
            candidate = scored[index][0]
            authority = round(abonus[index], 3) if abonus else 0.0
            reputation = round(rbonus[index], 3) if rbonus else 0.0
            row = {
                "action": candidate.action_type,
                "target": (candidate.parameters or {}).get("artifact_id")
                          or (candidate.parameters or {}).get("task_id"),
                "raw_score": round(base[index] - authority - reputation, 3),
                "authority_bonus": authority,
                "reputation_bonus": reputation,
                "final_score": round(utilities[index], 3),
            }
            row.update({key: round(value, 3) for key, value in penalties[index].items()})
            if profile_decisions is not None:
                decision = profile_decisions[index]
                row.update({"profile_bonus": round(decision.bonus, 3),
                            "profile_reason": decision.reason,
                            "profile_allowed": decision.allowed})
            rows.append(row)
        sink.append({
            "agent_id": agent.id,
            "tick": tick,
            "decision_id": decision_id,
            "decision_source": source,
            "action_selection_mode": getattr(world, "action_selection_mode", "profile_policy"),
            "profile_conditioning_enabled": self.use_profile_conditioning,
            "candidate_actions": rows,
            "masked_actions": getattr(world, "_attractor_masked", {}).get(agent.id, []),
            "chosen_action": scored[chosen][0].action_type if chosen is not None else None,
        })
        if len(sink) > 400:
            del sink[:len(sink) - 400]
