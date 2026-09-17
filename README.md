# Relic Agent

Relic Agent is the standalone, source-native B3 organization runtime extracted
from Relic.  The shipped CLI builds the real vendored `OrgWorld` and advances
it with `OrgWorld.step()`; public output is a privacy-filtered
`relic-trace-v1` projection for replay and Relic Inspector.

This repository is not the paper reproduction repository.  It does not ship or
run a paper benchmark, ProgramBench, CooperBench, an external evaluator, the
HCI human-seat application, NatureEnv, or a live provider workflow on its
default path.

The current release is a fixed source-native B3 host, not yet a fully generic
organization builder: it supports changing the public organization label,
seed, and tick count, but rejects arbitrary agent rosters, roles, models,
tools, governance, tasks, protocols, SDL/CLG interventions, and source
scenarios.  See [the extension guide](docs/extension_guide.md) for the exact
boundary and the work required to make that interface real rather than a
partial translation.

## Quickstart / 快速开始

The supplied runs are deterministic and make **zero model-provider calls**.
No API key or GPU is required for `check-env`, `smoke`, `run-minimal`,
`run-default`, replay, or Inspector.

### Linux / WSL2 native

```bash
git clone https://github.com/Hongyi-Du/Relic-Agent.git relic-agent
cd relic-agent
uv sync --extra dev --frozen
cp .env.example .env
uv run relic-agent check-env
uv run relic-agent smoke
uv run relic-agent run-default
```

`smoke` runs 12 ticks in a temporary directory.  `run-default` runs the
bundled 72-tick configuration and writes
`outputs/<run-id>/{config.yaml,status.json,run.json,trace.json}`.  Open a
completed trace locally:

```bash
uv run relic-agent inspect --trace outputs/<run-id>/trace.json --mode replay
```

Then visit <http://127.0.0.1:8765>.  Inspector serves only public trace data;
private workspaces, prompts, memories, raw reflections, and provider traffic
are not exported.

### Windows PowerShell → WSL2

Native Windows Python is not supported.  Clone and install inside the Linux
filesystem of WSL2, not under `/mnt/c`.  From Windows PowerShell, either call
the canonical Bash wrappers directly:

```powershell
wsl.exe -d Ubuntu --cd /home/<user>/relic-agent -- bash scripts/bash/check_env.sh
wsl.exe -d Ubuntu --cd /home/<user>/relic-agent -- bash scripts/bash/smoke.sh
wsl.exe -d Ubuntu --cd /home/<user>/relic-agent -- bash scripts/bash/run_default.sh
```

Or, from a PowerShell working directory that can access that checkout, use the
thin forwarding wrappers:

```powershell
$env:RELIC_AGENT_WSL_REPO = "/home/<user>/relic-agent"
.\scripts\powershell\check_wsl.ps1 -Distro Ubuntu
.\scripts\powershell\smoke.ps1 -Distro Ubuntu
.\scripts\powershell\run_default.ps1 -Distro Ubuntu
.\scripts\powershell\start_inspector.ps1 -Distro Ubuntu `
  -ForwardedArguments @("--trace", "outputs/<run-id>/trace.json")
```

PowerShell is a launcher only; it passes arguments to the same WSL Bash/Python
CLI and contains no separate runtime logic.

### Docker / Compose

```bash
git clone https://github.com/Hongyi-Du/Relic-Agent.git relic-agent
cd relic-agent
cp .env.example .env
mkdir -p outputs
docker compose build relic-agent-runtime
docker compose run --rm relic-agent-runtime check-env
docker compose run --rm relic-agent-runtime run-minimal --output-root /data/runs
docker compose run --rm relic-agent-runtime run-default --output-root /data/runs
docker compose up relic-inspector
```

Compose exposes Inspector only at `127.0.0.1:${RELIC_AGENT_INSPECTOR_PORT:-8765}`
on the host.  Native, Bash, PowerShell, and Docker entry points all invoke the
same `relic-agent` CLI.

## Bundled commands

| Command | Provider cost | Purpose |
|---|---:|---|
| `relic-agent check-env` | none | Check Python, YAML, config, source-host blobs, Inspector assets, and the bundled replay. |
| `relic-agent smoke` | none | Run the 12-tick source-native installation smoke. |
| `relic-agent run-minimal` | none | Write a short 12-tick source-native demo run. |
| `relic-agent run-default` | none | Write the canonical 72-tick local lifecycle-evidence run. |
| `relic-agent replay-example` | none | Validate the bundled sanitized replay. |
| `relic-agent inspect-example` | none | Open Inspector with the bundled replay. |

There is no supported high-cost/live-provider command in this release.  A
successful `workflow_acceptance: passed` means only that a run recorded real
source action, episode, reflection, proposal, and protocol-spec evidence.  It
is not an evaluator, benchmark, paper, or publication acceptance result.

## Configuration and customization

`configs/default.yaml` and `configs/minimal.yaml` use
`relic-agent-source-native-v1`.  They select the shipped `org_default` B3
scenario, deterministic seed, and tick count.  The source roster, task list,
governance, policy, and provider are source-owned.  A generic roster/task YAML
is rejected rather than silently translated into another runtime.

The default source agent ID `scarlett` remains stable for compatibility; its
public display name is **Los Xi**.  See:

- [Configuration](docs/configuration.md) for accepted keys and precedence;
- [Extension guide](docs/extension_guide.md) for supported and unsupported
  changes; and
- [Known release gaps](docs/KNOWN_RELEASE_GAPS.md) for the explicit generic
  organization/API gap.

## Environment configuration

Python 3.12+ and `uv` are required for the documented native path.  Inspector
is static HTML/CSS/JavaScript and requires no Node.js build or runtime.  No API
key, model configuration, cache path, concurrency setting, or external
benchmark path is used by the shipped deterministic workflow.

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `RELIC_AGENT_OUTPUT_ROOT` | No | `outputs` | Default parent for new run directories. |
| `RELIC_AGENT_INSPECTOR_PORT` | Compose only | `8765` | Host port for the Compose Inspector service. |
| `RELIC_AGENT_UID` | Docker only | `1000` | Host UID used by Compose bind mounts. |
| `RELIC_AGENT_GID` | Docker only | `1000` | Host GID used by Compose bind mounts. |

Configuration precedence is: explicit CLI option, `.env`/environment variable,
then the repository default.  `--output-root` therefore overrides
`RELIC_AGENT_OUTPUT_ROOT`.  See [installation](docs/installation.md) and
[Docker](docs/docker.md) for details.

## Supported platforms

| Platform | Support level | Recommended path |
|---|---|---|
| Linux | Full | Native `uv` / Bash or Docker / Compose |
| Windows 11 + WSL2 | Full | WSL2 Linux runtime, with optional PowerShell forwarding wrapper |
| Windows native PowerShell | Launcher only | Invoke WSL2; do not run the Python runtime natively |
| Docker Desktop on Windows | Full with WSL2 backend | Docker / Compose from the WSL2 workflow |
| macOS | Not release-validated | Docker may work, but is not claimed as a supported path |

Keep the checkout in the Linux filesystem (for example
`~/Projects/relic-agent`), rather than `/mnt/c`.  There is no multi-worker or
high-memory benchmark launcher in this repository, so the paper-repository
parallel-cell memory guidance does not apply to these bundled commands.

## Tests and `full-tests`

`main` contains the release-focused agent-runtime tests.  Run them locally
with:

```bash
uv run pytest
uv run ruff check .
```

This repository intentionally has no `full-tests` branch at the current
untagged release state.  Unlike the paper repository, Relic Agent does not use
the four-branch HCI/Cooper layout.  If a future release adds an agent-runtime
`full-tests` branch, it must be created from the corresponding release tag,
contain only agent-runtime regression tests, preserve `main` as the source of
truth, and classify slow/live/Docker tests so ordinary `pytest` never calls a
real provider.  It must not import paper, HCI, Cooper, or benchmark tests.

## Documentation

Start with [docs/README.md](docs/README.md).  The exact final execution record
is [REPRODUCTION_RUN_REPORT.md](docs/REPRODUCTION_RUN_REPORT.md); it is a
factual run report, not a replacement for unperformed fresh-clone or full-suite
validation.

## 中文说明

Relic Agent 当前运行的是 vendored、source-native 的 B3 `OrgWorld`：CLI 直接
调用 `OrgWorld.step()`，再导出经过隐私过滤的 `relic-trace-v1` 供 Inspector
回放。本仓库不是论文 benchmark 复现仓库，不包含 ProgramBench、CooperBench、
外部 evaluator、HCI 人类座位、NatureEnv 或默认在线模型调用。

当前版本还不是可任意替换成员、角色、模型、工具、治理规则、初始 protocol 或
SDL/CLG 的通用组织构建器。它只支持更改公开组织标签、seed 和 ticks；任意 roster/
task YAML 会 fail-closed。具体边界请看[扩展指南](docs/extension_guide.md)和
[已知发布缺口](docs/KNOWN_RELEASE_GAPS.md)。

### Linux / WSL2 可复制命令

```bash
git clone https://github.com/Hongyi-Du/Relic-Agent.git relic-agent
cd relic-agent
uv sync --extra dev --frozen
cp .env.example .env
uv run relic-agent check-env
uv run relic-agent smoke
uv run relic-agent run-default
uv run relic-agent inspect --trace outputs/<run-id>/trace.json --mode replay
```

### Docker 可复制命令

```bash
cp .env.example .env
mkdir -p outputs
docker compose build relic-agent-runtime
docker compose run --rm relic-agent-runtime check-env
docker compose run --rm relic-agent-runtime run-default --output-root /data/runs
docker compose up relic-inspector
```

这些默认命令均不需要 API key、GPU，也不会产生模型调用费用。`run-default` 的
输出在 `outputs/<run-id>/`，包括配置快照、状态、`run.json` 和公开 trace。Windows
请经 WSL2 运行同一套 Bash/Python CLI；PowerShell 仅作为转发 wrapper，不支持原生
Windows Python runtime。详见上方英文的 Windows/WSL2 命令、
[安装文档](docs/installation.md)和[Docker 文档](docs/docker.md)。
