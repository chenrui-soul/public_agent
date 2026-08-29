# ADR 0011：工具审批 checkpoint、恢复租约与 fencing

- 状态：Accepted
- 日期：2026-08-25

## 决策点

高风险工具在 `waiting_approval` 后如何跨进程、跨服务实例安全恢复，使人工批准只授权原始调用，
同时抵抗重复决定、并发恢复、进程崩溃、租约过期、旧 worker 覆盖、跨租户访问和运行时定义漂移。

## 候选方案

| 方案 | 优势 | 劣势 | 适用场景 |
|---|---|---|---|
| 审批请求线程直接继续执行 | 实现简单、延迟低 | HTTP 断开或多副本并发后无法确定 owner，崩溃可能重复副作用 | 单进程原型 |
| 只保存 approval 状态，恢复时重新规划 | checkpoint 小 | 会重新检索记忆/RAG，模型和工具调用可能漂移，批准内容不再精确 | 不采用 |
| 不可变 checkpoint + PostgreSQL 租约和 fencing | 可审计、可接管、精确重放、旧 owner 不能覆盖 | 需要持久化状态、租约管理和工具端幂等 | 当前生产基线 |

## 决策

选择“不可变运行 checkpoint、精确人工决定、有限 PostgreSQL 恢复租约和工具端稳定幂等键”。

### 1. checkpoint 保存恢复所需事实

`RunCheckpoint` 保存 assistant 工具调用已经加入后的完整消息历史、隔离供应商状态、当前及剩余
tool calls、RAG 引用 ID、step、agent spec 哈希，以及待审批工具的版本和完整定义哈希。恢复不重新
召回记忆、RAG 或重新请求模型猜测原调用。

审批只授权 `pending_approval.tool_call.id`。该调用成功后，同一响应中的剩余调用按原顺序继续；
遇到另一个高风险调用时创建新的 approval 和 checkpoint，前一次批准不能扩散到后续调用。

### 2. PostgreSQL 决定审批和恢复所有权

`prepare_resume` 在单事务内校验 tenant、agent、run、agent version 和 approval 作用域，并锁定 run 与
approval。首次决定追加 `approval.decided`；相同决定重放幂等，不同决定拒绝。批准后签发随机
`resume_token` 和有期限的 `resume_lease_expires_at`，并将 run 置为 `running`。

活动租约拒绝第二个 worker。租约过期后，新 worker 可以在行锁下签发新 token 并追加 reclaim 审计。
`finish` 必须同时满足 token 匹配且租约未过期；旧 token、过期 token 或无 owner 的 token 都不能写回。
数据库检查约束保证 token/lease 同时为空，或在 `running` 状态下同时存在。

### 3. 外部副作用依赖稳定幂等键

租约和 fencing 只能阻止数据库状态被旧 worker 覆盖，不能消除“外部副作用成功、本地提交前崩溃”的
窗口。因此可恢复审批工具必须声明 `idempotent=true`，运行时向工具传入稳定
`run_id:tool_call_id`。工具实现必须把该键持久化到自身去重表，或透传给支持幂等键的下游系统。

### 4. 拒绝和漂移全部失败关闭

拒绝决定在事务中把 run 置为 `canceled`，清除 checkpoint 和租约，不调用工具，也不进入成长沉淀。
agent spec、工具版本或定义、approval/run 绑定、租户/agent 作用域、checkpoint 结构、非幂等声明发生
变化时不尝试修复或重新规划，直接失败关闭。

`resume_token` 是内部所有权能力，不进入 `run.resume.claimed/reclaimed` 普通事件。事件只记录 approval、
租约秒数和到期时间，避免通过运行轨迹泄漏 bearer token。

## 反选论证

- 不选 HTTP 请求线程直接恢复：多副本、客户端重试和连接断开都会产生无法审计的重复执行窗口。
- 不选 Redis 锁作为事实来源：锁过期、淘汰或故障不能决定 PostgreSQL 运行状态，且无法原子写审批审计。
- 不选恢复时重新检索和重新规划：人工批准的是原始调用，不是未来某次模型生成的新调用。
- 不选“租约只看 token 不看到期时间”：过期 worker 可能抢在接管者前写回，违反租约语义。
- 不把 resume token 写入事件：事件和 trace 的读取范围通常大于内部运行所有权接口。
- 不依赖租约代替工具幂等：外部系统成功而本地崩溃的窗口只能由下游幂等或本地 outbox 消除。

## 接受的代价

- 首版没有租约续期；单次工具和后续模型循环必须在默认 300 秒内完成，否则由新 worker 接管。
- PostgreSQL 同时保存 checkpoint 和 approval 请求快照，换取独立审计和精确重放。
- 工具作者必须实现真实去重语义，不能只把 `idempotent=true` 当作描述字段。
- 当前是 Python 应用服务接口；认证审批 API、职责分离和 worker 调度留到后续管理 API Wave。

## 撤销条件

- 引入持久队列 worker 后，可把 claim 触发迁移到消费层，但 run 行锁、租约和 fencing 语义保留。
- 工具或模型循环经常超过租约时，增加带 token 校验的有限续租；禁止无 owner 续租和无限延长。
- 本地事务型工具可以增加 transactional outbox，外部工具仍保留稳定调用幂等键。
- 当 checkpoint 体积达到数据库保留阈值时迁移到加密对象存储，PostgreSQL 保留内容哈希、对象版本和
  审批绑定，恢复语义不变。

## 验证

- 运行时单元测试覆盖精确批准、稳定幂等键、连续高风险审批、agent/tool 漂移和非幂等工具拒绝。
- PostgreSQL 集成测试覆盖审批/等待点原子持久化、批准完成、拒绝零执行、相同决定终态重放和不同决定拒绝。
- 两个真实并发 session 竞争同一 approval 时只有一个获得活动租约；过期租约可被新 token 接管。
- 过期 worker 和接管后的旧 token 均不能 finish；claim/reclaim 事件不包含 resume token。
- 同名 agent 的另一租户不能读取或领取原租户 run；迁移往返和数据库检查约束通过。
- 全量 Ruff、Mypy、129 个 PostgreSQL Pytest、离线领域包、计算器和中文 RAG 作为最终门禁。

## 相关实现

- `src/public_agent/core/types.py`
- `src/public_agent/core/runtime.py`
- `src/public_agent/tools/base.py`
- `src/public_agent/application.py`
- `src/public_agent/storage/models.py`
- `src/public_agent/storage/runs.py`
- `migrations/versions/c73f9a2d4e10_add_approval_resume_leases.py`
- `migrations/versions/d84e1b6f5a20_harden_resume_lease_invariants.py`
- `tests/test_runtime.py`
- `tests/test_postgres_approval_resume.py`
