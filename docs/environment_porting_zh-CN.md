# 将 Relic Agent 移植到新环境

简体中文 · [English](environment_porting.md)

Relic Agent 的组织生命周期和成员实际工作的环境是两个不同的层次。换环境时，可以复用组织生命周期，但必须重新检查三组环境相关的约定：**SDL 动作策略（如果使用）、Protocol 与动作的绑定、Episode 识别与反思调度**。只更换模型、成员或注册工具，并不等于完成环境移植。

本指南针对当前通用配置 `relic-agent-v2`。`relic-agent-source-native-v1` / `run-source-b3` 保留源 B3 的既定行为，不适合作为修改新环境的配置入口。

## 1. 先决定是否让 SDL 选择动作

对于长期运行、规则稳定的单一环境，可以用 SDL 编码环境特定的动作策略。首先明确要采用哪种选择机制：

| `runtime.decision_mode` | 动作如何选择 | 移植时的责任 |
|---|---|---|
| `profile_policy` | SDL 评分；人格条件开启时参与评分，并通过有种子的采样选择动作。 | 为每种相关动作适配新环境的特征和效用。 |
| `flat_deterministic` | 仍使用评分策略，关闭人格条件并取最高分。 | 仍需适配特征和评分；这不是关闭 SDL 评分。 |
| `llm_direct` | 配置的 provider 从允许的候选动作中选择。 | SDL 不负责选择动作；仍需适配候选动作、可见上下文、工具 schema、权限和执行。 |

当前没有 `sdl.enabled` 开关。`runtime.sdl` 用来定制评分和采样，不是启停开关。`llm_direct` 下共享循环仍会提取候选动作特征。学习、协议形成、反思各有独立设置，切换动作选择模式不会自动关闭它们。

**如果使用 SDL，就要逐一检查所有动作**，包括共用同一个动作类型的不同工具。为动作及其参数、当前状态定义进展、证据或审阅收益、成本、风险和特征尺度，确保作用不同的动作能得到有区别的评分。现有通用适配器对若干任务动作和所有 `use_tool` 候选使用较粗的进展估计；注册新工具不会自动生成它在新环境里的策略。没有适配的动作可能只得到通用成本或零收益，也不一定报覆盖缺失错误。

当前可以分三个层次修改：

- **特征语义不变，只改偏好：**配置 `runtime.sdl.base_weights` 和 `profile_coefficients`。
- **替换评分逻辑：**配置 `runtime.sdl.scorer`。`execute(features, context)` 接收特征字典和包含动作、参数、成员、tick、scorer 配置的快照，返回一个有限数值的效用。自定义 scorer 替代加权特征求和。可参考[可运行配置](../examples/generic/custom-sdl.yaml)和[评分函数](../examples/generic/sdl_score.py)。
- **状态语义改变或需要新特征维度：**实现并接入 Python 特征适配器。当前 YAML 不能注册特征提取器，权重字段也只接受现有特征名；新增维度还需同步适配评分与配置。Scorer 的快照上下文不能直接访问任意可变环境状态。

代码入口是 [`runtime/builder.py`](../relic_agent/runtime/builder.py) 中的 `GenericActionMapper`、`GenericFeatures`、`GenericExecution` 及其组装。现有特征定义在 [`feature_extractor.py`](../environments/org_env/runtime_adapter/feature_extractor.py)，通用评分扩展在 [`runtime/sdl.py`](../relic_agent/runtime/sdl.py)。这些是 Python 实现的修改位置，当前并没有一个自动注册任意环境适配器的接口。准确配置字段见[配置参考](configuration.md#sdl-decision-policy)。

## 2. 重新绑定 Protocol 和可执行动作

协议的提出、审阅、采纳、使用、修订和废止流程可以复用；**协议在新环境里究竟检查什么、怎样影响动作，必须重新审查**。每条协议至少要定义：

- 适用的动作标识及参数条件，涉及哪些成员／角色、对象和作用范围。
- 由什么事件或动作尝试触发，在新环境里哪些证据或前置步骤可以满足它。
- 实际执行的判断条件，以及阻止、通知或环境已实现的其他响应；如何记录成功和失败。
- 检查应放在哪个执行边界，组合动作／学习到的工具是否也经过它。阻止动作的协议必须在环境发生副作用之前检查。

例如，新环境里的发布动作可能要求“**同一份产物的同一版本**已经通过测试”。这是需要实现和验证的领域规则，写一条人类可读的 checklist 并不会自动执行它。移植时，已有协议包也应按新动作和新证据的含义重新验证，之后才能作为可执行协议使用。

当前通用执行门只支持[治理说明](governance.md)中列出的作用范围、成员选择、动作别名，以及任务证据／审阅检查；不会将任意文字解释成代码。普通插件调用的动作类型是 `use_tool`，把插件 ID 写进协议不等于已经实现针对该工具的检查。任意工具、消息或外部副作用都需要明确的绑定和执行实现。

相关代码在 [`runtime/lifecycle.py`](../relic_agent/runtime/lifecycle.py)：`_protocol_applies`、`_matching_protocols`、`_protocol_gate_failure`、`before_action`、`after_action`。新增动作时，还应沿着 `GenericExecution` 和工具／任务边界检查直接执行与组合执行路径，保证它们遵守同样的规则。协议使用、违规和结果记录要关联到实际执行的动作。

## 3. 重新定义 Episode 和反思触发

Episode 是一段相关事件及其结果，不必等于一个 tick、一个任务或一次模型调用。每次切换环境，都要重新考虑以下定义：

| 边界 | 新环境需要定义的内容 |
|---|---|
| 事件归一化 | 稳定的成员、动作、对象／任务 ID、时间、频道，以及实际观察到的结果。 |
| 开启与归入 | 哪些事件开启 episode，哪些事件属于同一段经历，如何分开同时发生但不相关的工作。 |
| 结束与结果 | 新领域里的成功、失败、放弃、静默和规模限制；用什么证据确认结果。 |
| 反思 | 哪些成员参与，提供哪些 episode／事件上下文，采用什么周期、冷却时间、显著性门槛，以及怎样避免重复处理同一段经历。 |

当前实际运行的检测器是 [`OrgEpisodeManager`](../environments/org_env/episodes/episode_manager.py)：消费动作结果和世界事件，做归一化、开启／归入和结束判断。领域事件分类、对象分类和 episode 兼容关系在 [`episode.py`](../environments/org_env/episodes/episode.py)。如果新环境的对象、结果或因果关系不同，就要适配这些研究／产品工作流的既有假设。当前 YAML 没有任意 episode 检测器的回调注册字段。

新增世界级事件类型时，还需适配 `OrgWorld._EPISODE_WORLD_EVENTS` 和 `_observe_world_episodes`，确保共享循环实际把事件送入检测器；仅向 `world.events` 添加任意事件还不够。动作结果中的事件已经通过 `observe_result` 处理，应避免重复观察。

**Episode 结束和反思触发是两件事。** 当前共享的 `OrgWorld.step()` 流程会在 episode 结束时生成摘要，反思则通过批次管理器运行。通用配置提供 `learning.reflection.enabled`、`cadence_ticks`、`per_agent_cooldown`、`salience_threshold`；这些字段调节调度，不负责定义新领域的 episode。如果要求每段 episode 结束后立即反思，就需要明确实现这种调度，不能假定当前代码已经如此。通用调度适配在 [`runtime/lifecycle.py`](../relic_agent/runtime/lifecycle.py)，共享 step 在 [`world.py`](../environments/org_env/backend/simulation/world.py)。

## 可以复用什么，以及如何验证移植

在接口约定仍适用的情况下，可以保留组织生命周期、模型路由、配置与运行管理、治理流程和可观察性设施。环境观察、动作可用性和执行、工具 schema、结果证据，要和上面三组约定一起适配。工具可以沿用[插件接口](tools.md)，更深的特征、协议判断和 episode 分类改变则需要 Python 适配。

长时间运行真实模型前，先用短的确定性样例检查：

1. 列出所有原生动作／工具的参数、权限、副作用、产生的事件和成功／失败证据。使用 SDL 时，逐项检查特征与评分是否覆盖。
2. 检查代表性动作是否改变真实环境状态，并准确报告结果；工具调用成功不等于任务已完成。
3. 分别用缺失和有效证据触发协议，确认阻止发生在状态修改之前，组合动作也不能绕过；通知模式单独检查。
4. 回放相关和不相关的事件序列，检查 episode 的开启、归入、结束、结果，以及预期的反思时机。
5. 检查 trace／Inspector 中的关联和环境自己的结果标准。确定性样例验证的是接线，不能代替真实模型表现或研究结论。

可以先运行已有的离线配置检查：

```bash
uv run relic-agent validate --config my-org/organization.yaml
uv run relic-agent validate --config examples/generic/custom-sdl.yaml
```

这能验证支持的字段和引用，不能证明新领域的动作评分、协议条件或 episode 定义已经正确。
