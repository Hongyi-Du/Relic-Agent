# Relic Agent

[简体中文](README_zh-CN.md) · English

Relic Agent runs configurable agent organizations: members work on shared tasks,
reflect on recurring friction, propose improvements, and adopt, enforce, revise,
or retire organizational protocols. Generic organizations and the canonical
Relic B3 preset use the same `OrgWorld.step()` lifecycle.

## Run in five minutes

Python 3.12+ on Linux or WSL2:

```bash
git clone https://github.com/Hongyi-Du/Relic-Agent.git
cd Relic-Agent
uv sync --extra dev --frozen
uv run relic-agent init my-org
uv run relic-agent validate --config my-org/organization.yaml
uv run relic-agent run --config my-org/organization.yaml --run-id first
uv run relic-agent inspect --trace outputs/first/trace.json
```

Open <http://127.0.0.1:8765>. The generated project has two agents, a shared task,
and a deterministic provider; it needs no API key. Edit `my-org/organization.yaml`
and run again with a different run ID. Output contains `config.yaml`, `run.json`,
`status.json`, and the privacy-filtered `trace.json`.

## Make it your organization

- **Agents:** add or remove entries in `agents`; choose your own stable IDs,
  roles, skills, profiles, schedules, and permissions.
- **Models:** configure `providers`, then each agent's `provider` and `model`.
  Credentials and endpoints can reference environment variables in a project
  `.env`. See [providers](docs/providers.md).
- **Tools and tasks:** choose built-ins or register a Python tool plugin, grant
  it to selected agents, and write your own `tasks`. See [tools](docs/tools.md).
- **Governance and protocols:** configure approvers, quorum, review delay,
  initial protocols, and lifecycle parameters. See [governance](docs/governance.md).

[Customization](docs/customization.md) maps each change to its configuration field.
[Configuration](docs/configuration.md) explains validation and precedence.
[Examples](examples/README.md) provide five runnable starting points.

```bash
uv run relic-agent run-minimal       # two-agent generic example
uv run relic-agent run-default       # eight-agent research example
uv run relic-agent run-source-b3     # canonical source B3 compatibility preset
uv run relic-agent check-env --config my-org/organization.yaml
uv run relic-agent replay --trace outputs/first/trace.json
```

Public Inspector output includes provider/model names, tool permissions, tasks,
governance, feature switches, and lifecycle identifiers. It excludes private
memory, raw reflection text, prompts, and provider traffic. The original B3
preset remains in `configs/source-b3.yaml`; paper experiment matrices and
external benchmark scoring belong to the separate Relic repository.

## Porting to a new environment

Changing an environment means adapting its action and event semantics, not just
registering tools or changing the organization configuration. Three parts need
an explicit review on every port:

| Part | What the new environment must define |
|---|---|
| **SDL action policy, if used** | Decide whether SDL should choose actions. For a stable, long-lived environment, define features and scoring for every relevant action, including its parameters and current state. Existing research-workflow scores are not a ready-made policy for another domain. |
| **Protocol–action bindings** | Map protocols to the new actions, their scope, required evidence, and executable checks. Place blocking checks before the corresponding side effects; protocol text alone does not enforce a rule. |
| **Episodes and reflection** | Define which events open, belong to, and close an episode, then choose when and for whom reflection runs. Episode boundaries and outcomes depend on the environment. |

In the current config, `runtime.decision_mode: llm_direct` bypasses SDL action
selection; `flat_deterministic` still scores actions. The shared organization
lifecycle can be reused, while new domain semantics need an adapter.
See the [environment porting guide](docs/environment_porting.md) for supported
configuration, code extension points, and a port validation checklist.

## Development

```bash
uv run pytest
uv run ruff check relic_agent tests
```

The existing Bash and PowerShell launchers forward to the same CLI. For Docker,
use `docker compose run --rm relic-agent-runtime run-minimal`; mount your project
and output directory when using custom configuration. Historical extraction
and provenance notes remain in `docs/` as reference material.

## License

Relic Agent's original source is source-available under the
[PolyForm Noncommercial License 1.0.0](LICENSE): noncommercial use,
modification, and distribution are permitted under its terms. Commercial use
requires a separate written license from Hongyi Du; see
[Commercial licensing](COMMERCIAL_LICENSE.md).

Third-party components retain their own licenses. The repository-level license
does not replace those terms.
