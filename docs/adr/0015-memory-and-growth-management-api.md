# ADR 0015: 记忆与成长候选管理 API

## 状态

已接受，2026-08-25。

## 背景

v0.14 已经能够创建、查询、取消运行并处理工具审批，但正式记忆和成长候选仍只能通过内部仓储或测试代码
观察。生产运维需要稳定、可分页、最小权限的管理接口来完成以下动作：

- 查看和搜索已经发布的正式记忆；
- 查看成长候选、证据 ID、冲突、最新评测、人工决定和发布状态；
- 由可信评测器评估 pending 候选；
- 由授权人员批准发布或拒绝候选；
- 回滚已经发布的候选并停止运行时召回。

现有 `PostgresMemoryStore.search` 会并发安全地增加召回次数，因此不能直接作为管理列表/搜索；现有
`LearningService` 使用读后写状态转换，适合内存实现和同步沉淀管线，但不能直接暴露为多副本管理 API 的
生产并发入口；现有 `PostgresKnowledgeAssetPublisher` 已经保存发布和合并回滚的事务不变量，应继续作为
唯一正式记忆发布器。

## 决策

### 1. 独立的无副作用管理查询

新增 `PostgresGrowthManagementRepository`。正式记忆和候选查询直接按 PostgreSQL 事实读取，不调用运行时
`MemoryStore.search`，因此不会改变 `recall_count` 或 `last_recalled_at`。

列表按 `created_at DESC, id DESC` 排序，游标使用严格 URL-safe Base64 JSON：

```json
{"created_at":"2026-08-25T08:00:00+00:00","id":"...","v":1}
```

解码要求字段集合、版本、UUID、时区和 Base64 字符全部合法。记忆列表新增作用域/状态/时间索引；候选复用
已有治理扫描索引；审批新增 candidate + created_at + id 索引以支持最新决定投影和幂等检查。

### 2. 评测结果只来自可信服务端 evaluator

HTTP 评测请求只包含 agent/domain 和 `expected_version`，请求模型禁止额外字段。客户端不能提交
`passed`、score、metrics 或评测摘要。`AgentGrowthManagementService` 加载精确候选快照后调用部署时注入的
`CandidateEvaluator`，再由仓储在候选行锁事务中提交结果。

`pending -> evaluating -> awaiting_approval/rejected` 两个转换在一个数据库事务中聚合，候选版本增加 2，
避免管理 API 在 evaluator 返回后留下持久 `evaluating` 半状态。评测记录保存内部候选版本标记，用于识别
相同请求重放；该内部字段不进入外部 DTO。

### 3. 人工决定和发布保持单事务

扩展 `PostgresKnowledgeAssetPublisher`，新增作用域化 `approve_and_publish_scoped`。它在同一 PostgreSQL
事务和候选行锁下完成：

1. 校验认证 tenant、agent、domain、候选 ID、状态和 expected version；
2. 校验最新评测存在且通过；
3. 校验合并/压缩来源版本、状态和正式记忆；
4. 写入 approved 审批；
5. 创建唯一正式记忆；
6. 激活候选，并按既有规则弃用来源候选和来源记忆。

人工拒绝写入 rejected 审批并将候选置为 rejected；回滚继续复用发布器的来源恢复事务。相同
expected version、决定、reviewer 和 note 的重复批准/拒绝安全重放；并发相同批准只生成一条审批和一条正式
记忆。决定、版本或 note 变化返回状态冲突。

### 4. 最小权限与安全投影

权限分离为：

- `memories:read`
- `candidates:read`
- `candidates:evaluate`
- `candidates:promote`

tenant 只来自 Bearer Principal，agent 必须同时通过 grant。客户端 tenant header 不参与授权；跨租户或
同名 agent 查询返回 404，不泄漏资源存在性。

候选列表只返回 500 字符内容预览；详情白名单返回待审内容、namespace、memory type、置信度、重要度、
标签、适用范围、证据 ID、冲突摘要、最新评测/审批和正式记忆状态。以下内容不返回：

- 原始反思 prompt；
- provider state；
- checkpoint 和消息历史；
- 未脱敏运行事件正文；
- 内部评测重放字段；
- 任意未列入 DTO 白名单的 `proposed_change` 或 memory metadata。

### 5. 路由安全关闭

只有 `growth` 服务和可信 principal dependency 同时存在时才注册以下端点：

```text
GET  /v1/memories
GET  /v1/candidates
GET  /v1/candidates/{candidate_id}
POST /v1/candidates/{candidate_id}/evaluate
POST /v1/candidates/{candidate_id}/decide
POST /v1/candidates/{candidate_id}/rollback
```

注入 `PostgresAPIKeyService` 时复用既有 Bearer 认证依赖。

## 备选方案

### 复用 `MemoryStore.search`

拒绝。管理查询会污染召回统计，使候选生命周期治理错误地把人工浏览当成真实运行价值证据。

### 直接暴露 `LearningService`

拒绝。读后写转换无法在多副本并发下保证评测、审批、发布和回滚的一致性，也无法提供 tenant/agent/domain
作用域锁定和幂等重放。

### 客户端提交评测结论

拒绝。任何拥有 HTTP 写权限的调用方都可伪造 `passed=true`，绕过真实回归和证据检查。

### 为每个管理动作建立第二套状态表

拒绝。会与 `learning_candidates`、`evaluations`、`approvals` 和 `memories` 的既有事实产生双状态机漂移。

## 影响

### 正向

- 正式记忆管理搜索不影响运行时价值统计；
- 评测、人工决定、发布和回滚具有清晰的并发与幂等边界；
- 运维人员可在不接触 checkpoint 或原始轨迹正文的情况下治理成长资产；
- 继续复用 PostgreSQL 事实来源和唯一发布器，没有引入新服务或收费 API。

### 代价

- 评测聚合事务不持久展示短暂 `evaluating` 状态；未来异步 evaluator 需要任务/Outbox 状态；
- 首版管理文本搜索使用转义后的大小写不敏感子串匹配，适合有界管理查询但不是大规模语义搜索；
- `candidates:promote` 同时覆盖人工批准、拒绝和回滚，后续职责分离可能拆为更细权限；
- rollback 首版通过 expected version 和最终状态实现幂等，没有独立 rollback request key。

## 撤销条件

- evaluator 延迟需要脱离 HTTP 请求时，引入 PostgreSQL Outbox + Worker，但保留 trusted evaluator、候选版本和
  行锁提交协议；
- 管理搜索规模或延迟不满足 SLA 时，引入专用全文/向量索引，但不得复用会改变召回统计的运行时接口；
- 合规要求评测、批准和回滚必须由不同主体时，拆分权限并增加职责分离策略；
- PostgreSQL RLS 成为多租户强制基线时，在现有应用层 tenant/agent/domain 条件之外增加数据库策略，不移除
  应用层检查。
