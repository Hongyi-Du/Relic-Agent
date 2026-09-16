"""Local test harness for the byte-exact source-core suite.

The copied suite contains one cross-package re-export assertion. Its required
``society_core`` package is intentionally outside this extraction boundary, so
the source test is preserved byte-for-byte but skipped here rather than
replaced with a local stand-in.
"""

from __future__ import annotations

import pytest


_OUT_OF_SCOPE_REEXPORT = (
    "tests/organization_core/test_evidence.py::"
    "test_society_core_reexports_the_shared_evidence_types"
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.nodeid == _OUT_OF_SCOPE_REEXPORT:
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        "society_core is intentionally excluded; this release "
                        "vendors only the dependency-free organization_core"
                    )
                )
            )
