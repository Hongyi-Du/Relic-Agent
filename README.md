# Relic Agent

> **Run your own Relic organization.**

Relic Agent is a release-facing shell around a source-first organization-core
extraction. It is not the paper benchmark or a claim of complete B3/HCI
reproduction. The canonical portable contracts/state/host seam is vendored
from the audited source revision; the deterministic mock lifecycle remains only
to keep the CLI, public trace, Inspector, Docker path, and wrappers runnable.

The mock lifecycle is explicitly non-authoritative. It is observed through the
source-core event contract, and every `run.json` says that active HCI execution
is unavailable/fail-closed. Do not treat a successful mock run as a paper
result. See [source provenance and boundary](docs/SOURCE_PROVENANCE.md).

## Quickstart

Python 3.12+ and `uv` are required. Linux is the canonical runtime; Windows
users should work inside WSL2.

```bash
uv sync --extra dev --frozen
cp .env.example .env
uv run relic-agent check-env
uv run relic-agent smoke
uv run relic-agent run-minimal
uv run relic-agent run-default
uv run relic-agent replay-example
```

Open the bundled no-cost lifecycle in the public organization observatory:

```bash
uv run relic-agent inspect-example
```

Then open <http://127.0.0.1:8765>. To inspect a new run:

```bash
uv run relic-agent inspect \
  --trace outputs/<run-id>/trace.json \
  --mode replay
```

Equivalent Bash wrappers are available under `scripts/bash/`.

Docker quickstart:

```bash
mkdir -p outputs
docker compose build relic-agent-runtime
docker compose run --rm relic-agent-runtime check-env
docker compose run --rm relic-agent-runtime smoke --output-root /data/runs
docker compose run --rm relic-agent-runtime run-default --output-root /data/runs
docker compose up relic-inspector
```

The smoke and bundled replay make zero provider calls. Runs are written below
`outputs/<run-id>/` unless `--output-root` or `RELIC_AGENT_OUTPUT_ROOT` selects
another location.

Each run directory contains:

- `config.yaml`: exact organization config snapshot;
- `run.json`: run identity, timestamps, hashes, status, and summary;
- `status.json`: terminal status marker;
- `trace.json`: digest-bound `relic-trace-v1` public replay.

Private reflections, private memories, and provider messages are excluded from
the public trace. A public proposal can keep opaque lineage identifiers without
publishing the private reflection text that motivated it.

The Inspector synchronizes its Timeline, organization snapshot, Object
Inspector, and State Diff at event-level frames. Optional artifact, repository,
PR/CI, and evaluation panels say when records were not published rather than
treating missing data as zero. See [Inspector](docs/inspector.md).

## Run, replay, customize

Run an explicit organization config:

```bash
uv run relic-agent run \
  --config configs/minimal.yaml \
  --output-root outputs/minimal
```

Validate and summarize a generated trace:

```bash
uv run relic-agent replay \
  --trace outputs/minimal/<run-id>/trace.json
```

Copy `configs/minimal.yaml` and change the members, display names, roles,
profiles, skills, tools, tasks, governance thresholds, seed, or runtime length.
The loader is fail-closed: unknown fields, duplicate IDs, invalid owners,
out-of-range scores, and unsupported providers are rejected before a run starts.

See [installation](docs/installation.md),
[configuration](docs/configuration.md), [Docker](docs/docker.md),
[Inspector](docs/inspector.md), and [architecture](docs/architecture.md) for the
current supported surface.

## Platform support

| Platform | Current support | Recommended path |
|---|---|---|
| Linux | Full for this milestone | Native Python CLI |
| Windows 11 + WSL2 | Full for this milestone | WSL2 + Python CLI |
| Windows native PowerShell | Launcher only | PowerShell invokes WSL2 |
| Docker on Linux / Docker Desktop | Full for this milestone | Linux container |
| macOS | Best effort | Native CLI if checks pass |

## Full regression test suite / 完整回归测试

`main` contains the release-focused runtime, replay, Inspector, wrapper, and
Docker-boundary tests for this milestone. The default `uv run pytest` command
does not contact a model provider. No current default-suite test is marked
`live`, `llm`, `slow`, or `docker`; future tests using those markers must stay
opt-in because they may require credentials, substantial runtime, or a Docker
daemon.

The handoff allows a future `full-tests` branch for sanitized historical
agent-runtime regressions, but no such branch is present in this release
snapshot. If it is published later, it must be based on the matching release
commit, add test depth rather than a second runtime implementation, and exclude
paper benchmark tests, obsolete systems, private fixtures, credentials, and
development-machine paths.

`main` 包含本阶段默认执行的 release-focused tests，`uv run pytest` 默认不会
调用模型 provider。当前默认测试集没有标记为 `live`、`llm`、`slow` 或
`docker` 的测试；未来使用这些 marker 的测试必须只由用户显式启用。交接文档
允许未来建立保存清理后 agent-runtime 历史回归测试的
`full-tests` 分支；当前 release 快照尚未发布该分支，因此这里不提供会失败的
切换命令。

## 中文说明

> **运行你自己的 Relic organization。**

`relic-agent` 是一个面向发布的壳层，围绕 source-first 的 organization-core
抽取构建；它不是论文 benchmark，也不宣称已完整复现 B3/HCI。权威的通用
contracts/state/host seam 来自经审计的源代码；现有 deterministic mock lifecycle
仅用于保持 CLI、公开 trace、Inspector、Docker 和 wrappers 可运行。

mock lifecycle 被明确标记为非权威：其事件会经过 source-core contract 校验，
每个 `run.json` 都会声明 active HCI execution 为 unavailable/fail-closed。成功的
mock run 不能当作论文结果。详见
[source provenance and boundary](docs/SOURCE_PROVENANCE.md)。

当前可直接复制运行：

```bash
uv sync --extra dev --frozen
cp .env.example .env
uv run relic-agent check-env
uv run relic-agent smoke
uv run relic-agent run-minimal
uv run relic-agent run-default
uv run relic-agent replay-example
```

启动 bundled replay Inspector（无模型费用）：

```bash
uv run relic-agent inspect-example
```

然后在 Windows 或 Linux 浏览器打开 <http://127.0.0.1:8765>。查看用户新 run：

```bash
uv run relic-agent inspect --trace outputs/<run-id>/trace.json --mode replay
```

Windows 用户的正式路径是 **Windows → WSL2 → Linux runtime**。新版 Inspector
已经支持 bundled replay、用户 trace 和逐 tick 原子更新的 live trace；live model
provider adapter 尚未实现。Docker、Bash wrapper 已实机验证；PowerShell wrapper
已通过 PowerShell 7.6.6 语法和转发契约测试，但 Windows 宿主到 WSL 的端到端
fresh-clone 流程仍需在发布前人工验收。
