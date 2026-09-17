# Configuration

`relic-agent-v2` describes a generic organization. The separate
`relic-agent-source-native-v1` schema remains available in `configs/source-b3.yaml`
for canonical source B3 runs.

Run `relic-agent init my-org` for a working starting point, then
`relic-agent validate --config my-org/organization.yaml`. Validation checks types,
identifiers, references, provider declarations, and plugins without stepping the
world or calling a provider. Unknown keys are errors so spelling mistakes cannot
silently disable a setting. Agent count and role names are unrestricted.

The configuration sections are `organization`, `providers`, `agents`, `tools`,
`tasks`, `governance`, `protocols`, `learning`, `prompts`, `runtime`, and
`observability`. [Customization](customization.md) gives the field index;
[examples](../examples/README.md) contain runnable configurations.

## Precedence

Explicit CLI overrides take precedence over configuration. Secrets and provider
endpoints come from the environment variables referenced by the configuration;
missing optional settings use framework defaults. `.env` values do not replace
already-exported environment variables. A project `.env` is loaded beside its
configuration before execution.

`--ticks` overrides the configured horizon. `--output-root` overrides
`RELIC_AGENT_OUTPUT_ROOT`, whose default is `outputs`. `--run-id` sets a stable
output directory name; an existing run directory is never overwritten.

## Compatibility preset

```bash
relic-agent run-source-b3 --ticks 72
```

The source preset retains its canonical roster, tasks, baseline, and scenario.
Its schema intentionally does not accept generic organization overrides. Use
`relic-agent-v2` when changing membership, models, tasks, or learning behavior.

## Outputs

Each completed run writes a configuration snapshot, manifest, status, and public
trace. Inspector and replay read the trace without calling models. Configuration
snapshots are local run artifacts; keep initial private context out of shared
artifacts. Public exports contain structural lineage rather than private text.

Generic task `deadline` is an optional nonnegative tick number. Workspaces are
virtual file stores: configure `shared_workspace` or `private_workspace` with
`root` and a `files` list (`id`, `title`, `content`). Shared deliverables persist
in `workspace.json`; private files stay out of that public workspace export.
`organization-memory.json` records learned skills and company memory, and
`protocols.json` is a reusable protocol package. `observability.local_debug`
explicitly writes private local state to `debug.json`, still with credential
redaction. `public_trace: false` suppresses trace output; `inspector: false`
prevents serving that run in Inspector.

`observability.token_logging: false` omits token totals throughout provider and
agent metrics. `cost_logging: false` likewise omits cost estimates. Credentials
always remain redacted. Provider credentials written directly into configuration
are rejected before a run snapshot is created; use environment references.
