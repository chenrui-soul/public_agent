# ADR 0026：治理事件处置审批与恢复验证

## 状态

已接受，2026-08-25。

## 背景

v0.24 已能把审计突增、告警 SLA、重复复发和只读演练失败聚合为 PostgreSQL 内部事件，但事件确认之后缺少
可审计的处置责任分离、执行证据和恢复验证。直接接受任意命令、脚本或人工“已恢复”声明会绕过既有安全边界。

## 决策

采用 PostgreSQL 版本化处置单：事件信号固定映射到四个 Playbook；每个 `incident_id + reopened_count` 周期最多
一条处置单。状态机为
`awaiting_approval -> approved -> verification_pending -> verified`，并保留 `rejected/failed` 终态。

- request/approve/execute/verify 使用独立权限；请求人不能审批，执行人不能验证。
- 执行 API 只保存枚举结果和安全证据码，不接受命令正文、日志正文或外部凭据，也不执行生产变更。
- verify 必须读取同一事件周期的新 PostgreSQL 事实：事件已 resolved、版本高于执行快照且恢复时间晚于执行时间。
- expected version、行锁、handler advisory lock、唯一约束和追加审计共同保证并发顺序。

## 后果

处置流程可在不引入工单、通知或自动化平台的情况下形成可验证闭环；代价是首版执行仍由人工在外部完成，系统只
记录安全结果码。未来外部执行器必须消费已批准处置单，但不得成为事件或处置状态的权威来源。

## 回滚

先回退应用隐藏处置 API/控制台，再将 Alembic 从 `9f4e7c2d1a60` downgrade 到 `6b9d2f4a8c71`。回滚前导出
处置事实；迁移只删除 v0.25 新表，不改动事件、告警、策略或审计历史。
