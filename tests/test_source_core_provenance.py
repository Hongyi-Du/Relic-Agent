"""Provenance and fail-closed tests for the source-core extraction boundary."""

from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from organization_core import OrganizationEventType
from relic_agent.core import (
    SOURCE_CORE_COMMIT,
    SOURCE_CORE_FILE_BLOBS,
    SOURCE_CORE_SOURCE_PATH,
    SOURCE_CORE_SOURCE_REPOSITORY,
)
from relic_agent.events import Event
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

SOURCE_CORE_TEST_FILE_BLOBS = {
    "tests/organization_core/test_approval.py": "e6ea8a9dd2d3b88b7917d733d3053e2b2a09ea8d",
    "tests/organization_core/test_conformance.py": "dbca2c14f06d99685b37c97af1277fb38252c64b",
    "tests/organization_core/test_contracts.py": "4973b7b6c1d5f486da11d231bb06da5b63d6176c",
    "tests/organization_core/test_decision.py": "333ea6e543aa5336948d085429c88b460717cabf",
    "tests/organization_core/test_dependency_boundary.py": "b9b0677aa869ee2646433fe30711a656ca0f64b6",
    "tests/organization_core/test_evidence.py": "bc5845f05c8bb4b1fb2962e5d756caf37de8fee2",
    "tests/organization_core/test_formation.py": "a8c21a467c0ac7946af404a48dd166e09bf7b4a9",
    "tests/organization_core/test_gates.py": "c091768ca60684fd932544789d8b4ae931bc85b3",
    "tests/organization_core/test_host.py": "52db2e81f0daf43adefc04bdab6473e638c967be",
    "tests/organization_core/test_module.py": "122c777935a3a5924268e3300d4b249f5c4c58bf",
    "tests/organization_core/test_proposals.py": "7d3494fecc324e529655578e492af6ab4ffc2554",
    "tests/organization_core/test_repair.py": "fd2906d1a311104aba64394f34714a5d9f342572",
    "tests/organization_core/test_routing.py": "5aab3b975e833da5c1971212858f249ba7283101",
    "tests/organization_core/test_selection.py": "fb88d9b1ef5b7cdf720b26df2e59da80347afa8d",
    "tests/organization_core/test_state.py": "ece1bfb7f980a141c28f16e9eb9cabfd2a92fdf9",
    "tests/organization_core/test_synthesis.py": "da0a336b868b839772b46b68d73b35a48b5e2e00",
}


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
def test_vendored_source_core_and_direct_tests_are_byte_exact() -> None:
    assert SOURCE_CORE_SOURCE_REPOSITORY == "Hongyi-Du/SocioGenesis"
    assert SOURCE_CORE_COMMIT == "041ddee1aa109a9b65dfdad7bdb8e258ad0a293e"
    assert SOURCE_CORE_SOURCE_PATH == "organization_core"
    assert set(SOURCE_CORE_FILE_BLOBS) == {
        str(path.relative_to(ROOT))
        for path in sorted((ROOT / "organization_core").glob("*.py"))
    }
    for path, expected_blob in {
        **SOURCE_CORE_FILE_BLOBS,
        **SOURCE_CORE_TEST_FILE_BLOBS,
    }.items():
        assert _blob_id(path) == expected_blob


@pytest.mark.unit
def test_bridge_observes_only_source_core_envelopes_and_fails_closed_for_hci() -> None:
    bridge = SourceCoreObservationBridge.from_config(
        _archived_projection_config("builder"),
        run_id="source-core-test",
    )
    legacy = Event(
        event_id="event_000001",
        tick=1,
        event_type="task_started",
        actor_id="builder",
        object_ids=("task_1",),
        payload={"summary": "started"},
    )

    assert bridge.publish_legacy_event(legacy) is True
    assert bridge.publish_legacy_event(legacy) is False
    with pytest.raises(ValueError, match="reused with different content"):
        bridge.publish_legacy_event(
            Event(
                event_id="event_000001",
                tick=1,
                event_type="task_started",
                actor_id="builder",
                object_ids=("task_1",),
                payload={"summary": "altered"},
            )
        )
    assert bridge.complete_tick(1) is True
    snapshot = bridge.module.snapshot()
    assert snapshot.event_count == 3
    assert (
        bridge.module.events.events[1].event_type
        == OrganizationEventType.DOMAIN_EVENT_RECORDED.value
    )
    assert bridge.status().as_dict()["active_hci_host_adapter"] == "unavailable_fail_closed"

    with pytest.raises(HciHostAdapterUnavailableError, match="unavailable"):
        bridge.require_active_hci_host_adapter()
