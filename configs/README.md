# Organization configs

`default.yaml` and `minimal.yaml` use the strict `relic-agent-config-v1` schema.
They are the canonical bundled examples consumed by the Python CLI; shell,
PowerShell, and Docker wrappers must forward to that CLI rather than reimplement
their defaults.
