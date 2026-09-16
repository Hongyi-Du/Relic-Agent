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
docker compose up relic-inspector
```

Generated run directories appear under host `./outputs`. Removing a container
does not remove those runs. The `relic-inspector` service publishes the bundled
replay at `http://127.0.0.1:${RELIC_AGENT_INSPECTOR_PORT:-8765}`. Its host-side
mapping remains loopback-only even though the process binds all interfaces
inside the isolated container.

Inspect a trace under the read-only `/data/runs` mount:

```bash
docker compose run --rm --service-ports relic-inspector \
  inspect \
  --trace /data/runs/<run-id>/trace.json \
  --host 0.0.0.0 \
  --port 8765 \
  --allow-remote
```

Use `--mode live` when that file belongs to an active runtime. The server keeps
the last digest-verified, append-only snapshot during a transient update and
reports degraded health until a newer valid snapshot appears.

## Direct image use

```bash
docker build -t relic-agent:local .
docker run --rm --read-only --tmpfs /tmp relic-agent:local check-env
docker run --rm --read-only --tmpfs /tmp relic-agent:local replay-example
docker run --rm --read-only --tmpfs /tmp -p 127.0.0.1:8765:8765 \
  relic-agent:local inspect-example \
  --host 0.0.0.0 --port 8765 --allow-remote
```

The Docker entrypoint is the same `relic-agent` Python CLI used by native and
wrapper commands. No runtime or configuration semantics live in Docker shell
scripts.
