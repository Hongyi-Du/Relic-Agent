# Runnable organization examples

Run from the repository root after installing with `uv sync --extra dev`:

| Example | Config | What it demonstrates |
|---|---|---|
| A: Minimal | `configs/minimal.yaml` | Two agents, one task, simple governance |
| B: Research | `configs/default.yaml` | Eight custom roles, skills, profiles, governance and learning |
| C: Mixed models | `configs/mixed-model.yaml` | Independent provider/model routing for each agent |
| D: Custom tool | `examples/generic/custom-tool.yaml` | Plugin import, permission grant, and execution |
| E: Governance | `configs/custom-governance.yaml` | Configured approvers/quorum and an initial protocol |

```bash
uv run relic-agent validate --config configs/minimal.yaml
uv run relic-agent run --config configs/minimal.yaml --run-id example-a
uv run relic-agent run --config configs/default.yaml --run-id example-b
uv run relic-agent run --config configs/mixed-model.yaml --run-id example-c
uv run relic-agent run --config examples/generic/custom-tool.yaml --run-id example-d
uv run relic-agent run --config configs/custom-governance.yaml --run-id example-e
uv run relic-agent inspect --trace outputs/example-e/trace.json
```

These examples run deterministically without paid API calls and are exercised by
integration tests. The mixed-provider example uses two independently configured
mock providers so it is also runnable offline. Replace provider declarations with
the live settings in [providers](../docs/providers.md) to use your own endpoints.
The B3 research reference remains available with `relic-agent run-source-b3`.

The research example has three tasks, including a dependent validation task.
Its reviewer works every 12 ticks. Work waiting for those review windows creates
real coordination friction for the shared reflection and governance machinery;
protocols are not preloaded into that example. The mock drafts and approvals
are deterministic fixtures, not claims about the quality of a live model's work.
