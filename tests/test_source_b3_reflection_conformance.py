"""Provenance and fail-closed checks for the HCI reflection source port."""

from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import relic_agent.source_b3.reflection as reflection_boundary
from relic_agent.reflection.models import canon_wish_type, make_wish_fingerprint
from relic_agent.source_b3.reflection import (
    SourceB3ReflectionHostUnavailableError,
    SourceB3ReflectionLifecycleAdapter,
)
from relic_agent.source_b3.reflection.provenance import (
    SOURCE_B3_COMMIT,
    SOURCE_B3_REFLECTION_FILE_BLOBS,
    SOURCE_B3_REFLECTION_IMPORT_REWRITES,
    SOURCE_B3_REFLECTION_PORT_FILE_BLOBS,
    SOURCE_B3_REPOSITORY,
    source_b3_reflection_provenance,
)


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


def _source_shaped_world(
    adapter: SourceB3ReflectionLifecycleAdapter,
    *,
    provider: str,
) -> tuple[object, SimpleNamespace]:
    """Build a shape-only object to exercise gates without faking an HCI run."""

    world_type = type(
        "OrgWorld",
        (),
        {"__module__": "environments.org_env.backend.simulation.world"},
    )
    world = world_type()
    episode = SimpleNamespace(
        episode_id="episode_1",
        status="resolved",
        participants=["paul"],
        primary_agent_id="paul",
    )
    world.world_tick = 12
    world.agents = {"paul": SimpleNamespace(name="Paul")}
    world.agent_memories = {}
    world.events = []
    world.action_log = []
    world.episode_manager = SimpleNamespace(episodes={episode.episode_id: episode})
    world.product_artifacts = {}
    world.agent_log = []
    world.institutionalization_enabled = True
    world.reflection_manager = adapter.manager
    world.llm_client = SimpleNamespace(provider=provider, generate_json=lambda *_: {})
    return world, episode


@pytest.mark.unit
def test_source_reflection_port_is_pinned_to_hci_blobs() -> None:
    assert SOURCE_B3_REPOSITORY == "Hongyi-Du/SocioGenesis"
    assert SOURCE_B3_COMMIT == "dda36fb563375060ae8d8850300db01eb4695d29"
    assert SOURCE_B3_REFLECTION_FILE_BLOBS == {
        "environments/org_env/reflection/__init__.py": "1bdf844e7afe33f592fa0d961e03a6b88b554c3a",
        "environments/org_env/reflection/batch_manager.py": "38b20ff57fd6e8525bf655e64367419de675dd6d",
        "environments/org_env/reflection/failure_digest.py": "a93a74c442dfb01fbafa8e83b5f107e9d492891f",
        "environments/org_env/reflection/manager.py": "03e679153b2081b51fe3b7132272595d0e08bf97",
        "environments/org_env/reflection/objects.py": "942288178a705ed9c1b12989cfc687b1ad14ff9b",
    }
    assert SOURCE_B3_REFLECTION_IMPORT_REWRITES == {
        "environments.org_env.reflection.manager": "relic_agent.source_b3.reflection.manager",
        "environments.org_env.reflection.objects": "relic_agent.source_b3.reflection.objects",
    }
    for path, expected_blob in SOURCE_B3_REFLECTION_PORT_FILE_BLOBS.items():
        assert _blob_id(path) == expected_blob
    provenance = source_b3_reflection_provenance()
    assert provenance["source_file_blobs"] == SOURCE_B3_REFLECTION_FILE_BLOBS
    assert provenance["port_file_blobs"] == SOURCE_B3_REFLECTION_PORT_FILE_BLOBS
    assert "OrgWorld" in " ".join(provenance["unavailable_source_dependencies"])


@pytest.mark.unit
def test_source_reflection_objects_preserve_hci_wish_canonicalization() -> None:
    assert canon_wish_type("amend protocol") == "policy_repair_need"
    assert canon_wish_type("unrecognized category") == "workflow_need"
    assert make_wish_fingerprint(
        "protocol_need",
        "  Missing   review evidence ",
        ["issue_2", "art_1", "issue_2"],
    ) == "protocol_need|missing review evidence|art_1,issue_2"


@pytest.mark.unit
def test_reflection_port_refuses_shell_missing_or_non_openai_inputs() -> None:
    adapter = SourceB3ReflectionLifecycleAdapter()

    with pytest.raises(SourceB3ReflectionHostUnavailableError, match="orgworld_required"):
        adapter.reflect_on_closed_source_episode(None, world=None)

    with pytest.raises(SourceB3ReflectionHostUnavailableError, match="requires_hci_orgworld"):
        adapter.reflect_on_closed_source_episode(
            SimpleNamespace(episode_id="episode_1", status="resolved"),
            world=SimpleNamespace(),
        )

    anthropic_world, episode = _source_shaped_world(adapter, provider="anthropic")
    with pytest.raises(SourceB3ReflectionHostUnavailableError, match="not_openai_compatible:anthropic"):
        adapter.reflect_on_closed_source_episode(episode, world=anthropic_world)

    mock_world, episode = _source_shaped_world(adapter, provider="mock")
    with pytest.raises(SourceB3ReflectionHostUnavailableError, match="not_openai_compatible:mock"):
        adapter.reflect_on_closed_source_episode(episode, world=mock_world)

    assert not hasattr(adapter, "reflect")
    assert not hasattr(reflection_boundary, "ReflectionManager")
    assert dict(adapter.reflections) == {}
    assert dict(adapter.wishes) == {}
    status = adapter.status().as_dict()
    assert status["reflection_count"] == 0
    assert status["wish_count"] == 0
    assert "template_reflection_without_source_provider" in status["unavailable_fail_closed"]


@pytest.mark.unit
def test_source_template_fallback_is_guarded_before_it_can_create_records() -> None:
    adapter = SourceB3ReflectionLifecycleAdapter()
    original = adapter.manager._template_reflect

    with adapter._forbid_template_fallback():
        with pytest.raises(
            SourceB3ReflectionHostUnavailableError,
            match="provider_result_required",
        ):
            adapter.manager._template_reflect({}, SimpleNamespace())

    assert adapter.manager._template_reflect.__func__ is original.__func__
    assert dict(adapter.reflections) == {}
    assert dict(adapter.wishes) == {}
