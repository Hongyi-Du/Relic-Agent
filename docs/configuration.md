# Organization configuration

The public schema is `relic-agent-config-v1`. The two canonical examples are:

- `configs/minimal.yaml`: two members and one task;
- `configs/default.yaml`: four members and three tasks, including the public
  default display name **Los Xi**.

Top-level fields are strict:

| Field | Purpose |
|---|---|
| `organization` | Stable organization ID and display name |
| `agents` | Persistent member ID, display name, role, profile, skills, and tools |
| `tasks` | Work items, priorities, optional owners, and required skills |
| `governance` | Source-fixed distinct-approver floor and review latency |
| `runtime` | Seed, tick count, provider, and a legacy reflection-interval field |

Profile and skill values are bounded to `[0, 1]`. IDs must be unique, and task
owners must reference declared agents. The current milestone intentionally
accepts only `provider: mock`; an unknown or unqualified live provider fails
before execution.

`runtime.reflection_interval` remains accepted for backwards-compatible config
loading, but is intentionally ignored by the mock release shell. Source
reflection has the HCI batch cadence and only activates with a terminal source
episode, mounted HCI `OrgWorld`, and OpenAI-compatible source provider.

`governance.min_approvers` must be `2`, the pinned source proposal manager's
distinct-approver floor. `governance.review_ticks` must be `3`, the pinned
source lifecycle latency. Another value is rejected during config loading
rather than producing a nearby-but-different proposal/protocol lifecycle.

## Configuration precedence

For the currently exposed output location:

1. explicit CLI `--output-root`;
2. `RELIC_AGENT_OUTPUT_ROOT` loaded from the environment or `.env`;
3. repository default `outputs`.

Organization semantics come from the explicit YAML snapshot. Shell, future
PowerShell, and Docker entrypoints must call the same Python CLI and must not
carry their own hidden defaults.

Inspector configuration follows the same explicit-boundary rule:

1. CLI `--host`, `--port`, and `--mode`;
2. the CLI defaults `127.0.0.1`, `8765`, and `replay`.

`RELIC_AGENT_INSPECTOR_PORT` configures only the host-side Compose port mapping;
it does not silently override a native CLI argument. Remote binding requires
the separate `--allow-remote` acknowledgement.
