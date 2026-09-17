"""Pinned behavior and provenance checks for the narrow HCI B3 source port."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from organization_core import OrganizationEventType
from relic_agent.source_b3.protocol_lifecycle import (
    SourceB3ProtocolLifecycleAdapter,
    SourceB3ProtocolLifecycleUnavailableError,
)
from relic_agent.source_b3.provenance import (
    SOURCE_B3_COMMIT,
    SOURCE_B3_PROTOCOL_FILE_BLOBS,
    SOURCE_B3_PROTOCOL_IMPORT_REWRITES,
    SOURCE_B3_PROTOCOL_PORT_FILE_BLOBS,
    SOURCE_B3_REPOSITORY,
    stable_fingerprint,
)
from relic_agent.source_b3.protocols import PERSIST_MIN, USE_MIN
from relic_agent.source_core import (
    HciHostAdapterUnavailableError,
    SourceCoreObservationBridge,
)


ROOT = Path(__file__).resolve().parents[1]


def _archived_projection_config(*agent_ids: str) -> SimpleNamespace:
    return SimpleNamespace(
        organization_id="archived-projection",
        runtime=SimpleNamespace(seed=7),
        agents=tuple(
            SimpleNamespace(agent_id=agent_id, role="member", profile={}, skills={})
            for agent_id in agent_ids
        ),
    )


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
def test_source_b3_protocol_port_is_pinned_to_hci_blobs() -> None:
    assert SOURCE_B3_REPOSITORY == "Hongyi-Du/SocioGenesis"
    assert SOURCE_B3_COMMIT == "dda36fb563375060ae8d8850300db01eb4695d29"
    assert SOURCE_B3_PROTOCOL_FILE_BLOBS == {
        "environments/org_env/backend/protocol/objects.py": "0f3654f391953f6951a7fed7c6edca0561365afc",
        "environments/org_env/backend/protocol/registry.py": "315c5169fd2ee2e6f0ecad7ee0c07f85cb321670",
    }
    assert SOURCE_B3_PROTOCOL_IMPORT_REWRITES == {
        "environments.org_env.backend.protocol.objects": "relic_agent.source_b3.protocols.objects",
        "environments.org_env.experiments.provenance.stable_fingerprint": (
            "relic_agent.source_b3.provenance.stable_fingerprint"
        ),
    }
    for path, expected_blob in SOURCE_B3_PROTOCOL_PORT_FILE_BLOBS.items():
        assert _blob_id(path) == expected_blob


@pytest.mark.unit
def test_source_b3_fingerprint_dependency_slice_matches_hci_normalization() -> None:
    @dataclass(frozen=True)
    class Evidence:
        label: str
        values: set[int]

    assert stable_fingerprint(Evidence("protocol", {3, 1, 2})) == (
        "459434f98ec1d4b4483096872a4dbb18fa58b6b5ad8791c1a547ea9ab27ea711"
    )


def _emerged_protocol(adapter: SourceB3ProtocolLifecycleAdapter) -> str:
    """Direct port of the source registry's weak-emergence fixture."""

    protocol_id = adapter.propose(
        proposer_id="paul",
        protocol_type="review_before_merge",
        rule_summary="no merge without a review",
        scope="repo",
        tick=0,
    ).protocol_id
    for supporter in ("victor", "calvin", "sean"):
        adapter.support(supporter, protocol_id, tick=1)
    assert adapter.adopt(protocol_id, tick=10, approver_id="victor") is True
    for index in range(USE_MIN + 2):
        adapter.use(agent_id="sean", protocol_id=protocol_id, tick=20 + index * 5)
    adapter.use(agent_id="calvin", protocol_id=protocol_id, tick=20 + PERSIST_MIN + 40)
    return protocol_id


@pytest.mark.unit
def test_source_b3_registry_preserves_hci_emergence_behavior() -> None:
    adapter = SourceB3ProtocolLifecycleAdapter()
    protocol_id = _emerged_protocol(adapter)

    adapter.tick_adoptions(tick=300)

    evidence = adapter.emergence_evidence(protocol_id)
    assert evidence["repeated_use"] is True
    assert evidence["persistent"] is True
    assert adapter.protocols[protocol_id].emergence_level == "weak"
    assert [event.event_id for event in adapter.events[:3]] == ["pev_1", "pev_2", "pev_3"]


@pytest.mark.unit
def test_source_b3_lifecycle_rejects_non_source_review_latency() -> None:
    with pytest.raises(
        SourceB3ProtocolLifecycleUnavailableError,
        match="review_ticks_unsupported",
    ):
        SourceB3ProtocolLifecycleAdapter(review_ticks=4)


@pytest.mark.unit
def test_source_b3_lifecycle_projects_adoption_to_the_core_host_boundary() -> None:
    bridge = SourceCoreObservationBridge.from_config(
        _archived_projection_config("builder", "reviewer"),
        run_id="source-b3-protocol-boundary",
    )
    adapter = SourceB3ProtocolLifecycleAdapter()
    adapter.bind_source_core_bridge(bridge)
    protocol = adapter.propose(
        proposer_id="builder",
        protocol_type="review_before_merge",
        rule_summary="review before merge",
        scope="repo",
        tick=1,
    )
    adapter.support("reviewer", protocol.protocol_id, tick=2)
    adapter.tick_adoptions(tick=4)

    source_event_types = [event.event_type for event in bridge.module.events.events]
    assert source_event_types.count(OrganizationEventType.DOMAIN_EVENT_RECORDED.value) == 3
    assert source_event_types.count(OrganizationEventType.PROTOCOL_ADOPTED.value) == 1
    formation = bridge.module.formation_state()
    assert formation is not None
    assert [item.protocol_id for item in formation.protocols] == [protocol.protocol_id]
    assert bridge.status().as_dict()["source_b3_protocol_lifecycle"][
        "adoption_records_projected"
    ] == 1
    with pytest.raises(HciHostAdapterUnavailableError, match="unavailable"):
        bridge.require_active_hci_host_adapter()
