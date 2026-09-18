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

## SDL decision policy

`runtime.decision_mode` selects `profile_policy` (seeded SDL), `flat_deterministic`
(profile-off argmax), or `llm_direct` (provider chooses from the allowed candidate
pool). In generic mode, `runtime.sdl` customizes the SDL score and sampling
without editing the canonical source B3 preset:

```yaml
runtime:
  decision_mode: profile_policy
  sdl:
    temperature: 0.4
    jitter: 0.02
    base_weights: {progress_gain: 0.9, review_quality_gain: 0.5}
    profile_coefficients:
      - {trait: curiosity, feature: learning_gain, coefficient: 0.7}
```

Weights override the named dimensions of the existing 55-dimensional feature
vector; unspecified weights retain their defaults. A trait/feature pair
overrides that pair's coefficient; all other pairs retain their defaults. An
unknown feature, non-finite coefficient, non-positive temperature, or negative
jitter fails validation. To replace weighted scoring entirely, configure a
trusted Python scorer (for example
[`examples/generic/sdl_score.py`](../examples/generic/sdl_score.py)):

```yaml
runtime:
  decision_mode: profile_policy
  sdl:
    scorer:
      path: sdl_score.py
      entrypoint: execute
      config: {progress: 2.0, review: 1.0, tool_bonus: 0.3}
```

`execute(features, context)` returns one finite numeric utility. `features` is
the candidate's feature dictionary; `context` is a snapshot containing
`agent_id`, `role`, `profile`, `skills`, `tick`, `action`, `parameters`, and the
scorer's `config`. Relative paths resolve from the YAML directory. This is
trusted local Python code, like a tool plugin, and is not sandboxed or timed
out. The existing candidate menu,
permission checks, governance gates, and seeded sampler remain in force. A
custom scorer replaces the weighted score, so `base_weights` and
`profile_coefficients` do not affect it. Any non-default SDL override disables
the source policy's linear-profile counterfactual metric for that run, while
retaining candidate scores and choices in policy traces. `llm_direct` uses the scorer only for
explanatory policy traces, not to override the provider's accepted choice.
The source B3 schema does not accept `runtime.sdl`; its policy stays canonical.

## Precedence

Explicit CLI overrides take precedence over configuration. Secrets and provider
endpoints come from the environment variables referenced by the configuration;
missing optional settings use framework defaults. `.env` values do not replace
already-exported environment variables. A project `.env` is loaded beside its
configuration before execution.

`--ticks` overrides the configured horizon. `--output-root` overrides
`RELIC_AGENT_OUTPUT_ROOT`, whose default is `outputs`. `--run-id` sets a stable
output directory name; an existing run directory is never overwritten.

## Learning switches

`learning.external_signal_loop: false` disables community ticks, scheduled
external events, and the optional external-society bridge through the shared
engine's existing gate. The generic setting takes precedence over the legacy
`ORG_MECHANISM_ABLATIONS` external-bridge toggle; other ablations are preserved.

`learning.capability_learning: false` keeps each agent's configured skills and
base reputation/authority without applying growth at initialization or on later
ticks. Tasks and other independently enabled organization mechanisms still run.
With learning enabled, initial authority derivation and daily growth retain
their existing behavior. These settings do not change the source B3 preset.

Inspector shows the effective lifecycle switches after combining top-level and
nested settings, including protocol retirement, alongside public trace, local
debug, and token/cost logging settings. Private configuration values stay out of
this summary.

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
