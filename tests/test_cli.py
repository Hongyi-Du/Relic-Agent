import json
from pathlib import Path

import pytest

from relic_agent.cli import main


@pytest.mark.release
def test_check_env_and_smoke_are_no_cost(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check-env"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "passed"

    assert main(["smoke", "--output-root", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "completed"
    assert result["smoke"]["provider_calls_made"] == 0
    assert Path(result["trace_path"]).is_file()


@pytest.mark.release
def test_invalid_config_returns_friendly_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("schema_version: wrong\n", encoding="utf-8")

    assert main(["run", "--config", str(invalid), "--output-root", str(tmp_path / "out")]) == 2
    captured = capsys.readouterr()
    assert "relic-agent:" in captured.err
    assert "Traceback" not in captured.err
