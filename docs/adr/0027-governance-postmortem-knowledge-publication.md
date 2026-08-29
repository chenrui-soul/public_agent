# ADR 0027：治理复盘评审与知识安全发布

## 状态

已接受，2026-08-25。

## 背景

v0.25 已能用固定 Playbook、职责分离审批和新恢复事实把治理事件推进到 verified 处置，但处置经验仍停留在单次
记录中，无法被后续值守安全复用。直接把处置正文写入普通知识库会混淆 agent 作用域，也可能让历史文本被误当作
授权、恢复证据或可执行指令。

## 决策

新增 PostgreSQL 权威的结构化治理复盘与独立知识检索链路：

- 只有 verified remediation 可创建复盘；每个 remediation 最多一份，并保存 incident/remediation/cycle/version
  来源快照和内容指纹。
- 根因、影响和预防措施使用与 Playbook 兼容的受限枚举；安全摘要限 10-1000 字并拒绝凭据、连接串、代码块、
  Shell、SQL 和编排命令。
- read/request/review 使用独立权限；请求人不能评审自己的复盘。批准前后都重新鉴权，发布阶段加锁并重新验证
  来源版本，避免嵌入计算期间事实漂移。
- 评审批准和知识发布在同一事务内完成；拒绝、撤权、冲突或来源漂移不产生可检索词法文本或向量。
- 知识固定进入 `operations.governance.postmortems` namespace，使用 domain `operations-governance` 和 access tag
  `operations.governance:advisory`。`PostgresGovernanceKnowledgeRetriever` 通过中文全文检索、pgvector 和 RRF
  返回同 tenant 已发布复盘。
- 每个命中保留 incident/remediation/version/fingerprint 谱系，并声明 advisory-only、非授权来源、非恢复证据、
  非执行指令。治理知识不写入普通 agent-bound knowledge 表。

## 后果

verified 处置可以沉淀为可审计、可去重、可检索的值守经验，且不引入收费嵌入调用；默认离线确定性嵌入便于生产
门禁和迁移验证。代价是治理检索需要独立装配和显式 access tag，复盘分类受限，不能保存任意日志或运行手册正文。
未来如切换真实嵌入模型，必须版本化 profile 并重建向量，不能与现有维度静默混用。

## 回滚

先回退应用隐藏复盘 API、控制台和治理检索器，再将 Alembic 从 `b6d8e1f3a420` downgrade 到
`9f4e7c2d1a60`。回滚前导出已发布复盘及其来源谱系；迁移删除复盘表、GIN/HNSW 索引和审计 `postmortem_id`
外键，不修改事件、处置、告警、策略或既有审计事实。
