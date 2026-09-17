# Models and providers

Each agent selects a named provider and can override its `default_model` with
`agents[].model`. Runtime model names belong to your gateway; use the exact names
it accepts. Deterministic `mock` providers need no credentials.

The accepted provider type names are `mock` (also `deterministic`),
`openai_compatible` (also `openai`), `anthropic_compatible` (also `anthropic`),
and `generic_http` (also `http` or `generic`). Provider IDs are the keys under
`providers`; an agent's `provider` must refer to one of those keys.

```yaml
providers:
  writer:
    type: openai_compatible
    base_url_env: MODEL_BASE_URL
    api_key_env: MODEL_API_KEY
    default_model: your-writer-model
    timeout_seconds: 60
    retry_count: 2
  reviewer:
    type: anthropic_compatible
    base_url_env: REVIEW_BASE_URL
    api_key_env: REVIEW_API_KEY
    default_model: your-review-model
agents:
  - id: author
    role: researcher
    provider: writer
    generation: {temperature: 0.2, max_tokens: 1024}
    tools: [files, task_board]
  - id: critic
    role: reviewer
    provider: reviewer
    model: your-other-review-model
    tools: [files, task_board]
```

Set the referenced variables in your shell or in `.env` beside the config:

```dotenv
MODEL_BASE_URL=https://your-openai-compatible-endpoint/v1
MODEL_API_KEY=your-own-key
REVIEW_BASE_URL=https://api.anthropic.com
REVIEW_API_KEY=your-own-key
```

`openai_compatible` reuses the shared `OpenAIOrgLLMClient` and sends chat
completions by default. Add `parameters.wire_api: responses` when your gateway
implements the Responses API. An OpenAI base URL is passed to the client as-is;
the SDK appends its API path. `anthropic_compatible` appends `/v1/messages` to
an endpoint that ends at the host, `/`, or `/v1`; a full messages URL is used as
given. `generic_http` posts to the configured endpoint as-is with a JSON body
containing `model`, `system`, `prompt`, `temperature`, and `max_tokens`. Its
response may expose text through `text`, `content`, `result`, `output`, or a
standard `choices` message; a direct JSON object is accepted for
`generate_json`.

Generation settings are validated for the selected provider. OpenAI supports
`temperature`, `max_tokens`/`max_output_tokens`, and reasoning `effort`;
Anthropic also supports `top_p`, `top_k`, `stop_sequences`, and thinking budgets.
Generic HTTP forwards the supported sampling options and a reasoning object.
Agent settings override provider defaults, including when agents share a model.
Unknown provider parameters are rejected rather than silently ignored. Optional
`parameters.pricing` rates (for example `input_cost_per_1k` and
`output_cost_per_1k`) enable estimated cost reporting.

The router is an `OrgLLMClient`-compatible object. A runtime that has an agent
ID can use an explicit route:

```python
from relic_agent.config import load_config
from relic_agent.runtime.providers import ProviderRegistry

config = load_config("organization.yaml")
providers = ProviderRegistry(config)
answer = providers.client_for_agent("author").generate_text("system", "prompt")
structured = providers.generate_json_for_agent(
    "critic", "system", "prompt", {"type": "object", "required": ["answer"]}
)
```

When shared lifecycle code calls the two-argument client API, select the agent
for that turn with `with providers.use_agent("author"):`. Calls retry up to the
provider's `retry_count`, then follow its optional `fallback_provider`.
Transport failures expose only structural codes such as `timeout`,
`transport_error`, or `http_status_503`; response bodies and credentials are
never included in provider errors or usage stats.

`runtime.decision_mode` controls the organization decision policy (the default
is `profile_policy`); it does not select a provider. Provider routing comes from
`agents[].provider`, `agents[].model`, and the provider defaults above. All
clients are lazy: environment references are checked only when that route makes
a model call, so `validate` and dry-run paths do not contact a provider.

Never put credentials directly into YAML. Validation is local and does not
require credentials or call providers. `check-env` reports missing environment
references without displaying their values. Execution requires credentials only
when the corresponding live provider is called. Per-provider timeouts, retries,
and optional `fallback_provider` make failure handling explicit.

The public trace exposes provider/model names and structural decisions, not
provider request/response bodies. Run manifests record usage without credentials.
`configs/mixed-model.yaml` demonstrates distinct routing with two mock providers
and is tested offline; live endpoints require your own provider access.
