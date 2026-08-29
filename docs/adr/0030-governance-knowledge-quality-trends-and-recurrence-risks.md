# ADR 0030：治理知识质量趋势与复发风险

- 状态：Accepted for v0.28
- 日期：2026-08-25
- 关联：ADR 0025、ADR 0029、`operations.capacity_control`、`storage.capacity_control`

## 背景

v0.27 已能把受限反馈冻结为不可变质量快照，并在安全问题确认后隔离治理知识；误报恢复需要 24 小时保留、四方
职责分离和新知识版本。单条快照和单次恢复仍不足以回答两个生产问题：质量是否在一段时间内持续退化，以及知识在
恢复后是否再次被隔离。若只在控制台临时统计，结果不可复用、无法稳定分页，也不能与事件生命周期形成可审计证据；
若直接接入外部时序和告警平台，又会增加第二事实来源、凭据、同步和恢复边界。

## 选项

| 方案 | 优势 | 风险/代价 | 结论 |
|---|---|---|---|
| 控制台临时聚合质量快照 | 无 Schema 变化，界面实现快 | 每个客户端重复计算、无统一边界/cursor、无法作为事件证据，容易无界扫描 | 不采用 |
| 外部时序库与告警平台 | 长期趋势、通知和仪表盘生态完整 | 引入第二事实源、跨租户同步、凭据、保留、恢复和告警状态一致性成本 | 当前不采用 |
| PostgreSQL 不可变事实 + 有界趋势 + 复用内部事件状态机 | 事务、RBAC、审计、迁移和恢复边界统一；可确定性重放 | PostgreSQL 承担额外聚合与索引成本，首版无外部通知和长期时序优化 | 采用 |

## 决策

### 1. 质量趋势只聚合 PostgreSQL 不可变快照

新增 `CapacityGovernanceKnowledgeQualityTrendQuery/Point/Report`。查询必须提供 UTC `captured_from/to`，bucket 仅
允许 `hour/day`，assessment 可选，单页 `limit <= 366`。PostgreSQL 使用 `date_trunc` 在有界窗口内统计 total、
`insufficient/healthy/degraded/unsafe` 和 distinct postmortem；应用层只在配置的最大桶数内补零。cursor 绑定
当前 actor scope、handler、bucket、assessment 和完整时间窗，任一筛选变化均失败关闭。

迁移 `e9a2f4c6b810` 新增
`(tenant_id, handler_version, captured_at, id)` 索引。只读演练必须验证该 captured-time 趋势索引以及既有快照
append-only、assessment/count/version/evidence 控制，缺失任一项时 `passed=false`，不自动修复。

### 2. 三类质量风险复用内部治理事件

不建立第二事件状态机，新增三种 signal：

- `knowledge_unsafe_persistent`：风险窗口内达到 2/3 个独立 unsafe evidence 时 warning/critical，且最新快照仍为 unsafe；
- `knowledge_degraded_repeat`：达到 2/4 个独立 degraded evidence 时 warning/critical，且最新快照仍为 degraded；
- `knowledge_requarantined`：`restore_count >= 1` 且当前再次 quarantined，隔离时间晚于最近恢复时间，直接 critical。

规则版本固定为 v1。stable fingerprint 绑定 tenant、handler、signal、rule version 和 postmortem 目标；独立
evidence fingerprint 绑定实际快照集合或恢复/再隔离版本事实。相同证据幂等，新证据可升级、恢复或复发重开。

### 3. 截断扫描失败关闭

质量风险只读取配置窗口内最多 1000 个快照和同上限的相关 postmortem。任何快照、postmortem、候选或既有事件
读取被截断时，扫描报告标记 `truncated=true`；不完整质量证据不得创建或恢复三类风险事件。运维人员必须先缩小
窗口或基于索引、延迟和容量证据调整上限，不能把截断解释为无风险。

### 4. 恢复必须依赖更新质量事实

`acknowledged` 只表示人工接手。持续 unsafe/重复 degraded 只有出现晚于事件证据的更新质量快照且规则不再命中
时才能 resolved；再次隔离风险只有 postmortem 出现更新的隔离/恢复状态事实时才能 resolved。时间流逝、确认动作、
恢复申请、外部工单和 RAG 命中均不是恢复证据。

### 5. 固定处置，不自动执行

三类 signal 分别固定映射 `knowledge_safety_containment`、`knowledge_quality_review` 和
`knowledge_recurrence_review`，执行只记录受限证据码。系统不自动生成质量快照、不恢复知识、不发送通知、不撤权、
不发布/回滚容量策略、不调整 Worker，也不把 advisory-only RAG 内容当作授权或执行指令。

## 反选理由

- 不选控制台临时聚合：趋势边界、补零、分页和权限会散落到每个客户端，且无法提供 monitor 可复用的稳定事实。
- 不选外部时序/告警系统：当前规模和通知需求尚未证明 PostgreSQL 不足，引入外部状态会扩大租户隔离、即时撤权、
  备份恢复和告警生命周期一致性故障面。
- 不另建质量风险表：现有 incident 已具备 stable/evidence fingerprint、expected version、确认、恢复、复发、RBAC、
  审计和固定 Playbook；重复状态机只会增加漂移和迁移成本。

## 结果与取舍

- 质量趋势、风险事件、处置和原始快照保持同一 PostgreSQL 权威链路，可重放、可审计、可回滚。
- captured-time 索引和有界窗口限制聚合成本；长周期分析仍会占用 PostgreSQL，首版不解决跨年时序保留。
- 风险检测故意保守：独立证据计数和 truncated 失败关闭可能延迟恢复，但避免把缺失证据当作健康。
- 控制台提供 hour/day、assessment 和 24/72/168 小时窗口，但不成为授权或事件状态事实来源。

## 回滚

先停止 API 与 capacity-monitor，导出三类新风险 incident、关联 remediation 和审计事实，再执行
`alembic downgrade d8f1c2a4b730`。downgrade 删除 captured-time 趋势索引，并把 incident signal、Playbook 和执行
证据 CHECK 缩回 v0.27 集合；不会删除质量快照、反馈、恢复申请、postmortem 内容、向量或恢复谱系。旧应用无法
解释三类新枚举，回滚前必须处理或保留其导出证据。

## 撤销条件

当真实负载的 `EXPLAIN (ANALYZE, BUFFERS)`、备份恢复时长、表增长或趋势 P95 证明 PostgreSQL 无法满足生产 SLO，
或组织必须提供跨系统关联、长期冷热分层和真实通知 SLA 时，可接入外部时序/告警后端。替代方案必须经过租户隔离、
即时撤权、幂等同步、断线恢复、事件状态一致性和回滚演练；PostgreSQL 不可变快照与治理授权事实仍保留为权威来源。

## 验证证据

- 领域测试覆盖 UTC/桶/计数不变量、阈值边界、stable/evidence fingerprint、三类风险和反例；
- PostgreSQL 测试覆盖 `date_trunc`、补零、筛选绑定 cursor、并发去重、升级、恢复、复发、truncated 和审计失败回滚；
- 只读演练覆盖快照 append-only、captured-time 趋势索引、恢复约束与查询索引；
- API/控制台覆盖独立 403、24/72/168 小时窗口、断开清理和 390px 无横向溢出；
- Alembic 往返、全量 Pytest、Ruff、Mypy、Node.js、Compose 和生产发布门禁共同验收。
