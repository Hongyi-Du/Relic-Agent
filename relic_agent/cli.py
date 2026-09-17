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
from relic_agent.inspector import inspector_static_root, serve_inspector
from relic_agent.replay import load_trace
from relic_agent.runtime import OrganizationRuntime
from relic_agent.source_host import verify_critical_vendor_blobs


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

    init = commands.add_parser("init", help="create a configurable two-agent project")
    init.add_argument("directory", type=Path)
    validate = commands.add_parser("validate", help="validate configuration without running agents")
    validate.add_argument("--config", type=Path, required=True)
    source = commands.add_parser("run-source-b3", help="run the canonical source B3 preset")
    source.add_argument("--output-root", type=Path, default=_default_output_root())
    source.add_argument("--ticks", type=int, default=None)
    source.add_argument("--run-id", default=None)

    smoke = commands.add_parser("smoke", help="run a deterministic no-cost installation smoke")
    smoke.add_argument("--output-root", type=Path, default=None)

    run = commands.add_parser("run", help="run an organization from an explicit config")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output-root", type=Path, default=_default_output_root())
    run.add_argument("--ticks", type=int, default=None)
    run.add_argument("--run-id", default=None)

    default = commands.add_parser("run-default", help="run the bundled default organization")
    default.add_argument("--output-root", type=Path, default=_default_output_root())
    default.add_argument("--ticks", type=int, default=None)
    default.add_argument("--run-id", default=None)

    minimal = commands.add_parser("run-minimal", help="run the bundled minimal organization")
    minimal.add_argument("--output-root", type=Path, default=_default_output_root())
    minimal.add_argument("--ticks", type=int, default=None)
    minimal.add_argument("--run-id", default=None)

    replay = commands.add_parser("replay", help="validate and summarize a relic-trace-v1 file")
    replay.add_argument("--trace", type=Path, required=True)

    commands.add_parser("replay-example", help="validate the bundled no-cost lifecycle replay")

    inspect = commands.add_parser("inspect", help="open the public Inspector for a trace")
    inspect.add_argument("--trace", type=Path, required=True)
    _add_inspector_arguments(inspect)

    inspect_example = commands.add_parser(
        "inspect-example", help="open the public Inspector with the bundled lifecycle replay"
    )
    _add_inspector_arguments(inspect_example)
    return parser


def _add_inspector_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--mode", choices=("replay", "live"), default="replay")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="explicitly allow an unauthenticated non-loopback bind",
    )
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--verbose", action="store_true")


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
    config = None
    load_dotenv(selected.resolve().parent / ".env", override=False)
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
    if config is not None and config.schema_version == "relic-agent-v2":
        data = config.data
        active = {agent["provider"] for agent in data.get("agents", [])}
        missing = []
        for name in active:
            provider = data["providers"][name]
            if provider.get("type") in {"mock", "deterministic"}:
                continue
            for key in ("api_key_env", "base_url_env"):
                reference = provider.get(key)
                if reference and not os.environ.get(reference):
                    missing.append(reference)
        checks.append({"name": "provider_credentials", "status": "warn" if missing else "pass",
                       "details": {"missing_environment_variables": sorted(set(missing)),
                                   "provider_calls_made": 0}})
    else:
        blob_verification = verify_critical_vendor_blobs()
        checks.append({"name": "source_host_blobs",
                       "status": "pass" if blob_verification["verified"] else "warn",
                       "details": {"source_commit": blob_verification["source_commit"],
                                   "mismatch_count": len(blob_verification["mismatches"])}})
        checks.append({"name": "provider_credentials", "status": "skip",
                       "details": {"reason": "source preset does not require provider credentials"}})
    try:
        assets = inspector_static_root()
        replay = load_trace(_bundled_path("examples/replay", "trace.json"))
    except (FileNotFoundError, OSError, ValueError) as exc:
        checks.append({"name": "inspector", "status": "fail", "details": {"reason": str(exc)}})
    else:
        checks.append(
            {
                "name": "inspector",
                "status": "pass",
                "details": {
                    "assets": sorted(path.name for path in assets.iterdir() if path.is_file()),
                    "trace_schema": replay["schema_version"],
                    "runtime": "bundled-static",
                },
            }
        )
    failed = any(check["status"] == "fail" for check in checks)
    return {
        "schema_version": "relic-agent-environment-report-v1",
        "status": "failed" if failed else "passed",
        "exit_code": 2 if failed else 0,
        "checks": checks,
    }


def _init_project(directory: Path) -> dict:
    directory = directory.expanduser().resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("init requires an empty or new directory")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "organization.yaml").write_text(
        _bundled_path("configs", "minimal.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (directory / ".env.example").write_text(
        "MODEL_API_KEY=\nMODEL_BASE_URL=https://api.openai.com/v1\n", encoding="utf-8")
    (directory / "tools").mkdir(exist_ok=True)
    (directory / "tools" / "__init__.py").write_text("", encoding="utf-8")
    (directory / "prompts").mkdir(exist_ok=True)
    (directory / "prompts" / "instructions.txt").write_text(
        "Record deliverables and coordinate task ownership.\n", encoding="utf-8")
    (directory / ".gitignore").write_text(".env\noutputs/\n__pycache__/\n", encoding="utf-8")
    (directory / "README.md").write_text(
        "# Your organization\n\nEdit organization.yaml, then run:\n\n"
        "```sh\nrelic-agent validate --config organization.yaml\n"
        "relic-agent check-env --config organization.yaml\n"
        "relic-agent run --config organization.yaml --run-id first\n"
        "relic-agent inspect --trace outputs/first/trace.json\n```\n\n"
        "The default provider is deterministic and requires no credentials. "
        "For live models, configure a provider and its environment references; "
        "copy .env.example to .env and fill your own values.\n", encoding="utf-8")
    return {"status": "created", "directory": str(directory), "config": "organization.yaml"}


def _run(
    config_path: Path,
    output_root: Path,
    ticks: int | None,
    run_id: str | None = None,
) -> dict:
    load_dotenv(config_path.resolve().parent / ".env", override=False)
    config = load_config(config_path)
    result = OrganizationRuntime(config).run(output_root=output_root, ticks=ticks, run_id=run_id)
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
        "ticks": last["tick"],
        "frames": len(trace["frames"]),
        "agents": len(organization["agents"]),
        "tasks": len(organization["tasks"]),
        "proposals": len(organization["proposals"]),
        "protocols": len(organization["protocols"]),
    }


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            result = _init_project(args.directory)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "validate":
            config = load_config(args.config)
            result = {"status": "passed", "schema_version": config.schema_version,
                      "organization_id": config.organization_id, "sha256": config.digest}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
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
            result = _run(args.config, args.output_root, args.ticks, args.run_id)
        elif args.command == "run-source-b3":
            result = _run(_bundled_path("configs", "source-b3.yaml"),
                          args.output_root, args.ticks, args.run_id)
        elif args.command == "run-default":
            result = _run(
                _bundled_path("configs", "default.yaml"),
                args.output_root,
                args.ticks,
                args.run_id,
            )
        elif args.command == "run-minimal":
            result = _run(
                _bundled_path("configs", "minimal.yaml"),
                args.output_root,
                args.ticks,
                args.run_id,
            )
        elif args.command == "replay":
            result = _replay_summary(args.trace)
        elif args.command == "replay-example":
            result = _replay_summary(_bundled_path("examples/replay", "trace.json"))
        elif args.command in {"inspect", "inspect-example"}:
            trace_path = (
                args.trace
                if args.command == "inspect"
                else _bundled_path("examples/replay", "trace.json")
            )
            serve_inspector(
                trace_path=trace_path,
                host=args.host,
                port=args.port,
                mode=args.mode,
                open_browser=args.open_browser,
                verbose=args.verbose,
                allow_remote=args.allow_remote,
            )
            return 0
        else:
            raise AssertionError(f"unhandled command: {args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (ConfigError, FileNotFoundError, RuntimeError, ValueError, OSError) as exc:
        print(f"relic-agent: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
