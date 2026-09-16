"""Assertions that the release-compatible runner does not overclaim authority."""

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
def test_compatibility_run_records_source_core_observation_only_status(tmp_path: Path) -> None:
    result = OrganizationRuntime(load_config(ROOT / "configs" / "minimal.yaml")).run(
        output_root=tmp_path,
        run_id="source-core-boundary",
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["runtime"]["authority"] == "legacy_compatibility_runtime"
    assert manifest["source_core"] == {
        "schema_version": "relic-agent-source-core-status-v1",
        "source_repository": "Hongyi-Du/SocioGenesis",
        "source_commit": "041ddee1aa109a9b65dfdad7bdb8e258ad0a293e",
        "source_path": "organization_core",
        "file_count": 17,
        "vendoring": "byte_exact_git_blobs",
        "mode": "shadow_observation_only",
        "execution_authority": "legacy_compatibility_runtime",
        "state_materialization": "bootstrap_only",
        "active_hci_host_adapter": "unavailable_fail_closed",
        "observed_event_count": result.event_count + result.ticks + 1,
        "bootstrap_state_sha256": manifest["source_core"]["bootstrap_state_sha256"],
    }
    assert len(manifest["source_core"]["bootstrap_state_sha256"]) == 64


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
