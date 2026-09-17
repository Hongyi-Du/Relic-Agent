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
`additionalProperties`, `items`, `enum`, `minimum`, and `maximum`. The root describes
an arguments object; arrays may appear in its properties. Tool validation runs
before execution. Plugin failures publish an exception type, not raw
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

A plugin can return `status: pending` or `status: failed` when it did not finish
its work. Such calls do not satisfy task completion or apply returned artifacts.
Omitting `status` means successful completion. Read and review operations only
receive files visible to the acting agent, including when files are linked to a
shared task.

Deterministic tasks can request a plugin through
`tasks[].metadata.tool_calls: [{tool: word_count, arguments: {text: example}}]`.
The configured owner must have the tool grant; successful calls are required
before task completion. In `runtime.decision_mode: llm_direct`, the selected
agent sees its granted tool schemas and can choose its own plugin arguments.

Learned tools use the same `ToolSpec` objects produced by proposal adoption.
Generic mode exposes an active spec only when its `callable_by_agents` and
`callable_by_roles` grants match the caller and every `required_permissions`
entry is present in the caller's effective permissions. For example, a tool can
declare `required_permissions: [use_learned_tools]` and the intended agents can
receive `use_learned_tools` in their config `permissions`.

The generic executor composes only existing generic operations named in
`required_actions` (`claim_task`, `work_on_task`, `review_doc`,
`complete_task`, `send_message`, the built-in `files`/`task_board`/
`messaging`/`search` operations, or a configured plugin). Unknown action names,
missing grants, and nested learned-tool calls fail closed. A `tool_use_event` is
published only when the composition changes a task or workspace file.
