import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
BASH_DIR = ROOT / "scripts" / "bash"
POWERSHELL_DIR = ROOT / "scripts" / "powershell"

BASH_COMMANDS = {
    "check_env.sh": "check-env",
    "smoke.sh": "smoke",
    "run_default.sh": "run-default",
    "run_minimal.sh": "run-minimal",
    "replay_example.sh": "replay-example",
}

POWERSHELL_COMMANDS = {
    "check_wsl.ps1": "check_env.sh",
    "smoke.ps1": "smoke.sh",
    "run_default.ps1": "run_default.sh",
    "run_minimal.ps1": "run_minimal.sh",
    "replay_example.ps1": "replay_example.sh",
}


@pytest.mark.release
def test_bash_wrappers_are_executable_thin_cli_forwarders() -> None:
    for file_name, command in BASH_COMMANDS.items():
        path = BASH_DIR / file_name
        mode = path.stat().st_mode
        text = path.read_text(encoding="utf-8")

        assert mode & stat.S_IXUSR
        assert f"relic-agent {command}" in text
        assert '"$@"' in text
        subprocess.run(["bash", "-n", str(path)], check=True)


@pytest.mark.release
def test_bash_wrapper_resolves_repo_from_an_external_working_directory() -> None:
    completed = subprocess.run(
        [str(BASH_DIR / "replay_example.sh")],
        cwd="/tmp",
        check=False,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["status"] == "passed"


@pytest.mark.release
def test_powershell_wrappers_parse_and_only_forward_to_wsl() -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("pwsh is not installed")
    parser = (
        "$tokens=$null; $errors=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "$env:RELIC_AGENT_PS_TARGET,[ref]$tokens,[ref]$errors) | Out-Null; "
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
    )
    for path in sorted(POWERSHELL_DIR.glob("*.ps1")):
        env = os.environ.copy()
        env["RELIC_AGENT_PS_TARGET"] = str(path)
        completed = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-Command", parser],
            check=False,
            text=True,
            capture_output=True,
            env=env,
            timeout=30,
        )
        assert completed.returncode == 0, f"{path.name}: {completed.stderr}"

    common = (POWERSHELL_DIR / "common.ps1").read_text(encoding="utf-8")
    assert 'Get-Command "wsl.exe"' in common
    assert '"bash", "scripts/bash/$ScriptName"' in common
    for file_name, bash_script in POWERSHELL_COMMANDS.items():
        text = (POWERSHELL_DIR / file_name).read_text(encoding="utf-8")
        assert f'-ScriptName "{bash_script}"' in text
        assert "relic-agent" not in text.lower()


@pytest.mark.release
def test_docker_and_compose_call_the_canonical_cli() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["relic-agent-runtime"]

    assert 'ENTRYPOINT ["relic-agent"]' in dockerfile
    assert 'CMD ["run-default", "--output-root", "/data/runs"]' in dockerfile
    assert service["command"] == ["run-default", "--output-root", "/data/runs"]
    assert service["user"] == "${RELIC_AGENT_UID:-1000}:${RELIC_AGENT_GID:-1000}"
    assert service["read_only"] is True
    assert "./outputs:/data/runs" in service["volumes"]
