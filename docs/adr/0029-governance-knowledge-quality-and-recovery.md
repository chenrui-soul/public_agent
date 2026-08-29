# ADR 0029：治理知识质量快照与安全恢复

- 状态：Accepted for v0.27 Wave 4
- 日期：2026-08-25

## 背景

v0.27 Wave 1-3 已能让独立复核人确认安全反馈，并在同一 PostgreSQL 事务中隔离治理知识，使 RAG 立即排除该
版本。隔离保留了内容、向量和来源谱系，但仍缺少两个生产控制：一是把反馈事实冻结为可复核的质量证据，二是在
误报被确认后安全恢复知识。直接改回 `published`、复用旧知识版本或删除反馈都会破坏职责分离、证据完整性和
检索版本语义。

## 选项

| 方案 | 优势 | 风险 | 结论 |
|---|---|---|---|
| 管理员直接解除隔离 | 操作简单 | 无快照、无保留期、无职责分离，旧版本重新暴露 | 不采用 |
| 删除安全反馈后重新发布 | 表面上恢复干净 | 销毁证据和谱系，无法解释历史隔离 | 不采用 |
| 不可变质量快照 + 保留期 + 双人恢复审批 + 新知识版本 | 证据、授权和检索语义完整 | 增加表、迁移和审批步骤 | 采用 |

## 决策

### 1. 质量评测是不可变事实快照

质量快照绑定 tenant、handler、postmortem ID/version、knowledge version、内容指纹，以及由反馈 ID、version、
status 和受限分类规范化生成的独立证据指纹。同一证据重复评测幂等返回同一快照。评测只允许：

- 有确认安全反馈：`unsafe`；
- 无确认安全反馈且确认负面多于正面：`degraded`；
- 没有已确认正负质量反馈：`insufficient`；
- 其他情况：`healthy`。

数据库 trigger 拒绝 UPDATE，应用不提供删除接口。租户清理和 Schema downgrade 可按外键顺序删除，避免把运营
不可变性错误扩大为无法测试或无法回滚。

### 2. 隔离至少保留 24 小时

恢复只能绑定当前 `quarantined` postmortem、当前知识版本、当前内容指纹和最新 `unsafe` 快照。自
`last_quarantined_at` 起不足 24 小时一律失败关闭。请求理由只有结构化 `false_positive`，不接受自由文本、命令、
提示词或模型输出。

### 3. 恢复使用独立 RBAC 和四方职责分离

读取、申请和审批分别使用 `operations.capacity_knowledge_recovery:read/request/review`。请求人不能审批自己的
申请；恢复审批人还不能是触发隔离的安全反馈报告人或确认人。每个 postmortem version 同时最多一个
`awaiting_review` 申请，expected version、行锁和 handler advisory lock 防止并发重复推进。

### 4. 批准恢复生成新知识版本

批准事务必须同时完成：

1. 重验当前身份、Token、权限、tenant/global scope、恢复申请版本、postmortem 和快照证据；
2. 把恢复申请推进为 `approved`；
3. 把 postmortem 恢复为 `published`，生成新的 knowledge version，递增 `restore_count` 并记录时间；
4. 追加成功审计。

任一步失败全部回滚。旧反馈、旧快照、旧 knowledge version、内容、向量和 incident/remediation 谱系继续保留。
`PostgresGovernanceKnowledgeRetriever` 仍只选择当前 `published` 行，所以恢复后只返回新 knowledge version，且
metadata 继续声明 advisory-only、非授权来源、非恢复证据、非执行指令。

## 结果与取舍

- 质量判定可重放、可比较，陈旧快照无法被拿来恢复已变化的知识事实。
- 误报恢复不会抹除隔离原因，也不会让旧版本重新成为当前检索结果。
- 人工审批路径更长，但避免单一管理员同时制造反馈、确认反馈并恢复知识。
- 当前不自动生成快照、不自动恢复、不调用外部通知或生产修复系统。

## 回滚

应用回滚先停止 API 并导出快照、恢复申请和 postmortem/feedback 谱系。Alembic 从 `d8f1c2a4b730` 降到
`c7a4d2e9f610` 时删除恢复/快照表、索引和快照 UPDATE trigger，并移除复盘恢复历史列；不删除既有反馈、复盘
内容、事件、处置或审计事实。

## 撤销条件

只有外部知识治理平台经过验证，能提供等价的租户隔离、不可变证据、四方职责分离、事务式版本恢复、即时撤权、
RAG 当前版本选择和完整谱系时，才允许替换本控制面。外部系统仍不能成为智能体授权、自动恢复证据或执行指令来源。

## 验证证据

- 领域测试覆盖四种评测状态和默认角色权限；
- PostgreSQL 测试覆盖快照幂等与 UPDATE 拒绝、24 小时保留、陈旧快照、旧 expected version、重复活动申请、
  请求人/报告人/确认人审批拒绝、审计失败整体回滚和恢复后 RAG 新版本重入；
- API/控制台测试覆盖 assessment/status 筛选、独立 403 降级、断开清理和批准确认；
- Alembic 往返、只读治理演练、全量测试、静态检查和生产发布门禁共同验收。
