# ADR 0021: 真实负载校准、Outbox 分区归档与容量趋势治理

- 状态：已接受并实现
- 日期：2026-08-25
- 决策范围：Worker 真实处理耗时、容量观测/趋势/校准、Outbox 分区归档与受保护清理

## 背景

v0.19 已提供 PostgreSQL 容量快照、三级判级、有界 Worker 建议和生产 Compose，但阈值仍来自静态配置，
容量结果没有持久趋势，终态 Outbox 也会长期留在运行表。直接自动调整阈值或物理删除历史会引入三类风险：

1. 用估算耗时替代真实处理事实，导致建议副本数失真；
2. 只看瞬时快照，无法区分短时尖峰、持续积压和周期性变化；
3. 清理尚未精确归档或仍被人工重试审计引用的任务，破坏可追溯性。

## 决策

### 1. Worker 路径记录真实处理耗时

Outbox 保存 `last_started_at`、`last_processing_duration_ms` 和 `total_processing_duration_ms`。领取设置本次开始
时间；完成、失败、租约过期接管和耗尽进入 dead-letter 时，在持有任务行锁和 fencing 条件下累计耗时。
校准只读取已提交的 `succeeded/dead_letter` 终态事实，不调用模型供应商，也不根据日志正文反推耗时。

### 2. 容量观测、趋势和校准全部持久化到 PostgreSQL

`capacity-check` 和常驻 `capacity-monitor` 使用同一容量报告。观测以
`job_type + handler_version + observed_at` 幂等保存；趋势查询只允许小时或天桶、有限时间窗口和有限结果数。

`capacity-calibrate` 在指定 lookback 与样本上限内计算 nearest-rank P50/P95/P99、观察吞吐和有界阈值建议。
样本少于显式门槛时以退出码 6 失败关闭。校准报告和输入参数进入不可覆盖的历史记录，但不自动修改环境变量、
运行时阈值、Compose 副本或外部控制器。

### 3. Outbox 历史使用 PostgreSQL 原生范围分区

终态快照进入按 `completed_at` 分区的 `outbox_job_archives`。归档身份为
`job_id + completed_at + source_version`，父表不设置指向 `outbox_jobs`、`runs` 或租户表的外键，避免源运行
数据清理级联删除历史。首版建立覆盖全部时间范围的四个分区，后续可通过独立迁移细化时间粒度。

归档批次按 handler version、终态和截止时间筛选，使用 `FOR UPDATE SKIP LOCKED` 与批次/批数上限；并发维护
不会重复领取同一源行。

### 4. 物理清理失败关闭

`outbox-maintain` 默认 dry-run。只有显式 `--execute` 才复制归档；物理清理还必须同时显式提供 `--prune`。
源行只有在当前 `job_id + completed_at + version` 的精确归档副本存在时才可删除。有任何
`reflection_job_retry_requests` 引用时永不清理，避免外键级联删除人工重试事实。

## 候选方案与反选论证

### 方案 A：直接根据静态阈值或 CPU 自动扩缩容

优点是实现简单；缺点是 CPU 无法表达 handler version、积压年龄、dead-letter、供应商等待和真实任务耗时。

不选择原因：缺少持续窗口和真实吞吐证据，自动动作会把错误建议直接放大为生产变更。

### 方案 B：把容量和归档事实写入新的时序库或消息平台

优点是生态成熟、可做复杂分析；缺点是引入第二事实源、同步延迟、权限和恢复成本。

不选择原因：当前 PostgreSQL 已承担任务和容量事实，尚无吞吐证据证明需要额外平台。

### 方案 C：定时整表复制后直接删除源记录

优点是脚本短；缺点是无行锁、无版本身份、无 dry-run，容易与重试并发并破坏审计历史。

不选择原因：无法证明每条源记录都有精确归档副本，也不能失败关闭。

### 方案 D：PostgreSQL 真实历史 + 有界趋势 + 分区归档（选择）

优点是复用唯一事实源、迁移可逆、并发与清理条件可在数据库事务中验证；代价是趋势和归档仍占用 PostgreSQL，
阈值应用与长期分区维护需要运维流程。

## 数据与接口影响

- `outbox_jobs` 新增三项处理耗时字段和校准查询索引；
- 新增 `reflection_capacity_observations`、`reflection_capacity_calibrations`；
- 新增分区父表 `outbox_job_archives` 和四个范围分区；
- 新增 CLI：`capacity-monitor`、`capacity-trend`、`capacity-calibrate`、`outbox-maintain`；
- 生产 Compose 新增常驻 monitor 和 `ops` profile 的趋势、校准、归档维护入口。

## 回滚

应用可先回退到 v0.19 镜像，并停止 capacity-monitor 与所有治理一次性任务。数据库 downgrade 到
`b7e2c4a9d610` 会删除容量观测、校准、归档分区和处理耗时字段，因此执行前必须备份或导出这些新增数据。
回滚不会改变既有 Outbox 状态机字段、租约、heartbeat、人工重试和运行终态。

## 撤销条件

出现以下任一证据时重新评估：

- 容量历史增长或聚合 P95 无法通过保留策略、索引和预聚合满足目标；
- PostgreSQL 归档表体量、备份窗口或恢复时间超过生产 SLO；
- 多主机/多区域调度需要经过验证的外部时序、对象存储或控制器；
- 真实生产样本证明当前 nearest-rank P95 与目标 drain 模型不能代表供应商限流和季节性负载。

## 验证证据

- `tests/test_capacity_governance.py`、`tests/test_capacity_governance_cli.py`；
- `tests/test_postgres_capacity_governance.py`、`tests/test_postgres_outbox_worker.py`；
- Alembic `b7e2c4a9d610 -> c9f4e2a7b613 -> b7e2c4a9d610 -> head` 往返与 drift check；
- `references/deployment_capacity_cases.json` 和 `scripts/test_production_deployment.py`；
- `docker compose -f docker-compose.production.yml config --quiet`、生产镜像非 root/依赖/CLI/head 门禁。
