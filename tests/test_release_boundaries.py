from pathlib import Path

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
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in sorted(ROOT.rglob("*"))
        if path.is_file()
        and ".git" not in path.parts
        and "tests" not in path.parts
        and path.suffix in {".py", ".md", ".yaml", ".yml", ".json"}
    ).lower()

    for banned in BANNED_IDENTITIES:
        assert banned not in text
