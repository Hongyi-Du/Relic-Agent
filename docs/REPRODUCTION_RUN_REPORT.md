# Reproduction run report

## Scope

This is one final execution record, not a full release certification.  The
source worktree was clean on `main` at
`6860d92b2563213e2fc29521d9339caba9d3e1c3` (`merge: integrate inspector host
validation`) when the commands below began on 2026-09-17.

The image was rebuilt from that source rather than reusing an existing
application image:

```bash
docker build --pull --no-cache --tag relic-agent:repro-6860d92 .
```

The resulting local image was
`sha256:1e498b13f0585f3a8bff6caa3a075cc7010ed9b0c7a51706801a8eb3de85fe1d`.
`--pull --no-cache` was supplied; the base-image content-addressable layers
may still be present in Docker's local store, but no application build cache
was used.

The ephemeral host output root is
`/tmp/relic-agent-repro-mN4h5h`.  It is deliberately not a release artifact:

- `default-72/` contains `config.yaml`, `status.json`, `run.json`, and
  `trace.json` (2,191,751 bytes);
- `minimal-12/` contains the corresponding four files (its trace is 144,288
  bytes).

## Default source-native run

The public default entry point was run once in the fresh image, with a
read-only root filesystem, a writable temporary filesystem, no container
network, and only the ephemeral output root mounted writable:

```bash
docker run --rm --read-only --tmpfs /tmp --network none --user 1000:1000 \
  --mount type=bind,src=/tmp/relic-agent-repro-mN4h5h,dst=/data/runs \
  relic-agent:repro-6860d92 run-default --output-root /data/runs \
  --run-id default-72
```

It completed successfully.  `run.json` reports `authority:
source_native_orgworld`, `action_execution: source_orgworld_step`, source pin
`b567122022e131bab9555e6afb3b57147d591c8d`, verified critical vendor blobs,
and no loaded forbidden modules.  The recorded provider-call count is `0`.

| Recorded field | Result |
|---|---:|
| ticks | 72 |
| events | 370 |
| tasks / completed tasks | 15 / 3 |
| episodes | 14 |
| reflections | 13 |
| wishes | 5 |
| proposals | 4 |
| source protocol specs | 1 |
| adopted protocols | 2 |
| public protocols in final trace | 2 |
| public trace frames | 73 |
| `workflow_acceptance` | `passed` |

The acceptance reason is
`real_source_action_episode_reflection_proposal_protocol_evidence_present`.
This is local lifecycle evidence only; it is not an evaluator, benchmark, or
paper result.  The public trace's canonical digest is
`9c45d2b1d4702ef861e425189c8bb4ab22c6d5f21bd7ffd3a5f70da93029a835`
(the raw file SHA-256 is
`01d5ad492c6b9d46f6b782317f0a8abcd78cf974cb9f4c0dd590a80428f39f35`).

## Public replay and Inspector HTTP checks

The generated public trace replayed successfully in the same fresh image:

```bash
docker run --rm --read-only --tmpfs /tmp --network none --user 1000:1000 \
  --mount type=bind,src=/tmp/relic-agent-repro-mN4h5h,dst=/data/runs,readonly \
  relic-agent:repro-6860d92 replay \
  --trace /data/runs/default-72/trace.json
```

Replay returned `status: passed`, 73 frames, 72 ticks, 8 agents, 15 tasks, 4
proposals, 2 protocols, and the digest above.

For the Inspector check, the same image served that trace with `--allow-remote`
inside the container but published its port only at
`127.0.0.1:18765` on the host.  The following requests were made against
`/api/health`:

| Request Host header | HTTP status | Observed result |
|---|---:|---|
| normal loopback (`127.0.0.1:18765`) | 200 | `status: ready`, `last_verified_frame: 72` |
| literal IP (`192.0.2.10:8765`) | 200 | accepted in explicit remote mode |
| DNS rebinding name (`rebind.example:8765`) | 400 | `{"status": "invalid_host"}` |

A normal `GET /api/trace` returned the same `relic-trace-v1` run ID, 73
frames, final tick 72, and canonical digest as the replay command.

## Minimal entry point

The minimal entry point was also run once, using the same fresh image and
temporary output root:

```bash
docker run --rm --read-only --tmpfs /tmp --network none --user 1000:1000 \
  --mount type=bind,src=/tmp/relic-agent-repro-mN4h5h,dst=/data/runs \
  relic-agent:repro-6860d92 run-minimal --output-root /data/runs \
  --run-id minimal-12
```

It completed at 12 ticks with 48 events, 9 tasks (0 completed), 5 episodes,
1 reflection, 0 proposals, 0 source protocol specs, and 0 adopted protocols.
Its provider-call count is `0`.  It correctly records
`workflow_acceptance: unavailable_fail_closed` with reason
`missing_real_source_evidence:source_proposals,source_protocol_specs`; this
short run did not manufacture missing lifecycle evidence.

## Deliberately not run here

This final execution did not rerun the full test/lint/release suite, perform a
fresh-clone exercise, call a live model provider, invoke an external evaluator,
or fetch, push, tag, or publish to a remote.  Those omissions are scope facts,
not failed checks; see [known release gaps](KNOWN_RELEASE_GAPS.md).
