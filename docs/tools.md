# Tools and plugins

Built-ins are `files` (shared workspace read/write), `task_board` (list, claim,
assign, complete), `messaging` (configured channels), and `search` (visible
workspace content). Declare them in `tools.builtins` and grant each agent only
its selected `agents[].tools`. Agent and role permissions govern restricted
operations such as assigning other members' tasks.

A plugin is a trusted Python function:

```python
def execute(arguments, context):
    return {"word_count": len(arguments["text"].split())}
```

Register it relative to your YAML file:

```yaml
tools:
  plugins:
    - id: word_count
      path: tools/word_count.py
      entrypoint: execute
      schema:
        type: object
        properties:
          text: {type: string}
        required: [text]
        additionalProperties: false
      timeout_seconds: 2
      side_effect_policy: none
```

Add `word_count` to the desired agent's tool list. `context` contains its stable
ID, current tick, organization ID, visible files, public tasks, and plugin
configuration. It is a snapshot, not the mutable world.

The supported argument schema subset is `type`, `properties`, `required`,
`additionalProperties`, `items`, `enum`, `minimum`, and `maximum`. Tool validation
runs before execution. Plugin failures publish an exception type, not raw
exception messages or arguments.

To return workspace artifacts, use `side_effect_policy: workspace` and return
`artifacts: [{id: ..., title: ..., content: ..., task_ids: [...]}]`. Artifacts are
registered through the existing company workspace after successful execution.
`external` effects additionally require the agent's `external` permission.
`none` and `read_only` prohibit returned artifact writes. Built-in file/task/message
tools default to workspace effects; configure `read_only` to prohibit mutations.

Plugins are trusted local code, not sandboxed programs. Timeouts stop waiting and
prevent late results from entering the world; they cannot terminate arbitrary
Python background work or undo a plugin's external side effects. Keep plugin
operations short and use network timeouts inside plugins that contact services.

See `examples/generic/custom-tool.yaml` and its `word_count.py` for a runnable
example. Inspector shows tool execution status without exposing plugin arguments,
private contents, or provider traffic.
