# ADR 0009：OpenAI Responses 生成模型适配器与无状态工具历史

- 状态：Accepted
- 日期：2026-08-25

## 决策点

通用 `ModelProvider` 如何接入真实 OpenAI 生成接口，同时保持供应商可替换、并发运行隔离、工具调用历史
可恢复、重试边界可审计，并保证供应商异常、请求正文和凭证不会泄露到运行事件或项目记忆。

## 候选方案

| 方案 | 优势 | 劣势 | 适用场景 |
|---|---|---|---|
| 手写 HTTP + 自定义响应模型 | 协议控制最直接 | 重复维护 OpenAPI、错误类型和 SDK 兼容，泄漏风险高 | SDK 不可用时 |
| SDK + provider 内 `previous_response_id` | 请求历史较短 | 共享实例易串话，进程重启和审批检查点无法仅凭事实状态恢复 | 单会话短进程 |
| 官方 SDK + 运行时完整历史 + 隔离供应商状态 | 并发、恢复、验证和替换边界完整 | 重复发送历史，需保存 reasoning 输出项 | 当前项目 |

## 决策

选择“官方异步 SDK + Responses API + 运行时完整历史 + 隔离供应商状态”。

### 1. 使用 Responses API，不接入旧 Chat Completions 私有结构

`OpenAIModelProvider` 调用 `responses.create`。系统、用户和 assistant 文本进入 `input`；工具定义使用
Responses 的扁平 `type/name/description/parameters/strict` 结构。业务输入/输出 Schema 深拷贝后原样发送，
适配器不自动补 required、删除字段或放宽 `additionalProperties`。

### 2. 工具调用历史属于运行时事实

运行时在执行工具前追加一个包含 `tool_calls` 的 assistant 消息，工具执行后再追加包含相同 `call_id` 的
tool 消息。OpenAI 输出中的 reasoning、message 和 function call 原始顺序保存在隔离 `provider_state`；
下一轮由同一适配器校验其与供应商无关字段一致后重放。provider 实例不保存运行级会话状态，也不使用
进程内 `previous_response_id`。

这使多运行可以共享一个连接池，同时为未来“审批后恢复”保留完整 assistant 调用和 tool 输出。
旧版本中缺少 assistant tool call 的悬空历史不会被猜测修复，而是失败关闭。

### 3. 输出和工具白名单严格校验

只接受 completed 响应中的 reasoning、assistant output text 和 function call。每个 function call 必须：

- 具有非空且唯一的 `call_id`；
- 工具名存在于本次 `ModelRequest.tools`；
- arguments 是合法 JSON 对象；
- 在后续历史中恰好有一个名称一致的 tool output。

重复工具定义、未知工具、重复调用 ID、非对象参数、provider state 篡改、悬空/乱序/重复 tool output、
refusal、未知输出项、负 token 用量以及无文本且无工具调用全部转换为安全 `ModelProviderError`。

### 4. 显式重试、幂等和数据边界

SDK 内建重试设置为 0。适配器只对超时、HTTP 429 和 5xx 执行配置上限内的指数退避；400 等参数错误
不重试。模型、输入、工具 Schema 和输出上限经过规范化 JSON 后生成稳定 SHA-256 幂等键，同一次逻辑请求
的所有重试复用该键。

请求使用 `store=false`、`truncation=disabled`、明确总超时和最大输出 token。API Key 通过
`SecretStr`/环境变量或注入客户端进入 SDK；任意运行 metadata 不发送给 OpenAI。异常只保留安全分类和
可选 HTTP 状态码，不保留 SDK 原始消息、请求/响应正文或传输异常文本。

## 反选论证

- 不选手写 HTTP：认证、连接池、响应模型和错误兼容都由项目重复维护，成本和安全面大于收益。
- 不选 provider 内 `previous_response_id`：同一实例服务并发运行时缺少可靠关联，进程重启后也没有事实来源。
- 不选 SDK 默认重试：默认错误集合和项目规则不完全一致，双层重试会放大延迟和可能费用。
- 不选静默修复工具 Schema：修改业务 Schema 会让模型调用约束与注册表验证约束不一致，难以审计。
- 不选未知工具交给运行时再失败：供应商边界已掌握白名单，应在进入策略和执行层前尽早拒绝。

## 接受的代价

- 完整历史会增加输入 token；后续应通过可审计上下文压缩解决，不能用隐藏 provider 会话状态替代。
- reasoning 加密状态会进入审批检查点等运行状态，但不会进入普通事件日志；持久化层需继续按敏感运行数据保护。
- strict tool 要求领域工具 Schema 本身完整；不兼容 Schema 会在 API 边界显式失败而不是被适配器改写。
- 默认模型只是可覆盖生产默认值，账户权限、地区可用性和成本仍由部署环境负责。

## 撤销条件

- 若 PostgreSQL 引入可审计的供应商会话映射，并证明并发隔离、恢复、保留期和跨版本兼容，可评估
  `previous_response_id` 作为优化，但完整工具历史仍保留为事实来源。
- 若输入历史成本超过领域 SLA，先引入版本化上下文压缩和回归评测，不允许静默截断。
- 若官方 SDK 提供可精确声明重试错误集合、幂等键和观测回调的稳定策略接口，可删除项目手写退避循环。
- 若更换生成供应商，继续复用 `ModelProvider`、`Message.tool_calls` 和隔离 `provider_state` 边界，
  不让 OpenAI 私有字段进入领域逻辑。

## 验证

- Mock HTTP 契约覆盖普通文本、模型名、token 用量、Schema 保真和并行工具调用。
- 两轮请求覆盖 reasoning、多个 function call 和对应 function call output 的原顺序重放。
- 反例覆盖未知工具、重复工具/调用 ID、非法 JSON、非对象参数、乱序历史、状态篡改、拒绝和空输出。
- 重试覆盖 429 → 500 → 成功、稳定幂等键、400 不重试和超时/供应商正文脱敏。
- 运行时测试确认第二轮请求同时包含上一轮 assistant 工具调用和 tool 输出。
- 全量 Ruff、Mypy、PostgreSQL Pytest 和离线示例作为发布门禁，不执行真实收费请求。

## 相关实现

- `src/public_agent/providers/openai.py`
- `src/public_agent/providers/__init__.py`
- `src/public_agent/core/types.py`
- `src/public_agent/core/runtime.py`
- `src/public_agent/config.py`
- `tests/test_openai_provider.py`
- `tests/test_runtime.py`
