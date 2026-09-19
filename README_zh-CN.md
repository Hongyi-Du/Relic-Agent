# Relic Agent

简体中文 · [English](README.md)

Relic Agent 用配置文件运行可定制的 agent 组织：成员完成共享任务、从工作摩擦中反思和提出改进，并通过治理流程采纳、执行、修订或废止组织协议。通用组织和 Relic B3 预设共用 `OrgWorld.step()` 生命周期。论文实验矩阵和外部基准评测属于独立的 Relic 仓库，不是这里的默认运行结果。

## 五分钟运行

需要 Python 3.12+；推荐 Linux 或 WSL2：

```bash
git clone https://github.com/Hongyi-Du/Relic-Agent.git
cd Relic-Agent
uv sync --extra dev --frozen
uv run relic-agent init my-org
uv run relic-agent validate --config my-org/organization.yaml
uv run relic-agent run --config my-org/organization.yaml --run-id first
uv run relic-agent inspect --trace outputs/first/trace.json
```

浏览器打开 <http://127.0.0.1:8765>。初始配置是两个成员和一个共享任务，使用确定性 provider，不需要 API 密钥，也不能据此声称真实模型的表现。修改 `my-org/organization.yaml` 后请使用新的 run ID。产出包括配置副本、运行与状态记录，以及经过隐私过滤的 `trace.json`。

## 定制和检查

- 在 `agents` 中配置成员、角色、技能、权限，以及每个成员的 provider 和 model；密钥和端点可从项目 `.env` 引用。参见[模型接入](docs/providers.md)。
- 在 `tasks` 中配置任务和交付物，授权内建工具或注册 Python 插件。参见[工具](docs/tools.md)和[定制指南](docs/customization.md)。
- 配置审批人、法定人数、初始协议和学习参数。协议的通用执行语义有明确边界，参见[治理说明](docs/governance.md)。
- Inspector 的公开 trace 展示任务、成员、治理和结构化行为，不包含私有记忆、原始反思、prompt 或 provider 流量。本机 loopback 查看自己运行的最终共享工作区文本，不会把正文写入公开 trace；见 [Inspector 说明](docs/inspector.md)。

```bash
uv run relic-agent run-minimal    # 两成员确定性示例
uv run relic-agent run-default    # 八成员确定性研究组织示例
uv run relic-agent run-source-b3  # 源 B3 兼容预设
uv run relic-agent check-env --config my-org/organization.yaml
uv run relic-agent replay --trace outputs/first/trace.json
```

配置字段见[配置参考](docs/configuration.md)，另有[可运行示例](examples/README.md)和 [Docker 指南](docs/docker.md)。运行真实模型前，请先验证自己的端点、凭据与预算；本仓库的确定性示例不是付费模型或论文结论的验收。

## 开发

```bash
uv run --extra dev pytest
uv run --extra dev ruff check relic_agent tests
```

Bash 和 PowerShell 启动器最终调用同一 CLI。历史提取与来源记录保留在 `docs/` 供维护者核对。

## 许可

Relic Agent 的原创源码以 [PolyForm Noncommercial License 1.0.0](LICENSE)
进行源码公开：遵守协议时，可以免费用于非商业目的，也可以修改和分发。
任何商业用途都需要事先取得 Hongyi Du 的单独书面授权；参见
[商业授权说明](COMMERCIAL_LICENSE.md)。

仓库中的第三方组件继续适用各自的许可证；仓库级许可证不会替代这些条款。
