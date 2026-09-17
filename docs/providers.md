# Models and providers

Each agent selects a named provider and can override its `default_model` with
`agents[].model`. Runtime model names belong to your gateway; use the exact names
it accepts. Deterministic `mock` providers need no credentials.

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

Never put credentials directly into YAML. Validation is local and does not
require credentials or call providers. `check-env` reports missing environment
references without displaying their values. Execution requires credentials only
when the corresponding live provider is called. Per-provider timeouts, retries,
and optional `fallback_provider` make failure handling explicit.

The public trace exposes provider/model names and structural decisions, not
provider request/response bodies. Run manifests record usage without credentials.
`configs/mixed-model.yaml` demonstrates distinct routing with two mock providers
and is tested offline; live endpoints require your own provider access.
