"""Release-boundary checks for the active source-native runtime."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from relic_agent.source_host import (
    SOURCE_B3_COMMIT,
    SOURCE_B3_IS_ANCESTOR_OF_HCI,
    SOURCE_HCI_COMMIT,
    source_host_provenance,
    verify_critical_vendor_blobs,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.release
def test_source_host_provenance_records_hci_ancestry_and_parity_claim() -> None:
    provenance = source_host_provenance()

    assert SOURCE_B3_IS_ANCESTOR_OF_HCI is True
    assert provenance["source_commit"] == SOURCE_B3_COMMIT
    assert provenance["comparison_hci_commit"] == SOURCE_HCI_COMMIT
    assert provenance["source_commit_is_ancestor_of_comparison_hci_commit"] is True
    reference = provenance["conformance_reference"]
    assert reference["ticks"] == 336
    assert reference["seeds"] == [17, 701, 2026]
    assert reference["comparison_hci_structures_identical"] is True
    assert verify_critical_vendor_blobs()["verified"] is True


@pytest.mark.release
def test_fresh_source_native_process_does_not_import_archived_compatibility_runtime() -> None:
    script = """
import json
import sys
import tempfile
from pathlib import Path
from relic_agent.config import load_config
from relic_agent.runtime import OrganizationRuntime

with tempfile.TemporaryDirectory() as directory:
    result = OrganizationRuntime(load_config(Path('configs/source-b3.yaml'))).run(
        output_root=directory, ticks=1, run_id='fresh-import-boundary'
    )
    manifest = json.loads(result.manifest_path.read_text(encoding='utf-8'))
    prefixes = ('organization_core', 'relic_agent.source_b3', 'relic_agent.source_core',
                'relic_agent.governance', 'relic_agent.events')
    loaded = sorted(name for name in sys.modules if any(
        name == prefix or name.startswith(prefix + '.') for prefix in prefixes
    ))
    print(json.dumps({'loaded': loaded, 'manifest': manifest['source']['loaded_module_boundary']}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
    )
    report = json.loads(completed.stdout)
    assert report["loaded"] == []
    assert report["manifest"]["forbidden_modules_loaded"] == []
    assert report["manifest"]["archived_compat_modules_loaded"] == []
