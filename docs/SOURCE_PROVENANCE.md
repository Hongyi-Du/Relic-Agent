# Source provenance and release boundary

## Active runtime

The active runtime is a source-preserving dynamic closure from
[`Hongyi-Du/SocioGenesis`](https://github.com/Hongyi-Du/SocioGenesis) commit
`b567122022e131bab9555e6afb3b57147d591c8d` (`b567`). The host builds
`environments.org_env.backend.simulation.OrgWorld` from
`default_scenario(seed=...)` and advances it exclusively through
`OrgWorld.step()`.

`b567` is an ancestor of the authoritative HCI revision
`dda36fb563375060ae8d8850300db01eb4695d29` (`dda`), verified in the source
checkout with `git merge-base --is-ancestor b567 dda`. It was selected as the
closed B3 baseline, not as a divergent substitute for HCI. The seven
source-owned structures at 336 ticks for seeds 17, 701, and 2026 have identical
count/SHA-256 fingerprints at `b567` and `dda`:

- action log and policy trace;
- episodes, reflections, and wishes;
- proposals and protocol specifications.

`relic_agent/source_host/provenance.py` records those goldens and the Git blob
IDs for the host-critical source files. Every release run verifies the blobs
before building a world and writes the pin, comparison relation, and result to
`run.json`.

## What is active

The default 72-tick run uses the real source roster, candidate/policy path,
action execution, episode manager, reflection manager, proposal manager,
protocol registry, growth/policy seams, and source-generated lifecycle state.
The public trace is a projection only: it neither builds a second organization
state machine nor turns an old shell event into source behavior.

`workflow_acceptance: passed` means that one local run has source action,
episode, reflection, proposal, and protocol-spec evidence. It does **not**
mean a paper benchmark result, an official evaluator result, or a participant
study result.

## Deliberately excluded capabilities

The standalone release has no published, source-complete implementation or
release binding for:

- ProgramBench and its external evaluator/time-machine data;
- CooperBench and its official evaluator workflow;
- `society_core`, external society, and the Nature environment;
- HCI human-seat / live web application hosting;
- a published paper trace, official evaluator image digest, or score ledger.

These are fail-closed. The default process checks that the unavailable module
prefixes were not imported. Source files can still contain lazy references to
those upstream-only paths: they remain in blob-verified source modules rather
than being silently reimplemented. Narrow package initializers prevent broad
upstream aggregators from loading them incidentally. The run manifest records
the forbidden-module check and whether any archived compatibility modules were
already visible in the process.

## Archived compatibility material

`organization_core/`, `relic_agent/source_b3/`, `relic_agent/source_core.py`,
and related legacy governance/event modules are retained only for historical
API compatibility and audit tests. They are lazily isolated: a fresh
`relic-agent` CLI process does not import them, and they are not an execution
fallback. The old generic YAML and clock/trace runtime have been removed from
the default path; an unsupported generic organization config fails before a
run begins.

## Public-output boundary

Only `relic-trace-v1` feeds Inspector. It publishes selected public facts
(agent identity/role, task state, decision identifiers, public protocol facts,
and sanitized episode summaries) with a digest. It excludes private workspace
content, raw model traffic, private memories/reflections, policy candidate
scores, hidden evaluator data, and local filesystem/credential-like values.

The Inspector has an allowlisted static/API surface, loopback-by-default bind,
Host-header protection, and append-only live-trace validation. It is a public
release adapter and is intentionally not replaced by the upstream deep world
debugger.
