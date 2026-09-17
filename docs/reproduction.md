# Reproduction guide

This guide reproduces the standalone source-native B3 release behavior.  It
does not reproduce a paper benchmark, a participant study, CooperBench,
ProgramBench, or an official evaluator score.

## Native Linux / WSL2

```bash
uv sync --extra dev --frozen
cp .env.example .env
uv run relic-agent check-env
uv run relic-agent smoke
uv run relic-agent run-minimal
uv run relic-agent run-default
uv run relic-agent replay-example
uv run relic-agent inspect --trace outputs/<run-id>/trace.json --mode replay
```

`check-env`, `smoke`, `run-minimal`, `run-default`, and `replay-example` use
the shipped deterministic source-native path and make no model-provider calls.
`smoke` uses a temporary output directory unless `--output-root` is supplied.
The other run commands write a self-contained directory containing
`config.yaml`, `status.json`, `run.json`, and `trace.json` below `outputs/` by
default.

The 12-tick minimal run is an installation/demo path.  It may correctly report
`workflow_acceptance: unavailable_fail_closed` when the source lifecycle has
not yet formed a proposal and protocol.  The 72-tick default run is the
shipped local lifecycle-evidence path; its result is still not a benchmark or
publication claim.

## Docker

```bash
mkdir -p outputs
docker compose build relic-agent-runtime
docker compose run --rm relic-agent-runtime check-env
docker compose run --rm relic-agent-runtime smoke --output-root /data/runs
docker compose run --rm relic-agent-runtime run-minimal --output-root /data/runs
docker compose run --rm relic-agent-runtime run-default --output-root /data/runs
docker compose run --rm relic-agent-runtime replay-example
docker compose up relic-inspector
```

Compose maps the Inspector to loopback only.  See [Docker](docker.md) for
bind-mount UID/GID setup and [Inspector](inspector.md) for replay/live modes.

## What this verifies

The commands verify the checked-in environment, source-host blob pin,
source-native `OrgWorld` execution, output writing, public trace validation,
and Inspector/replay entry points.  They do not substitute for a fresh-clone
release check, a full test suite, a real provider run, or an external
evaluation.  The exact final execution evidence and commands are recorded in
[REPRODUCTION_RUN_REPORT.md](REPRODUCTION_RUN_REPORT.md).
