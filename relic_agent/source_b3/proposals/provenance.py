"""Provenance for the bounded HCI proposal-manager source port."""
from __future__ import annotations


SOURCE_B3_REPOSITORY = "Hongyi-Du/SocioGenesis"
SOURCE_B3_COMMIT = "dda36fb563375060ae8d8850300db01eb4695d29"
SOURCE_B3_PROPOSAL_SOURCE_PATH = "environments/org_env/proposals"
SOURCE_B3_PROPOSAL_FILE_BLOBS: dict[str, str] = {
    "environments/org_env/proposals/objects.py": "33af715a6733f8b7fa262b3a8e7d1596e9c1b5ef",
    "environments/org_env/proposals/families.py": "7308f94e8006ffc69a91579aa5507d059ba2c6c4",
    "environments/org_env/proposals/manager.py": "0dc1656617d0b8b7c939b8fbccd93fae7dee6ef8",
    "environments/org_env/llm/semantic_dedup.py": "01b73e7adba73753ba15c1029f0255e9651d8842",
}
SOURCE_B3_PROPOSAL_PORT_FILE_BLOBS: dict[str, str] = {
    "relic_agent/source_b3/proposals/objects.py": "c19a51ae4398a6c363c800c00464b135c12ed194",
    "relic_agent/source_b3/proposals/families.py": "700199279b19a2e0e8ae14a582d595f6c2aa9654",
    "relic_agent/source_b3/proposals/semantic_dedup.py": "31429038f8d8fc3076a96a9d866851fc2909a86d",
    "relic_agent/source_b3/proposals/manager.py": "f56cc8a2923c719f0dc039aed750a12fe413a79f",
    "relic_agent/source_b3/proposals/capabilities.py": "60fe536644f5ef6903aacd5dda240356f5b4913b",
}
SOURCE_B3_PROPOSAL_IMPORT_REWRITES: dict[str, str] = {
    "environments.org_env.proposals.families": "relic_agent.source_b3.proposals.families",
    "environments.org_env.proposals.objects": "relic_agent.source_b3.proposals.objects",
    "environments.org_env.llm.semantic_dedup": "relic_agent.source_b3.proposals.semantic_dedup",
    "environments.org_env.backend.actions.registered_action_types": "relic_agent.source_b3.proposals.capabilities.registered_action_types",
    "environments.org_env.experiments.ablations": "relic_agent.source_b3.proposals.capabilities.institutionalization_enabled",
}
SOURCE_B3_PROPOSAL_UNAVAILABLE_SOURCE_DEPENDENCIES = (
    "environments.org_env.llm.proposal_generator",
    "environments.org_env.backend.protocol.harm",
    "environments.org_env.programbench",
)


def source_b3_proposal_provenance() -> dict[str, object]:
    """Return a detached record of source blobs and intentional boundary cuts."""

    return {
        "source_repository": SOURCE_B3_REPOSITORY,
        "source_commit": SOURCE_B3_COMMIT,
        "source_path": SOURCE_B3_PROPOSAL_SOURCE_PATH,
        "source_file_blobs": dict(SOURCE_B3_PROPOSAL_FILE_BLOBS),
        "port_file_blobs": dict(SOURCE_B3_PROPOSAL_PORT_FILE_BLOBS),
        "vendoring": "source_manager_port_with_explicit_orgworld_capability_seams",
        "import_rewrites": dict(SOURCE_B3_PROPOSAL_IMPORT_REWRITES),
        "unavailable_source_dependencies": list(
            SOURCE_B3_PROPOSAL_UNAVAILABLE_SOURCE_DEPENDENCIES
        ),
    }


__all__ = [
    "SOURCE_B3_COMMIT",
    "SOURCE_B3_PROPOSAL_FILE_BLOBS",
    "SOURCE_B3_PROPOSAL_IMPORT_REWRITES",
    "SOURCE_B3_PROPOSAL_PORT_FILE_BLOBS",
    "SOURCE_B3_PROPOSAL_SOURCE_PATH",
    "SOURCE_B3_PROPOSAL_UNAVAILABLE_SOURCE_DEPENDENCIES",
    "SOURCE_B3_REPOSITORY",
    "source_b3_proposal_provenance",
]
