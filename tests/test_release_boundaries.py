from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
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
        and path.name not in {"LICENSE", "COMMERCIAL_LICENSE.md"}
        and (path.suffix in text_suffixes or path.name in text_names)
    ).lower()

    for banned in BANNED_IDENTITIES:
        assert banned not in text
