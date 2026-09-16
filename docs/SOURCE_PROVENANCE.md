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

## Active HCI B3 proposal and protocol closures

The default organization exposes its proposal and protocol lifecycles through the HCI source
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
- `environments/org_env/proposals/objects.py`, `families.py`, and the
  deterministic portion of `llm/semantic_dedup.py` are ported under
  `relic_agent/source_b3/proposals/` with both their HCI source blob IDs and
  shipped-port blob IDs recorded in `relic_agent/source_b3/proposals/provenance.py`;
- `environments/org_env/proposals/manager.py` supplies validation, sparse
  review/approval latency, source family folding/deduplication, revision, and
  `ProtocolSpec` materialization. Its host-only imports are explicit capability
  seams, not replacement policies.

The active source manager owns a caller-supplied proposal's validation,
role-routing, review-latency, adoption, tool/protocol deduplication, and
materialization. The active source registry owns support, opposition,
review-latency adoption, use, violation, enforcement, amendment, obsolescence,
emergence evidence, and independent-outcome-attestation transitions. It is
mounted on the existing `organization_core` host boundary: every source registry
ledger event is append-only evidence, and each source adoption produces one
immutable portable protocol record. The release test suite pins upstream blobs,
source lifecycle behavior, deterministic deduplication, the fingerprint, and
the host projection.

`governance.review_ticks` is source-fixed at `3`, and the proposal manager's
distinct-approver floor is source-fixed at `2`; different values are rejected
during config validation rather than silently altering lifecycle semantics.

## Deliberate remaining boundary

This remains deliberately not a claim that Relic Agent is a complete B3 or HCI
execution extraction. The deterministic compatibility runner still supplies
the CLI, `relic-trace-v1`, Inspector, wrappers, Docker smoke path, and task
shell. It does **not** turn a mock event, episode, reflection, or wish into a
source cognitive record or a fixed proposal. A successful run is not paper
evidence.

Each `run.json` states:

- `execution_authority: legacy_compatibility_runtime`;
- `mode: source_b3_protocol_lifecycle_plus_shadow_observation`;
- `state_materialization: bootstrap_plus_source_b3_protocol_adoption`;
- `active_hci_host_adapter: unavailable_fail_closed`.

The manifest also has `source_b3_protocol_lifecycle`,
`source_proposal_lifecycle`, `source_episode_lifecycle`, and
`source_reflection_lifecycle`, which name the exact source revision, blobs,
active scope, projected event counts, and unavailable capabilities. The default
compatibility run leaves the episode and reflection adapters unbound because it
has neither an HCI world-event host nor a mounted HCI cognitive host. Full
source OrgWorld action execution, LLM reflection/source proposal generation,
ProgramBench-only registry repair, growth/policy execution, and the HCI
human-seat host adapter remain unavailable/fail-closed. No substitute
implementation is provided for them.

## Source event-to-episode closure

`relic_agent/source_b3/episodes/episode.py` is byte-exact from the HCI
revision `dda36fb563375060ae8d8850300db01eb4695d29`; its
`episode_manager.py` has one recorded import rewrite into the local closed
package.  The port keeps the HCI trigger, attach, bounded-close, template
summary, outcome, and debugging-recall semantics intact.

The manager is deliberately not pointed at the release-shell `EventStore`.
That ledger has compatibility event names and does not constitute a source
`OrgWorld` event stream.  `SourceB3EpisodeLifecycleAdapter` therefore accepts
only a caller-supplied HCI-shaped world event or `ExecutionResult`, plus the
explicit world object.  It cannot construct an OrgWorld or reinterpret a
mock task event as a source episode.

When a source episode reaches a terminal status, the adapter can project one
immutable `OrganizationEpisodeState` into the existing organization-core
formation ledger.  Open episodes are never projected, avoiding a mutable
episode record behind an append-only boundary.  The compatibility runtime
leaves this adapter unbound and its public trace contains no episode evidence;
that is intentional rather than a claim that mock events reproduce HCI
episodes.

## Source episode-to-reflection/wish closure

`relic_agent/source_b3/reflection/objects.py`, `manager.py`,
`batch_manager.py`, and `failure_digest.py` come from the same HCI revision;
their source and shipped blob IDs are recorded in
`relic_agent/source_b3/reflection/provenance.py`. The former release-shell
reflection dataclasses now re-export the source objects, and its deterministic
fixed-story `ReflectionManager` has been removed.

The closure deliberately has no compatibility `reflect(Event)` route. It can
run only after a caller supplies a terminal episode owned by a real HCI
`OrgWorld`, explicitly mounts the vendored manager on that world, and provides
an `openai` or OpenAI-compatible `http` source client exposing `generate_json`.
No source template fallback is permitted through this boundary; a missing
world/episode/provider, a `mock` provider, or a native `anthropic` provider
fails before a reflection or wish is generated.

The HCI `event_appraisal.py`, `episode_summarizer.py`, and
`wish_interpreter.py` have been audited and are listed with source blobs as
unported LLM adjuncts. Their prompt-assets/client/world closure is not present
in Relic Agent, so they are not reimplemented or called against mock state.
This is an explicit gap, not a replacement implementation.

`relic-trace-v1` and Inspector remain public-only. The compatibility runtime
reports zero source reflections and wishes, and neither hidden mock cognition
nor source raw reflection text is emitted into a trace or Inspector response.
