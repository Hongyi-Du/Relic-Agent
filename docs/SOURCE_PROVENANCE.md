# Source provenance and current boundary

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

## Active HCI B3 protocol lifecycle closure

The default organization routes its protocol lifecycle through the HCI source
revision `dda36fb563375060ae8d8850300db01eb4695d29`. The closure is intentionally
narrow:

- `environments/org_env/backend/protocol/objects.py` is byte-exact;
- `environments/org_env/backend/protocol/registry.py` has exactly two import
  rewrites, recorded alongside its source and local Git blob IDs in
  `relic_agent/source_b3/provenance.py`;
- the dependency-free `_json_compatible` / `stable_fingerprint` slice from
  `environments/org_env/experiments/provenance.py` is retained because the
  source registry requires it. Evaluator and experiment-record helpers from
  that module are not included.

The active source registry owns proposal, support, opposition, review-latency
adoption, use, violation, enforcement, amendment, obsolescence, emergence
evidence, and independent-outcome-attestation transitions. It is mounted on
the existing `organization_core` host boundary: every source ledger event is
append-only evidence, and each source adoption produces one immutable portable
protocol record. The release test suite pins both source blobs, the two import
rewrites, the fingerprint behavior, weak-emergence behavior, and the host
projection.

`governance.review_ticks` is therefore source-fixed at `3`; a different value
is rejected during config validation rather than silently altering lifecycle
semantics. The approval threshold remains an explicit configuration input to
the source registry.

## Deliberate remaining boundary

This remains deliberately not a claim that Relic Agent is a complete B3 or HCI
execution extraction. The deterministic compatibility runner still supplies
the CLI, `relic-trace-v1`, Inspector, wrappers, Docker smoke path, task shell,
and compatibility proposal input. A successful run is not paper evidence.

Each `run.json` states:

- `execution_authority: legacy_compatibility_runtime`;
- `mode: source_b3_protocol_lifecycle_plus_shadow_observation`;
- `state_materialization: bootstrap_plus_source_b3_protocol_adoption`;
- `active_hci_host_adapter: unavailable_fail_closed`.

The manifest also has `source_b3_protocol_lifecycle`, which names the exact
source revision, blobs, active scope, projected event counts, and every
unavailable capability. Full source OrgWorld action execution, LLM reflection,
source proposal generation, growth/policy execution, and the HCI human-seat
host adapter remain unavailable/fail-closed. No substitute implementation is
provided for them.
