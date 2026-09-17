# Relic Agent

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

## Development

```bash
uv run pytest
uv run ruff check relic_agent tests
```

The existing Bash and PowerShell launchers forward to the same CLI. For Docker,
use `docker compose run --rm relic-agent-runtime run-minimal`; mount your project
and output directory when using custom configuration. Historical extraction
and provenance notes remain in `docs/` as reference material.
