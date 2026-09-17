"""Immutable provenance and conformance helpers for the B3 source host.

The host is a source-preserving extraction from one sealed SocioGenesis commit.
The newer HCI head is used only as a behavioural comparison point: it is not
mixed into this package.  The selected B3 paths have identical 336-tick
structure for the pinned smoke seeds on both commits.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


SOURCE_REPOSITORY = "Hongyi-Du/SocioGenesis"
SOURCE_B3_COMMIT = "b567122022e131bab9555e6afb3b57147d591c8d"
SOURCE_HCI_COMMIT = "dda36fb563375060ae8d8850300db01eb4695d29"
# Audited with ``git merge-base --is-ancestor`` in the source checkout.  The
# B3 pin is intentionally an ancestor of the authoritative HCI head rather
# than a fork selected by resemblance.  The source-native closure below has
# identical 336-tick seven-structure fingerprints at both revisions.
SOURCE_B3_IS_ANCESTOR_OF_HCI = True

# These are Git blob object ids from SOURCE_B3_COMMIT.  They name the runtime
# seams that must remain source-exact; adapters live only under relic_agent.
SOURCE_B3_FILE_BLOBS: dict[str, str] = {
    "agent_sdk/lived/domain/interfaces.py": "3736fff4ec1138709e8c624ae2723b4068bf9609",
    "environments/org_env/backend/simulation/world.py": "cabd7275c70685433b60412a564db6392cadd65b",
    "environments/org_env/runtime_adapter/execution.py": "90a9d671ec9fcc2b56c41c774d1feaf5157a78b9",
    "environments/org_env/runtime_adapter/policy.py": "a3eaa2bdc642976005e29ef7ff8d9788e33d760b",
    "environments/org_env/policy/attractor_guard.py": "aa92a0972baff330e462c79232cc1546984e6b6f",
    "environments/org_env/episodes/episode_manager.py": "ab71b94447359110f6ed98108b999f19931fed06",
    "environments/org_env/reflection/manager.py": "4c278e4401b5bdffc6c50e825c5a1820ef64cdce",
    "environments/org_env/proposals/manager.py": "8e622ff4afc5a985fa0371837c6de6d91cfd1df4",
    "environments/org_env/growth/appraiser.py": "66813251464b8b8157ea0601c3bb761cd5073cc9",
    "environments/org_env/backend/protocol/registry.py": "fa8789ce2b64fcafd8e9e43fbd8f3f6fc918199a",
    "environments/org_env/experiments/ablations.py": "e20dbc6b6380d00f40a7937bee265b5dde87d210",
    "environments/org_env/experiments/resources.py": "2fbeabcce37a6a3fca55d9b6b91a4a70756fc49e",
    "environments/org_env/experiments/controlled_execution.py": "a6903af776862e502f215953065ce049f3f7c42f",
    "environments/org_env/coding/metrics.py": "38e356b63d2b30af38bfa54ff93574c0e3b7a9f1",
}

DISABLED_CAPABILITIES = (
    "programbench",
    "benchmark_evaluator",
    "oss_time_machine_data",
    "main_experiment_runner",
    "cooperbench",
    "nature_env",
    "hci_human_seat",
    "external_society",
    "society_core",
    "external_market_three_event_synthetic_adapter",
)

FORBIDDEN_MODULE_PREFIXES = (
    "society_core",
    "environments.org_env.programbench",
    "environments.org_env.external_society",
    "environments.org_env.product.substrates.final_evaluation",
    "environments.nature_env",
)

# These prior extraction packages remain in the tree as archival compatibility
# material.  They are not imported by a fresh source-native CLI process.  They
# are deliberately reported separately from external forbidden modules: users
# of the old direct Python API may still import them explicitly, whereas a
# release run must never load the unavailable external systems above.
ARCHIVED_COMPAT_MODULE_PREFIXES = (
    "organization_core",
    "relic_agent.source_b3",
    "relic_agent.source_core",
    "relic_agent.governance",
    "relic_agent.events",
)

# Golden B3 world structures, recorded from both SOURCE_B3_COMMIT and the HCI
# comparison head.  The seven structures cover execution, policy, episodes,
# reflection/wish formation, proposal formation, and protocol specifications.
STRUCTURAL_GOLDENS: dict[int, dict[str, dict[str, object]]] = {
    17: {
        "action_log": {"count": 579, "sha256": "3303e711b7862b76d375aa06d364bfb7ec34aee692df046110a414b763d111e5"},
        "policy_trace": {"count": 400, "sha256": "f70f3ca444dd7d8f971f539a4f91f89f79635fceac2638be64a6530e6e81034d"},
        "episodes": {"count": 48, "sha256": "f7ab7b6d56eebf8c7e63603ed630fe778d8dbaaf5ca9cb3501b29a50f748b37a"},
        "reflections": {"count": 64, "sha256": "8130da23e7ad7e0ad7990bb72323eeeb450ec227cc539009b8b25ae877b2e14c"},
        "wishes": {"count": 25, "sha256": "cda52157934680069fda51b84f7f6ff85c348662a200ade79d4726967da4f088"},
        "proposals": {"count": 26, "sha256": "e8d72c31801cd7410b9f7da132297253feaceec8ab19d2fd38d4b910361d7016"},
        "protocol_specs": {"count": 1, "sha256": "c2c64ebd513acddfe043e074dfe91b4c766036b2722df5d254d4c3b27981ab07"},
    },
    701: {
        "action_log": {"count": 442, "sha256": "ccfa1f29ca815cdf2112596848dc70b7942cb506c754fa2021e8e5f61425fc50"},
        "policy_trace": {"count": 400, "sha256": "4b47fe7d5e94cc644b19d6770103b8e269d6d6eaab592e41d1ef531ad84c9a55"},
        "episodes": {"count": 31, "sha256": "8aa3871b28edf44f47dc8d8d238cae04c4e4151de0d174aab31496b86c98935d"},
        "reflections": {"count": 43, "sha256": "3e9d3e7b8428ef019845e48841c3c0227137b9a5dbcd4624ba9e91267d359a68"},
        "wishes": {"count": 15, "sha256": "7520bf006fdf481168c81947bf5b218c0e2d75f6e1a5e967c30a36e8d165cd13"},
        "proposals": {"count": 16, "sha256": "ea8b11ede774a87049a5d17b6453b6a18f1bd4e3bdd6649ecc001970afecdc8a"},
        "protocol_specs": {"count": 2, "sha256": "bbf39db0e6a81210ca7f4b533a5b867f385dd2f83c669d824b809c8c6b61a7c0"},
    },
    2026: {
        "action_log": {"count": 487, "sha256": "0b69e6ba17a30e5dc2e1faceec3993805f25c957535b84fc7e00148a78fa8978"},
        "policy_trace": {"count": 400, "sha256": "8a028c477c401f23e489288ccdbb099cbb5bb044d5c4d9be76c945f97aa06ec5"},
        "episodes": {"count": 43, "sha256": "2946755c46ec69f0f358f72c88a1d2747f086afbc0210da25af99d9c8a53996f"},
        "reflections": {"count": 63, "sha256": "d0c3083bdba1db74d35a69763e8de57db57539bc0640e21805e25aa16dabb39c"},
        "wishes": {"count": 27, "sha256": "e02c6437962420fdd1ce4e4e1284f0b37472804cf2ce4f34c65f77089dd4c57a"},
        "proposals": {"count": 28, "sha256": "735329e9dcccd3697ade9d125d030f8680f5400f367584161fc6fbb05a13f936"},
        "protocol_specs": {"count": 2, "sha256": "13c44f8cd56e90e7d77c980fcec0e626bb49c0350a8500ee98c532dbf4c99328"},
    },
}


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git_blob_id(path: Path) -> str:
    content = path.read_bytes()
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def verify_critical_vendor_blobs() -> dict[str, object]:
    """Verify host-critical source files without requiring a Git checkout."""

    root = _repository_root()
    actual = {
        relative: _git_blob_id(root / relative)
        for relative in sorted(SOURCE_B3_FILE_BLOBS)
    }
    mismatches = {
        path: {"expected": SOURCE_B3_FILE_BLOBS[path], "actual": actual[path]}
        for path in actual
        if actual[path] != SOURCE_B3_FILE_BLOBS[path]
    }
    return {
        "verified": not mismatches,
        "source_commit": SOURCE_B3_COMMIT,
        "file_blobs": dict(SOURCE_B3_FILE_BLOBS),
        "mismatches": mismatches,
    }


def assert_no_forbidden_loaded_modules() -> dict[str, object]:
    """Return and enforce the release package's forbidden import boundary."""

    loaded = sorted(
        name
        for name in sys.modules
        if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_MODULE_PREFIXES)
    )
    if loaded:
        raise RuntimeError("forbidden_source_modules_loaded:" + ",".join(loaded))
    return {
        "forbidden_module_prefixes": list(FORBIDDEN_MODULE_PREFIXES),
        "forbidden_modules_loaded": loaded,
        "status": "passed",
    }


def archived_compat_modules_loaded() -> list[str]:
    """List archived compatibility modules visible in the current process.

    This is diagnostic rather than an unconditional error so a Python caller
    can inspect old APIs in the same interpreter.  The release acceptance test
    verifies a fresh CLI process keeps this list empty.
    """

    return sorted(
        name
        for name in sys.modules
        if any(
            name == prefix or name.startswith(prefix + ".")
            for prefix in ARCHIVED_COMPAT_MODULE_PREFIXES
        )
    )


def _normalize(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _normalize(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {
            str(key): _normalize(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(
            (_normalize(item) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True, default=str),
        )
    if hasattr(value, "__dict__"):
        return {
            str(key): _normalize(item)
            for key, item in sorted(vars(value).items())
            if not str(key).startswith("_")
        }
    return value


def _structure_hash(value: Any) -> dict[str, object]:
    normalized = _normalize(value)
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return {"count": len(normalized), "sha256": hashlib.sha256(payload).hexdigest()}


def structural_conformance(world: Any) -> dict[str, dict[str, object]]:
    """Hash the seven source-owned structures used for 336-tick parity tests."""

    structures: dict[str, Callable[[Any], Any]] = {
        "action_log": lambda value: value.action_log,
        "policy_trace": lambda value: value.policy_trace,
        "episodes": lambda value: value.episode_manager.episodes,
        "reflections": lambda value: value.reflection_manager.reflections,
        "wishes": lambda value: value.reflection_manager.wishes,
        "proposals": lambda value: value.proposal_manager.proposals,
        "protocol_specs": lambda value: value.proposal_manager.protocol_specs,
    }
    return {name: _structure_hash(getter(world)) for name, getter in structures.items()}


def source_host_provenance() -> dict[str, object]:
    """Metadata persisted in every source-native run manifest."""

    return {
        "source_repository": SOURCE_REPOSITORY,
        "source_commit": SOURCE_B3_COMMIT,
        "comparison_hci_commit": SOURCE_HCI_COMMIT,
        "source_commit_is_ancestor_of_comparison_hci_commit": SOURCE_B3_IS_ANCESTOR_OF_HCI,
        "vendoring": "source_preserving_b3_dynamic_closure",
        "critical_file_blobs": dict(SOURCE_B3_FILE_BLOBS),
        "disabled_capabilities": list(DISABLED_CAPABILITIES),
        "conformance_reference": {
            "ticks": 336,
            "seeds": sorted(STRUCTURAL_GOLDENS),
            "comparison_hci_structures_identical": True,
            "structures": [
                "action_log",
                "policy_trace",
                "episodes",
                "reflections",
                "wishes",
                "proposals",
                "protocol_specs",
            ],
        },
    }


__all__ = [
    "DISABLED_CAPABILITIES",
    "ARCHIVED_COMPAT_MODULE_PREFIXES",
    "FORBIDDEN_MODULE_PREFIXES",
    "SOURCE_B3_COMMIT",
    "SOURCE_B3_FILE_BLOBS",
    "SOURCE_B3_IS_ANCESTOR_OF_HCI",
    "SOURCE_HCI_COMMIT",
    "SOURCE_REPOSITORY",
    "STRUCTURAL_GOLDENS",
    "assert_no_forbidden_loaded_modules",
    "archived_compat_modules_loaded",
    "source_host_provenance",
    "structural_conformance",
    "verify_critical_vendor_blobs",
]
