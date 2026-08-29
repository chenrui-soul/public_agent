# ADR 0017: PostgreSQL 反思 Outbox 与租约 Worker

## 状态

已接受，2026-08-25。

## 背景

完整轨迹 `ReflectionEngine`、候选冲突/合并和受控发布闭环已经可运行，但反思仍可能跟随在线应用服务执行。
真实模型延迟、供应商抖动或长轨迹处理会放大运行 API 尾延迟；进程在运行终态提交后崩溃，还可能形成
“运行已结束但反思永久未执行”的事务缝隙。

异步化必须保留既有生产不变量：PostgreSQL 是唯一事实来源；同一运行不能重复生成成长资产；多 Worker
并发只能有一个有效 owner；崩溃后可接管但旧 owner 不能覆盖；任务、输出、checkpoint、供应商状态和凭据
不得复制进队列 payload 或错误日志。

## 决策

### 1. 运行终态与 Outbox 同事务提交

新增 `outbox_jobs`。`PostgresRunPersistence` 在运行进入 `succeeded`、`failed`、`canceled` 或 `timed_out`
时，在修改 run 的同一 PostgreSQL 事务中调用 `enqueue_reflection_job`。终态幂等重放也执行 ensure enqueue，
补偿旧调用方或短暂版本切换造成的缺口。

任务唯一键为 `job_type + run_id + handler_version`。相同处理器版本重复入队返回既有任务；新处理器版本
可以显式重放同一运行，而不覆盖历史任务。

### 2. 最小安全 payload 与事实重载

Outbox payload 固定为：

```json
{"schema_version": 1}
```

不复制 task、output、trace、checkpoint、provider state、Token、Authorization header 或供应商错误正文。
Worker 领取后按 job ID/run ID 从 PostgreSQL 重新加载终态 run、tenant、agent version 和严格顺序事件，
再构造 `ReflectionJobInput`。不支持的 payload schema 或非终态运行失败关闭。

`runs(id, tenant_id)` 增加唯一约束，`outbox_jobs(run_id, tenant_id)` 使用命名复合外键
`fk_outbox_jobs_run_scope`，使一个真实 tenant ID 与另一个 tenant 的 run ID 无法在数据库层配对。

### 3. SKIP LOCKED、有限租约、heartbeat 与 fencing

Worker 通过 `FOR UPDATE SKIP LOCKED` 领取到期的 `pending`、`retry_wait` 或租约已过期的 `processing` 任务。
领取时增加 attempts，生成随机 lease token，记录 worker ID 和有限过期时间。处理期间 heartbeat 只允许当前
token 延长租约。

complete/fail 在行锁事务中重新验证：

- 状态仍为 `processing`；
- lease token 与当前 owner 一致；
- 租约仍未过期。

任一条件不满足都抛出 `ReflectionJobLeaseLostError`。租约过期后新 Worker 生成新 token 接管，旧 Worker
无法提交成功或失败终态。

### 4. 有界重试、dead-letter 与安全错误

失败使用 `base * 2^(attempt-1)` 的有界指数退避，并限制 base、maximum 和 max attempts。未耗尽时进入
`retry_wait`；耗尽后进入 `dead_letter` 并记录完成时间。持久化错误必须匹配安全机器码格式，Worker 只从
异常类型生成 `reflection_worker.<type>`，绝不保存异常正文。

完成、重试和 dead-letter 向 `run_events` 追加安全事件，只包含计数、候选 ID、attempts、max attempts、
outbox job ID 和机器错误码。

### 5. 复用成长资产幂等不变量

Worker 调用既有 `KnowledgeSedimentationPipeline`，不建立第二套候选或发布逻辑。处理器版本重放继续受以下
不变量保护：独立候选指纹、作用域 advisory lock、保守冲突检测、确定性合并 ID、可信评测、人工审批和
正式记忆事务发布。因此成功重放可以产生零个新候选，而不会重复正式记忆或破坏谱系。

PostgreSQL 决定任务状态、租约、重试和终态。Redis 只允许作为未来的非权威唤醒提示，Redis 丢失或重复
消息不得影响任务正确性。

## 备选方案

### 在运行请求中继续同步反思

拒绝。实现简单，但模型延迟和失败直接放大在线尾延迟，进程崩溃也缺少独立重试与 dead-letter。

### Celery/Redis 作为任务事实来源

拒绝。运行终态和队列消息跨系统提交会引入双写一致性；Redis 淘汰或故障不能决定知识沉淀是否完成。

### Kafka 或独立消息服务

暂不采用。当前吞吐没有证明需要独立事件平台；PostgreSQL 已具备事务、SKIP LOCKED、行锁和审计能力。

### 在 payload 中复制完整轨迹

拒绝。会复制敏感运行状态、扩大存储和泄漏面，并造成 run 事实与任务快照漂移。

### 只用 worker ID 不使用随机 fencing token

拒绝。同名进程重启或租约接管后无法区分旧执行者，可能由过期 owner 覆盖新 owner 的终态。

## 影响

### 正向

- 在线运行终态与反思任务不存在事务缝隙；
- 反思延迟、重试和供应商抖动不再阻塞运行 API；
- 多 Worker 可安全并发，崩溃后可接管且旧 owner 被 fencing；
- 重试、dead-letter、处理器版本和安全运行事件可审计；
- Outbox 不复制轨迹和凭据，跨租户配对由数据库拒绝；
- 既有候选、冲突、合并、评测、审批和发布不变量继续作为唯一成长链路。

### 代价与限制

- PostgreSQL 同时承担运行事实和任务领取压力，需要监控积压、最老任务年龄、锁等待和查询延迟；
- v0.18 已补 `ReflectionWorkerRunner`、有限 drain、PostgreSQL Worker heartbeat/instance fencing、
  handler version 积压快照、生产 CLI、租户/agent 任务查询、死信重试 API 和独立运维权限；
- Outbox 表保留成功和 dead-letter 历史，后续需要基于合规策略定义归档而不是直接删除；
- handler version 重放仍会调用反思管线，虽然资产幂等，但会产生模型成本，必须由受权运维显式触发。

## 撤销条件

- PostgreSQL 领取锁、表膨胀或跨区域部署经过容量证据证明不满足需求时，可引入 Kafka/SQS 等消息平台，
  但必须继续使用同事务 Outbox 发布、幂等 consumer 和 PostgreSQL 终态/fencing 事实；
- 反思需要独立数据保留、搜索或合规查询时，可新增不可变 reflection execution 表，但不能把原始秘密复制
  到普通事件或管理响应；
- 引入 Redis 唤醒或调度器时，轮询 PostgreSQL 仍必须作为恢复路径，Redis 不得成为完成事实。
