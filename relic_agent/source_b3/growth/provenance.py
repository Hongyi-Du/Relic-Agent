"""Provenance for the bounded HCI growth and coding-profile source port."""

from __future__ import annotations


SOURCE_B3_REPOSITORY = "Hongyi-Du/SocioGenesis"
SOURCE_B3_COMMIT = "dda36fb563375060ae8d8850300db01eb4695d29"
SOURCE_B3_GROWTH_SOURCE_PATH = "environments/org_env/growth"
SOURCE_B3_GROWTH_FILE_BLOBS: dict[str, str] = {
    "environments/org_env/coding/__init__.py": "f9d1bf6c2477b8e7d42174c26a23388a8c224f89",
    "environments/org_env/coding/profile.py": "eb9ba17cdb2c4ddada42d3c5ef16a359c8254c7b",
    "environments/org_env/growth/__init__.py": "fcc18c08e208ddeed6222012a7386ec5f772fa8c",
    "environments/org_env/growth/objects.py": "b61c7f5f856ce6172d5b7ec4b2de42fc272a9954",
    "environments/org_env/growth/authority.py": "e123ccc0047a41890d120f85c5b0a35e079af72f",
    "environments/org_env/growth/appraiser.py": "66813251464b8b8157ea0601c3bb761cd5073cc9",
    "environments/org_env/growth/reconciler.py": "f404e0fc36d9950f44b5e32938a58411a11c44e4",
}
# coding/profile.py and coding/__init__.py are byte exact. The growth files
# only redirect imports to that closed local source port; their dynamic product
# imports remain HCI paths and are gated by the lifecycle adapter.
SOURCE_B3_GROWTH_PORT_FILE_BLOBS: dict[str, str] = {
    "relic_agent/source_b3/coding/__init__.py": "f9d1bf6c2477b8e7d42174c26a23388a8c224f89",
    "relic_agent/source_b3/coding/profile.py": "eb9ba17cdb2c4ddada42d3c5ef16a359c8254c7b",
    "relic_agent/source_b3/growth/__init__.py": "1ebdf833262554c84aa7abab185219affb81c0b1",
    "relic_agent/source_b3/growth/objects.py": "411aaa60ea1c8a68f6855dc4a83fd71c353344f2",
    "relic_agent/source_b3/growth/authority.py": "142794c5e83fca2168f2a982a12d0bfcc997cf0a",
    "relic_agent/source_b3/growth/appraiser.py": "f3739983912f40ef6a411b89644c4b2b6335dc92",
    "relic_agent/source_b3/growth/reconciler.py": "dd94882e6524a10e985897023033bb700a36c4f8",
}
SOURCE_B3_GROWTH_IMPORT_REWRITES: dict[str, str] = {
    "environments.org_env.coding.profile": "relic_agent.source_b3.coding.profile",
    "environments.org_env.growth.appraiser": "relic_agent.source_b3.growth.appraiser",
    "environments.org_env.growth.authority": "relic_agent.source_b3.growth.authority",
    "environments.org_env.growth.objects": "relic_agent.source_b3.growth.objects",
    "environments.org_env.growth.reconciler": "relic_agent.source_b3.growth.reconciler",
}
SOURCE_B3_GROWTH_UNAVAILABLE_SOURCE_DEPENDENCIES = (
    "environments.org_env.backend.simulation.world.OrgWorld",
    "environments.org_env.runtime_adapter.execution.ExecutionResult",
    "environments.org_env.product.objects.artifact_purpose",
    "environments.org_env.runtime_adapter.execution.OrgActionMapper",
    "environments.org_env.runtime_adapter.policy",
    "agent_sdk.lived.core.contracts.ActionCandidate",
)
# These are paper-experiment facilities, not a portable substitute for growth.
# They are recorded so a release audit cannot mistake their absence for a new
# implementation decision.
SOURCE_B3_UNPORTED_CAPABILITY_EXPERIMENTS: dict[str, dict[str, str]] = {
    "environments/org_env/experiments/capability_carriers.py": {
        "source_blob": "5c57d68bf28d6015cb97bdfcc13b3a8063081a8e",
        "reason": (
            "requires the HCI source document/protocol world and "
            "society_core organizational-capability taxonomy"
        ),
    },
    "environments/org_env/experiments/capability_evidence.py": {
        "source_blob": "1412827503a5b6b8e81830c5ed24dcd0c8c2cac4",
        "reason": "paper evaluator-facing capability metrics; no release-shell evaluator host",
    },
    "environments/org_env/experiments/capability_transfer.py": {
        "source_blob": "629620cd329760da83a765a4fc355e5532ac8f76",
        "reason": "frozen paper transfer arms require source-world injection and benchmark harness",
    },
}


def source_b3_growth_provenance() -> dict[str, object]:
    """Return source and local-closure evidence without claiming activation."""

    return {
        "source_repository": SOURCE_B3_REPOSITORY,
        "source_commit": SOURCE_B3_COMMIT,
        "source_path": SOURCE_B3_GROWTH_SOURCE_PATH,
        "source_file_blobs": dict(SOURCE_B3_GROWTH_FILE_BLOBS),
        "port_file_blobs": dict(SOURCE_B3_GROWTH_PORT_FILE_BLOBS),
        "vendoring": "source_growth_appraiser_reconciler_and_coding_profile_with_explicit_hci_host_gate",
        "import_rewrites": dict(SOURCE_B3_GROWTH_IMPORT_REWRITES),
        "unavailable_source_dependencies": list(
            SOURCE_B3_GROWTH_UNAVAILABLE_SOURCE_DEPENDENCIES
        ),
        "unported_capability_experiments": {
            path: dict(details)
            for path, details in SOURCE_B3_UNPORTED_CAPABILITY_EXPERIMENTS.items()
        },
    }


__all__ = [
    "SOURCE_B3_COMMIT",
    "SOURCE_B3_GROWTH_FILE_BLOBS",
    "SOURCE_B3_GROWTH_IMPORT_REWRITES",
    "SOURCE_B3_GROWTH_PORT_FILE_BLOBS",
    "SOURCE_B3_GROWTH_SOURCE_PATH",
    "SOURCE_B3_GROWTH_UNAVAILABLE_SOURCE_DEPENDENCIES",
    "SOURCE_B3_REPOSITORY",
    "SOURCE_B3_UNPORTED_CAPABILITY_EXPERIMENTS",
    "source_b3_growth_provenance",
]
