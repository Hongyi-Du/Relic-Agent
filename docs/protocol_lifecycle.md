# Protocol lifecycle

The shipped runtime delegates organization behavior to the vendored B3 source
world.  It does not maintain a second protocol state machine in the release
shell.  In that source lifecycle, selected work can produce events and
episodes; episode/reflection processing can produce wishes and proposals; and
proposal review can create protocol specifications and registry records.

## Source-owned lifecycle

The active source world owns:

1. membership, task ownership, and shared organization state;
2. structured action selection and its policy trace;
3. event-to-episode formation;
4. reflection and wish formation;
5. proposal, review, approval, and adoption transitions; and
6. protocol registration, use, violation/enforcement records, and any
   source-defined revision or retirement transition.

`OrganizationRuntime` only builds the source world, calls `OrgWorld.step()`,
stores run artifacts atomically, and creates the privacy-filtered public
trace.  The source pin and critical blob verification are described in
[SOURCE_PROVENANCE.md](SOURCE_PROVENANCE.md).

## What the public trace shows

`relic-trace-v1` publishes selected public agents, tasks and ownership,
episodes, selected-action summaries, proposals, protocol summaries, and
protocol lifecycle events.  Inspector uses those records to show the
proposal/governance/protocol relationship without reading a private runtime
dump.

Private reflections, wishes, prompts, policy candidate features/scores,
provider traffic, private memory, local paths, and credentials are excluded.
The public trace is evidence of recorded sequence, not proof that a protocol
caused an outcome.

## Current public-boundary limitation

The source supports more lifecycle detail than the current exporter publishes.
In particular, the current source-native projection does not yet expose every
safe lineage/revision field required for a complete public account of
reflection → wish → proposal → protocol amendment/retirement.  This is a
release implementation gap, not a reason to infer private content.  See
[KNOWN_RELEASE_GAPS.md](KNOWN_RELEASE_GAPS.md) before treating the Inspector
as a complete governance audit trail.
