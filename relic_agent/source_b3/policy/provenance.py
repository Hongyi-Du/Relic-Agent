"""Provenance for the bounded HCI structural-protocol policy port."""

from __future__ import annotations


SOURCE_B3_REPOSITORY = "Hongyi-Du/SocioGenesis"
SOURCE_B3_COMMIT = "dda36fb563375060ae8d8850300db01eb4695d29"
SOURCE_B3_POLICY_SOURCE_PATH = "environments/org_env/policy"
SOURCE_B3_POLICY_FILE_BLOBS: dict[str, str] = {
    "environments/org_env/policy/__init__.py": "147acb6e745defa3040ff575e4291053dc71dc7a",
    "environments/org_env/policy/protocol_affordance.py": "5b21f55f28df0c3c76fd352341b3f202b9f937ca",
    "environments/org_env/policy/attractor_guard.py": "73046ab902a8f74cebf0c17c9ab40b026502baa8",
}
# The protocol-affordance module is byte exact. The source initializer imports
# AttractorGuard, whose large HCI execution/product closure is deliberately not
# mounted by the release shell, so the initializer is represented by a narrow
# explicit-host boundary instead of an importable partial package.
SOURCE_B3_POLICY_PORT_FILE_BLOBS: dict[str, str] = {
    "relic_agent/source_b3/policy/protocol_affordance.py": "5b21f55f28df0c3c76fd352341b3f202b9f937ca",
}
SOURCE_B3_POLICY_IMPORT_REWRITES: dict[str, str] = {}
SOURCE_B3_POLICY_UNAVAILABLE_SOURCE_DEPENDENCIES = (
    "environments.org_env.backend.simulation.world.OrgWorld",
    "agent_sdk.lived.core.contracts.ActionCandidate",
    "environments.org_env.runtime_adapter.execution.OrgActionMapper",
    "environments.org_env.runtime_adapter.policy",
    "environments.org_env.policy.attractor_guard.AttractorGuard",
    "environments.org_env.product",
    "environments.org_env.backend.repo",
    "environments.org_env.human",
)
SOURCE_B3_POLICY_UNPORTED_COMPONENTS: dict[str, dict[str, str]] = {
    "environments/org_env/policy/attractor_guard.py": {
        "source_blob": "73046ab902a8f74cebf0c17c9ab40b026502baa8",
        "reason": (
            "requires the full HCI workspace, product, repository, meeting, "
            "and ProgramBench-aware action host"
        ),
    },
    "environments/org_env/runtime_adapter/policy.py": {
        "source_blob": "9d8795b85e0ad0a891d0089774f9796ad2a926af",
        "reason": (
            "is the HCI profile/action selector and depends on source candidates, "
            "work rhythm, attractor guard, and the full OrgWorld"
        ),
    },
    "environments/org_env/runtime_adapter/execution.py": {
        "source_blob": "cebb61ce26f6c88002b0357e5a5ef4314719184b",
        "reason": "source candidate generation and execution require the full HCI action host",
    },
}


def source_b3_policy_provenance() -> dict[str, object]:
    """Return source and exact-port evidence without claiming action selection."""

    return {
        "source_repository": SOURCE_B3_REPOSITORY,
        "source_commit": SOURCE_B3_COMMIT,
        "source_path": SOURCE_B3_POLICY_SOURCE_PATH,
        "source_file_blobs": dict(SOURCE_B3_POLICY_FILE_BLOBS),
        "port_file_blobs": dict(SOURCE_B3_POLICY_PORT_FILE_BLOBS),
        "vendoring": "byte_exact_source_protocol_affordance_with_explicit_hci_candidate_gate",
        "import_rewrites": dict(SOURCE_B3_POLICY_IMPORT_REWRITES),
        "unavailable_source_dependencies": list(
            SOURCE_B3_POLICY_UNAVAILABLE_SOURCE_DEPENDENCIES
        ),
        "unported_components": {
            path: dict(details)
            for path, details in SOURCE_B3_POLICY_UNPORTED_COMPONENTS.items()
        },
    }


__all__ = [
    "SOURCE_B3_COMMIT",
    "SOURCE_B3_POLICY_FILE_BLOBS",
    "SOURCE_B3_POLICY_IMPORT_REWRITES",
    "SOURCE_B3_POLICY_PORT_FILE_BLOBS",
    "SOURCE_B3_POLICY_SOURCE_PATH",
    "SOURCE_B3_POLICY_UNAVAILABLE_SOURCE_DEPENDENCIES",
    "SOURCE_B3_POLICY_UNPORTED_COMPONENTS",
    "SOURCE_B3_REPOSITORY",
    "source_b3_policy_provenance",
]
