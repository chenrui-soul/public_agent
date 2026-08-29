# ADR 0022: 版本化容量阈值变更治理

- 状态：已接受并实现
- 日期：2026-08-25
- 决策范围：容量策略、持续窗口、人工审批、发布冷却、效果复核和精确回滚

## 背景

v0.20 能从真实终态任务生成持久化校准建议，但建议不会改变运行时阈值。直接写 `.env`、Compose 或副本数无法
证明审批顺序、并发一致性和精确回滚，也会让数据库事实与部署文件形成双事实。

## 决策

新增 `reflection_capacity_policies` 与 `reflection_capacity_change_requests`。PostgreSQL 保存 handler version
隔离的 active 策略、校准来源、exact base policy、持续窗口证据、整数 version、具名审批、发布/冷却时间、
效果证据和回滚事实。Settings 仅在没有 active 策略时作为安全 fallback。

状态机为：

```text
pending_window -> awaiting_approval -> approved -> cooling_down -> effective
       |                  |                                -> ineffective -> rolled_back
       |                  -> rejected
       -> blocked (不改状态；继续采样后重试)
```

发布与回滚在单事务中使用 handler 级 advisory lock、请求/策略行锁、expected version 和 partial unique active
索引。切换 active 身份时先 flush 旧策略退出 active，再 flush 新/旧目标进入 active，避免唯一索引竞态。

效果复核只读取发布后的持久化容量观测，与发布前窗口证据比较 critical/unhealthy 比例以及原始 ready、最老
ready age 和 dead-letter。样本或时间跨度不足时不推进状态。回滚只允许当前发布策略仍为 active，并精确恢复
其 `previous_policy_id`。

## 候选方案与反选

1. **直接修改环境文件**：简单，但无事务、无并发序列、无法证明 exact previous policy；不采用。
2. **外部配置中心或 Kubernetes 控制器**：传播能力强，但当前会形成第二事实源和额外恢复面；证据不足时不采用。
3. **PostgreSQL 版本化策略（采用）**：复用现有校准、观测、锁、迁移和失败关闭边界；代价是首版审批入口为 CLI。

## 安全边界

- 校准、窗口验证、审批、发布和复核不可跳步；旧 version 失败关闭。
- 发布只改变阈值策略，不修改 Worker 副本、Compose、`.env` 或外部控制器。
- 同一 handler version 冷却期内禁止第二次发布。
- 请求、校准和 active policy 均按 handler version 隔离。
- 不调用模型供应商或真实收费 API。

## 回滚

业务回滚使用 `capacity-policy rollback`，只恢复 exact previous policy。Schema 回滚先导出治理事实，再执行
`alembic downgrade c9f4e2a7b613`；应用随后使用 Settings fallback。应用镜像回滚不要求删除 v0.21 表。

## 撤销条件

多主机配置传播、组织审批或变更审计经实测无法由 PostgreSQL/CLI 满足，并且已有经过故障恢复与回归验证的
外部配置控制器时重新评估。即使迁移控制面，容量观测、校准和任务事实仍以 PostgreSQL 为权威来源。

## 验证证据

- 领域与运行时：`tests/test_capacity_policy_governance.py`、`tests/test_capacity.py`；
- CLI：`tests/test_capacity_governance_cli.py`；
- PostgreSQL 状态机：`tests/test_postgres_capacity_governance.py`；
- 模型与迁移：`tests/test_storage_models.py`、Alembic `c9 -> f2 -> c9 -> f2` 和 drift check；
- 发布：`references/deployment_capacity_cases.json`、`scripts/test_production_deployment.py`。
