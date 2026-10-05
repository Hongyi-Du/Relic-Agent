# Extension guide

Use `relic-agent-v2` configuration to change organization members, roles, models,
tasks, tools, governance, protocols, prompts, and learning settings. You do not
need to edit `SEED_TEAM`, `SEED_TASKS`, or `default_scenario()`.

A new tool needs one Python plugin plus a `tools.plugins` entry and
an agent tool grant. See [tools](tools.md). Provider adapters reuse the existing
OrgEnv clients; see [providers](providers.md). New domain-specific workflows can
build on the shared task and protocol interfaces without copying the simulation.

The source B3 compatibility schema remains `relic-agent-source-native-v1`.
`run-source-b3` selects `configs/source-b3.yaml`; it preserves canonical seed
members and source behavior. Generic mode builds a different initial world and
then runs the same organization engine and lifecycle managers.

See [customization](customization.md) for the complete configuration index.
When changing the environment itself, follow the
[environment porting guide](environment_porting.md)
([简体中文](environment_porting_zh-CN.md)): action scoring, executable protocol
bindings, and episode boundaries require domain-specific adaptation beyond tool
registration.
