# Runtime architecture

Relic Agent currently has three deliberately separated layers.

```text
source-core contracts/state/host seam (canonical)
  -> byte-exact organization_core from 041ddee1aa109a9b65dfdad7bdb8e258ad0a293e
  -> append-only source event evidence and portable bootstrap state
  -> active HCI host adapter: unavailable, fail closed

source B3 proposal + protocol lifecycle (active, narrow)
  -> source-ported HCI proposal objects/manager/dedup + protocol registry
  -> validate/review/adopt/materialize, then support/use/violation/enforcement
  -> immutable adoption record projected into source-core formation state

legacy compatibility shell (non-authoritative)
  -> deterministic mock lifecycle used by existing CLI/trace/Inspector smoke
  -> task/episode/reflection shell; mock wishes do not become source proposals
  -> no claim of HCI/B3 execution parity
```

The canonical layer is deliberately dependency-free. It owns public contracts,
portable organization state, host capability/receipt checks, approval policy,
typed gates, routing, and synthesis request/result schemas. Its provenance and
byte-exact conformance checks are described in
[source provenance](SOURCE_PROVENANCE.md).

The source B3 closure includes the proposal object's manager, family classifier,
and deterministic semantic deduplication as well as the registry and its
fingerprint dependency. Upstream blobs and behavior are pinned by provenance
and conformance tests. It does not emulate an OrgWorld: a caller may submit a
source-shaped proposal through the explicit host seam, but the compatibility
shell cannot generate one from its mock reflection. `governance.review_ticks`
is fixed to the source value of `3`, and the source protocol distinct-approver
floor is fixed to `2`; other values fail before execution.

The compatibility shell is retained only to avoid breaking the release-facing
CLI, `relic-trace-v1`, Inspector, Docker image, and wrappers while the real HCI
host adapter is ported. It does not materialize source episode/reflection/LLM
proposal generation/growth/policy/action execution behavior. Its run manifest
makes that boundary explicit and a request for active HCI execution fails
instead of silently using the mock implementation.

`relic-trace-v1` remains the only Inspector input. Each public event is paired
with a post-event organization snapshot; private compatibility events are not
exported. The runtime atomically replaces `trace.json` after every tick. Live
Inspector refreshes are append-only: a digest failure, partial write, shortened
history, or rewritten frame is ignored while the last verified trace remains
visible with degraded health. The Inspector never reads private memories,
provider messages, or source-core private envelopes.
