# Source-native configuration

The only supported schema is `relic-agent-source-native-v1`:

```yaml
schema_version: relic-agent-source-native-v1
organization:
  id: relic-default-organization
  name: Relic Default Organization
runtime:
  scenario: org_default
  baseline: b3
  seed: 42
  ticks: 72
  profile_causality: source_recorded
  capability_transfer: {}
```

The configuration does not define a roster, tasks, governance rules, model
provider, or action policy. Those belong to the source `OrgWorld`; accepting a
generic YAML and translating it would create a different runtime. Unknown
fields, another scenario, another baseline, non-empty capability transfer, or
a non-source profile intervention fail before execution.

`baseline` accepts `b3`, `full`, or `sociogenesis` only as aliases for the
same shipped B3 source default. It does not select an arbitrary upstream
branch. `profile_causality: source_recorded` is descriptive and no custom
causal injection is allowed. The effective provider is always `source_native`;
the default local lifecycle makes zero model-provider calls.

## Output location

The output-root precedence is:

1. CLI `--output-root`;
2. `RELIC_AGENT_OUTPUT_ROOT` from the environment or `.env`;
3. `outputs`.

Every launcher invokes the same Python CLI. Inspector host/port/mode are also
explicit flags; Compose's `RELIC_AGENT_INSPECTOR_PORT` affects only its host
port mapping. A non-loopback Inspector bind needs `--allow-remote`.
