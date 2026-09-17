# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.12.15 AS uv

FROM python:3.12-slim

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=uv /uv /uvx /bin/

RUN useradd --create-home --uid 10001 relic

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY configs ./configs
COPY examples ./examples
COPY relic_agent ./relic_agent
COPY organization_core ./organization_core
COPY agent_sdk ./agent_sdk
COPY environments ./environments

RUN uv sync --frozen --no-dev --no-editable \
    && mkdir -p /data/runs \
    && chown -R relic:relic /app /data/runs

USER relic

VOLUME ["/data/runs"]
EXPOSE 8765
ENTRYPOINT ["relic-agent"]
CMD ["run-default", "--output-root", "/data/runs"]
