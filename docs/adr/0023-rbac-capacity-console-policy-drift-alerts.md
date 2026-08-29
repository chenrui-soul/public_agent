# ADR 0023: RBAC 容量审批控制台、策略漂移检测与治理告警

- 状态：Accepted
- 日期：2026-08-25
- 版本：v0.22

## 背景

v0.21 已有 PostgreSQL 版本化容量策略、持续窗口、CLI 审批、发布、复核和 exact rollback，但 CLI 的 operator
只是审计标签，无法证明调用者当前有效，也没有可供值守人员使用的 Web 控制台。容量观测已持久化实际阈值
快照，却没有把运行时阈值与当前 active policy 的差异形成可确认、可恢复、可审计的治理事件。

## 候选方案

| 方案 | 优势 | 代价 |
|---|---|---|
| 独立 IAM、SPA 和外部告警平台 | 组织能力完整 | 引入多个事实源、构建链、部署与恢复面 |
| CLI 角色字符串与日志告警 | 改动最小 | 身份不可验证、撤权不即时、无可靠去重和生命周期 |
| 复用 PostgreSQL API Token RBAC、原生控制台和 PostgreSQL 告警 | 事务、审计、部署和回滚边界统一 | 首版无 SSO、外部通知和复杂工作流 |

## 决策

采用第三种方案：

1. 新增容量请求、审批、发布、复核、回滚、告警读取与告警管理细粒度权限和推荐角色模板。
2. 所有治理动作在状态变更事务内重验配置 tenant、active Principal、未撤销未过期 Token、当前权限、
   `all_agents` 和空 agent grants；operator 只取 Principal subject。
3. 使用原生 HTML/CSS/JS 控制台调用公开 API。Token 只保存于当前标签页 `sessionStorage`，页面启用 CSP、
   `no-store`、`nosniff` 和 `no-referrer`，渲染只使用 `textContent`/DOM API。
4. 以规范化阈值 SHA-256 指纹比较容量观测和当前 active policy；无 active policy 时使用 Settings fallback。
   告警按 handler + expected + observed 指纹去重，支持 warning/critical、确认、恢复和复发重开。
5. 只有出现晚于告警最后观测的新事实才允许自动关闭。active policy 切换后，旧 expected 告警关闭，当前
   expected/observed 组合独立建警，避免重复未恢复告警。
6. 成功审计与治理状态同事务提交；拒绝/冲突审计独立追加；数据库 trigger 拒绝审计 UPDATE。

## 安全与一致性不变量

- 控制台不是授权依据；前端隐藏或显示动作只影响 UX。
- 请求人不能审批自己的请求；所有写动作使用 expected version 和现有行锁/advisory lock 状态机。
- 请求/告警查询有界，keyset cursor 绑定资源类型、筛选和 actor scope；无效或跨上下文 cursor 返回 400。
- API 不返回 Token、Token ID、digest、数据库 URL、内部异常正文或未脱敏运行事实。
- 漂移确认不等于恢复；无新观测不关闭；复发重开清除旧确认人并递增版本和复发次数。
- 系统不自动修改 Worker 副本、Compose、环境文件、Kubernetes 或外部配置中心。

## 影响

- 新增 `reflection_capacity_governance_alerts` 和 append-only
  `reflection_capacity_governance_audit_events`，Alembic head 为 `2d6f8b1c4a90`。
- `public-agent serve` 成为生产管理入口，并要求 API-only 的独立 Token pepper Secret。
- `capacity-monitor` 在新观测持久化后运行漂移扫描；人工也可通过管理 API 触发扫描。
- 首版仍需组织自行分发最小权限 Token；没有 OIDC/SSO、外部通知或自动扩缩容。

## 回滚与撤销条件

回滚时先停止 API/容量监测并切回 v0.21 镜像，导出新增告警和审计，再 downgrade 到 `f2a7d9c4e681`。
当组织审批、跨主机策略传播或通知 SLA 无法由当前控制面满足，且替代系统能保留即时撤权、单一发布顺序、
append-only 审计、expected-version 并发保护和 exact rollback 时，可重新评估独立 IAM/工作流/告警平台。
