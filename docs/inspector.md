# Relic Inspector / 公开组织观测器

Relic Inspector is a public organization observatory for a validated
`relic-trace-v1` export. It is not an omniscient runtime debugger and never
opens checkpoints, arbitrary files, provider messages, private memories, or
private reflection text.

## Start a replay

The bundled boundary sample is a digest-valid empty public trace and makes no
model call. It demonstrates the Inspector interface, not an organization run:

```bash
uv run relic-agent inspect-example
```

Open `http://127.0.0.1:8765`. A completed user run can be loaded directly:

```bash
uv run relic-agent inspect \
  --trace outputs/<run-id>/trace.json \
  --port 8765 \
  --mode replay
```

The Bash wrapper exposes the same CLI:

```bash
scripts/bash/start_inspector.sh \
  --trace outputs/<run-id>/trace.json \
  --mode replay
```

On Windows, run the server in WSL2 and open the same localhost URL in the
Windows browser. `scripts/powershell/start_inspector.ps1` only forwards to the
WSL Bash wrapper.

## Replay and live semantics

`replay` validates and caches the file once. Later filesystem changes do not
alter the displayed trace.

`live` revalidates the public file while the runtime atomically replaces it
after each tick:

```bash
uv run relic-agent inspect \
  --trace outputs/<run-id>/trace.json \
  --mode live
```

A live update must preserve the run identity and every already published
frame. A partial write, digest mismatch, shortened trace, changed schema, or
rewritten frame is not accepted. The API continues serving the last verified
snapshot and marks `/api/health` as `degraded` until a valid append-only update
appears. This avoids treating a half-written file as organization state.

## Trace and privacy contract

The loader rejects the trace unless all of the following hold:

- the top-level schema is exactly `relic-trace-v1`;
- its SHA-256 covers the complete public payload as a self-consistency check
  (not an author signature or independent proof of run provenance);
- frame sequence is contiguous and ticks are non-decreasing;
- each frame contains at most one ordinary public event, so every published
  event is paired with one post-event organization snapshot;
- core agents, tasks, proposals, and protocols are explicitly published;
- all privacy flags are present and `false`;
- nested public records use typed allowlists rather than arbitrary data
  containers;
- private events, blocked private fields (including camelCase variants),
  credential-like values, absolute local paths, inconsistent object references,
  duplicate JSON keys, non-finite numbers, and unsupported schema fields are
  absent;
- public decision records contain only the selected action/object summary, not
  candidate features, utilities, prompts, rationale, or evaluator-side policy
  audit.

Opaque reflection and wish identifiers may remain on a public proposal to show
lineage. They do not provide a route to the private reflection content.

## Interaction model

Timeline selection moves to the event-level snapshot. The Object Inspector and
State Diff update to the same frame. Public relations make it possible to
follow proposal → protocol → lifecycle event and to navigate back from task or
repository records when those records publish typed protocol references.

The panels are:

- Overview, Members, Tasks, Timeline, and Episodes;
- privacy-preserving Reflections, Proposals, Governance, and Protocols;
- Artifacts, Repo / PR / CI, Evaluation, and public Decisions;
- Object Inspector and added/changed/removed State Diff.

Artifacts, repository state, and evaluation annotations are optional. When a
trace does not publish one of these collections, the panel says unavailable;
it does not display a misleading zero.

The Inspector is descriptive evidence. Recorded sequence, object lineage, and
state differences do not establish causal attribution to a protocol, member, or
mechanism. Any HCI-facing use is an interface demonstration or formative
artifact unless separately supported by reviewed participant-study evidence; it
is not a powered participant evaluation.

## Network boundary

Native and WSL launches bind `127.0.0.1` by default and emit no CORS allowance.
The server exposes only `/`, `/index.html`, `/app.css`, `/app.js`,
`/api/health`, and `/api/trace`. Responses use a restrictive Content Security
Policy, loopback Host-header validation, and no-store headers.

The Inspector has no authentication. A non-loopback bind is therefore rejected
unless `--allow-remote` is supplied explicitly; that opt-in also permits remote
Host headers. Docker uses this opt-in only inside the container while Compose
maps the host port to `127.0.0.1`.

## 当前边界

Inspector 只读取经过严格验证的公开 trace，不读取内部 runtime dump。Timeline
按公开 event 对齐 post-event snapshot；live 模式只接受同一 run 的 append-only
原子更新。缺失的 artifact、Repo/PR/CI 或 evaluation 数据会显示“未发布”，不会
当成 0。公开 decision 只保留已选择的 action/object，候选分数和 policy audit
仍属于内部审计数据。
