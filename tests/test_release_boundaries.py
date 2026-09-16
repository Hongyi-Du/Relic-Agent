from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PROVENANCE_FILES = {
    ROOT / "relic_agent" / "core" / "provenance.py",
    ROOT / "docs" / "SOURCE_PROVENANCE.md",
}
SOURCE_PORT_ROOTS = {
    ROOT / "relic_agent" / "source_b3",
}
BANNED_IDENTITIES = (
    "sociogenesis",
    "natureenv",
    "evolving agent",
    "programbench",
    "cooperbench",
    "lanternforge",
    "lanternscout",
)


@pytest.mark.release
def test_public_runtime_has_no_paper_or_legacy_identity_leaks() -> None:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    candidates = [ROOT / line for line in completed.stdout.splitlines() if line]
    text_suffixes = {
        ".css",
        ".example",
        ".html",
        ".js",
        ".json",
        ".lock",
        ".md",
        ".ps1",
        ".py",
        ".sh",
        ".toml",
        ".yaml",
        ".yml",
    }
    text_names = {"Dockerfile", ".dockerignore", ".gitattributes"}
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in sorted(candidates)
        if path.is_file()
        and "tests" not in path.parts
        # Attribution is required for vendored source and is not a product
        # identity leak. It stays constrained to these audit-only files.
        and path not in SOURCE_PROVENANCE_FILES
        and not any(root in path.parents for root in SOURCE_PORT_ROOTS)
        and path.name not in {"LICENSE", "COMMERCIAL_LICENSE.md"}
        and (path.suffix in text_suffixes or path.name in text_names)
    ).lower()

    for banned in BANNED_IDENTITIES:
        assert banned not in text
