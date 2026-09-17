# Bundled source-native configurations

`default.yaml` and `minimal.yaml` use the strict
`relic-agent-source-native-v1` schema. They select the shipped `org_default`
B3 source world, a deterministic seed, and a tick count; they do not recreate
the source roster, task list, governance, or action policy in YAML.

Every Bash, PowerShell, Docker, and native launcher forwards to the same
Python CLI. Copying an example is appropriate only for changing the public
organization label, seed, or length within the supported source-native surface.
See [configuration](../docs/configuration.md) for the fail-closed rules.
