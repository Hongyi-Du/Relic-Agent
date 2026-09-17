# Extension guide and current boundary

Relic Agent is intended to become a general organization runtime, but the
current release is a source-native extraction with a deliberately narrow,
fail-closed configuration surface.  This guide distinguishes changes that are
supported now from work that still needs a source-backed adapter.

## Supported today

Copy `configs/default.yaml` or `configs/minimal.yaml` and change only:

- `organization.id` and `organization.name` (public run labels);
- `runtime.seed`; and
- `runtime.ticks`.

The accepted schema is documented in [configuration.md](configuration.md).
Every launcher calls the same CLI, so `--output-root` and
`RELIC_AGENT_OUTPUT_ROOT` have identical precedence in native, Bash,
PowerShell, and Docker paths.

## Not yet a supported extension surface

The current schema intentionally rejects a generic roster, task board, model
provider, tool declaration, governance rules, initial protocols, SDL/CLG
interventions, capability-transfer payload, or a non-default source scenario.
Those values cannot be partially translated into the vendored source B3 world
without changing its behavior.  The shipped default is therefore an example
organization, not a customizable organization template.  Its public display
maps source ID `scarlett` to **Los Xi**; the stable source ID remains unchanged.

## Required work for a generic organization API

A future generic release needs a tested source-backed adapter that can create
or validate all of the following without silently falling back to the fixed
source scenario:

- agent membership, roles, tools, and model/provider configuration;
- initial tasks, ownership, shared context, and visibility;
- governance, initial protocols, SDL/CLG policy configuration; and
- public trace fields that preserve safe lifecycle lineage and revision state.

That work must add a documented schema, validation, source-native conformance
tests, example configurations, and a privacy review.  Until then, do not
advertise a modified YAML as an equivalent Relic organization run.  This is
the principal implementation gap against the intended framework scope and is
also listed in [KNOWN_RELEASE_GAPS.md](KNOWN_RELEASE_GAPS.md).
