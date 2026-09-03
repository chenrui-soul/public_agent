# ADR 0031：治理知识再认证、生命周期监控与退役

- 状态：Accepted
- 日期：2026-09-03
- 关联：ADR 0025、ADR 0030、`f1b3c7d9e2a4`

## 背景

治理复盘知识发布后仍需要持续确认其有效性。时间到期不能被当作质量失败，也不能触发自动隔离或退役；
退役必须是可审计、可回滚认知的人工决定，同时不能破坏历史证据和 RAG 谱系。

## 决策

1. 使用固定且有界的 UTC 再认证窗口，把 published 知识只读投影为 `current`、`due`、`overdue`、
   `quarantined` 或 `retired`。
2. `capacity-monitor` 每次采样执行生命周期聚合扫描，并与容量 JSON 一起输出；扫描不写业务事实、不发外部通知。
3. 再认证申请绑定 postmortem version、knowledge version、content fingerprint 和独立 quality evidence fingerprint，
   通过幂等键去重；request、review、retirement 使用独立 RBAC 权限和 PostgreSQL 事务重验。
4. `retired` 只改变 postmortem 生命周期及退役审计字段，保留内容、向量、反馈、快照和谱系；KnowledgeRetriever
   只允许 published 且非 quarantined/retired 的当前版本。
5. 只读治理演练检查再认证表的生命周期/决定/原因/版本约束、活动请求唯一性和租户/状态/复盘索引。

## 后果

- 值守人员能在单次 monitor 采样中看到再认证积压和到期趋势，且不会误触发自动变更。
- 退役和 RAG 排除具备明确事实边界，历史审计可继续复核。
- 再认证窗口策略当前为代码级有界默认值；若未来需要租户级配置，应新增版本化策略表并保持请求绑定。

## 撤销条件

当合规要求外部签名/WORM 证据、跨系统通知 SLA 或租户级动态窗口时，可引入独立策略与通知后端；在替代方案通过
身份重验、幂等、回滚和 RAG 排除验证前，不得移除 PostgreSQL 再认证事实及只读演练。
