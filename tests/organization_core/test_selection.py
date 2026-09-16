import math
import random

import pytest

from organization_core import (
    LinearUtilityModel,
    OrganizationSelectionEngine,
    ProfileUtilityModel,
    ProfileUtilityRequest,
    ProfileWeightRule,
    SelectionMode,
    SelectionOption,
    SelectionRequest,
    UtilitySelectionEngine,
)


def _legacy_softmax(utilities, rng, *, temperature=0.6, jitter=0.05):
    jittered = [value + rng.uniform(-jitter, jitter) for value in utilities]
    maximum = max(jittered)
    weights = [math.exp((value - maximum) / temperature) for value in jittered]
    draw = rng.random() * (sum(weights) or 1.0)
    accumulated, selected = 0.0, len(weights) - 1
    for index, weight in enumerate(weights):
        accumulated += weight
        if draw <= accumulated:
            selected = index
            break
    return selected


def test_linear_utility_model_keeps_domain_features_and_weights_external():
    model = LinearUtilityModel()
    assert model.score(
        {"progress": 0.8, "risk": 0.5, "domain_only": 4.0},
        {"progress": 0.6, "risk": -0.4},
    ) == pytest.approx(0.28)


def test_profile_utility_applies_ordered_rules_then_context_multiplier():
    result = ProfileUtilityModel().evaluate(
        ProfileUtilityRequest(
            feature_values={"review": 0.8, "cost": 2.0},
            base_weights={"review": 0.3, "cost": -0.1},
            profile_values={"quality": 0.5, "risk": 0.25},
            rules=(
                ProfileWeightRule("quality", "review", 0.6),
                ProfileWeightRule("risk", "review", 0.4),
                ProfileWeightRule("risk", "cost", -0.2),
            ),
            feature_multipliers={"cost": 2.0},
        )
    )

    assert result.effective_weights == pytest.approx(
        {"review": 0.7, "cost": -0.3}
    )
    assert result.utility == pytest.approx(-0.04)


def test_softmax_selection_is_byte_for_byte_rng_compatible():
    actual_rng = random.Random(31)
    legacy_rng = random.Random(31)
    utilities = [0.3, 0.5, 0.1]
    expected = _legacy_softmax(utilities, legacy_rng)

    engine = UtilitySelectionEngine()
    assert isinstance(engine, OrganizationSelectionEngine)
    decision = engine.select(
        SelectionRequest(
            request_id="selection-1",
            options=tuple(
                SelectionOption(str(index), utility)
                for index, utility in enumerate(utilities)
            ),
            temperature=0.6,
            jitter=0.05,
        ),
        rng=actual_rng,
    )

    assert decision is not None
    assert decision.selected_index == expected
    assert actual_rng.getstate() == legacy_rng.getstate()


def test_disallowed_options_consume_no_rng_and_cannot_be_selected():
    actual_rng = random.Random(47)
    legacy_rng = random.Random(47)
    allowed_utilities = [0.2, 0.7]
    expected_local = _legacy_softmax(allowed_utilities, legacy_rng)
    expected_global = (0, 2)[expected_local]

    decision = UtilitySelectionEngine().select(
        SelectionRequest(
            request_id="selection-2",
            options=(
                SelectionOption("first", 0.2),
                SelectionOption("blocked", 100.0, allowed=False),
                SelectionOption("third", 0.7),
            ),
            temperature=0.6,
            jitter=0.05,
        ),
        rng=actual_rng,
    )

    assert decision is not None
    assert decision.selected_index == expected_global
    assert decision.selectable_indices == (0, 2)
    assert decision.jittered_utilities[1] is None
    assert actual_rng.getstate() == legacy_rng.getstate()


def test_argmax_keeps_first_tie_after_jitter_is_disabled():
    decision = UtilitySelectionEngine().select(
        SelectionRequest(
            request_id="selection-3",
            options=(SelectionOption("a", 1.0), SelectionOption("b", 1.0)),
            mode=SelectionMode.ARGMAX,
            jitter=0.0,
        ),
        rng=random.Random(1),
    )
    assert decision is not None
    assert decision.selected_option_id == "a"


def test_empty_or_fully_blocked_pool_returns_no_decision_without_rng_use():
    rng = random.Random(5)
    before = rng.getstate()
    engine = UtilitySelectionEngine()
    assert engine.select(
        SelectionRequest(request_id="empty", options=()), rng=rng
    ) is None
    assert engine.select(
        SelectionRequest(
            request_id="blocked",
            options=(SelectionOption("a", 1.0, allowed=False),),
        ),
        rng=rng,
    ) is None
    assert rng.getstate() == before


def test_selection_request_rejects_ambiguous_or_non_finite_inputs():
    with pytest.raises(ValueError, match="unique"):
        SelectionRequest(
            request_id="duplicate",
            options=(SelectionOption("a", 1.0), SelectionOption("a", 2.0)),
        )
    with pytest.raises(ValueError, match="finite"):
        SelectionOption("bad", math.inf)
    with pytest.raises(ValueError, match="temperature"):
        SelectionRequest(request_id="cold", options=(), temperature=0.0)
