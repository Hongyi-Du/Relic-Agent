"""Source-native B3 host for the Relic-Agent release surface.

This package deliberately mounts the vendored Relic B3 ``OrgWorld`` rather
than translating it into the former small release-shell state machine.
"""

from relic_agent.source_host.projection import project_public_frame
from relic_agent.source_host.provenance import (
    ARCHIVED_COMPAT_MODULE_PREFIXES,
    SOURCE_B3_COMMIT,
    SOURCE_B3_IS_ANCESTOR_OF_HCI,
    SOURCE_HCI_COMMIT,
    STRUCTURAL_GOLDENS,
    archived_compat_modules_loaded,
    assert_no_forbidden_loaded_modules,
    source_host_provenance,
    structural_conformance,
    verify_critical_vendor_blobs,
)

__all__ = [
    "ARCHIVED_COMPAT_MODULE_PREFIXES",
    "SOURCE_B3_COMMIT",
    "SOURCE_B3_IS_ANCESTOR_OF_HCI",
    "SOURCE_HCI_COMMIT",
    "STRUCTURAL_GOLDENS",
    "archived_compat_modules_loaded",
    "assert_no_forbidden_loaded_modules",
    "project_public_frame",
    "source_host_provenance",
    "structural_conformance",
    "verify_critical_vendor_blobs",
]
