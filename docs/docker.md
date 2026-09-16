# Docker

The image is Linux-based and uses:

- `python:3.12-slim`;
- `uv` 0.12.15;
- the checked-in `uv.lock`;
- an unprivileged runtime user;
- a read-only root filesystem under Compose;
- `/data/runs` as the writable data boundary.

No GPU is required.

## Compose quickstart

Set the bind-mount identity in `.env` if your WSL/Linux user is not UID/GID
1000. Check it with `id -u` and `id -g`.

```bash
cp .env.example .env
mkdir -p outputs
docker compose build relic-agent-runtime
docker compose run --rm relic-agent-runtime check-env
docker compose run --rm relic-agent-runtime smoke --output-root /data/runs
docker compose run --rm relic-agent-runtime run-minimal --output-root /data/runs
docker compose run --rm relic-agent-runtime run-default --output-root /data/runs
docker compose run --rm relic-agent-runtime replay-example
```

Generated run directories appear under host `./outputs`. Removing a container
does not remove those runs.

## Direct image use

```bash
docker build -t relic-agent:local .
docker run --rm --read-only --tmpfs /tmp relic-agent:local check-env
docker run --rm --read-only --tmpfs /tmp relic-agent:local replay-example
```

The Docker entrypoint is the same `relic-agent` Python CLI used by native and
wrapper commands. No runtime or configuration semantics live in Docker shell
scripts.
