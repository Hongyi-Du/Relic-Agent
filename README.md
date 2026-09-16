# Relic Agent

> **Run your own Relic organization.**

Relic Agent is a general-purpose agent organization runtime distilled from the
Relic B3 implementation. It is not the paper benchmark or reproduction
repository. Organizations have persistent members, roles, task ownership,
profile-conditioned decisions, events, episodes, reflection, proposals,
governance, protocol lifecycles, and auditable replay.

This repository is currently in its first P1 extraction milestone. The
deterministic mock runtime, default/minimal organizations, public trace contract,
and replay CLI are implemented. Live model providers, the public Inspector,
Docker, and WSL launch wrappers remain release work and are not claimed here.

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
[configuration](docs/configuration.md), and
[architecture](docs/architecture.md) for the current supported surface.

## Platform support

| Platform | Current support | Recommended path |
|---|---|---|
| Linux | Full for this milestone | Native Python CLI |
| Windows 11 + WSL2 | Full for this milestone | WSL2 + Python CLI |
| Windows native PowerShell | Not yet released | Use WSL2 directly |
| Docker on Linux / Docker Desktop | Not yet released | Planned P1 milestone |
| macOS | Best effort | Native CLI if checks pass |

## 中文说明

> **运行你自己的 Relic organization。**

`relic-agent` 是从 Relic B3 中提取的通用 Agent Organization runtime，不是
论文 benchmark 或论文复现仓库。当前第一阶段已经提供持久 agent、角色、任务与
ownership、profile-conditioned 决策、event/episode、reflection/wish、显式审批、
protocol lifecycle、公开 trace 和无 API 成本 replay。

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

Windows 用户的正式路径是 **Windows → WSL2 → Linux runtime**。当前尚未完成
新版 Inspector、Docker、PowerShell/WSL wrapper 和 live model provider，因此本
阶段不承诺这些入口。它们会在后续 P1 阶段完成并经过 fresh-clone 验收后再进入
公开发布口径。
