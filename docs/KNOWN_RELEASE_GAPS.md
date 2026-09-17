# Known release gaps

This document records the boundary of the current standalone release.  It is
not evidence that the items below are broken; it distinguishes what was
delivered and exercised from what remains unshipped, unvalidated, or requires
separate release authority.

## Upstream-only lazy references

The vendored source retains textual and lazy-import references to ProgramBench,
`society_core`, and evaluator-oriented paths.  The normal source-native host
does not load those forbidden prefixes: the recorded 72-tick run reports an
empty `forbidden_modules_loaded` list.  That is an observation about this
process, not proof that every vendored upstream-only reference has been removed
or that those paths can operate without their missing dependencies.

## Configuration and arbitrary input boundary

The supported CLI accepts only the strict
`relic-agent-source-native-v1` schema and uses safe YAML loading.  Generic
rosters/tasks, unknown keys, custom capability transfer, and unsupported
source scenarios fail before the source host starts.  This is a source-native
capability boundary, not a claim of a complete adversarial arbitrary-YAML
injection/containment system or a sandbox for user-modified Python/source.

## Live model-provider path

The recorded default and minimal runs made zero provider calls.  Live LLM
credentials, provider configuration, cost controls, permission handling, and
end-to-end provider behavior are not part of this release execution and must
not be inferred from the deterministic source-native run.

## Evaluators and paper claims

No external ProgramBench/CooperBench/final evaluator workflow, paper score,
participant study, or official score ledger is included or exercised by the
standalone host.  `workflow_acceptance` is only a local check for source
action/episode/reflection/proposal/protocol-spec evidence in one run.

## Legal and citation release material

This repository currently has no top-level `LICENSE`, `NOTICE`, or `CITATION`
file.  Source provenance is documented, but release owners still need to
choose and add the applicable license, third-party notices, and citation
metadata before a public tagged release.

## Release engineering and verification scope

No signed or release tag was created in this execution.  No remote
fetch/push/published-release verification was performed.  The full pytest,
lint, release, Docker, clean-clone, and cross-platform suites were not rerun
as part of the final reproduction record; the completed commands are exactly
those listed in [the reproduction report](REPRODUCTION_RUN_REPORT.md).
