"""Provenance for the bounded HCI reflection and wish source port."""

from __future__ import annotations


SOURCE_B3_REPOSITORY = "Hongyi-Du/SocioGenesis"
SOURCE_B3_COMMIT = "dda36fb563375060ae8d8850300db01eb4695d29"
SOURCE_B3_REFLECTION_SOURCE_PATH = "environments/org_env/reflection"
SOURCE_B3_REFLECTION_FILE_BLOBS: dict[str, str] = {
    "environments/org_env/reflection/__init__.py": "1bdf844e7afe33f592fa0d961e03a6b88b554c3a",
    "environments/org_env/reflection/batch_manager.py": "38b20ff57fd6e8525bf655e64367419de675dd6d",
    "environments/org_env/reflection/failure_digest.py": "a93a74c442dfb01fbafa8e83b5f107e9d492891f",
    "environments/org_env/reflection/manager.py": "03e679153b2081b51fe3b7132272595d0e08bf97",
    "environments/org_env/reflection/objects.py": "942288178a705ed9c1b12989cfc687b1ad14ff9b",
}
# ``objects.py``, ``batch_manager.py``, and ``failure_digest.py`` are byte exact.
# The manager only redirects its static local objects import; its HCI-only
# dynamic imports deliberately remain source paths.  The package initializer
# additionally exposes the release-specific explicit-host boundary.
SOURCE_B3_REFLECTION_PORT_FILE_BLOBS: dict[str, str] = {
    "relic_agent/source_b3/reflection/__init__.py": "91124242a1c05f8f09da1cd03e42c68229ce2357",
    "relic_agent/source_b3/reflection/batch_manager.py": "38b20ff57fd6e8525bf655e64367419de675dd6d",
    "relic_agent/source_b3/reflection/failure_digest.py": "a93a74c442dfb01fbafa8e83b5f107e9d492891f",
    "relic_agent/source_b3/reflection/manager.py": "9f68e6658d7fad31b2b87c755d058d346077ab45",
    "relic_agent/source_b3/reflection/objects.py": "942288178a705ed9c1b12989cfc687b1ad14ff9b",
}
SOURCE_B3_REFLECTION_IMPORT_REWRITES: dict[str, str] = {
    "environments.org_env.reflection.manager": "relic_agent.source_b3.reflection.manager",
    "environments.org_env.reflection.objects": "relic_agent.source_b3.reflection.objects",
}
SOURCE_B3_REFLECTION_UNAVAILABLE_SOURCE_DEPENDENCIES = (
    "environments.org_env.backend.simulation.world.OrgWorld",
    "environments.org_env.llm.client.OpenAIOrgLLMClient",
    "environments.org_env.llm.prompt_assets",
    "environments.org_env.llm.wish_interpreter",
    "environments.org_env.llm.episode_summarizer",
    "environments.org_env.growth",
    "environments.org_env.human",
)
SOURCE_B3_REFLECTION_UNPORTED_LLM_ADJUNCTS: dict[str, dict[str, str]] = {
    "environments/org_env/llm/event_appraisal.py": {
        "source_blob": "40942c789a371c3d9eee82e85503cbe6ea9ecdf8",
        "reason": "requires the HCI world/prompt-assets context; no release-shell event conversion",
    },
    "environments/org_env/llm/episode_summarizer.py": {
        "source_blob": "99a843183ede732041dfde239d1f73e3eaef5ab3",
        "reason": "requires a closed HCI episode and source provider/prompt context",
    },
    "environments/org_env/llm/wish_interpreter.py": {
        "source_blob": "cd09ae8f55302735377d7cf2ded5377476e1ed6c",
        "reason": "requires source reflection text plus HCI provider/prompt-assets context",
    },
}


def source_b3_reflection_provenance() -> dict[str, object]:
    """Return source and local-closure evidence without claiming activation."""

    return {
        "source_repository": SOURCE_B3_REPOSITORY,
        "source_commit": SOURCE_B3_COMMIT,
        "source_path": SOURCE_B3_REFLECTION_SOURCE_PATH,
        "source_file_blobs": dict(SOURCE_B3_REFLECTION_FILE_BLOBS),
        "port_file_blobs": dict(SOURCE_B3_REFLECTION_PORT_FILE_BLOBS),
        "vendoring": "source_reflection_manager_port_with_explicit_hci_host_gate",
        "import_rewrites": dict(SOURCE_B3_REFLECTION_IMPORT_REWRITES),
        "unavailable_source_dependencies": list(
            SOURCE_B3_REFLECTION_UNAVAILABLE_SOURCE_DEPENDENCIES
        ),
        "unported_llm_adjuncts": {
            path: dict(details)
            for path, details in SOURCE_B3_REFLECTION_UNPORTED_LLM_ADJUNCTS.items()
        },
    }


__all__ = [
    "SOURCE_B3_COMMIT",
    "SOURCE_B3_REFLECTION_FILE_BLOBS",
    "SOURCE_B3_REFLECTION_IMPORT_REWRITES",
    "SOURCE_B3_REFLECTION_PORT_FILE_BLOBS",
    "SOURCE_B3_REFLECTION_SOURCE_PATH",
    "SOURCE_B3_REFLECTION_UNAVAILABLE_SOURCE_DEPENDENCIES",
    "SOURCE_B3_REFLECTION_UNPORTED_LLM_ADJUNCTS",
    "SOURCE_B3_REPOSITORY",
    "source_b3_reflection_provenance",
]
