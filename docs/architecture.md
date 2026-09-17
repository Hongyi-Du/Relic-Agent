# Runtime architecture

Both public modes execute the same `OrgWorld.step()` and its episode, reflection,
wish, proposal, governance, protocol, communication, and capability machinery.
The CLI does not select actions or maintain another simulation state.

- The **generic builder** creates members, task specifications, tools, provider
  bindings, context, and governance from validated `relic-agent-v2` configuration.
- The **source B3 builder** constructs the canonical source scenario unchanged.
  Its fixed roster and product backlog are an explicit compatibility preset.
- `OrganizationRuntime` manages run directories, stepping, status, manifests,
  and public projection. Builders determine initial state and policy settings.
- Provider adapters route each agent to its configured model. Deterministic
  operation is the default for examples; live providers are explicitly configured.
- Tool registration and permissions sit at the action boundary. New plugins use
  this boundary instead of changing world implementation.

Public projection is an allowlist, separate from internal debugger snapshots.
It exports safe identifiers and lifecycle relationships, configuration summaries,
and public work state. Private reflection text, memory, credentials, prompts,
and provider request/response bodies are excluded. Inspector consumes that public
trace, and replay verifies its schema and digest.

Historical source extraction and archived compatibility code remain documented
in `SOURCE_PROVENANCE.md`. They are not additional prerequisites for configuring
or running a generic organization.
