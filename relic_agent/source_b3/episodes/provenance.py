"""Provenance for the bounded HCI event-to-episode source port."""

from __future__ import annotations


SOURCE_B3_REPOSITORY = "Hongyi-Du/SocioGenesis"
SOURCE_B3_COMMIT = "dda36fb563375060ae8d8850300db01eb4695d29"
SOURCE_B3_EPISODE_SOURCE_PATH = "environments/org_env/episodes"
SOURCE_B3_EPISODE_FILE_BLOBS: dict[str, str] = {
    "environments/org_env/episodes/episode.py": "85e3946e73d9d36a0ceda0320747e78b290af73e",
    "environments/org_env/episodes/episode_manager.py": "ab71b94447359110f6ed98108b999f19931fed06",
}
# Updated by the source-port conformance test whenever a deliberate import
# rewrite changes.  ``episode.py`` is byte-exact; the manager only rewrites
# its import location into this closed local package.
SOURCE_B3_EPISODE_PORT_FILE_BLOBS: dict[str, str] = {
    "relic_agent/source_b3/episodes/episode.py": "85e3946e73d9d36a0ceda0320747e78b290af73e",
    "relic_agent/source_b3/episodes/episode_manager.py": "62cbecae8e0b217f2f87a621e0fd7f51babd81f7",
}
SOURCE_B3_EPISODE_IMPORT_REWRITES: dict[str, str] = {
    "environments.org_env.episodes.episode": "relic_agent.source_b3.episodes.episode",
}
SOURCE_B3_EPISODE_UNAVAILABLE_SOURCE_DEPENDENCIES = (
    "environments.org_env.backend.simulation.world.OrgWorld",
    "environments.org_env.backend.actions.ExecutionResult",
    "environments.org_env.reflection",
    "environments.org_env.growth",
    "environments.org_env.human",
)


def source_b3_episode_provenance() -> dict[str, object]:
    """Return detached source and local-closure evidence for this port."""

    return {
        "source_repository": SOURCE_B3_REPOSITORY,
        "source_commit": SOURCE_B3_COMMIT,
        "source_path": SOURCE_B3_EPISODE_SOURCE_PATH,
        "source_file_blobs": dict(SOURCE_B3_EPISODE_FILE_BLOBS),
        "port_file_blobs": dict(SOURCE_B3_EPISODE_PORT_FILE_BLOBS),
        "vendoring": "source_episode_manager_port_with_explicit_orgworld_input",
        "import_rewrites": dict(SOURCE_B3_EPISODE_IMPORT_REWRITES),
        "unavailable_source_dependencies": list(
            SOURCE_B3_EPISODE_UNAVAILABLE_SOURCE_DEPENDENCIES
        ),
    }


__all__ = [
    "SOURCE_B3_COMMIT",
    "SOURCE_B3_EPISODE_FILE_BLOBS",
    "SOURCE_B3_EPISODE_IMPORT_REWRITES",
    "SOURCE_B3_EPISODE_PORT_FILE_BLOBS",
    "SOURCE_B3_EPISODE_SOURCE_PATH",
    "SOURCE_B3_EPISODE_UNAVAILABLE_SOURCE_DEPENDENCIES",
    "SOURCE_B3_REPOSITORY",
    "source_b3_episode_provenance",
]
