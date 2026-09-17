# Relic Agent

Relic Agent runs the vendored, source-native B3 `OrgWorld` host.  The default
CLI builds the real source world and calls `OrgWorld.step()`; it is no longer a
separate clock/trace compatibility runtime.  Public output is a deliberately
filtered `relic-trace-v1` projection of that world for replay and Inspector.

The release host is not a claim that this standalone package reproduces every
paper result.  Its normal source-native path does not load or execute an
external evaluator, ProgramBench, CooperBench, HCI human-seat, or live model
provider workflow.  That path-level statement is not proof that the vendored
source contains no lazy references to upstream-only systems.  See [source
provenance and boundaries](docs/SOURCE_PROVENANCE.md) and [known release
gaps](docs/KNOWN_RELEASE_GAPS.md).

## Quickstart

Python 3.12+ and `uv` are required.  Linux and WSL2 are the supported paths.

```bash
uv sync --extra dev --frozen
uv run relic-agent check-env
uv run relic-agent smoke
uv run relic-agent run-default
```

`smoke` runs the real host for 12 ticks; `run-default` runs the bundled seed 42
configuration for 72 ticks.  These deterministic paths record their actual
provider-call count in `run.json`; they do not configure or validate a live
provider workflow.  A default run is a local source-lifecycle observation: it
may contain source actions, episodes, reflections, proposals, and protocol
specifications when that fixed lifecycle reaches them.  It is not a claim
about altered seeds/tick counts, an external evaluator, or paper results.

Runs are written below `outputs/<run-id>/` unless `--output-root` or
`RELIC_AGENT_OUTPUT_ROOT` is supplied.  Each run includes:

- `config.yaml` — exact source-native configuration snapshot;
- `run.json` — source pin, blob verification, lifecycle summary, and public
  boundary status;
- `status.json` — atomic running/completed status;
- `trace.json` — digest-bound public replay trace.

Inspect a completed trace locally:

```bash
uv run relic-agent inspect --trace outputs/<run-id>/trace.json --mode replay
```

Then open <http://127.0.0.1:8765>.  The Inspector serves only the public trace;
private workspaces, prompts, memories, raw reflections, and provider traffic
are not exported.

## Configuration surface

`configs/default.yaml` and `configs/minimal.yaml` use
`relic-agent-source-native-v1`.  They select only the shipped `org_default` B3
scenario, seed, and tick count.  A generic roster/task YAML is rejected rather
than partially translated into a different organization.  The source’s stable
agent IDs remain intact; the public projection displays `scarlett` as `Los Xi`.

`run-minimal` is a short source-native run (seed 7, 12 ticks).  It is useful
for an installation check but may not yet have enough lifecycle evidence for
`workflow_acceptance: passed`; `run-default` is the canonical 72-tick local
workflow-evidence path.  A `passed` value means that one run recorded source
action, episode, reflection, proposal, and protocol-spec evidence; it is not
an evaluator, benchmark, or publication acceptance result.

## Docker

```bash
mkdir -p outputs
docker compose build relic-agent-runtime
docker compose run --rm relic-agent-runtime check-env
docker compose run --rm relic-agent-runtime run-default --output-root /data/runs
docker compose up relic-inspector
```

Docker uses the same CLI and source-native host as native execution.  See
[Docker](docs/docker.md) for the read-only container boundary and
[installation](docs/installation.md) for WSL details.

## Verification

```bash
uv run pytest
uv run ruff check .
```

The repository includes source-host conformance checks for critical source
blobs and the seven source-owned structures (action log, policy trace,
episodes, reflections, wishes, proposals, and protocol specifications) at the
pinned 336-tick smoke seeds.  The trace and Inspector tests are code-level
public-boundary checks; they are not an external evaluator or paper-study
result.

## 中文说明

`relic-agent` 默认直接构建并推进 vendored 的 B3 `OrgWorld`，不是旧的独立时钟/
trace 机制。`run-default` 使用 seed 42 运行 72 ticks，并导出
经过隐私过滤的 `relic-trace-v1`，可用 Inspector 查看。该独立发布包不声称复现
论文全部结果。正常 source-native 路径不会加载或执行外部 evaluator、ProgramBench、
CooperBench、HCI 人类座位或在线模型工作流；这不等于 vendored 源码中完全没有
指向上游系统的 lazy 引用。配置只允许选择已封装的 source-native B3 情景，不能把任意
两三人 YAML 静默翻译成另一套组织运行时。`workflow_acceptance: passed` 只表示一次
本地运行收集到规定的 source lifecycle 证据，不是 benchmark、evaluator 或论文验收。
