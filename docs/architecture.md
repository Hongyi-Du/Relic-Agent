# Runtime architecture

```text
source-native CLI / Bash / PowerShell / Docker
  -> strict source-native config (scenario, seed, ticks only)
  -> OrgWorld(default_scenario(seed)).build()
  -> repeated OrgWorld.step()
  -> source-owned action / policy / episode / reflection / proposal / protocol state
  -> privacy-filtered relic-trace-v1 projection
  -> replay validator and public Inspector
```

`OrganizationRuntime` owns release mechanics only: safe run directories,
atomic status/trace writes, configuration snapshots, provenance, and a public
projection. It does not select actions, maintain a parallel task state machine,
or synthesize episodes/reflections/proposals. Those transitions remain inside
the vendored source world.

The active host uses the sealed B3 commit described in
[source provenance](SOURCE_PROVENANCE.md). Its source-critical blobs are
verified before a run, and the default runtime checks that unavailable external
systems were not imported. Small package-initializer adapters make upstream
aggregators lazy; they do not edit the pinned world/lifecycle seams.

The prior `organization_core` and `source_b3` slices remain archival,
lazy-import compatibility material. They are not reached from the normal CLI,
Docker command, wrapper, or `OrgWorld` step path.

## Trace and Inspector

The source world contains deep/private state. `source_host.projection` reads
only selected public facts and emits strict frames after each source tick.
Decisions identify source actions without exposing candidate scores or private
reasoning. The trace validator enforces digest, references, privacy fields, and
append-only frame semantics. Inspector accepts only that public trace and
remains a release adapter rather than a source-world debugger.

## Unsupported paths

The package intentionally does not mount ProgramBench, CooperBench, external
evaluators, `society_core`, external society, NatureEnv, or the HCI human-seat
application. A request to enable those through generic config is rejected; no
compatibility fallback is substituted.
