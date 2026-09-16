"""Pinned behavior and provenance checks for the HCI source episode closure."""

from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from organization_core import OrganizationEventType
from relic_agent.config import load_config
from relic_agent.source_b3.episodes import (
    SourceB3EpisodeHostUnavailableError,
    SourceB3EpisodeLifecycleAdapter,
)
from relic_agent.source_b3.episodes.provenance import (
    SOURCE_B3_COMMIT,
    SOURCE_B3_EPISODE_FILE_BLOBS,
    SOURCE_B3_EPISODE_IMPORT_REWRITES,
    SOURCE_B3_EPISODE_PORT_FILE_BLOBS,
    SOURCE_B3_REPOSITORY,
    source_b3_episode_provenance,
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


def _world(*, tick: int = 0, build_error: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        world_tick=tick,
        agents={
            "paul": SimpleNamespace(name="Paul"),
            "scarlett": SimpleNamespace(name="Scarlett"),
            "calvin": SimpleNamespace(name="Calvin"),
        },
        product_artifacts={
            "artifact_source_tracker": SimpleNamespace(
                artifact_id="artifact_source_tracker",
                linked_file_path="tools/source_tracker.py",
                artifact_type="file",
                status="open",
            )
        },
        _build_error=build_error,
    )


@pytest.mark.unit
def test_source_episode_port_is_pinned_to_hci_blobs() -> None:
    assert SOURCE_B3_REPOSITORY == "Hongyi-Du/SocioGenesis"
    assert SOURCE_B3_COMMIT == "dda36fb563375060ae8d8850300db01eb4695d29"
    assert SOURCE_B3_EPISODE_FILE_BLOBS == {
        "environments/org_env/episodes/episode.py": "85e3946e73d9d36a0ceda0320747e78b290af73e",
        "environments/org_env/episodes/episode_manager.py": "ab71b94447359110f6ed98108b999f19931fed06",
    }
    assert SOURCE_B3_EPISODE_IMPORT_REWRITES == {
        "environments.org_env.episodes.episode": "relic_agent.source_b3.episodes.episode",
    }
    for path, expected_blob in SOURCE_B3_EPISODE_PORT_FILE_BLOBS.items():
        assert _blob_id(path) == expected_blob
    provenance = source_b3_episode_provenance()
    assert provenance["source_file_blobs"] == SOURCE_B3_EPISODE_FILE_BLOBS
    assert provenance["port_file_blobs"] == SOURCE_B3_EPISODE_PORT_FILE_BLOBS
    assert "OrgWorld" in " ".join(provenance["unavailable_source_dependencies"])


@pytest.mark.unit
def test_source_episode_trigger_attachment_and_ambient_no_spam_are_preserved() -> None:
    adapter = SourceB3EpisodeLifecycleAdapter()
    world = _world(tick=10)

    opened = adapter.observe_source_world_event(
        {
            "type": "external_signal_event",
            "subtype": "customer_trial",
            "agent_id": "scarlett",
            "post_id": "post_customer_trial_1",
            "channel_id": "customer_feedback",
            "converted": False,
            "outcome": "rejected",
            "tick": 10,
        },
        world=world,
    )

    assert opened is not None
    assert opened.episode_type == "feedback_ingestion_episode"
    assert opened.primary_agent_id == "scarlett"
    assert opened.linked_external_signal_ids == ["post_customer_trial_1"]

    attached = adapter.observe_source_world_event(
        {
            "type": "requested_action_event",
            "subtype": "create_issue",
            "agent_id": "calvin",
            "task_id": "task_customer_fix",
            "channel_id": "customer_feedback",
            "tick": 11,
        },
        world=world,
    )

    assert attached is opened
    assert opened.linked_task_ids == ["task_customer_fix"]
    assert opened.participants == ["scarlett", "calvin"]

    ambient = SourceB3EpisodeLifecycleAdapter()
    assert (
        ambient.observe_source_world_event(
            {
                "type": "action_event",
                "action_type": "share_external_post",
                "agent_id": "scarlett",
                "post_id": "post_ambient_9_1",
                "channel_id": "customer_feedback",
                "tick": 12,
            },
            world=world,
        )
        is None
    )
    assert ambient.episodes == {}


@pytest.mark.unit
def test_source_execution_result_normalization_is_preserved() -> None:
    adapter = SourceB3EpisodeLifecycleAdapter()
    result = SimpleNamespace(
        action_type="propose_protocol",
        agent_id="paul",
        created_objects=["proto_review_before_merge"],
        modified_objects=[],
        events=[
            {
                "type": "protocol_proposal_event",
                "protocol_id": "proto_review_before_merge",
                "tick": 4,
            }
        ],
        state_delta={},
        success=True,
    )

    touched = adapter.observe_source_result(result, world=_world(tick=4))

    assert len(touched) == 1
    episode = touched[0]
    assert episode.episode_type == "protocol_formation_episode"
    assert episode.linked_protocol_ids == ["proto_review_before_merge"]
    assert episode.produced_protocols == ["proto_review_before_merge"]
    assert episode.timeline[0]["action_type"] == "propose_protocol"


@pytest.mark.unit
def test_closed_source_debugging_episode_projects_once_to_core_boundary() -> None:
    bridge = SourceCoreObservationBridge.from_config(
        load_config(ROOT / "configs" / "minimal.yaml"),
        run_id="source-b3-episode-projection",
    )
    adapter = SourceB3EpisodeLifecycleAdapter()
    adapter.bind_source_core_bridge(bridge)
    world = _world(
        tick=50,
        build_error="tools/source_tracker.py:234 | ValueError: tracker invalid",
    )

    opened = adapter.observe_source_world_event(
        {
            "type": "release_event",
            "subtype": "readiness_check",
            "status": "blocked",
            "blockers": ["gate_smoke_test_passes"],
            "candidate_id": "rc_1",
            "agent_id": "paul",
            "tick": 50,
        },
        world=world,
    )
    assert opened is not None
    assert opened.episode_type == "debugging_episode"
    assert opened.suspected_module == "tools/source_tracker.py"
    formation = bridge.module.formation_state()
    assert formation is not None
    assert formation.episodes == ()

    adapter.observe_source_world_event(
        {
            "type": "product_event",
            "subtype": "patch_applied",
            "artifact_id": "artifact_source_tracker",
            "action_type": "edit_repo_file",
            "agent_id": "calvin",
            "tick": 52,
        },
        world=world,
    )
    world.world_tick = 56
    world._build_error = ""
    adapter.update_open_episodes(world=world)

    assert opened.status == "resolved"
    assert "Fixed tools/source_tracker.py" in opened.resolution
    formation = bridge.module.formation_state()
    assert formation is not None
    assert [item.episode_id for item in formation.episodes] == [opened.episode_id]
    assert formation.episodes[0].attributes["source_lifecycle"] == "source_hci_episode_manager"
    event_types = [event.event_type for event in bridge.module.events.events]
    assert event_types.count(OrganizationEventType.EPISODE_RECORDED.value) == 1
    assert adapter.status().closed_episode_records_projected == 1
    assert bridge.status().as_dict()["source_b3_episode_lifecycle"][
        "closed_episode_records_projected"
    ] == 1


@pytest.mark.unit
def test_episode_port_requires_explicit_source_host_inputs() -> None:
    adapter = SourceB3EpisodeLifecycleAdapter()
    with pytest.raises(SourceB3EpisodeHostUnavailableError, match="orgworld_required"):
        adapter.observe_source_world_event({}, world=None)
    with pytest.raises(
        SourceB3EpisodeHostUnavailableError,
        match="execution_result_missing_fields",
    ):
        adapter.observe_source_result(SimpleNamespace(), world=_world())

    # The pre-port ``observe(Event)`` path was the self-authored classifier that
    # converted release-shell events into episodes.  Its absence is intentional.
    assert not hasattr(adapter, "observe")
