# Customize an organization

Start with `relic-agent init my-org`. All relative configuration assets resolve
from the directory containing `organization.yaml`. Use `validate --config` before
running; validation never calls a model.

| Change | Where | Field | Code needed? |
|---|---|---|---|
| Organization identity and context | organization.yaml | organization.id/name/description/brief | No |
| Shared goals and teams | organization.yaml | organization.shared_goals/topology | No |
| Channels and shared documents | organization.yaml | organization.channels/initial_documents | No |
| Agent count | organization.yaml | agents | No |
| Roles and mandates | organization.yaml | agents[].role/role_mandate | No |
| Models | organization.yaml | providers; agents[].provider/model | No |
| Profiles and skills | organization.yaml | agents[].profile/skills | No |
| Generation settings | organization.yaml | agents[].generation/reasoning | No |
| Work schedules | organization.yaml | agents[].work_schedule | No |
| Tool access | organization.yaml | agents[].tools/permissions | No |
| Built-in tools | organization.yaml | tools.builtins | No |
| New custom tool | tools/*.py and organization.yaml | tools.plugins | Plugin only |
| Initial tasks | organization.yaml | tasks | No |
| Governance | organization.yaml | governance | No |
| Initial protocols | organization.yaml | protocols.initial | No |
| Loaded protocols | protocol package and organization.yaml | protocols.packages | No |
| Reflection cadence | organization.yaml | learning.reflection | No |
| Other learning mechanisms | organization.yaml | learning | No |
| Prompt context | prompts/ and organization.yaml | prompts | No |
| API credentials | .env | providers.*.api_key_env | No |
| Inspector and public trace | CLI and organization.yaml | observability | No |

IDs identify objects across the run; keep them stable when renaming display names.
Roles are free-form strings. There is no eight-member requirement or mandatory
product backlog. Task owners and approvers must reference configured agents.
Start without protocols by setting `protocols.initial: []`.

See [configuration](configuration.md), [providers](providers.md), [tools](tools.md),
and [governance](governance.md) for the supported settings and runnable examples.
