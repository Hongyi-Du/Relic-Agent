# Next-stage validation record

Scope: `relic_next_stage_spec.md`, the local Relic and Relic-Agent repositories,
and the supplied Relic paper PDF. Historical handoff documents are not release
requirements for this stage. Work stays on local Git branches; no remote push is
part of this change.

The paper's main study remains 240 runs: two model labels × ten workloads ×
three seeds × four conditions, each at 336 ticks. B2 and B3 share SDL and member
learning; B3 adds governed protocol learning. Transfer Text and Exec keep the
same frozen content and disable new formation and revision. Generic framework
settings do not redefine these experimental conditions.

| Acceptance area | Implementation / evidence |
|---|---|
| Generic schema and references | `config_schema.py`; `test_generic_config.py` |
| Scaffold, validation, examples | CLI; `test_generic_cli_examples.py` |
| Safe tool plugins and grants | `runtime/tools.py`; `test_generic_tools.py` |
| Structural lifecycle export | `source_host/projection.py`; `test_public_lineage.py` |
| Friction through adoption and switch behavior | `test_generic_acceptance.py`; 72-tick default example completes its three tasks |
| Learned tools and deadlines | `test_generic_learned_tools.py`; real task artifacts, permission checks, overdue events and late completion |
| Repeated runs and credential redaction | `test_generic_engine.py`; independent traces and redacted persisted artifacts |
| Inspector config/model/tool display | Inspector assets; frontend and HTTP tests |
| Source B3 compatibility | `configs/source-b3.yaml`; source conformance suites |

The final integration suite passed **242 tests, with one intentional skip**, on
2026-09-17. The skipped test requires `society_core`, which is deliberately not
vendored. The suite includes the 336-tick source B3 structural goldens at all
three reference seeds, local Inspector HTTP tests, and local HTTP provider
fixtures for OpenAI-compatible, Anthropic-compatible, and generic HTTP routes.
An additional OpenAI Responses wire check passed against a local fixture.
No paid model call or full 240-run paper study was performed.

The standalone 72-tick generic acceptance suite passed all six checks. The
seven focused governance checks cover real enforcement/use, permission and
quorum checks, review latency, successful amendment and retirement, and registry
identity preservation. Ruff and `git diff --check` passed. Socket tests ran with
local socket access; restricted-sandbox socket errors are not provider failures.
Final review also verified owner assessment for live tasks without collaborators,
protocol gates within learned tools, successful read/message use, and accurate
pending status for partially completed composed tools.

A clean local clone of implementation commit `6927de7` built a wheel, which was
installed with its declared dependencies in a new Python 3.12 virtualenv.
All commands below ran outside the repository, importing the installed package:

- `init → validate → run → replay`: two agents, one completed task, 12 ticks;
- `run-default`: eight agents, three completed tasks, 72 ticks, three episodes,
  24 reflections, two wishes, five proposals, and one adopted protocol;
- `run-source-b3 --ticks 12`: the installed compatibility preset completed;
- `inspect`: the installed localhost server served its UI and trace API with
  the generated eight agents and three tasks; the temporary server was stopped.

The 12-tick installed source run checks packaging and startup. The full 336-tick
source conformance evidence comes from the test suite above. Offline providers
are deterministic fixtures, so completed example work is not evidence of live
model quality. Initial dependency installation needed network access because a
required wheel was absent from the local cache; model access was unnecessary.

P2 items—distributed execution, plugin sandboxing, hosted services, signed
artifacts, supply-chain certification, and historical-run reconstruction—are
outside this stage.
