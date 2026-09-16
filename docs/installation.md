# Installation / 安装

## Supported environment

- Python 3.12 or newer
- `uv` with the checked-in `uv.lock`
- Linux or Windows 11 with WSL2
- No GPU requirement
- No API key for `check-env`, `smoke`, `run-minimal`, `run-default`, or replay

Keep the repository in the Linux filesystem, for example
`~/Projects/relic-agent`, rather than under `/mnt/c`.

```bash
uv sync --extra dev --frozen
cp .env.example .env
uv run relic-agent check-env
uv run relic-agent smoke
```

`uv sync --extra dev --frozen` installs the exact lock resolution and includes
the test/lint tools. A normal user installation may omit `--extra dev`.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `RELIC_AGENT_OUTPUT_ROOT` | No | `outputs` | Default parent directory for new runs |

The CLI loads a repository-local `.env` file when present. Explicit
`--output-root` takes precedence over the environment variable.

Live-provider credentials and Inspector settings will be documented only when
those components are implemented and validated.

## Windows / WSL2

Native Windows Python is not a supported runtime path. Enter WSL2, clone into
the WSL filesystem, and run the same commands shown above. PowerShell launchers
will be added as thin WSL wrappers in a later milestone; they will not contain
separate runtime logic.

## 中文

要求 Python 3.12+ 和 `uv`。Windows 用户请在 WSL2 的 Linux 文件系统中 clone，
不要长期在 `/mnt/c` 下运行。当前 mock runtime 不需要 API key，也不需要 GPU。

```bash
uv sync --extra dev --frozen
cp .env.example .env
uv run relic-agent check-env
uv run relic-agent smoke
```
