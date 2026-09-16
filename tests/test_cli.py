import json
from pathlib import Path

import pytest

import relic_agent.cli as cli
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


@pytest.mark.release
def test_inspect_example_forwards_only_canonical_server_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received = {}

    def fake_serve(**kwargs) -> None:
        received.update(kwargs)

    monkeypatch.setattr(cli, "serve_inspector", fake_serve)

    assert (
        main(
            [
                "inspect-example",
                "--host",
                "0.0.0.0",
                "--port",
                "9012",
                "--mode",
                "live",
                "--allow-remote",
                "--verbose",
            ]
        )
        == 0
    )
    assert received == {
        "allow_remote": True,
        "host": "0.0.0.0",
        "mode": "live",
        "open_browser": False,
        "port": 9012,
        "trace_path": cli._bundled_path("examples/replay", "trace.json"),
        "verbose": True,
    }
