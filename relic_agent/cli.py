"""Canonical command-line interface for every Relic Agent launcher."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv

from relic_agent.config import ConfigError, load_config
from relic_agent.replay import load_trace
from relic_agent.runtime import OrganizationRuntime


def _source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _bundled_path(kind: str, name: str) -> Path:
    source_candidate = _source_root() / kind / name
    if source_candidate.is_file():
        return source_candidate
    installed_dir = {
        "configs": "bundled_configs",
        "examples/replay": "bundled_replay",
    }[kind]
    installed_candidate = Path(__file__).resolve().parent / installed_dir / name
    if installed_candidate.is_file():
        return installed_candidate
    raise FileNotFoundError(f"bundled asset not found: {kind}/{name}")


def _default_output_root() -> Path:
    return Path(os.environ.get("RELIC_AGENT_OUTPUT_ROOT", "outputs"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="relic-agent")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check-env", help="validate the runtime and an organization config")
    check.add_argument("--config", type=Path, default=None)

    smoke = commands.add_parser("smoke", help="run a deterministic no-cost installation smoke")
    smoke.add_argument("--output-root", type=Path, default=None)

    run = commands.add_parser("run", help="run an organization from an explicit config")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output-root", type=Path, default=_default_output_root())
    run.add_argument("--ticks", type=int, default=None)

    default = commands.add_parser("run-default", help="run the bundled default organization")
    default.add_argument("--output-root", type=Path, default=_default_output_root())
    default.add_argument("--ticks", type=int, default=None)

    minimal = commands.add_parser("run-minimal", help="run the bundled minimal organization")
    minimal.add_argument("--output-root", type=Path, default=_default_output_root())
    minimal.add_argument("--ticks", type=int, default=None)

    replay = commands.add_parser("replay", help="validate and summarize a relic-trace-v1 file")
    replay.add_argument("--trace", type=Path, required=True)

    commands.add_parser("replay-example", help="validate the bundled no-cost lifecycle replay")
    return parser


def _environment_report(config_path: Path | None) -> dict:
    selected = config_path or _bundled_path("configs", "minimal.yaml")
    checks = [
        {
            "name": "python",
            "status": "pass" if sys.version_info >= (3, 12) else "fail",
            "details": {"required": "3.12", "found": platform.python_version()},
        },
        {
            "name": "yaml",
            "status": "pass" if importlib.util.find_spec("yaml") else "fail",
            "details": {},
        },
    ]
    try:
        config = load_config(selected)
    except (ConfigError, OSError) as exc:
        checks.append({"name": "config", "status": "fail", "details": {"reason": str(exc)}})
    else:
        checks.append(
            {
                "name": "config",
                "status": "pass",
                "details": {
                    "file_name": config.source_path.name,
                    "sha256": config.digest,
                    "provider": config.runtime.provider,
                },
            }
        )
    checks.extend(
        [
            {
                "name": "provider_credentials",
                "status": "skip",
                "details": {"reason": "mock provider does not require credentials"},
            },
            {
                "name": "inspector",
                "status": "warn",
                "details": {
                    "reason": "Inspector integration is scheduled for the next P1 milestone"
                },
            },
        ]
    )
    failed = any(check["status"] == "fail" for check in checks)
    return {
        "schema_version": "relic-agent-environment-report-v1",
        "status": "failed" if failed else "passed",
        "exit_code": 2 if failed else 0,
        "checks": checks,
    }


def _run(config_path: Path, output_root: Path, ticks: int | None) -> dict:
    config = load_config(config_path)
    result = OrganizationRuntime(config).run(output_root=output_root, ticks=ticks)
    return result.to_dict()


def _replay_summary(trace_path: Path) -> dict:
    trace = load_trace(trace_path)
    last = trace["frames"][-1]
    organization = last["organization"]
    return {
        "schema_version": "relic-agent-replay-summary-v1",
        "status": "passed",
        "run_id": trace["run_id"],
        "trace_sha256": trace["trace_sha256"],
        "ticks": len(trace["frames"]),
        "agents": len(organization["agents"]),
        "tasks": len(organization["tasks"]),
        "proposals": len(organization["proposals"]),
        "protocols": len(organization["protocols"]),
    }


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check-env":
            report = _environment_report(args.config)
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return int(report["exit_code"])
        if args.command == "smoke":
            if args.output_root is None:
                with tempfile.TemporaryDirectory(prefix="relic-agent-smoke-") as temporary:
                    result = _run(
                        _bundled_path("configs", "minimal.yaml"),
                        Path(temporary),
                        None,
                    )
            else:
                result = _run(_bundled_path("configs", "minimal.yaml"), args.output_root, None)
            result["smoke"] = {"provider_calls_made": 0, "model_cost": "none"}
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.command == "run":
            result = _run(args.config, args.output_root, args.ticks)
        elif args.command == "run-default":
            result = _run(_bundled_path("configs", "default.yaml"), args.output_root, args.ticks)
        elif args.command == "run-minimal":
            result = _run(_bundled_path("configs", "minimal.yaml"), args.output_root, args.ticks)
        elif args.command == "replay":
            result = _replay_summary(args.trace)
        elif args.command == "replay-example":
            result = _replay_summary(_bundled_path("examples/replay", "trace.json"))
        else:
            raise AssertionError(f"unhandled command: {args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (ConfigError, FileNotFoundError, ValueError, OSError) as exc:
        print(f"relic-agent: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
