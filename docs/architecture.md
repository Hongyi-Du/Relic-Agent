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

source B3 event-to-episode lifecycle (explicit-input only)
  -> source-ported HCI event ontology + episode manager
  -> caller supplies a source world event or ExecutionResult and its OrgWorld
  -> closed source episode may be projected immutably into source-core state

source B3 episode-to-reflection/wish lifecycle (explicit-input only)
  -> source-ported reflection objects/manager/batch/failure digest
  -> caller supplies a terminal source episode, mounted OrgWorld, and
     OpenAI-compatible provider; no template or native-Anthropic fallback
  -> private source cognition is never projected into relic-trace-v1

source B3 growth + structural protocol-policy closures (explicit-input only)
  -> source growth appraisal/reconciliation requires exact mounted OrgWorld and
     ExecutionResult types; source policy masking requires exact ActionCandidate
  -> no compatibility task/event/candidate translation, selector, or execution

compatibility trace shell (non-authoritative)
  -> emits static config snapshots and append-only clock/trace envelopes for
     CLI/Inspector/Docker smoke only
  -> no task is claimed, started, progressed, blocked, completed, selected, or
     executed; no shell record becomes a source episode, reflection, wish, or proposal
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

The episode closure is also source-pinned, but it is not an active conversion
of the compatibility event ledger. Its adapter requires a caller-supplied HCI
world event or `ExecutionResult` plus the source world. A closed source episode
can then become one immutable portable record; an open episode is never
projected. This preserves the source manager's lifecycle without claiming that
a mock task event reconstructs an HCI episode.

The reflection closure vendors the HCI object, manager, batch-selection, and
failure-digest modules with recorded blobs. It does not reinterpret the mock
event ledger and rejects a missing terminal source episode, unmounted HCI
`OrgWorld`, mock provider, and native Anthropic provider. The source LLM
adjuncts (event appraisal, episode summarization, and wish interpretation) are
recorded as unavailable because their prompt/assets and HCI host boundary are
not closed here. They are not replaced with a local provider implementation.

The compatibility trace shell is retained only to keep the release-facing CLI,
`relic-trace-v1`, Inspector, Docker image, and wrappers testable while the real
HCI host adapter is ported. It emits static config snapshots plus clock/trace
envelopes; it does not claim, start, progress, block, complete, select, or
execute a configured task. It does not materialize source
episode/reflection/LLM proposal generation/growth/policy/action-execution
behavior. Its run manifest marks `workflow_acceptance` as
`unavailable_fail_closed`; a `completed` shell run is not final workflow
acceptance and a request for active HCI execution fails instead of silently
using a local substitute.

`relic-trace-v1` remains the only Inspector input. When an explicitly supplied
public event exists, it is paired with a post-event organization snapshot; the
unbound trace shell emits no invented public action event. The runtime
atomically replaces `trace.json` after every tick. Live Inspector refreshes are
append-only: a digest failure, partial write, shortened history, or rewritten
frame is ignored while the last verified trace remains visible with degraded
health. The Inspector never reads private memories, provider messages, or
source-core private envelopes.
