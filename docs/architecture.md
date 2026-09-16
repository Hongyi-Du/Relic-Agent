# Runtime architecture

This milestone is an extraction of the general organization mechanisms from
the locked Relic B3 code at commit
`dda36fb563375060ae8d8850300db01eb4695d29`. It deliberately excludes the paper
arms, benchmark/evaluator stack, scenario-specific domain systems, HCI code,
and historical development material.

The retained mechanism chain is:

```text
config
  -> persistent members + tasks/ownership
  -> profile-conditioned structured action selection
  -> append-only events
  -> episodes
  -> private reflection -> wish
  -> public proposal
  -> explicit distinct approvals + review latency
  -> protocol adoption -> use/amendment/retirement
  -> digest-bound public trace and replay
```

Important invariants:

- a raw reflection or wish cannot directly mutate organization protocols;
- protocol adoption requires explicit approvals and cannot occur in the proposal tick;
- profiles affect scored action utility and every selection keeps a policy trace;
- private reflection text and memories are not exported in `relic-trace-v1`;
- config snapshots and trace digests make a run self-describing;
- mock execution is deterministic for a fixed config and seed and makes no provider call.

The extracted task, episode, reflection/wish, proposal, and protocol schemas,
plus the protocol registry, retain the B3 lineage. The new boundary code is the
standalone package/config/CLI layer and a substrate-independent mock runtime used
for installation and lifecycle validation.
