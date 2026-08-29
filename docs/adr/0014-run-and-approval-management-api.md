# ADR 0014：运行与审批管理 API

## 状态

已接受，2026-08-25。

## 背景

框架已经具备持久运行、不可变事件、审批 checkpoint、恢复租约、fencing token、领域能力包发布和
PostgreSQL API Token，但缺少一条面向调用方的安全运行管理纵向链路。若 API 重新实现运行或审批状态机，
会产生两套终态、重复工具执行和恢复语义漂移；若直接序列化内部 `RunResult`/checkpoint，又会泄漏
provider state、工具参数、定义哈希和恢复所有权。

## 候选方案

| 方案 | 优势 | 劣势 |
|---|---|---|
| API 内新建任务/审批状态机 | 端点实现直接 | 与运行内核形成双状态机，恢复和并发事实可能冲突 |
| 直接暴露内部 RunResult/checkpoint | DTO 少 | 泄漏供应商状态、工具参数和内部 token，客户端耦合内部格式 |
| 应用编排服务 + 现有 PostgreSQL 状态机 + 安全 DTO | 单一事实来源、可审计、可复用既有恢复语义 | 需要 active 领域包装配和只读管理投影 |

## 决策

选择第三种方案：

- `ActiveAgentAssembler` 只从 PostgreSQL 当前 active 领域包构造 `Agent`，并验证 agent key、agent version、
  manifest、内容哈希和资产索引；包内 domain id 只进入领域 metadata，不作为外部 agent key。
- `AgentRunManagementService` 复用 `PersistentAgentService`、`PostgresRunPersistence.prepare_resume` 和
  `finish`，不复制运行或审批状态机。
- `POST /v1/runs` 使用 tenant 级 `Idempotency-Key`，绑定 agent、active version、task 和完整运行上下文。
- `GET/POST run` 与 `GET/POST approval` 全部以认证 tenant + agent grant + permission 做服务端授权。
- run cancel 在行锁事务中取消 pending approval、清除 checkpoint 和恢复租约；canceled 终态拒绝旧
  runtime/resume owner 的 finish。
- HTTP 响应使用独立安全 DTO，只返回 run/approval 标识、状态、版本、步骤、最终输出和通用安全错误。
  不返回 checkpoint、provider state、resume token、工具参数、工具定义哈希或原始内部错误。

## 权限与端点

| 端点 | 权限 |
|---|---|
| `POST /v1/runs` | `runs:write` |
| `GET /v1/runs/{id}` | `runs:read` |
| `POST /v1/runs/{id}/cancel` | `runs:write` |
| `GET /v1/approvals/{id}` | `runs:read` |
| `POST /v1/approvals/{id}/decide` | `approvals:decide` |

tenant 永远来自 Bearer Principal。客户端 tenant header 无授权作用；运行 `user_id` 固定为认证 subject。

## 并发与幂等

- 相同幂等键和相同完整请求重放同一 run；agent、版本、task 或上下文变化返回冲突。
- 相同审批决定、actor 和 note 重放当前结果；不同决定失败关闭。
- 批准仍使用 checkpoint 中的精确 tool call 和稳定 `run_id:tool_call_id`。
- 活动恢复租约拒绝第二 owner；过期租约可按既有规则接管。
- cancel 清空恢复 token/lease；旧 owner 即使完成外部工作，也不能覆盖 canceled 数据库终态。

## 安全与容量

- task 上限 100,000 字符；metadata 最多 64 个键和 16 KiB 规范化 JSON。
- 客户端 metadata 禁止写入 `authorized_knowledge_access_tags` 等服务器授权事实。
- 原始数据库 error 不进入响应；失败、取消和超时使用稳定机器码。
- approval 原因返回通用安全说明，工具参数和 checkpoint 只保留在 PostgreSQL 内部审批事实中。
- 未配置运行服务或认证依赖时，不注册运行与审批路由。

## 代价与限制

- `POST /v1/runs` 当前在 HTTP 请求内执行，直到成功、失败或等待审批；后续高吞吐场景应由 Outbox/Worker
  接管执行触发，但保留相同 run 状态机和 API 投影。
- cancel 通过数据库 fencing 保证旧执行者不能提交终态，但不能撤销已经在外部系统完成的副作用；工具仍
  必须实现真实幂等和业务补偿。
- 当前审批查询是按 ID 获取，不包含游标列表；批量审批工作台后续增加分页投影。

## 撤销条件

只有当运行执行迁移到独立 worker、对象存储承载 checkpoint 或 API Gateway 接管认证时才调整装配位置；
任何替代方案仍必须保留 PostgreSQL 运行/审批事实、不可变 checkpoint、精确恢复、fencing、幂等键和安全
DTO 契约，不得回退为 API 线程内第二套状态机。

## 验证

- Ruff 与 Mypy 全量通过。
- FastAPI 单元测试覆盖路由安全关闭、权限、可信 tenant、保留 metadata 和敏感字段反例。
- 真实 PostgreSQL/FastAPI 测试覆盖 active 领域包装配、Bearer Token、幂等冲突、跨租户隐藏、批准、拒绝、
  重复决定、取消和 stale resume owner fencing。
- 全量 PostgreSQL Pytest、Alembic current/check 和离线领域/RAG 示例作为发布门禁。
