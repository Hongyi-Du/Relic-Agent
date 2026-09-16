"""Assertions for the bounded source B3 protocol lifecycle integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from relic_agent.config import load_config
from relic_agent.governance import GovernanceManager
from relic_agent.reflection.models import Wish
from relic_agent.runtime import OrganizationRuntime


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_default_run_records_source_b3_protocol_lifecycle_status(tmp_path: Path) -> None:
    result = OrganizationRuntime(load_config(ROOT / "configs" / "minimal.yaml")).run(
        output_root=tmp_path,
        run_id="source-core-boundary",
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["runtime"]["authority"] == "legacy_compatibility_runtime"
    source_core = manifest["source_core"]
    assert source_core["source_repository"] == "Hongyi-Du/SocioGenesis"
    assert source_core["source_commit"] == "041ddee1aa109a9b65dfdad7bdb8e258ad0a293e"
    assert source_core["mode"] == "source_b3_protocol_lifecycle_plus_shadow_observation"
    assert source_core["execution_authority"] == "legacy_compatibility_runtime"
    assert source_core["state_materialization"] == "bootstrap_plus_source_b3_protocol_adoption"
    assert source_core["active_hci_host_adapter"] == "unavailable_fail_closed"
    source_b3 = source_core["source_b3_protocol_lifecycle"]
    assert source_b3["source_commit"] == "dda36fb563375060ae8d8850300db01eb4695d29"
    assert source_b3["activation"] == "active_source_hci_protocol_registry"
    assert source_b3["source_core_projection"] == "bound_active"
    assert source_b3["adoption_records_projected"] == 1
    assert source_b3["source_events_projected"] > 0
    assert "source_orgworld_action_execution" in source_b3["unavailable_fail_closed"]
    assert source_core["observed_event_count"] == (
        result.event_count
        + result.ticks
        + 1
        + source_b3["source_events_projected"]
        + source_b3["adoption_records_projected"]
    )
    assert len(source_core["bootstrap_state_sha256"]) == 64


@pytest.mark.unit
def test_compatibility_governance_uses_source_core_approval_policy() -> None:
    manager = GovernanceManager(agent_ids=("a", "b"), min_approvers=2, review_ticks=3)
    wish = Wish(
        wish_id="wish-1",
        agent_id="a",
        source_reflection_id="reflection-1",
        source_reflection_ids=["reflection-1"],
        source_episode_id="episode-1",
        related_episode_ids=["episode-1"],
        source_event_ids=["event-1"],
        wish_type="protocol_need",
        fingerprint="stable",
        interpreted_need="review",
        target_problem="missing review",
        suggested_improvement="review",
        missing_support_type="protocol",
        urgency=0.8,
        expected_benefit="quality",
        risk_if_unaddressed="risk",
        created_at_tick=1,
        updated_at_tick=1,
    )
    proposal = manager.propose_from_wish(wish, tick=1)

    pending = manager.approval_decision(proposal, tick=3)
    assert pending.ready is False
    assert "review_latency_pending" in pending.reason_codes
    for agent_id in proposal.approval_required_from:
        manager.approve(proposal.proposal_id, agent_id, tick=4)
    assert manager.approval_decision(proposal, tick=4).ready is True
