"""Immutable provenance for the vendored dependency-free organization core.

This is deliberately data-only: the source modules under ``organization_core``
are copied byte-for-byte from the recorded source revision. Conformance tests
compare their Git blob ids so later release work cannot silently fork them.
"""

from __future__ import annotations


SOURCE_CORE_SOURCE_REPOSITORY = "Hongyi-Du/SocioGenesis"
SOURCE_CORE_COMMIT = "041ddee1aa109a9b65dfdad7bdb8e258ad0a293e"
SOURCE_CORE_SOURCE_PATH = "organization_core"

SOURCE_CORE_FILE_BLOBS: dict[str, str] = {
    "organization_core/__init__.py": "1158534ffd02ac18f911666a4da97f65063a78df",
    "organization_core/approval.py": "e7b8f0819f12af323cf432879bb79950175b5d2d",
    "organization_core/conformance.py": "19c0e994046cb1ce0ab4d3a19828592e8adb2289",
    "organization_core/contracts.py": "3b24a843ca1ac13fe6a228cb20f2a43b7de4e291",
    "organization_core/decision.py": "ff7bec3414fc9da71810305531adc6fe1a743d22",
    "organization_core/evidence.py": "a21cfbbee7063a2ffc9b8678f5e16bd393ea9b6b",
    "organization_core/formation.py": "e75d49f1206bca8cd4e9e3118eb25cd62561138e",
    "organization_core/gates.py": "d4541e79e688e181119bd1d233623f36d6e07481",
    "organization_core/host.py": "d34bd6062510d3be2bd74de63711781bdaf2b589",
    "organization_core/module.py": "7fa774288c0b0ad64a382031f57a89941c01dbf5",
    "organization_core/proposals.py": "64f74591f4d1a528fe0a4d568384c0458b5408ca",
    "organization_core/repair.py": "c1df8c83997ebe507cacd68a2ca78445544f193f",
    "organization_core/routing.py": "e5397f8c0f88a5ed7bb1130b5b95a43bb1e4b4f1",
    "organization_core/runtime.py": "f21ec26ed54ef7a880e24db514a262976a753d96",
    "organization_core/selection.py": "69ab1c699683d1656b57c72c1561590ff44307bf",
    "organization_core/state.py": "526846bbf545fbb0664f8ec9dea71bc48e0378a0",
    "organization_core/synthesis.py": "d1088d1ff7c94ee20bbb1f0de1a8f87184cf5b49",
}


def source_core_provenance() -> dict[str, object]:
    """Return a detached manifest suitable for a run record or audit report."""

    return {
        "source_repository": SOURCE_CORE_SOURCE_REPOSITORY,
        "source_commit": SOURCE_CORE_COMMIT,
        "source_path": SOURCE_CORE_SOURCE_PATH,
        "file_count": len(SOURCE_CORE_FILE_BLOBS),
        "vendoring": "byte_exact_git_blobs",
    }


__all__ = [
    "SOURCE_CORE_COMMIT",
    "SOURCE_CORE_FILE_BLOBS",
    "SOURCE_CORE_SOURCE_PATH",
    "SOURCE_CORE_SOURCE_REPOSITORY",
    "source_core_provenance",
]
