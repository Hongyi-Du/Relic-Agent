# Source-core provenance and current boundary

The top-level `organization_core/` package is a byte-exact vendoring of
`organization_core/` from
[`Hongyi-Du/SocioGenesis`](https://github.com/Hongyi-Du/SocioGenesis) commit
`041ddee1aa109a9b65dfdad7bdb8e258ad0a293e`. It contains the dependency-free
contracts, portable state,
append-only shadow runtime, approval, gate, host, decision, formation, routing,
and synthesis seams. `relic_agent/core/provenance.py` records the expected Git
blob id for every vendored source file; the release suite verifies them.

The direct source tests under `tests/organization_core/` are also copied
byte-for-byte. One test is skipped rather than faked: it checks a re-export
from the intentionally excluded `society_core` package. All other copied tests
run as part of the normal suite.

This is deliberately not a claim that Relic Agent is a complete B3 or HCI
execution extraction. The existing deterministic mock runner remains a
compatibility shell for the CLI, `relic-trace-v1`, Inspector, wrappers, and
Docker smoke path. Its events are validated through source-core envelopes in
shadow-observation mode, and each `run.json` states:

- `execution_authority: legacy_compatibility_runtime`;
- `state_materialization: bootstrap_only`;
- `active_hci_host_adapter: unavailable_fail_closed`.

There is no substitute HCI adapter in this repository. A future source-first
stage must port the source HCI OrgWorld mapping for episode/reflection/proposal/
protocol/growth/policy/execution materialization, then replace the compatibility
runtime rather than extending it. ProgramBench-specific adapters, assets,
configuration, results, protocol masks, and later workspace code are outside
this extraction boundary.
