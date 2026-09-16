"""Pinned provenance and behavioral checks for the HCI proposal source slice."""
from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from relic_agent.governance.models import Proposal, ToolSpec
from relic_agent.governance import GovernanceManager
from relic_agent.config import load_config
from relic_agent.source_b3.proposals.families import classify_family
from relic_agent.source_b3.proposals.manager import (
    MAX_ACTIVE_TOOLS_PER_FAMILY,
    ProposalManager,
    protocol_depth_ok,
    recurrence_behind,
)
from relic_agent.source_b3.proposals.provenance import (
    SOURCE_B3_COMMIT,
    SOURCE_B3_PROPOSAL_FILE_BLOBS,
    SOURCE_B3_PROPOSAL_IMPORT_REWRITES,
    SOURCE_B3_PROPOSAL_PORT_FILE_BLOBS,
    SOURCE_B3_REPOSITORY,
    source_b3_proposal_provenance,
)
from relic_agent.source_core import SourceCoreObservationBridge


ROOT = Path(__file__).resolve().parents[1]


def _blob_id(path: str) -> str:
    completed = subprocess.run(
        ["git", "hash-object", path],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


@pytest.mark.unit
def test_source_proposal_port_records_hci_commit_and_source_blobs() -> None:
    assert SOURCE_B3_REPOSITORY == "Hongyi-Du/SocioGenesis"
    assert SOURCE_B3_COMMIT == "dda36fb563375060ae8d8850300db01eb4695d29"
    assert SOURCE_B3_PROPOSAL_FILE_BLOBS == {
        "environments/org_env/proposals/objects.py": "33af715a6733f8b7fa262b3a8e7d1596e9c1b5ef",
        "environments/org_env/proposals/families.py": "7308f94e8006ffc69a91579aa5507d059ba2c6c4",
        "environments/org_env/proposals/manager.py": "0dc1656617d0b8b7c939b8fbccd93fae7dee6ef8",
        "environments/org_env/llm/semantic_dedup.py": "01b73e7adba73753ba15c1029f0255e9651d8842",
    }
    assert SOURCE_B3_PROPOSAL_IMPORT_REWRITES[
        "environments.org_env.proposals.objects"
    ] == "relic_agent.source_b3.proposals.objects"
    provenance = source_b3_proposal_provenance()
    assert provenance["source_file_blobs"] == SOURCE_B3_PROPOSAL_FILE_BLOBS
    assert "programbench" in " ".join(provenance["unavailable_source_dependencies"]).lower()
    # The local manager is a source port with documented capability seams, not
    # an unpinned reimplementation. Hashing it makes the audited shipped blob
    # observable alongside the immutable upstream blob ids above.
    for path, expected_blob in SOURCE_B3_PROPOSAL_PORT_FILE_BLOBS.items():
        assert _blob_id(path) == expected_blob


@pytest.mark.unit
def test_source_family_depth_and_deterministic_dedup_behaviors_are_preserved() -> None:
    evidence = Proposal(
        proposal_id="p1",
        proposal_type="protocol_proposal",
        title="Claim evidence gate",
        summary="verify claims have sources",
        source_episode_ids=["episode_a"],
        source_wish_ids=["wish_a", "wish_b"],
    )
    assert classify_family(evidence) == "evidence_governance"
    assert recurrence_behind(evidence) == 2
    assert protocol_depth_ok(evidence) is True

    shallow = Proposal(
        proposal_id="p2",
        proposal_type="protocol_proposal",
        title="one-off gate",
        summary="single occurrence",
        source_episode_id="episode_a",
        source_wish_ids=["wish_a"],
    )
    assert protocol_depth_ok(shallow) is False

    manager = ProposalManager()
    world = type("World", (), {"llm_client": None, "world_tick": 5, "events": [], "agents": {}})()
    manager.tools = {
        "tool_a": ToolSpec(
            tool_id="tool_a", name="smoke checker", family="debugging", status="active"
        ),
        "tool_b": ToolSpec(
            tool_id="tool_b", name="ci checker", family="debugging", status="active"
        ),
    }
    candidate = Proposal(
        proposal_id="p3",
        proposal_type="tool_proposal",
        title="bug localizer",
        summary="localize smoke failures",
        family="debugging",
    )
    assert MAX_ACTIVE_TOOLS_PER_FAMILY == 2
    assert manager._covering_tool(candidate, world).tool_id in {"tool_a", "tool_b"}


@pytest.mark.unit
def test_source_proposal_materialization_projects_the_existing_source_registry() -> None:
    bridge = SourceCoreObservationBridge.from_config(
        load_config(ROOT / "configs" / "minimal.yaml"),
        run_id="source-proposal-projection",
    )
    manager = GovernanceManager(
        agent_ids=("paul", "victor"),
        agent_roles={"paul": "founder", "victor": "cofounder"},
        known_actions=("claim_task",),
        min_approvers=2,
        review_ticks=3,
    )
    manager.bind_source_core_bridge(bridge)
    proposal = manager.submit(
        Proposal(
            proposal_id="proposal_projection",
            proposal_type="protocol_proposal",
            title="Review completion evidence",
            summary="require a review record",
            proposer_agent_id="paul",
            source_episode_ids=["episode_a", "episode_b"],
            required_actions=["claim_task"],
        ),
        tick=0,
    )
    manager.approve(proposal.proposal_id, "paul", tick=1)
    manager.approve(proposal.proposal_id, "victor", tick=1)

    assert manager.adopt(proposal.proposal_id, tick=3) == "proto_spec_1"
    status = bridge.status().as_dict()["source_b3_protocol_lifecycle"]
    assert status["adoption_records_projected"] == 1
    assert status["source_events_projected"] > 0
