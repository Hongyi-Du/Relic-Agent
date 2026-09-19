# Scope and validation limits

Generic `relic-agent-v2` configuration and the source B3 compatibility preset
are the public organization entrypoints. See [customization](customization.md)
for configurable behavior and [next-stage validation](next_stage_validation.md)
for the checks run during this stage.

The standalone framework does not run the paper benchmark matrices or external
ProgramBench/CooperBench evaluators. Those belong to the Relic repository.
Source B3 conformance checks preserve its reference behavior; generic
organizations are configurable and need not reproduce a paper result.

Public traces expose structured reflection/wish/proposal/protocol relationships,
configuration summaries, work state, and lifecycle transitions. Private thoughts,
memory, prompts, workspace contents, and provider traffic remain local.
When inspecting a run on the same computer through the loopback-only server,
the final shared workspace may be displayed separately; it is not serialized
into the portable trace. Docker's remote-bind mode disables this local view.

Provider protocol tests use deterministic clients and local HTTP fixtures.
Actual access to a model and its gateway depends on the user's credentials and
provider account. The shipped offline examples require neither credentials nor
paid calls.

Plugins are trusted local Python. This stage includes tool permissions, argument
validation, timeouts, and error isolation, but not a container sandbox or a
third-party plugin marketplace. Distributed execution, hosted services, signed
artifacts, cross-platform byte identity, and historical artifact reconstruction
remain outside this stage. Tests stay in the ordinary repository; a `full-tests`
branch is not required.

The repository's original source is available under the PolyForm Noncommercial
License 1.0.0. Commercial use requires a separate written license; third-party
components remain subject to their own terms.
