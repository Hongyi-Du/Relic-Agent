from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.release
def test_public_release_entrypoints_describe_shared_runtime_and_presets() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    configuration = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    engine = (ROOT / "relic_agent" / "runtime" / "engine.py").read_text(encoding="utf-8")

    assert "OrgWorld.step()" in readme
    assert "relic-agent-source-native-v1" in configuration
    assert "compatibility trace shell" not in readme.lower()
    assert "relic-agent-v2" in configuration
    assert "generic" in architecture.lower()
    assert '"authority": "source_native_orgworld"' in engine
    assert "does not select actions" in architecture


@pytest.mark.release
def test_vendor_and_archived_paths_are_explicitly_documented_as_non_public_apis() -> None:
    provenance = (ROOT / "docs" / "SOURCE_PROVENANCE.md").read_text(encoding="utf-8")
    core_init = (ROOT / "relic_agent" / "core" / "__init__.py").read_text(encoding="utf-8")

    assert "Deliberately excluded capabilities" in provenance
    assert "Archived compatibility material" in provenance
    assert "CLI process does not import them" in provenance
    assert "lazy compatibility boundary" in core_init
