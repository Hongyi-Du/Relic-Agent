"""Conformance guards for the source-native B3 host."""

from __future__ import annotations

import pytest

from environments.org_env.backend.simulation import OrgWorld
from environments.org_env.config.scenarios import default_scenario
from relic_agent.source_host import (
    STRUCTURAL_GOLDENS,
    assert_no_forbidden_loaded_modules,
    structural_conformance,
)


@pytest.mark.integration
@pytest.mark.parametrize("seed", sorted(STRUCTURAL_GOLDENS))
def test_source_native_host_preserves_b3_and_hci_336_tick_structure_hashes(seed: int) -> None:
    world = OrgWorld(default_scenario(seed=seed)).build()
    for _ in range(336):
        world.step()

    assert structural_conformance(world) == STRUCTURAL_GOLDENS[seed]
    assert assert_no_forbidden_loaded_modules() == {
        "forbidden_module_prefixes": [
            "society_core",
            "environments.org_env.programbench",
            "environments.org_env.external_society",
            "environments.org_env.product.substrates.final_evaluation",
            "environments.nature_env",
        ],
        "forbidden_modules_loaded": [],
        "status": "passed",
    }
