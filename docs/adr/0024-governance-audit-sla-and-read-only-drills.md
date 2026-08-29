# ADR 0024：治理审计查询、告警 SLA 与只读运营演练

- **状态**：Accepted
- **日期**：2026-08-25
- **关联**：ADR 0023、`operations.capacity_control`、`storage.capacity_control`、容量治理控制台

## 背景

v0.22 已把容量治理动作写入 PostgreSQL append-only 审计，并提供漂移告警与审批控制台。但审计事实仍需要
数据库权限才能查询；未确认告警没有统一响应时限；发布和值守人员也缺少一个不会修改治理数据的控制面演练。

## 决策

1. 新增独立 `operations.capacity_audit:read` 权限和 auditor 角色。每次查询和演练继续在 PostgreSQL 事务内
   重验 tenant、active Principal、Token、permission、`all_agents` 和无 agent grant。
2. 审计查询按 actor subject、action、outcome 和 UTC 时间窗精确过滤，最多返回 100 条。keyset cursor 绑定
   当前 principal、handler version 和全部筛选条件；响应排除 Token ID，对 safe metadata 再做键和值类型白名单。
3. 不为 SLA 新增持久状态机。服务端根据告警 `first_seen_at`、当前 lifecycle 和有界 Settings 阈值派生
   `within_sla/due/breached/acknowledged/resolved`，只用于运营排序，不驱动授权、自动确认或自动关闭。
4. 新增只读治理演练报告。它在当前 actor 重验成功后查询 PostgreSQL catalog，检查职责分离、audit UPDATE
   rejection trigger、告警 policy/lifecycle CHECK 和审计查询索引。证据缺失时返回 `passed=false`，不自动修复。
5. 新增复合审计过滤索引 `tenant_id + handler_version + outcome + action + created_at + id`；Alembic
   `e3c8a1f7b920` 可独立 downgrade，既有审计和告警数据不删除。

## 反选方案

- **直接开放数据库只读账号**：泄露面、租户边界和即时撤权流程难以统一。
- **把审计权限合并到 viewer/alert operator**：破坏职责分离，并使普通值守人员获得不必要的行为历史。
- **持久化 SLA 状态并由后台任务更新**：引入时间驱动的第二状态机、写放大和竞态；当前可由事实确定性派生。
- **演练创建临时 Principal/Token 并执行真实状态变更**：会污染审计和治理事实，生产清理与恢复风险更高。
- **接入外部 SIEM/通知平台**：需要凭据、网络、保留和恢复授权，超出本阶段范围。

## 后果

### 正面

- 审计员无需数据库权限即可安全复核治理历史，Token 撤销立即生效。
- 未确认告警可按统一 SLA 排序，控制台能突出 due/breached。
- 发布后可运行零业务写入的控制面证据检查，缺失约束或索引会失败关闭。
- 继续保持 PostgreSQL 为治理事实和授权事实的唯一来源。

### 代价

- actor subject 查询需要连接 Principal 表；Principal 已删除时只能显示为空。
- 首版 safe metadata 是小型白名单；新增审计元数据必须显式评审后才能进入响应。
- SLA breached 只在本地 API/控制台可见，不会发送外部通知。
- catalog 演练证明关键数据库保护存在，但不替代隔离环境中的完整灾难恢复或渗透测试。

## 安全与回滚

- 控制台不是授权依据；后端事务重验仍是唯一判定。
- cursor 使用规范 URL-safe Base64，绑定 actor/scope/filter，任何篡改返回 `invalid_cursor`。
- 演练仅执行 SELECT；不创建身份、不修改请求/告警/审计，不自动修复缺失对象。
- 回退应用后执行 `alembic downgrade 2d6f8b1c4a90` 只删除新增索引；v0.22 数据保持不变。

## 撤销条件

当生产合规要求 WORM/不可抵赖签名、跨系统关联、长期冷热分层或通知 SLA，且经过恢复演练的 SIEM/对象存储/
通知平台能保持租户隔离、即时撤权、安全投影和单一治理发布序列时，可替换查询/通知后端；PostgreSQL 原始治理
事实与 append-only 约束仍保留。
