# Relic Agent documentation

This directory documents the configurable Relic Agent runtime and its separate
canonical B3 compatibility preset. Paper benchmarks, HCI studies, CooperBench,
and external evaluators belong to the Relic research repository.

- [Installation](installation.md) — supported platforms, `uv`, WSL2, and
  environment variables.
- [Docker](docker.md) — the Docker and Compose entry points.
- [Configuration](configuration.md) — organization, members, providers, tasks,
  tools, and governance configuration.
- [Architecture](architecture.md) — what executes in a run and what is only a
  public projection.
- [Protocol lifecycle](protocol_lifecycle.md) — source lifecycle terms and the
  public observability boundary.
- [Extension guide](extension_guide.md) — supported customization and current
  extension boundaries.
- [Inspector](inspector.md) — trace validation, privacy, replay, and live
  viewing semantics.
- [Reproduction guide](reproduction.md) — repeatable local/Docker commands and
  the scope of the recorded execution.
- [Source provenance](SOURCE_PROVENANCE.md) — pinned upstream source and
  vendoring boundary.
- [Known release gaps](KNOWN_RELEASE_GAPS.md) — deliberately unshipped or
  unvalidated release capabilities.
- [Reproduction run report](REPRODUCTION_RUN_REPORT.md) — the factual record
  of the final executed commands and their outcomes.

The source name in the provenance document is a historical source reference,
not a second public product name or a supported runtime path.
