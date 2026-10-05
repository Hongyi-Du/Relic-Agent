# Porting Relic Agent to a new environment

[简体中文](environment_porting_zh-CN.md) · English

Relic Agent separates the organization lifecycle from the environment in which
members act. When moving to a new environment, reuse that lifecycle but review
three environment-specific contracts: **SDL action policies (if used),
protocol–action bindings, and episode detection plus reflection scheduling**.
Changing providers, members, or tools alone does not complete a port.

This guide describes the current generic `relic-agent-v2` runtime. The
`relic-agent-source-native-v1` / `run-source-b3` preset preserves canonical B3
behavior; it is not the configurable starting point for a new environment.

## 1. Decide whether SDL should select actions

For a single, stable environment that will run over a long period, SDL can be
used to encode environment-specific action policies. First decide whether that
is the desired selection mechanism:

| `runtime.decision_mode` | Action selection | Porting responsibility |
|---|---|---|
| `profile_policy` | SDL scores candidates with profile conditioning when enabled and seeded sampling. | Adapt each relevant action's features and utility to the new environment. |
| `flat_deterministic` | The scoring policy uses argmax with profile conditioning off. | Still adapt features and scores; this does not disable SDL scoring. |
| `llm_direct` | The configured provider chooses from the allowed candidate pool. | SDL does not select the action. Adapt candidate generation, visible context, tool schemas, permissions, and execution. |

There is no `sdl.enabled` switch. `runtime.sdl` customizes scoring and sampling;
it does not turn the layer on or off. The shared loop still extracts candidate
features in `llm_direct`. Learning, protocol formation, and reflection have
separate settings; changing the decision mode does not disable them.

If SDL is used, review **every action**, including different tools represented
by the same action type. Define the relevant state and parameters, expected
progress, evidence or review gains, costs, risks, and feature scales. Actions
with different effects need distinguishable scores. In the stock generic
adapter, several task actions and all `use_tool` candidates share a broad
progress estimate. Registering a plugin does not create a domain-specific
policy for it, and an unmapped action may receive generic costs or zero gains
without producing a coverage error.

The current extension points are:

- **Existing features, different preferences:** configure
  `runtime.sdl.base_weights` and `profile_coefficients`.
- **Different scoring logic:** configure `runtime.sdl.scorer`. Its
  `execute(features, context)` receives the feature dictionary and a snapshot
  including the action, parameters, agent, tick, and scorer config; it returns
  one finite utility. The custom scorer replaces the weighted feature sum.
  See the runnable [custom SDL example](../examples/generic/custom-sdl.yaml)
  and [scorer](../examples/generic/sdl_score.py).
- **New state semantics or feature dimensions:** implement and wire a Python
  feature adapter. YAML does not register a feature extractor, and weight keys
  are limited to the existing feature vocabulary. New dimensions also need
  compatible scoring/configuration changes. A scorer cannot access arbitrary
  mutable environment state through its snapshot context.

For code changes, start with `GenericActionMapper`, `GenericFeatures`,
`GenericExecution`, and their wiring in
[`runtime/builder.py`](../relic_agent/runtime/builder.py). The existing feature
definitions are in
[`feature_extractor.py`](../environments/org_env/runtime_adapter/feature_extractor.py);
generic scoring customization is in
[`runtime/sdl.py`](../relic_agent/runtime/sdl.py). These are Python implementation
seams, not a promised plug-and-play environment-adapter registration API.
See [configuration](configuration.md#sdl-decision-policy) for exact fields.

## 2. Rebind protocols to executable actions

A protocol's organizational lifecycle can be reused: proposal, review,
adoption, use, revision, and retirement. Its **execution semantics must be
reviewed for the new environment**. For each protocol, define:

- The affected action identifiers and parameter conditions, agents/roles,
  objects, and scope.
- What event or attempted action triggers it, and what evidence or prior steps
  satisfy it in the new environment.
- The executable predicate and response: block, notify, or the environment's
  implemented response; how success and failure are recorded.
- Where the check runs, including composed/learned-tool paths. A blocking
  protocol must check before the environment applies the side effect.

For example, a new environment's release action may require a passing test
result for the **same artifact revision**. That is a domain rule to implement
and verify, not something a human-readable checklist automatically enforces.
On a port, revalidate loaded protocol packages against the new action and
evidence meanings before treating them as executable.

The current generic gate supports the scopes, selectors, action aliases, and
task evidence/review predicates documented in [governance](governance.md).
It does not interpret arbitrary prose as code. Ordinary plugin calls use the
action type `use_tool`; listing a plugin's ID in a protocol does not by itself
provide a tool-specific check. Arbitrary tool, messaging, or external side
effects need an explicit binding and enforcement implementation.

The relevant generic code is in
[`runtime/lifecycle.py`](../relic_agent/runtime/lifecycle.py):
`_protocol_applies`, `_matching_protocols`, `_protocol_gate_failure`,
`before_action`, and `after_action`. Trace a new action through
`GenericExecution` and the tool/task boundaries so its direct and composed
paths obey the same rule. Keep protocol use, violation, and outcome records
connected to the action that actually ran.

## 3. Redefine episodes and reflection triggers

An episode is a related sequence of events with an outcome, not necessarily
one tick, one task, or one model call. Reconsider its definition whenever the
environment changes:

| Boundary | What the adapter must establish |
|---|---|
| Event normalization | Stable actors, actions, object/task IDs, time, channels, and observed results. |
| Opening and attachment | Which events start an episode and which belong to it; how to keep simultaneous, unrelated work separate. |
| Closure and outcome | Domain-specific success, failure, abandonment, inactivity, and size limits; what evidence establishes the outcome. |
| Reflection | Eligible participants, relevant episode/event context, cadence, cooldown, salience, and deduplication of processed experience. |

The active OrgEnv detector is
[`OrgEpisodeManager`](../environments/org_env/episodes/episode_manager.py).
It consumes action results and world events, normalizes them, opens/attaches
episodes, and checks closure. Domain event families, object classification,
and episode compatibility are in
[`episode.py`](../environments/org_env/episodes/episode.py). Their existing
research/product-workflow assumptions need adaptation when the new environment
has different objects, outcomes, or causal relationships. The current YAML
schema does not provide an arbitrary episode-detector callback.

For new world-level event families, adapt `OrgWorld._EPISODE_WORLD_EVENTS` and
`_observe_world_episodes` so the shared loop actually forwards them to the
detector. Appending an arbitrary event to `world.events` is not sufficient.
Action-result events already pass through `observe_result`; avoid duplicate
observation.

**Episode closure and reflection are separate.** In the current shared
`OrgWorld.step()` flow, closure summarizes the episode; reflection runs through
a batch manager. Generic configuration exposes
`learning.reflection.enabled`, `cadence_ticks`, `per_agent_cooldown`, and
`salience_threshold`. Those settings tune scheduling, not the domain's episode
definition. If a port requires reflection immediately after every episode,
implement that scheduling behavior explicitly rather than assuming it already
exists. The generic scheduling adaptation is in
[`runtime/lifecycle.py`](../relic_agent/runtime/lifecycle.py), and the shared
step is in
[`world.py`](../environments/org_env/backend/simulation/world.py).

## Reuse and port validation

Keep the organization lifecycle, provider routing, configuration/run handling,
governance flow, and observability infrastructure where their contracts still
fit. Adapt environment observations, action availability/execution, tool
schemas, and outcome evidence together with the three contracts above.
Tools can use the existing [plugin interface](tools.md); deeper changes to
features, protocol predicates, or episode ontology require Python adaptation.

Before running a long live organization, check a short deterministic port:

1. Inventory all native actions/tools and their parameters, permissions, side
   effects, emitted events, and success/failure evidence. If using SDL, include
   feature/scoring coverage for every action.
2. Check that representative actions change the real environment state and
   publish accurate outcomes; a successful tool call alone is not task success.
3. Exercise an applicable protocol with missing and valid evidence. Verify
   blocking occurs before mutation and cannot be bypassed through a composed
   action; verify notify behavior separately.
4. Replay related and unrelated event sequences. Check episode opening,
   attachment, closure, outcomes, and the intended reflection schedule.
5. Check the resulting trace/Inspector relationships and the environment's
   outcome criteria. Deterministic fixtures validate wiring, not live-model
   performance or research results.

Start with the existing offline configuration/example checks:

```bash
uv run relic-agent validate --config my-org/organization.yaml
uv run relic-agent validate --config examples/generic/custom-sdl.yaml
```

These validate supported fields and references; they cannot establish that a
new domain's action scores, protocol predicates, or episode meanings are correct.
