# ADR 0018: 反思任务安全运维、幂等重试与追加审计

## 状态

已接受，2026-08-25。

## 背景

ADR 0017 已把反思沉淀迁移到 PostgreSQL Outbox，并实现租约、heartbeat、fencing、有界重试和 dead-letter。
生产环境仍需要回答三个问题：某个租户/智能体是否积压；一个具体死信为何处于当前安全状态；受权运维如何
恢复它而不绕过租户、权限、并发和旧 owner 防线。

直接暴露 `outbox_jobs` 或允许后台脚本执行 UPDATE 会泄漏 payload/result metadata、跳过 agent grants、依赖
过期的 Bearer 权限快照，并在并发 retry 时复活旧 lease 或重复重置任务。运维链路必须继续以 PostgreSQL 为
唯一事实来源，并且不能把 task、output、trace、checkpoint、provider state、异常正文或凭据带入响应和审计。

## 选项

### 直接开放数据库只读与人工 SQL

拒绝。虽然实现成本低，但无法统一 tenant/agent 授权、安全投影、expected version、幂等和追加审计，也容易
误改 lease/token/attempts 字段。

### 复用通用 runs 权限和认证时 Principal 快照

拒绝。运行读写和后台任务恢复是不同职责；认证后 Token 可能已撤销、Principal 已禁用或 grants 已收窄，旧
快照不能作为管理写事实。

### 只用 `updated_at` 和 HTTP 幂等缓存

拒绝。时间戳序列化和精度容易产生比较歧义，缓存不是持久事实，进程重启后无法证明同请求是否已经改变任务。

### PostgreSQL 当前事实复核 + version + 行锁 + 不可变幂等请求

采用。独立权限、严格安全 DTO、筛选绑定 keyset、任务版本、事务锁和追加审计共同形成完整纵向链路。

## 决策

### 1. 独立运维权限与当前事实复核

新增：

- `operations.jobs:read`
- `operations.jobs:retry`

运维服务不信任 `AuthenticatedPrincipal.permissions` 或 agent allowlist 的旧快照。每次查询重新验证 active
tenant、active Principal、未撤销且未过期 Token、当前权限和 grants；每次 retry 在同一写事务内锁定并复核
这些事实。跨 tenant job 按 not found 隐藏；tenant 内无 grant 返回 forbidden。

### 2. 安全投影与严格 keyset

stats/list/detail 只返回 job/run/agent ID、handler version、状态、version、尝试计数、安全时间和机器错误码。
响应模型没有 task、output、trace、payload、result metadata、worker ID、lease token、checkpoint 或 provider
state 字段。

列表按 `created_at DESC, id DESC` 排序。游标必须是规范化、无 padding 的 URL-safe Base64，JSON 字段集合
严格固定并带版本；游标绑定 handler version、状态、agent 过滤和当前 grant scope 哈希，不能跨筛选条件复用。

### 3. 历史尝试与当前重试轮次分离

`outbox_jobs.attempts` 保留任务整个生命周期的历史总尝试；新增 `attempts_in_cycle` 决定当前自动重试预算。
Worker 领取同时递增两者，耗尽判定只使用单轮计数。人工 retry 将 `attempts_in_cycle` 重置为零，但不清空
历史 attempts。

新增 `outbox_jobs.version`。claim、heartbeat、complete、fail、租约耗尽 dead-letter 和人工 retry 都递增版本，
使管理写可以对明确事实版本做比较，而不是依赖可变时间戳。

### 4. 只允许 dead-letter 到 pending

retry 请求必须提供 `Idempotency-Key` 和 `expected_version`。事务执行顺序为：

1. 复核并锁定当前 actor Principal/Token；
2. 对 tenant + 幂等键 SHA-256 取得事务 advisory lock；
3. 查找不可变 retry request，决定回放或冲突；
4. 锁定目标 Outbox 行，复核 tenant、agent grant、expected version 和最新状态；
5. 只允许 `dead_letter -> pending`；
6. 清空旧 lease token/expiry/worker、机器错误码、完成时间和旧结果摘要，重置单轮尝试，保留历史 attempts，
   递增 version；
7. 在提交前追加幂等请求事实和运维审计。

旧 `ReflectionWorkItem` 的 lease token 因状态、token 和 version 已变化，不能 complete/fail。

### 5. 幂等请求与尝试审计分离

`reflection_job_retry_requests` 保存每个 tenant 幂等键哈希绑定的 actor principal、job/run/agent、expected version、
前后状态、结果 version、outcome 和安全机器码。相同 actor/target/version 请求回放同一结果；同键绑定不同请求
返回 idempotency conflict。

`reflection_job_operation_audit_events` 为每次尝试追加 actor principal/token ID、target ID、动作、结果、前后
状态、版本、机器码和幂等键哈希。两张表均由 PostgreSQL 触发器拒绝 UPDATE。原始幂等键、payload、运行正文、
异常正文、Token、digest 和 Authorization header 不入库。

## 影响

### 正向

- 运维查询与恢复具备 tenant、agent、handler 和权限最小边界；
- Token 撤销、Principal 禁用、权限/grant 收窄在管理写事务中即时生效；
- 相同 retry 可安全回放，不同请求、旧 version 和非 dead-letter 状态稳定冲突；
- 历史总尝试不丢失，同时可开启新的有限自动重试轮次；
- 旧 lease owner 无法在人工恢复后覆盖任务；
- 响应、错误、幂等事实和审计均无运行正文或凭据。

### 代价与限制

- 每次运维查询增加 Principal/Token/grant 数据库读取；retry 还会锁定 actor、幂等键和任务行；
- 同一 actor 的并发管理写会因 Principal/Token 行锁串行，优先保证撤销和权限变更的一致性；
- 审计首版只在 PostgreSQL 内保留，尚未导出到独立 WORM/合规存储；
- stats 按任务事实聚合，Worker heartbeat 仍是全局进程事实，不向租户 DTO 暴露；
- 成功历史和审计尚无归档/保留策略，后续必须按合规要求设计分区或归档，不能直接硬删除。

## 撤销条件

- actor 行锁在真实管理并发下成为明显瓶颈时，可改为更细粒度的数据库锁或序列化授权版本，但必须保证撤销、
  禁用、permission/grant 收窄不能与 retry 提交交叉绕过；
- PostgreSQL 历史规模导致 keyset/聚合延迟超标时，可增加分区、汇总表或只读副本，但 tenant/agent/handler
  过滤和 PostgreSQL 权威任务状态不能被缓存替代；
- 合规要求物理不可变或跨区域留存时，将审计流复制到 WORM 存储，但业务事务中的最小追加证据仍需保留；
- 若未来任务类型共享相同管理状态机，可抽取通用 JobOperations 内核，但在至少第二种任务证明契约一致前不做
  推测性抽象。
