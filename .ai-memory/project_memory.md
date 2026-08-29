# public_agent 项目记忆

## Glossary

### 治理审计安全投影

- **规范名称**：治理审计安全投影（Governance Audit Safe Projection）
- **定义**：从 append-only 审计事实中仅返回 actor subject、动作、结果、目标资源、白名单标量元数据和时间的 API DTO；明确排除 Token ID、凭据和内部异常正文。

### 告警响应 SLA

- **规范名称**：告警响应 SLA（Alert Response SLA）
- **定义**：根据告警 first_seen、当前 lifecycle 和版本化有界阈值确定性派生的 within_sla、due、breached、acknowledged 或 resolved 运营状态；不形成第二授权或关闭状态机。

### 只读治理演练

- **规范名称**：只读治理演练（Read-only Governance Drill）
- **定义**：在当前数据库身份重验后，通过 PostgreSQL catalog 验证职责分离、append-only trigger、lifecycle CHECK 和关键索引，且不创建身份、不修改治理业务事实的控制面证据报告。

## Project Convention: 阶段验收后自主续推

**类型**: 协作约定 / 长任务执行规范
**适用范围**: 后续版本阶段、Wave 收口、项目计划与 handoff

### 规范内容

一个阶段达到既定验收标准后，主 Agent 必须直接选择并启动下一条有界、可运行的纵向链路，不再逐阶段请求用户确认。下一阶段仍需先完成现状审计、范围与验收定义，并遵守生产安全边界。

只有下一步需要新增授权或外部协调时暂停，包括生产/数据破坏性操作、真实外部通知、凭据或权限授予，以及明显超出既定项目目标的范围扩张。自动续推不授权提交、推送、付费 API、自动扩缩容或连接外部服务。

### 发现来源

2026-08-25 用户明确要求阶段完成后自行开启下一阶段任务。

## Decision Record: 治理审计查询、告警 SLA 与只读演练

**日期**: 2026-08-25
**问题**: 如何让独立审计员安全复核容量治理历史、让值守人员识别响应超时，并在不污染生产事实的情况下验证关键控制面。

### 决策

**选择**: 独立 `operations.capacity_audit:read`、actor/filter-bound keyset、安全 DTO、服务端 UTC SLA 派生和 PostgreSQL catalog 只读演练。
**理由**: 复用 PostgreSQL 的身份、事务、审计和 Schema 事实，避免数据库账号扩散、第二 SLA 状态机和演练写入污染。
**Trade-offs**: Principal 删除后 actor subject 只能为空；safe metadata 新键需显式评审；breached 暂不发送外部通知；catalog 演练不替代灾备恢复或渗透测试。

### 撤销条件

合规要求 WORM/签名、跨系统关联、长期冷热分层或真实通知 SLA，且替代系统经过租户隔离、即时撤权、恢复和安全投影验证时，允许接入外部后端；PostgreSQL 原始治理事实和 append-only 约束仍保留。

## Project Convention: 审计、SLA 与演练失败关闭

**类型**: 安全规范 / 运营治理
**适用范围**: 容量治理审计 API、控制台、SLA 派生、治理演练、迁移和生产门禁

### 规范内容

审计查询必须使用独立权限并在事务内重验当前 tenant、Principal、Token 和 global scope；cursor 绑定 actor、handler 和全部筛选。响应不得返回 Token ID，safe metadata 采用键和值类型双白名单。SLA 只能从持久时间事实派生，不得自动确认或关闭告警。治理演练只读 catalog，证据缺失返回失败，不自动修复或制造测试事实。控制台各面板按权限独立降级，断开或切换身份必须清除旧视图。

### 发现来源

2026-08-25 v0.23 实现、安全审查、浏览器烟雾和生产门禁。

### 领域能力包

- **规范名称**：领域能力包（Domain Package）
- **定义**：用于将通用运行内核专业化的版本化资产集合，包含身份、知识、工具、流程、策略、记忆命名空间和评测。

### 成长候选

- **规范名称**：成长候选（Learning Candidate）
- **定义**：从运行经验中提取、尚未进入正式能力库的记忆、策略或技能变更提案。

### 知识沉淀管线

- **规范名称**：知识沉淀管线（Knowledge Sedimentation Pipeline）
- **定义**：将持久化运行经验依次转化为去重候选、评测结果、人工审批和正式可回滚资产的受控纵向链路。

### 完整运行轨迹

- **规范名称**：完整运行轨迹（Run Trace）
- **定义**：按不可变事件序列记录一次运行中的模型响应、工具授权与结果、验证和终态，并作为反思证据的事实来源。

### 反思引擎

- **规范名称**：反思引擎（ReflectionEngine）
- **定义**：读取经过脱敏和容量控制的完整运行轨迹，输出绑定真实事件证据的结构化成长候选提取器。

### 候选冲突评估

- **规范名称**：候选冲突评估（Candidate Conflict Assessment）
- **定义**：在同一租户、智能体、领域、候选类型和记忆作用域内，将候选关系保守分类为重复、兼容、矛盾或无冲突的可审计判断。

### 合并候选

- **规范名称**：合并候选（Merged Candidate）
- **定义**：由两个或多个兼容成长候选显式生成、保留完整来源谱系并重新经过评测和人工审批的新成长候选。

### 外部知识

- **规范名称**：外部知识（External Knowledge）
- **定义**：来自企业文档、手册、法规等可更新且可引用的资料；与经过成长治理发布的智能体记忆严格分离。

### 知识检索器

- **规范名称**：知识检索器（KnowledgeRetriever）
- **定义**：按租户、智能体、领域、命名空间和权限返回带来源与分数的外部知识命中，供运行时作为不可信上下文使用的统一协议。

### 嵌入配置档案

- **规范名称**：嵌入配置档案（Embedding Profile）
- **定义**：由供应商/模型名称和固定维度组成的不可变标识；同一知识索引和评测基线不得静默混用不同档案。

### RAG 评测集

- **规范名称**：RAG 评测集（RAG Evaluation Dataset）
- **定义**：以稳定 `source_key` 为相关性真值、具有版本和规范化内容哈希的 YAML/JSONL 领域检索回归资产。

### 词法配置档案

- **规范名称**：词法配置档案（Lexical Profile）
- **定义**：绑定中文分词实现版本、参数和领域词典哈希的不可变标识；全文查询只使用与当前档案一致的派生索引。

### 知识重排器

- **规范名称**：知识重排器（KnowledgeReranker）
- **定义**：在混合召回和 RRF 融合后，对有界候选执行二次排序并提供独立 profile、分数、超时与安全降级边界的统一协议。

## Decision Record: 生产级架构基线

**日期**: 2026-08-25
**问题**: 通用智能体框架应采用轻量本地架构、生产级模块化单体还是微服务架构。

### 选项分析

| 选项 | 优势 | 劣势 | 适用场景 |
|---|---|---|---|
| 本地轻量单体 | 启动简单 | 并发、审计、权限和迁移成本不足 | 个人原型 |
| 生产级模块化单体 | 事务、治理、演进能力完整 | 初始工程成本较高 | 当前项目 |
| 微服务 | 独立扩容 | 分布式复杂度过高 | 明确的组织和扩容边界 |

### 决策

**选择**: Python 异步模块化单体，PostgreSQL + pgvector 主存储，Redis 辅助。
**理由**: 支持通用内核、领域能力包、受控成长、审计、审批、版本与回滚，同时避免初期微服务复杂度。
**Trade-offs**: 本地开发需要容器化数据库和迁移维护。

### 撤销条件

单体已经通过实测确认无法满足隔离或吞吐要求，或出现明确的独立团队、独立合规和独立扩容需求。

## Decision Record: 受控知识沉淀纵向闭环

**日期**: 2026-08-25
**问题**: 如何让终态运行经验形成可复用资产，同时避免未经验证的输出直接污染正式记忆。

### 选项分析

| 选项 | 优势 | 劣势 | 适用场景 |
|---|---|---|---|
| 成功后直接写长期记忆 | 延迟低、实现简单 | 无法控制幻觉、冲突和恶意输入污染 | 非生产实验 |
| 模块化单体内受控闭环 | 事务、审批、审计和回滚边界清晰 | 当前反思跟随应用服务执行 | 当前项目 |
| 独立事件总线与反思 Worker | 可独立扩容 | 过早引入消息一致性和运维成本 | 反思吞吐形成独立压力后 |

### 决策

**选择**: 模块化单体内受控闭环，通过协议保留未来异步 Worker 边界。
**理由**: 终态运行只产生候选；候选经过作用域指纹去重、证据评测和显式审批后，才以事务发布为正式记忆。
**Trade-offs**: 完整轨迹模型反思已经实现；技能/领域包发布器和领域回归评测仍需后续实现。

### 撤销条件

反思耗时影响在线任务尾延迟，或出现独立扩缩容、重试、死信和跨部署消费需求。

## Project Convention: 成长资产发布边界

**类型**: 架构约束
**适用范围**: `growth`, `memory`, `storage`, application services

### 规范内容

运行输出不得直接写入正式记忆；必须经过成长候选、评测、人工审批和发布器。正式记忆回滚时必须同时停止运行时召回。

### 示例

**正确**: `process_run → awaiting_approval → approve_and_publish → memory.recalled`

**错误**: 任务成功后直接调用 `MemoryStore.save` 将模型输出激活。

### 发现来源

2026-08-25 知识沉淀与复用闭环实现。

## Decision Record: 完整轨迹受控反思

**日期**: 2026-08-25
**问题**: 如何从模型、工具、验证和失败的完整轨迹中提取经验，同时防止秘密泄漏、提示注入和伪造证据。

### 选项分析

| 选项 | 优势 | 劣势 | 适用场景 |
|---|---|---|---|
| 只反思最终输出 | 简单、成本低 | 丢失工具和失败证据 | 离线安全基线 |
| 原始轨迹直接给模型 | 上下文完整 | 泄密、注入和容量风险 | 不采用 |
| 脱敏限流后结构化反思 | 完整、可审计、可控 | 可能裁剪事件并增加尾延迟 | 当前项目 |

### 决策

**选择**: 复用不可变 `run_events` 加载完整轨迹，反思前递归脱敏和容量控制，模型结果必须通过严格 JSON Schema 并引用实际呈现的事件 ID。
**理由**: 同时保留模型、工具、验证和失败证据，并让提示注入、伪造引用和自动发布在边界处失败关闭。
**Trade-offs**: 容量裁剪会省略部分事件；反思当前同步执行；暂不提供独立 reflections 查询表。

### 撤销条件

反思尾延迟需要独立 Worker，或审计查询证明候选 JSON 无法满足独立反思记录检索需求。

## Project Convention: 反思证据边界

**类型**: 安全与架构约束
**适用范围**: `core.trace`, `growth.reflection`, `growth.pipeline`, `storage.runs`

### 规范内容

运行轨迹内容只能作为不可信证据；发送模型前必须脱敏和限流。每个反思项至少绑定一个本次实际呈现的事件 ID；缺失、伪造或裁剪范围外的 ID 必须拒绝整次反思。反思结果不得自动发布。

### 示例

**正确**: `load_trace → redact/limit → JSON reflection → validate event ids → awaiting_approval`

**错误**: 将原始工具输出直接拼入 Prompt，或接受没有证据事件 ID 的模型总结。

### 发现来源

2026-08-25 完整轨迹 ReflectionEngine 实现。

## Decision Record: 候选冲突与合并治理

**日期**: 2026-08-25
**问题**: 如何在候选规模增长后进行可索引的精确去重、保守冲突识别和可回滚合并，同时不破坏拒绝后重提与人工审批边界。

### 选项分析

| 选项 | 优势 | 劣势 | 适用场景 |
|---|---|---|---|
| JSON 指纹扫描 | 实现简单 | 查询无索引、冲突关系不可治理 | 小规模原型 |
| 唯一约束并自动覆盖 | 并发去重直接 | 阻止 rejected/rolled_back 后重提，绕过审批 | 不采用 |
| 独立索引、保守检测、显式合并 | 可扩展、可审计、可回滚 | 需要事务锁和来源谱系 | 当前项目 |

### 决策

**选择**: 独立指纹列和非唯一作用域索引；精确创建使用 advisory lock；兼容与矛盾只做审计标记，合并必须显式创建新候选。
**理由**: 保持 PostgreSQL 查询效率、并发幂等、拒绝/回滚后重新提出，以及评测和人工审批边界。
**Trade-offs**: 规则检测器是保守启发式；合并增加候选和评测记录；语义冲突召回当前最多检查 100 个同作用域候选。

### 撤销条件

规则检测器在领域回归集上误判率不可接受，或候选规模使同作用域冲突扫描超出延迟目标时，引入领域模型、语义键或全文/向量召回，但保持冲突评估与合并谱系契约。

## Project Convention: 合并候选发布与回滚边界

**类型**: 架构与事务约束
**适用范围**: `growth.conflicts`, `growth.pipeline`, `storage.repositories`

### 规范内容

冲突评估不得自动覆盖或发布候选。合并必须创建新候选，保存来源候选 ID、版本、状态、指纹、运行和事件证据，并重新评测和审批。合并发布时必须在同一事务中激活合并记忆、弃用来源候选并停用来源记忆；合并回滚时必须恢复发布前来源状态和来源记忆。

### 示例

**正确**: `compatible sources → explicit merge → awaiting_approval → publish/deprecate sources → rollback/restore sources`

**错误**: 检测到相似文本后直接覆盖活动记忆，或回滚合并资产但不恢复来源记忆。

### 发现来源

2026-08-25 候选冲突检测、合并和独立指纹索引实现。

## Decision Record: PostgreSQL 混合 RAG

**日期**: 2026-08-25
**问题**: 如何将可更新、可引用的专业领域资料接入运行时，同时不污染受控成长记忆。

### 选项分析

| 选项 | 优势 | 劣势 | 适用场景 |
|---|---|---|---|
| 复用 MemoryStore | 表和接口较少 | 混淆外部资料与已验证经验，治理状态和 ACL 不匹配 | 不采用 |
| 独立向量数据库 | 专用扩展能力强 | 增加一致性、备份、权限和运维成本 | PostgreSQL 实测不满足规模后 |
| PostgreSQL 独立知识模型 + 混合检索 | 复用事实来源、事务和租户边界，可引用可回滚 | 每次检索两条查询，中文分词需后续增强 | 当前项目 |

### 决策

**选择**: 独立 `knowledge_documents` / `knowledge_chunks`，使用 `tsvector` GIN 与 pgvector HNSW 双路召回，应用层 RRF 融合并通过 `KnowledgeRetriever` 接入运行时。
**理由**: 保持外部知识和成长记忆治理边界，复用 PostgreSQL 的事务、审计和多租户隔离，并避免过早引入独立向量服务。
**Trade-offs**: 单部署固定 384 维；确定性嵌入仅供测试；`pg_catalog.simple` 的中文分词质量有限；访问标签依赖上层认证授权上下文。

### 撤销条件

真实负载的 EXPLAIN、召回评测或尾延迟证明 PostgreSQL 混合检索无法满足目标，或中文分词质量无法通过领域回归集时，引入可替换检索后端，但保持 `KnowledgeRetriever` 契约和知识/记忆边界。

## Project Convention: RAG 知识与成长记忆隔离

**类型**: 安全与架构约束
**适用范围**: `knowledge`, `core.runtime`, `storage.knowledge`, `growth`

### 规范内容

原始 RAG 文档不得直接写入正式记忆或自动成为成长候选。检索必须先按租户、智能体、领域、命名空间和授权访问标签过滤；呈现给模型的来源必须容量受限、标记为不可信数据并保留 `[Kx]` 引用和运行事件证据。

### 示例

**正确**: `document → versioned chunks → hybrid retrieval → [Kx] answer → run trace → reflection candidate → approval`

**错误**: 文档上传后直接调用 `MemoryStore.save` 激活，或把文档中的指令当作系统指令执行。

### 发现来源

2026-08-25 PostgreSQL 全文 + pgvector 混合 RAG 纵向链路实现。

## Decision Record: 真实嵌入与 RAG 质量门禁

**日期**: 2026-08-25
**问题**: 如何用真实语义嵌入替代测试哈希嵌入，并在模型、知识或检索参数升级后可靠发现质量退化。

### 选项分析

| 选项 | 优势 | 劣势 | 适用场景 |
|---|---|---|---|
| 摄取服务直接调用供应商 | 代码入口少 | 密钥、重试和响应校验扩散 | 不采用 |
| Provider 协议 + 临时测试断言 | 可替换 | 无历史评测资产和持久门禁 | 小型原型 |
| Provider 协议 + 版本化评测 + PostgreSQL 门禁 | 可替换、可审计、可回归 | 增加 SDK、迁移和数据集维护 | 当前项目 |

### 决策

**选择**: 官方 OpenAI 异步 SDK 的 `OpenAIEmbeddingProvider`，默认 `text-embedding-3-small` 显式 384 维；RAG 评测使用版本化 YAML/JSONL、确定性检索/引用指标、绝对阈值和上一成功运行回归门禁，并持久化到 PostgreSQL。
**理由**: 保持 `EmbeddingProvider`/`KnowledgeRetriever` 可替换边界，同时让嵌入和检索升级具有可复现、可审计证据。
**Trade-offs**: 生产嵌入产生费用和外部延迟；384 维变更必须全量重建；首版引用规则不判断复杂语义事实性。

### 撤销条件

统一模型网关提供等价能力；领域评测证明 384 维不足；PostgreSQL 明细分析规模不足；或规则引用指标与人工判断偏差需要版本化模型裁判。

## Project Convention: RAG 评测安全与发布边界

**类型**: 安全与架构约束
**适用范围**: `knowledge.embeddings`, `evaluation`, `storage.evaluations`, 领域能力包发布流程

### 规范内容

API Key 只能经 `SecretStr`/环境变量进入客户端，供应商原始异常不得进入日志、评测报告或项目记忆。回归策略必须绑定持久化评测仓储；单案例异常只保存安全错误类型。评测文档、召回文本和答案不得直接成为正式成长资产。

### 示例

**正确**: `versioned dataset → RAGEvaluator → absolute/regression gate → PostgreSQL report → release decision`

**错误**: 开启回归策略但不提供历史仓储，或把通过评测的答案直接写入 `MemoryStore`。

### 发现来源

2026-08-25 真实 EmbeddingProvider 与 RAG 评测体系实现和反例审查。

## Decision Record: 版本化中文分词与候选重排

**日期**: 2026-08-25
**问题**: 如何增强 PostgreSQL 混合 RAG 的中文专业术语召回，并允许后续替换重排实现而不破坏检索契约和可用性。

### 选项分析

| 选项 | 优势 | 劣势 | 适用场景 |
|---|---|---|---|
| 查询端逐字 tsquery | 无迁移 | 噪声高、领域词典不可版本化 | 不采用 |
| 应用层预分词 + PostgreSQL GIN + 独立重排协议 | 复用事实来源、可审计、可降级 | 增加派生字段、迁移和本地 CPU | 当前项目 |
| 独立搜索服务 + 交叉编码器 API | 能力上限高 | 一致性、权限、运维、费用和外部故障面增加 | 领域评测证明当前方案不足后 |

### 决策

**选择**: `JiebaChineseSegmenter` 搜索模式生成版本化 `lexical_text`/`lexical_profile`，全文和向量分支分别匹配 lexical/embedding profile；RRF 后通过 `KnowledgeReranker` 有界重排，默认使用确定性中文覆盖率 + 语义 + 融合分数，失败降级原 RRF。
**理由**: 在保持 PostgreSQL 事实来源、384 维向量、租户/ACL 和 `KnowledgeRetriever` 边界的前提下，提高中文全文召回并为交叉编码器保留替换点。
**Trade-offs**: 摄取和重建增加本地 CPU；迁移重建生成列和 GIN；jieba 与规则重排仍有领域质量上限。

### 撤销条件

中文领域评测无法满足 Recall/MRR/NDCG/P95 门槛，候选池 CPU 不可接受，或 PostgreSQL GIN 在真实规模下无法满足 SLA 时，以相同协议替换分词、重排或搜索后端。

## Project Convention: 中文词法索引和重排安全边界

**类型**: 架构与安全约束
**适用范围**: `knowledge.segmentation`, `knowledge.reranking`, `storage.knowledge`, RAG 评测与运行事件

### 规范内容

分词 profile 必须随实现、参数或词典变化；全文分支不得混用旧 profile，向量分支不得被词典 profile 过滤。重排器只能重排已有有界候选，不得新增、重复或篡改身份与内容；任何非法结果、异常或超时必须回退原 RRF，并只保存安全错误类型。分词词项、重排结果和评测答案不得直接成为正式成长记忆。

### 示例

**正确**: `versioned lexical_text → lexical/vector candidates → RRF → validated reranker → [Kx]`；词典变化后调用幂等 `reindex_lexical`。

**错误**: 仅按新词典查询旧索引、用 lexical profile 过滤向量候选、接受重排器返回的新文档，或把高分重排结果直接写入 `MemoryStore`。

### 发现来源

2026-08-25 中文分词与重排 v0.7 纵向链路、迁移回填和 PostgreSQL 反例验证。

## Project Convention: Alembic 回填后 DDL 游标边界

**类型**: 数据库迁移规范
**适用范围**: Alembic 中“读取旧数据 → Python 派生回填 → ALTER TABLE/索引重建”的迁移

### 规范内容

同一 PostgreSQL 迁移会话中，回填查询必须在后续 DDL 前完全结束。禁止保留 server-side streaming cursor 后执行 `ALTER TABLE`；使用有界 keyset 分页并缓冲单批结果，确保没有活动查询占用目标表。

### 示例

**正确**: `SELECT ... WHERE id > last_id ORDER BY id LIMIT 500 → executemany UPDATE → 下一批 → ALTER TABLE`

**错误**: `stream_results=True` 遍历后直接 `ALTER TABLE`，导致 asyncpg `ObjectInUseError`。

### 发现来源

2026-08-25 `d7b3a1e9f240` 首次迁移验证；事务自动回滚后改用 keyset 分页并通过往返测试。

## Glossary Addendum

### 候选生命周期治理

- **规范名称**：候选生命周期治理（Candidate Lifecycle Governance）
- **定义**：使用版本化保护规则、价值评分、有界扫描和幂等审计，对成长候选及正式记忆执行软过期、低价值淘汰或提出待审批压缩候选的受控流程。

### 压缩候选

- **规范名称**：压缩候选（Compressed Candidate）
- **定义**：由多个兼容或重复的活跃候选生成、保存关系化来源版本和状态、且必须重新评测审批后才能替换来源的新成长候选。

## Decision Record: 候选软过期、价值淘汰与可审批压缩

**日期**: 2026-08-25
**问题**: 候选和正式记忆持续增长后，如何处理陈旧、低价值和重复资产，同时不破坏证据、审批、发布和回滚边界。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
|---|---|---|---|
| TTL 物理删除 | 实现和空间回收直接 | 证据与谱系不可恢复，误判无法审计 | 低 |
| 原地覆盖正式记忆 | 表少、替换快 | 压缩绕过评测审批，并发回滚不安全 | 中 |
| 软过期、价值评分、新建压缩候选 | 审计、保护、幂等、回滚完整 | 增加使用统计、谱系和策略治理 | 高 |

### 决策

**选择**: 候选进入 `expired`、正式记忆进入 `expired`；价值评分组合最近评测、重要度、置信度、召回次数和新鲜度；压缩只创建带关系化谱系的新候选并重新评测审批。
**理由**: 自动治理不能删除不可变证据，也不能绕过现有成长资产发布边界；扫描后重新校验候选版本、状态、召回计数和活跃后代可抵抗并发竞态。
**Trade-offs**: 召回增加轻量统计写；软过期不释放历史空间；确定性压缩器只提供安全基线，不生成新摘要。

### 影响范围

- `growth.governance`, `growth.models`, `growth.conflicts`, `growth.pipeline`
- `storage.models`, `storage.repositories`, Alembic `a91c4e7d2b60`
- 候选、正式记忆、关系谱系、治理动作、评测和审批测试

### 撤销条件

同步召回统计影响检索 P95 时改为 Outbox 异步聚合；价值评分误伤率超过领域门槛时发布新策略版本；确定性压缩信息保留不足时接入版本化模型压缩器，但仍只能生成候选并重新评测审批。

## Project Convention: 自动遗忘与压缩保护边界

**类型**: 成长治理与数据安全约束
**适用范围**: 候选扫描、正式记忆召回、过期/淘汰 Worker、合并或压缩发布和回滚

### 规范内容

自动治理不得物理删除运行、事件、候选、评测、审批、正式记忆或谱系。高风险、显式保护、评测/审批中、已批准待发布、高价值或被非终态派生候选引用的来源不得自动过期或淘汰。压缩不得继承旧 `merge`/`compression` 直接派生元数据形成嵌套双谱系；必须创建新的唯一直接关系，并在发布前保持来源状态不变。

### 示例

**正确**: `keyset scan → protection/value decision → row/version/recall recheck → expired audit`；或 `compatible sources → compressed candidate → evaluation → approval → atomic source replacement → rollback restore`。

**错误**: 按 TTL `DELETE`、在召回后仍按旧计数淘汰、压缩器直接覆盖正式记忆、让候选同时携带 `merge` 和 `compression` 两套直接来源。

### 发现来源

2026-08-25 v0.8 候选生命周期治理实现、并发 PostgreSQL 反例、旧合并谱系迁移回填和深度代码审查。

## Glossary Addendum: 生成模型供应商

### 隔离供应商状态

- **规范名称**：隔离供应商状态（Provider-isolated State）
- **定义**：为正确重放某一模型供应商多轮协议而保存在通用消息上的不透明状态；领域逻辑和其他供应商不得读取或依赖其私有字段。

## Decision Record: OpenAI Responses 无状态生成适配器

**日期**: 2026-08-25
**问题**: 如何接入真实 OpenAI 生成模型，同时保持并发隔离、工具历史可恢复、错误脱敏和供应商可替换。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
|---|---|---|---|
| 手写 HTTP | 协议直接 | 重复维护认证、模型和错误协议，泄漏面大 | 中 |
| SDK + provider 内 previous_response_id | 历史请求短 | 并发串话、进程恢复和审批检查点缺少事实状态 | 中 |
| 官方 SDK + 完整消息历史 + 隔离供应商状态 | 并发、恢复、验证和替换边界完整 | 输入 token 增加，需保存 reasoning 状态 | 中 |

### 决策

**选择**: 使用官方异步 SDK 的 Responses API；运行时保存 assistant 工具调用和 tool 输出，OpenAI reasoning/output 作为隔离供应商状态重放，不在 provider 实例中保存运行会话。
**理由**: PostgreSQL/检查点可持有完整事实，provider 可被并发共享；未知工具、乱序调用和篡改状态能在供应商边界失败关闭。
**Trade-offs**: 完整历史增加输入 token；reasoning 加密状态需按敏感运行状态保护；strict tool 要求领域 Schema 本身完整。

### 影响范围

- `core.types`, `core.runtime`
- `providers.openai`, `providers.__init__`
- OpenAI 生成配置、Mock HTTP 契约测试和运行时工具历史测试

### 撤销条件

只有在 PostgreSQL 提供可审计的供应商会话映射并证明并发隔离、恢复和保留期合规后，才评估 `previous_response_id` 优化；历史成本超标时先做版本化上下文压缩，不允许静默截断。

## Project Convention: 生成供应商工具与错误安全边界

**类型**: 模型供应商协议与安全规范
**适用范围**: 生成模型请求转换、工具调用、多轮历史、重试、异常和运行事件

### 规范内容

生成适配器必须以本次 `ModelRequest.tools` 作为唯一工具白名单；function call 参数必须是 JSON 对象，
调用 ID 唯一且与恰好一个同名 tool output 配对。SDK 内建重试必须关闭，只允许超时、429 和 5xx 在稳定
幂等键下有限重试。API Key、请求/响应正文、供应商原始异常和隔离 reasoning 状态不得进入普通事件日志、
异常正文或项目记忆。

### 示例

**正确**: `assistant(function_call) → tool(function_call_output) → validated Responses input`；400 立即返回安全 HTTP 分类，429/5xx 复用同一幂等键重试。

**错误**: provider 实例保存全局 previous response、接受未注册工具、把数组参数当对象、猜测修复悬空 tool output、透传 SDK 原始错误或对 4xx 自动重试。

### 发现来源

2026-08-25 v0.9 OpenAI Responses 适配器、reasoning 重放、工具历史配对和 Mock HTTP 安全反例。

## Glossary Addendum: 领域能力发布

### 领域包资产

- **规范名称**：领域包资产（Domain Package Asset）
- **定义**：隶属于一个不可变领域包版本的 instructions、skill、policy、workflow 或 evaluation 文本资产；每个资产具有独立内容哈希，但首版只能随领域包聚合原子发布。

## Decision Record: 内容寻址的领域能力包发布聚合

**日期**: 2026-08-25
**问题**: 技能、策略、工作流和领域能力包如何版本化发布，同时保证路径安全、内容不可变、评测审批、并发原子性和回滚审计。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
|---|---|---|---|
| 文件目录覆盖 | 实现最少 | 无不可变证据、事务发布和可靠回滚 | 低 |
| 各资产类型独立版本与发布器 | 单资产可独立晋升 | 重复状态机，首版需要跨资产兼容协调 | 高 |
| 领域包版本聚合 + 类型化不可变资产 | 一次门禁原子发布完整能力集合 | 单资产不能独立晋升 | 中 |

### 决策

**选择**: 以 `domain_package_versions` 为发布聚合，instructions、skills、policies、workflows 和 evaluations 进入 `domain_package_assets` 并保留独立 SHA-256；评测、审批、activate 与 rollback 使用追加表。
**理由**: 当前专业智能体能力必须作为兼容集合经过同一回归门禁，统一聚合可避免技能已激活而策略或工作流仍是旧版本的半发布。
**Trade-offs**: 单个技能更新也要提升领域包版本和重跑包级评测；文本资产直接保存在 PostgreSQL；独立技能市场后置。

### 影响范围

- `domains.models`, `domains.loader`, CLI 离线构建输出
- `storage.models`, `storage.domain_packages`, Alembic `b42e6f8a1c30`
- 领域包路径/容量/哈希测试和 PostgreSQL 发布/回滚集成测试

### 撤销条件

多个领域包稳定共享同一技能、技能独立发布频率显著高于领域包，或包级回归成本超过领域 SLA 时，拆出独立 skill version/release；领域包仍保存解析后的兼容版本快照并通过包级门禁。

## Project Convention: 领域包版本发布与回滚边界

**类型**: 领域能力治理、数据安全与发布约束
**适用范围**: 领域包构建、资产入库、评测、审批、agent version 激活和回滚

### 规范内容

领域包文本必须在入库前完成包内路径解析、UTF-8/NFC/LF 规范化、单资产/总容量限制和独立哈希。
同一租户、智能体、领域和语义版本不得绑定不同内容。评测失败、缺失人工批准、跨作用域、幂等键冲突或
并发状态变化时必须失败关闭。发布和回滚必须锁定 agent 聚合，在一个 PostgreSQL 事务中追加审计并切换
`active_version_id`；不得物理删除或原地覆盖版本、资产、评测、审批和发布记录。

### 示例

**正确**: `safe build → immutable draft → evaluation → human approval → agent row lock → activate audit + active_version switch`；回滚追加记录并恢复前一版本。

**错误**: 从包外读取资产、同版本覆盖内容、只凭评测自动激活、Redis 决定活跃版本、覆盖旧 agent version、回滚时删除失败版本和审批记录。

### 发现来源

2026-08-25 v0.10 领域能力包内容哈希、PostgreSQL 状态机、并发幂等发布和原子回滚实现。

## Glossary Addendum: 审批运行恢复

### 恢复租约

- **规范名称**：恢复租约（Resume Lease）
- **定义**：由 PostgreSQL 在批准工具调用后签发、绑定单个 run 和 owner token、具有明确到期时间的临时运行恢复所有权。

### Fencing Token

- **规范名称**：恢复 Fencing Token
- **定义**：每次领取或接管恢复租约时生成的新 UUID；finish 必须同时持有当前 token 且租约未过期，旧 worker 不得覆盖新状态。

## Decision Record: 不可变审批 checkpoint、恢复租约与工具幂等

**日期**: 2026-08-25
**问题**: 高风险工具在等待人工审批后，如何跨进程安全恢复原调用，并抵抗重复决定、并发领取、崩溃接管、旧 worker 覆盖和外部副作用重复。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
|---|---|---|---|
| HTTP 审批线程直接继续 | 实现少、延迟低 | 断线和多副本后 owner 不明确 | 低 |
| 只保存决定并重新规划 | checkpoint 小 | 记忆、RAG、模型和工具调用会漂移 | 中 |
| 不可变 checkpoint + PostgreSQL 租约/fencing + 工具幂等 | 精确、可审计、可接管、失败关闭 | 需要持久状态和下游去重 | 高 |

### 决策

**选择**: checkpoint 保存完整消息/provider state、当前及剩余调用、引用 ID、agent/tool 哈希；批准事务签发有限租约和 fencing token；工具使用稳定 `run_id:tool_call_id`。
**理由**: 人工批准必须绑定原始事实；数据库所有权防并发和旧状态覆盖；下游幂等覆盖外部成功但本地提交前崩溃的窗口。
**Trade-offs**: 首版无租约续期；长运行可能被接管；审批工具必须实现真实幂等；checkpoint 增加 PostgreSQL 容量。

### 影响范围

- `core.types`, `core.runtime`, `tools.base`, `application`
- `storage.runs`, `storage.models`, Alembic `c73f9a2d4e10` 与 `d84e1b6f5a20`
- 审批恢复单元、真实并发 PostgreSQL、跨租户和迁移往返测试

### 撤销条件

引入队列 worker 时可迁移 claim 触发位置但保留行锁/fencing；运行经常超过租约时增加受 token 约束的有限续租；checkpoint 体积达到阈值时迁移到加密对象存储并在 PostgreSQL 保留哈希和对象版本。

## Project Convention: 工具审批恢复安全边界

**类型**: 运行时并发、安全与外部副作用约束
**适用范围**: 高风险工具审批、checkpoint、恢复 worker、运行事件和持久化 finish

### 规范内容

批准只授权 checkpoint 中的一个精确调用；恢复不得重新检索记忆/RAG 或重新规划。可恢复工具必须声明并实现幂等，稳定调用键为 `run_id:tool_call_id`。活动租约拒绝并发 owner；过期 token 和被接管的旧 token 均不能 finish。resume token 只存于运行所有权字段，不进入普通事件、错误或项目记忆。拒绝原子取消且工具调用数为零；任意 agent/tool/scope/checkpoint 漂移失败关闭。

### 示例

**正确**: `waiting checkpoint → approved → row lock + lease token → exact tool(idempotency key) → fenced finish`；崩溃后 `expired lease → new token reclaim`。

**错误**: 审批后重新请求模型、用 Redis 锁决定 owner、租约到期仍允许旧 worker finish、把 token 写入 trace、或将 `idempotent=true` 当作无需下游去重的标记。

### 发现来源

2026-08-25 v0.11 工具审批后的运行恢复、真实并发租约、过期检查、跨租户隔离和迁移加固。

## Glossary Addendum: 知识导入管理

### 知识导入任务

- **规范名称**：知识导入任务（Knowledge Ingestion Job）
- **定义**：以 PostgreSQL 为事实来源、按 parsing/embedding/publishing 有界推进、具有请求幂等键、阶段进度、Step 租约、fencing token 和安全终态的持久文档导入状态机。

### 知识认证主体

- **规范名称**：知识认证主体（Knowledge Principal）
- **定义**：由服务端认证依赖产生、绑定 subject、tenant、允许 agent 集合和权限集合的可信管理 API 身份；客户端 tenant header/body 不能扩大其作用域。

## Decision Record: 安全文档解析与持久知识管理 API

**日期**: 2026-08-25
**问题**: 如何把不可信文本、HTML、PDF 和 DOCX 安全接入混合 RAG，并保证大文档有界处理、崩溃恢复、并发幂等、跨租户授权、稳定错误、分页和归档后停止召回。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
|---|---|---|---|
| HTTP 同步解析/嵌入/发布 | 接口最少 | 超时、断线和重复发布窗口不可控 | 低 |
| Redis 任务状态 | 队列接入快 | 缓存不能决定知识版本与审计事实 | 中 |
| PostgreSQL Init-Step-Poll + staging + 租约/fencing | 可恢复、可审计、可重试、作用域一致 | 增加任务表和轮询 | 中 |

### 决策

**选择**: 使用 allowlist `DocumentParser`、PostgreSQL `knowledge_ingestion_jobs/chunks`、分阶段 Init-Step-Poll、有限 Step 租约与 fencing；FastAPI 只在知识服务和可信 `KnowledgePrincipal` 同时配置时暴露路由。
**理由**: 文档解析、任务进度、不可变知识版本、租户授权和归档必须共享 PostgreSQL 事实来源，同时 HTTP 不能承载无界外部嵌入工作。
**Trade-offs**: 原始字节在 parsing 前短暂保存于 PostgreSQL；首版无 OCR、自动 worker、取消、租约续期和具体 Bearer/API Token 存储；反向代理仍需请求体上限。

### 影响范围

- `knowledge.parsing`, `knowledge.errors`, `storage.knowledge_management`, `storage.models`
- `api.knowledge`, `api.app`, Alembic `e95f2c7a6b31`
- 文档解析、真实 PostgreSQL 导入/租约/归档、权限和 API 集成测试

### 撤销条件

上传规模或合规要求证明 bytea 暂存不合适时迁移到加密对象存储并保留哈希/对象版本；接入 Outbox/队列时由 worker 触发 Step 但保留状态机；领域评测证明 OCR/版面结构必要时引入隔离解析 worker；多租户规模需要时增加 PostgreSQL RLS。

## Project Convention: 知识上传、任务和管理授权边界

**类型**: 文档安全、长任务并发、API 授权和知识生命周期约束
**适用范围**: 文档解析、知识导入任务、知识管理 API、文档分页/归档和 RAG 召回

### 规范内容

上传文档必须经过媒体/扩展、大小、字符、PDF 和 DOCX ZIP 安全门禁；解析器不得联网或执行活动内容。
原始正文/二进制不得进入错误、事件或模型，解析成功和终态失败必须清除任务原始字节。Init 幂等键绑定
完整请求哈希；Step 使用数据库行锁、有限租约和 fencing，旧 owner 不得提交或二次标记失败。最终发布
继续遵守不可变知识版本和单 active 版本。管理 tenant 只来自服务端认证主体，agent allowlist 与
`knowledge:read/write` 在后端校验；未配置认证或知识服务时路由必须不存在。归档只做 active -> archived，
不物理删除历史，RAG 只召回 active。

### 示例

**正确**: `trusted principal tenant → Init(request hash) → leased bounded Step → staging → idempotent publish → keyset list → active archive → no RAG recall`。

**错误**: 信任 `X-Tenant-Id`、同步 HTTP 跑完整嵌入、只凭扩展名解析、Redis 决定任务终态、旧 token 写回、归档物理删除 chunks、或未配置认证仍暴露管理端点。

### 发现来源

2026-08-25 v0.12 安全文档解析、PostgreSQL 持久知识导入、真实 API 与租约并发反例。

## Glossary Addendum: API 身份认证

### API Principal

- **规范名称**：API 认证主体（API Principal）
- **定义**：绑定单个 tenant、subject、状态、显式 permissions 以及 agent grants 或 all_agents 的 PostgreSQL 服务身份；认证成功后解析为可信 `AuthenticatedPrincipal`。

### API Token Prefix

- **规范名称**：API Token Prefix
- **定义**：Token 中用于数据库 O(1) 定位的 12 字符随机非秘密标识；不能单独认证，必须和 256 bit secret 的 pepper HMAC 摘要做常量时间比较。

## Decision Record: PostgreSQL 高熵 API Token 与最小权限授权

**日期**: 2026-08-25
**问题**: 如何为管理 API 提供真实、可撤销、跨租户失败关闭且数据库不保存明文凭据的服务身份认证。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
|---|---|---|---|
| 明文 API Key | 可恢复 | 数据库泄漏即失陷 | 低 |
| Argon2/bcrypt Token | 抗低熵口令猜测 | 高熵 Token 每请求成本无必要 | 中 |
| 随机 prefix + pepper HMAC-SHA256 | 快速定位、常量时间验证、数据库单独泄漏不可用 | pepper 轮换需重签 | 中 |

### 决策

**选择**: `api_principals + api_principal_agent_grants + api_tokens`；Token 为 12 字符 prefix 和 256 bit secret，数据库只保存带部署 pepper 的 32 字节 HMAC 摘要，FastAPI 使用 Bearer dependency 生成可信主体。
**理由**: 高熵 secret 不需要慢密码哈希；PostgreSQL 每请求检查可以保证撤销、过期、主体禁用和 tenant 停用立即生效；复合 tenant 外键直接阻止跨租户 grant。
**Trade-offs**: 当前无公开 Token 管理端点、独立认证事件表和多 pepper keyring；轮换 pepper 前必须先重签并切换客户端；每请求需要 PostgreSQL 查询。

### 影响范围

- `auth.base`, `auth.tokens`, `storage.auth`, `storage.models`
- `api.auth`, `api.knowledge`, `api.app`, Alembic `fa6c3d9e2b40`
- Token 单元、真实 PostgreSQL 生命周期、权限和 Bearer API 集成测试

### 撤销条件

接入 OIDC/mTLS/API Gateway 时替换认证后端但保留 Principal；需要无中断 pepper 轮换时增加 key version/KMS keyring；认证 QPS 成为瓶颈时引入不绕过撤销的短 TTL 缓存；合规要求时增加职责分离和追加认证审计。

## Project Convention: API Token 与认证失败边界

**类型**: 凭据安全、租户授权和管理 API 认证约束
**适用范围**: API Principal、Token 签发/验证/撤销、FastAPI 认证依赖和管理端点

### 规范内容

完整 Token 只能在签发时以 `SecretStr` 返回一次，禁止进入数据库明文列、日志、错误、测试快照和项目记忆。
认证必须先严格解析格式、按随机 prefix 查询、对摘要执行 `hmac.compare_digest`；未知、错误、过期、撤销、
主体禁用和 tenant 停用统一返回 401。tenant、agent grants/all_agents 和 permissions 只来自服务端事实来源，
客户端 header/body 不得扩大。撤销幂等并即时生效；认证数据库异常返回通用 503，禁止回退到未验证身份。

### 示例

**正确**: `one-time SecretStr → HMAC digest only → Bearer → PostgreSQL state checks → trusted principal → permission/agent enforcement`。

**错误**: 保存明文 Token、按错误原因区分未知/撤销、信任 X-Tenant-Id、用 Redis 缓存绕过撤销、数据库故障时匿名放行、或把 Token/digest 写入 trace。

### 发现来源

2026-08-25 v0.13 PostgreSQL API Token、并发主体创建、撤销/过期/禁用和真实 Bearer 知识 API。

## Glossary Addendum: 运行管理 API

### 安全运行投影

- **规范名称**：安全运行投影（Safe Run Projection）
- **定义**：从 PostgreSQL 运行/审批事实生成的外部 DTO，只包含状态、版本、步骤、最终输出和通用错误，不包含 checkpoint、provider state、resume token、工具参数或原始内部错误。

## Decision Record: active 领域包装配与运行审批管理 API

**日期**: 2026-08-25
**问题**: 如何对外提供运行创建、查询、取消和审批决定，同时避免双状态机、跨租户越权、定义漂移和内部恢复状态泄漏。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
|---|---|---|---|
| API 新建运行/审批状态机 | 端点直接 | 与内核事实冲突，可能重复工具执行 | 高 |
| 直接暴露 RunResult/checkpoint | DTO 少 | 泄漏供应商状态、参数和所有权 | 低 |
| 应用编排 + 现有 PostgreSQL 状态机 + 安全投影 | 单一事实、可审计、可 fencing | 需要 active 装配和只读投影 | 中 |

### 决策

**选择**: `ActiveAgentAssembler + AgentRunManagementService + PostgresRunPersistence + Safe Run Projection`。
**理由**: 领域包发布、运行、审批、租约和终态继续以 PostgreSQL 为唯一事实；API 只负责可信授权、编排和脱敏投影。
**Trade-offs**: POST run 当前仍在 HTTP 请求内执行到终态或等待审批；cancel 不能撤销已经完成的外部副作用；审批当前只支持按 ID 查询。

### 影响范围

- `application`, `factory`, `storage.runs`, `storage.domain_packages`
- `api.base`, `api.auth`, `api.runs`, `api.app`
- FastAPI/PostgreSQL Bearer、幂等、跨租户、审批和 cancel fencing 测试

### 撤销条件

引入 Outbox/Worker、对象存储 checkpoint 或 API Gateway 时可以移动执行触发和认证位置，但必须保留 PostgreSQL 状态机、精确 checkpoint、fencing、工具幂等和安全 DTO 契约。

## Project Convention: 运行与审批 API 安全边界

**类型**: API、权限、并发与敏感状态约束
**适用范围**: 运行创建/查询/取消、审批查询/决定、active agent 装配

### 规范内容

tenant 和 user_id 只能来自认证主体；agent 必须同时通过 grant 和 permission。运行只允许当前 active 领域包，manifest/hash/assets 或 agent key 漂移失败关闭。创建幂等键绑定 agent/version/task/完整上下文。取消必须在 run 行锁下清除 checkpoint 和 resume ownership，canceled 终态拒绝旧执行者 finish。HTTP 不得返回 checkpoint、provider state、resume token、工具参数、定义哈希或原始错误。

### 示例

**正确**: `Bearer Principal → active package verify → PersistentAgentService → PostgreSQL run/approval → safe DTO`。

**错误**: 信任 `X-Tenant-Id`、用 domain id 代替 agent key、API 自建状态机、取消后允许旧 token finish、或直接序列化 `RunResult.checkpoint`。

### 发现来源

2026-08-25 v0.14 运行与审批管理 API、真实 Bearer/跨租户/幂等/审批/cancel fencing 反例。

## Glossary Addendum: 成长管理 API

### 无副作用记忆管理查询

- **规范名称**：无副作用记忆管理查询（Side-effect-free Memory Management Query）
- **定义**：只读取正式记忆管理投影、不会增加召回次数或修改最近召回时间的 PostgreSQL 查询路径；与运行时真实召回严格分离。

## Decision Record: 可信评测与事务性成长管理 API

**日期**: 2026-08-25
**问题**: 如何对外提供正式记忆查询、成长候选评测、人工决定、发布和回滚，同时避免管理浏览污染召回价值、客户端伪造评测或并发决定造成重复发布。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
|---|---|---|---|
| 复用 MemoryStore/LearningService | 接口少 | 管理查询污染 recall，读后写并发不安全 | 低 |
| API 新建管理状态机 | DTO 独立 | 与候选/审批/记忆事实漂移 | 高 |
| 独立只读仓储 + 可信 evaluator + 既有发布器行锁事务 | 单一事实、可审计、并发幂等 | 增加安全投影和版本协议 | 中 |

### 决策

**选择**: `PostgresGrowthManagementRepository + AgentGrowthManagementService + PostgresKnowledgeAssetPublisher`。
**理由**: 管理列表使用无副作用 keyset；评测结果只由服务端 evaluator 产生；批准、审批记录、正式记忆和候选激活在同一事务完成。
**Trade-offs**: 评测在单事务中聚合两次状态转换；首版管理文本搜索为子串匹配；promote 权限同时覆盖批准、拒绝和回滚。

### 影响范围

- `growth.management`, `storage.growth_management`, `storage.repositories`
- `api.growth`, Alembic `1c7e9a4b6d20`
- 真实 Bearer、keyset、并发批准、拒绝、回滚和敏感字段反例

### 撤销条件

评测延迟需要异步 Worker；管理搜索规模超过 PostgreSQL 子串查询 SLA；或合规要求批准、拒绝、回滚进一步职责分离。

## Project Convention: 记忆与候选管理安全边界

**类型**: API、评测、并发与敏感投影约束
**适用范围**: `growth.management`, `api.growth`, `storage.growth_management`, candidate publisher

### 规范内容

管理记忆查询不得调用会更新召回统计的运行时 MemoryStore.search。客户端不得提交候选 passed/score/metrics；只允许服务端可信 evaluator 产生评测。人工批准必须在候选行锁事务中重新校验最新通过评测、作用域和 expected version，再原子写审批、正式记忆与 active 状态。相同请求可重放，决定、版本或备注变化必须冲突。响应只白名单投影待审资产和证据 ID，不返回反思 prompt、provider state、checkpoint 或未脱敏事件正文。

### 示例

**正确**: `Bearer scope → side-effect-free keyset → trusted evaluate → row lock approve+publish → safe DTO`。

**错误**: 管理员查看记忆时增加 recall；接受客户端 `passed=true`；先 approve 提交、后 publish；直接返回完整 proposed_change。

### 发现来源

2026-08-25 v0.15 记忆与成长候选管理 API、并发重复批准和敏感字段反例。

## Glossary Addendum: 认证管理与审计

### 可用安全管理员

- **规范名称**：可用安全管理员（Usable Security Administrator）
- **定义**：active、`all_agents`、持有至少一个关键认证管理权限，并具有至少一个未撤销且未过期 Token 的 API Principal；最后管理员保护按每项关键职责分别计算。

### 安全认证审计元数据

- **规范名称**：安全认证审计元数据（Safe Authentication Audit Metadata）
- **定义**：仅由服务端常量、布尔和计数构造的审计附加字段，不接收 Token、digest、Authorization header 或自由错误正文。

## Decision Record: Principal/Token 生命周期与追加认证审计

**日期**: 2026-08-25
**问题**: 如何让生产运维通过 API 安全管理主体和 Token，同时阻止自提权、agent scope 扩大、并发删除最后恢复入口和凭据泄漏。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
|---|---|---|---|
| 单一 auth:admin 权限 | 简单 | 无职责分离，泄漏影响面大 | 低 |
| 管理脚本直连认证表 | 灵活 | 绕过业务不变量和统一审计 | 低 |
| 独立权限 + 事务内 actor 复核 + tenant 锁 + 追加审计 | 最小权限、并发安全、可追溯 | 每次管理与认证增加数据库读写 | 中 |

### 决策

**选择**: `auth.principals/read-write + auth.tokens/read-issue-revoke + auth.audit/read`，由 `PostgresAPIKeyService` 在事务内复核 actor，并使用 `authentication_audit_events` 追加审计。
**理由**: PostgreSQL 继续作为 Principal、Token、grant、tenant 状态与审计的唯一事实来源；管理 API 不能信任客户端 tenant 或旧权限快照。
**Trade-offs**: 成功认证新增审计写；unknown/missing credentials 产生 tenant 为空的全局事件；新增业务权限需要更新服务端 allowlist；审计触发器首版只禁止 UPDATE。

### 影响范围

- `auth.management`, `storage.auth`, `storage.models`
- `api.auth`, `api.auth_management`, `api.app`
- Alembic `7e2d4f8a9c10`、真实 Bearer/并发/泄漏/不可更新反例

### 撤销条件

接入 OIDC/mTLS/API Gateway 时可替换 Token 验证器但保留 Principal 和管理不变量；认证审计吞吐成为瓶颈时可改为同事务 Outbox 后归档；合规要求物理 WORM 时同步到专用不可变存储。

## Project Convention: 认证管理安全边界

**类型**: 凭据、权限委派、并发恢复与审计约束
**适用范围**: Principal 创建/列表/状态、Token 签发/列表/撤销、认证依赖和审计查询

### 规范内容

管理动作必须按 actor principal/token ID 事务内复核 tenant、active 状态、过期/撤销、权限和 grants。新主体权限必须同时是 actor 权限和服务端 allowlist 的子集，agent scope 只能收窄。主体写、Token 签发和撤销使用独立权限。禁用全租户安全管理员或撤销其最后 Token 前取得 tenant advisory lock，并为每项关键职责保留另一个可用安全管理员。完整 Token 只在签发响应出现一次；列表、审计、错误和日志禁止 Token、digest、Authorization header。审计只追加，UPDATE 由数据库拒绝。

### 示例

**正确**: `Bearer → current actor recheck → delegation subset → tenant lock → state mutation + safe audit → one-time secret`。

**错误**: 信任旧 Principal 快照、授予 actor 不具备的权限、让 scoped admin 授予 all_agents、并发撤销最后管理员、或把 Authorization header 写入审计。

### 发现来源

2026-08-25 v0.16 Principal/Token 管理 API、最后管理员并发保护和审计泄漏/不可更新反例。

## Glossary Addendum: 反思 Outbox 任务

### 反思 Outbox 任务

- **规范名称**：反思 Outbox 任务（Reflection Outbox Job）
- **定义**：由运行终态事务幂等创建、只携带安全 schema 版本，并由租约 Worker 按 run ID 重载完整已提交事实后执行知识沉淀的 PostgreSQL 后台任务。

### 处理器版本

- **规范名称**：处理器版本（Handler Version）
- **定义**：与 run 和 job type 共同构成 Outbox 幂等身份的实现版本；新版本允许显式重放，既有版本不得原地改变任务身份。

## Decision Record: PostgreSQL 反思 Outbox 与租约 Worker

**日期**: 2026-08-25
**问题**: 如何把完整轨迹反思和知识沉淀从在线运行路径解耦，同时避免终态双写缝隙、重复成长资产、崩溃丢任务、旧 Worker 覆盖和敏感轨迹复制。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
|---|---|---|---|
| 在线请求同步反思 | 简单、立即返回候选 | 放大尾延迟，崩溃后无独立恢复 | 低 |
| Redis/Celery 直接队列 | 生态成熟 | 与 PostgreSQL 运行终态双写，Redis 不能作为事实来源 | 中 |
| PostgreSQL Outbox + 租约 Worker | 终态同事务、可重试、可 fencing、复用现有事实 | 增加任务表、轮询和运维面 | 中 |
| Kafka 独立事件平台 | 吞吐和跨服务能力强 | 当前规模证据不足，运维与一致性复杂度高 | 高 |

### 决策

**选择**: PostgreSQL Outbox + `FOR UPDATE SKIP LOCKED` 租约 Worker；payload 只含 schema version，Worker 按 run ID 重载轨迹。
**理由**: PostgreSQL 已是运行、事件、候选和发布的唯一事实来源，可在同一事务消除终态/入队缝隙，并用行锁、有限租约和随机 token 提供崩溃接管与 fencing。
**Trade-offs**: PostgreSQL 承担任务领取与历史保留压力；首版只有有界 `process_step`，常驻入口、积压/死信管理和安全人工重试进入 v0.18。

### 影响范围

- `storage.models`, `storage.runs`, `storage.outbox`
- `workers.reflection`, Alembic `4b8f2c6d1a30`
- PostgreSQL 并发/迁移/敏感错误反例、README、技术设计和 ADR 0017

### 撤销条件

PostgreSQL 锁等待、表膨胀、跨区域或吞吐证据证明不满足需求时，可迁移到外部消息平台，但必须保留同事务 Outbox、幂等 consumer、PostgreSQL 终态和 fencing 事实。

## Project Convention: Outbox 安全与租约边界

**类型**: 异步任务、并发、租户隔离与敏感状态约束
**适用范围**: 运行终态入队、ReflectionWorker 领取/续租/完成/失败、处理器版本重放

### 规范内容

运行终态与 Outbox enqueue 必须同事务；终态重放必须补偿 ensure enqueue。任务 payload 只保存版本化安全 schema，不复制运行正文、轨迹、checkpoint、provider state 或凭据。领取使用有限租约和随机 fencing token；complete/fail 必须复核 token 与未过期租约。run/tenant 配对必须由复合外键保护。失败只保存机器错误码，重放继续复用候选指纹、冲突/合并、评测、审批和发布不变量。

### 示例

**正确**: `terminal run transaction → safe outbox row → SKIP LOCKED claim → heartbeat → reload committed trace → idempotent sedimentation → fenced complete`。

**错误**: 终态提交后再跨事务发 Redis、把 task/output/checkpoint 放进 payload、只用 worker name 当 owner、租约过期后仍允许 complete、或用客户端 tenant/run 组合创建任务。

### 发现来源

2026-08-25 v0.17 PostgreSQL Outbox、ReflectionWorker、迁移重放和真实并发/跨租户/泄漏反例。

## Decision Record: Worker 实例 heartbeat 与优雅停止

**日期**: 2026-08-25
**问题**: 如何把有界 `process_step` 提升为可长期运行的 Worker，同时识别同名旧进程、观测积压并在停止时避免继续领取任务。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
|---|---|---|---|
| 仅 shell 无限循环 | 简单 | 无进程身份、无停止 drain、无心跳事实 | 低 |
| Redis worker presence | 更新快 | TTL/淘汰不能作为事实，故障时漂移 | 中 |
| PostgreSQL instance token + heartbeat + runner | 与任务事实一致，可 fencing、可审计 | 增加心跳写和表 | 中 |

### 决策

**选择**: `ReflectionWorkerRunner` 每次领取一个任务；停止事件阻止新领取并允许有限 drain。`reflection_worker_heartbeats` 以 worker ID 为槽位、随机 instance token 为进程身份；同名重注册 fencing 旧心跳。
**理由**: 任务本身已经使用 PostgreSQL 租约和 fencing，Worker 生命周期继续使用同一事实来源可避免 Redis presence 与任务 owner 漂移。
**Trade-offs**: 重复 worker ID 会主动替换旧实例，部署必须给并发进程分配唯一 ID；首版 heartbeat 是内部运维事实，租户安全管理 API 后续接入。

### 影响范围

- `workers.runner`, `workers.__init__`
- `storage.outbox`, `storage.models`, Alembic `9c3e5a7b1d40`
- runner/heartbeat/backlog PostgreSQL 与单元反例

### 撤销条件

若部署平台已经提供带 fencing 身份的权威 Worker 控制面，可替换 heartbeat 存储，但任务领取、租约和终态仍由 PostgreSQL 决定；外部 presence 不得绕过数据库 owner 检查。

## Glossary Addendum: 反思任务安全运维

### 单轮尝试计数

- **规范名称**：单轮尝试计数（Attempts In Cycle）
- **定义**：当前自动重试轮次已经领取的次数；人工 retry 可归零以开启新轮次，但历史总 attempts 永不归零。

### 运维幂等请求事实

- **规范名称**：运维幂等请求事实（Operations Idempotency Record）
- **定义**：将 tenant 内幂等键哈希绑定到 actor principal、目标 job、expected version 和安全结果的不可变 PostgreSQL 记录，用于跨进程回放同一管理请求。

## Decision Record: 反思任务安全运维与人工重试

**日期**: 2026-08-25
**问题**: 如何让生产运维安全查询和恢复 dead-letter 反思任务，同时阻止跨租户/agent 越权、旧授权快照、并发重复重置、历史 attempts 丢失、旧 lease 复活和敏感正文泄漏。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
|---|---|---|---|
| 人工 SQL 更新 Outbox | 实现快 | 绕过权限、状态机、幂等、审计和 lease 清理 | 低 |
| 复用 runs 权限与认证快照 | 权限较少 | 职责混合，撤销/禁用/grant 收窄可能不即时 | 低 |
| 当前事实复核 + version + 行锁 + 不可变幂等事实 | 最小权限、并发安全、可回放、可审计 | 增加查询、锁和两张运维事实表 | 中 |

### 决策

**选择**: 独立 `operations.jobs:read/retry`；所有运维方法从 PostgreSQL 重载当前 actor 事实；查询使用安全 DTO 和筛选绑定严格 keyset；retry 使用 expected version、幂等 advisory lock、job 行锁、不可变 request 和 append-only audit。
**理由**: PostgreSQL 已是任务、认证和授权事实来源，可在单事务中同时证明 actor 当前有效、目标作用域正确、状态和版本未漂移，并让相同请求跨进程安全回放。
**Trade-offs**: 同一 actor 管理写会因 Principal/Token 行锁串行；每次查询增加认证/grant 读取；审计首版仍在 PostgreSQL，尚无 WORM 导出和归档策略。

### 影响范围

- `operations.reflection_jobs`, `storage.operations`, `api.operations`
- `storage.models/outbox`, `auth.management`, Alembic `a4d6f8b2c510`
- 运维 API、并发/越权/泄漏反例、ADR 0018 和专项 ground truth

### 撤销条件

真实管理并发证明 actor 行锁不可接受时，可改为授权版本或更细粒度锁，但撤销、禁用、permission/grant 收窄不得与 retry 提交交叉绕过；合规要求物理不可变时同步到 WORM，但事务内最小审计证据继续保留。

## Project Convention: 反思任务运维安全边界

**类型**: 运维 API、权限、并发幂等和敏感信息约束
**适用范围**: reflection job stats/list/detail/retry、Outbox Worker 状态变更、运维审计

### 规范内容

运维查询和写入不得信任 Bearer 认证时的 permission/grant 快照，必须从 PostgreSQL 复核 active tenant/Principal、Token 当前状态、权限和 agent scope。管理投影只允许 ID、状态、version、计数、安全时间和机器码。人工 retry 只允许 dead-letter 到 pending，保留历史 attempts、重置 attempts_in_cycle 和所有旧 owner 字段，并递增 version。自动耗尽判定和退避都必须使用 attempts_in_cycle；历史 attempts 只用于生命周期可观测性。原始幂等键、payload、运行正文、异常正文和凭据不得进入运维事实或审计。

### 示例

**正确**: `Bearer actor → current DB facts → idempotency lock → job row lock → expected version/state → dead_letter→pending + immutable request/audit → old lease fenced`。

**错误**: 只看旧 Principal 快照、返回 task/output/payload、把 attempts 清零、用 Redis 记录幂等、不同键重复重置 pending 任务、或把原始 Idempotency-Key/Authorization header 写入审计。

### 发现来源

2026-08-25 v0.18 Wave 5-7 安全运维 API、并发重试、actor 撤销/禁用和敏感字段反例。

## Glossary Addendum: 生产反思 Worker 入口

### 生产反思 Worker 入口

- **规范名称**：生产反思 Worker 入口（Production Reflection Worker Entry Point）
- **定义**：通过 `public-agent reflection-worker` 装配真实 PostgreSQL、生成模型反思管线和租约 Runner，负责配置、信号、资源生命周期与安全退出码，但不复制任务状态机的进程入口。

## Decision Record: reflection-worker 生产 CLI 与应用生命周期

**日期**: 2026-08-25
**问题**: 如何让进程管理器安全启动完整反思沉淀链路，同时避免部署脚本装配漂移、密钥进入参数、信号绕过 drain、资源泄漏和异常正文进入日志。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
|---|---|---|---|
| 每个部署脚本自行装配 | 灵活 | 构造与生命周期容易漂移，难统一测试 | 低 |
| application 生命周期 + 薄 CLI | 装配唯一、可替身测试、边界清晰 | 增加一个显式进程层 | 中 |
| Celery/Redis Worker 命令 | 生态成熟 | 引入第二任务事实来源并扩大运维面 | 高 |

### 决策

**选择**: 使用 `ReflectionWorkerApplication` 统一拥有 Database、OpenAI Provider、ReflectionEngine、PostgreSQL 学习/发布仓储、Outbox Store、Worker 和 Runner；CLI 只负责 Settings、信号、安全事件和退出码。
**理由**: 既有 PostgreSQL 租约、fencing、heartbeat 和沉淀幂等状态机已经完整，生产入口应复用而不是复制；应用协议允许测试替身覆盖启动/停止而不调用收费 API。
**Trade-offs**: CLI 仍是单进程轮询，不负责扩缩容；drain timeout 后需等待数据库 lease 接管；详细内部诊断必须进入额外的安全可观测系统而不能直接输出异常正文。

### 影响范围

- `config`, `cli`, `workers.application`, `workers.__init__`
- Worker CLI/Runner 测试、ground truth、README、技术设计和 ADR 0019

### 撤销条件

部署平台提供经过验证的统一 Worker 生命周期和 secret 控制面时可以替换 CLI 外壳，但必须继续调用同一 application/Runner 协议，并保留 PostgreSQL 事实、有限 drain、安全事件和密钥不入参数。

## Project Convention: Worker 生产入口安全边界

**类型**: 进程生命周期、配置、安全日志与依赖方向
**适用范围**: `cli`, `workers.application`, Worker 部署和离线验证

### 规范内容

API Key 只能来自 Settings/secret manager，CLI 不提供密钥参数。SIGINT/SIGTERM/SIGBREAK 只设置停止事件；任务状态由 Runner 和 PostgreSQL 决定。stdout/stderr 仅允许白名单 JSON 字段和机器错误码。核心 `workers.__init__` 不得 eager 导入依赖 storage 的生产 application，避免 `storage.outbox -> workers.reflection -> workers.__init__ -> workers.application -> storage` 循环。

### 示例

**正确**: `public-agent reflection-worker → Settings → workers.application → Runner → PostgreSQL facts`，application 通过显式子模块导入，Provider/Database 都尝试关闭。

**错误**: `--api-key secret`、信号处理器直接 fail job、打印异常正文，或从 `workers.__init__` eager 导出反向依赖 storage 的 application。

### 发现来源

2026-08-25 v0.18 Wave 8 生产 CLI、全量收集阶段循环导入反例和真实 Windows SIGBREAK 子进程验证。

## Known Issue: Worker application eager 导出导致循环导入

**发现日期**: 2026-08-25
**问题类型**: 兼容性陷阱
**严重度**: 严重

### 现象

单独 CLI 测试通过，但全量 Pytest 收集 `storage.outbox` 时出现 partially initialized module，多个 PostgreSQL 测试无法导入。

### 根因

`storage.outbox` 导入 `workers.reflection` 会先执行 `workers.__init__`；若 `__init__` eager 导入 `workers.application`，application 又导入 storage，形成包初始化环。

### 规避方案

生产 application 使用显式 `public_agent.workers.application` 导入；`workers.__init__` 只导出不反向依赖 storage 的核心 reflection/runner 类型；全量收集测试作为发布门禁。

### 相关文件

- `src/public_agent/workers/__init__.py`
- `src/public_agent/workers/application.py`
- `src/public_agent/storage/outbox.py`

### 状态

已修复并由 195 个全量测试验证。

## Glossary Addendum: 生产容量治理

### Reflection Worker 容量报告

- **规范名称**：Reflection Worker 容量报告（Reflection Worker Capacity Report）
- **定义**：按 job type + handler version 从 PostgreSQL Outbox 与 Worker heartbeat 当前事实生成的安全聚合报告，包含三级状态、原因码、推荐 Worker 数和 scale delta，但不自动修改部署副本或任务状态。

## Decision Record: Docker Compose 生产编排与 PostgreSQL 容量建议

**日期**: 2026-08-25
**问题**: 如何在不引入第二任务事实源的前提下交付可运行、可回滚、可观测的生产部署和 Worker 容量治理。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
|---|---|---|---|
| Kubernetes/HPA | 原生滚动发布、探针和调度 | 当前无集群与指标适配器证据，CPU不能表达handler/dead-letter/任务年龄 | 高 |
| Celery/Kafka/SQS | 消费生态成熟、独立扩展 | 与现有Outbox/租约/fencing形成双事实和一致性成本 | 高 |
| Docker Compose + PostgreSQL容量建议 | 复用现有状态机、低门槛、可完整回滚 | 单主机且首版需要人工调整副本 | 中 |

### 决策

**选择**: Docker Compose + PostgreSQL容量建议。
**理由**: 当前PostgreSQL领取与聚合尚未被容量证据证明不足；先闭合镜像、迁移、Secret、资源、日志、容量CLI和运行手册，可保留未来外部控制器接口而不扩大一致性故障面。
**Trade-offs**: 首版不提供跨主机调度和自动扩缩容；推荐副本数只基于积压工作量与静态阈值，需要真实负载继续校准。

### 影响范围

- `operations.capacity`, `storage.outbox/models`, `workers.application`, `cli`, `config`
- `Dockerfile`, `requirements.lock`, `docker-compose.production.yml`, Alembic `b7e2c4a9d610`
- 容量/CLI/PostgreSQL/部署测试、README、技术设计、ADR 0020、运行手册

### 撤销条件

PostgreSQL claim/聚合P95或锁等待无法满足目标，单主机无法满足可用区要求，或已有经过回归验证的指标适配器和外部控制器时，重新评估Kubernetes/消息平台；仍保持容量报告和任务事实契约。

## Project Convention: 容量事实与数据库套件隔离

**类型**: 架构约束 / 测试规范
**适用范围**: Reflection Outbox、Worker、capacity-check、PostgreSQL集成测试

### 规范内容

容量与扩缩容判断只读取PostgreSQL安全聚合，不直接写部署状态。使用共享数据库且采用默认handler version的集成套件必须串行执行；需要并行时，每套件必须生成唯一handler version，避免跨套件领取任务。

### 示例

**正确**: `reflection-capacity-<unique>` 用于并行测试，或串行运行 `test_reflection_worker.py` 与 operations套件。

**错误**: 两个并行数据库套件都用 `reflection-v1` 并断言全局pending恰好为1。

### 发现来源

2026-08-25 v0.19发布门禁并行验证；串行重跑Worker专项32/32通过，确认属于测试共享事实干扰而非实现缺陷。

## Glossary Addendum: 容量生命周期治理

### Reflection Worker 容量校准

- **规范名称**：Reflection Worker 容量校准（Reflection Worker Capacity Calibration）
- **定义**：从指定 handler version 的真实终态 Outbox 处理耗时计算 P50/P95/P99、观察吞吐和有界阈值建议，并保存校准历史；样本不足失败关闭，结果不自动修改配置或副本。

### Outbox 归档快照

- **规范名称**：Outbox 归档快照（Outbox Archive Snapshot）
- **定义**：以 `job_id + completed_at + version` 唯一标识、保存于 PostgreSQL 原生范围分区且不回指运行表外键的终态任务历史副本。

## Decision Record: 真实负载校准、容量趋势与受保护 Outbox 分区归档

**日期**: 2026-08-25
**问题**: 如何在不引入第二任务事实源、不自动修改生产容量且不破坏人工重试审计的前提下，用真实负载治理 Worker 阈值和长期 Outbox 数据。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
|---|---|---|---|
| 静态阈值 + CPU 自动扩缩容 | 实现简单 | 无法表达处理器版本、积压年龄、真实耗时和供应商等待 | 低 |
| 新时序库/消息平台 | 分析生态成熟 | 形成第二事实源、同步与恢复成本 | 高 |
| PostgreSQL 真实历史 + 有界趋势 + 分区归档 | 复用现有事务、锁、迁移和审计边界 | 长期历史仍占用 PostgreSQL，阈值应用需人工流程 | 中 |

### 决策

**选择**: PostgreSQL 真实历史 + 有界趋势 + 分区归档。
**理由**: Worker 状态、终态、处理耗时和 retry history 已在 PostgreSQL；同一事务边界可以证明归档身份和清理前置条件，并用样本门槛阻止伪校准。
**Trade-offs**: 校准只产生建议；首版范围分区较粗，未自动导出对象存储、预测或扩缩容。

### 影响范围

- `operations.capacity_history/outbox_retention/application`
- `storage.capacity_history/outbox_retention/outbox/models`
- CLI、Compose、Alembic `c9f4e2a7b613`、容量治理测试与生产运维文档

### 撤销条件

容量历史聚合或归档表备份/恢复超过生产 SLO，单机 PostgreSQL 无法满足容量，或已有经过回归验证的外部时序/对象存储/控制器时重新评估；任务、租约、重试与终态仍保持唯一权威事实契约。

## Project Convention: 容量校准与 Outbox 保留失败关闭

**类型**: 架构约束 / 数据安全 / 运维规范
**适用范围**: capacity-check/monitor/trend/calibrate、Outbox Worker、outbox-maintain、相关迁移与生产运行手册

### 规范内容

校准必须使用真实已完成任务、显式最小样本和有界窗口，建议只持久化不自动应用。Outbox 维护默认 dry-run；归档必须显式 execute，物理清理必须显式 execute+prune，并同时满足精确 `id+completed_at+version` 归档已存在、终态、handler version 匹配且无 retry request 引用。迁移 downgrade 前必须停止治理进程并备份新增历史。

### 示例

**正确**: `public-agent outbox-maintain --execute --prune --archive-after-days 7 --purge-after-days 90`，并先保存 dry-run 与备份证据。

**错误**: 根据少量样本自动改 Worker 副本，或在只确认 `job_id` 存在归档时直接删除源 Outbox 行。

### 发现来源

2026-08-25 v0.20 真实负载校准、Outbox 分区归档与容量趋势治理纵向链路。

## Known Issue: 时间敏感数据库测试使用固定墙钟会跨过 available_at

**发现日期**: 2026-08-25
**问题类型**: Bug模式
**严重度**: 一般

### 现象

耗时测试在当天时间晚于固定 UTC 领取时间后，动态创建任务的 `available_at` 晚于模拟 `utc_now`，导致 `claim()` 正确返回空并产生时段相关失败。

### 根因

测试把墙钟固定为某个当天时刻，却没有保证该时刻晚于动态 fixture 的创建时间。

### 规避方案

时间敏感测试以 fixture 创建后的当前 UTC 加安全余量作为领取时间，再显式构造完成时间差；不要使用会在同一天自然过期的固定时刻。

### 相关文件

- `tests/test_postgres_outbox_worker.py`

### 状态

已修复

## Glossary Addendum: 容量阈值变更治理

### 容量策略

- **规范名称**：容量策略（Capacity Policy）
- **定义**：按 job type + handler version 隔离、包含完整容量阈值和单调版本号的 PostgreSQL 记录；同一作用域最多一个 active 策略。

### 容量变更请求

- **规范名称**：容量变更请求（Capacity Change Request）
- **定义**：把一次校准建议绑定到基线策略、窗口证据、人工审批、发布、冷却、效果复核和回滚事实的版本化状态机记录。

### 效果复核

- **规范名称**：容量策略效果复核（Capacity Policy Effect Review）
- **定义**：冷却期结束后，用发布后的持久化容量观测对比发布前窗口证据，结合 warning/critical 比例和原始 ready、age、dead-letter 护栏判定策略 effective 或 ineffective。

## Decision Record: 版本化容量阈值变更治理

**日期**: 2026-08-25
**问题**: 如何把真实负载校准建议安全推进为运行时阈值，同时证明持续窗口、人工审批、并发顺序、效果和精确回滚。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
|---|---|---|---|
| 直接修改 `.env`/Compose | 实现快 | 无事务、无审批事实、多实例漂移且无法精确回滚 | 低 |
| 外部配置中心/控制器 | 发布与审批生态成熟 | 引入第二事实源和新的恢复、权限与运维边界 | 高 |
| PostgreSQL 版本化策略与变更请求 | 复用现有事务、锁、迁移、审计和容量证据 | 首版需要显式 CLI 运维动作 | 中 |

### 决策

**选择**: PostgreSQL 版本化策略与变更请求。
**理由**: 容量观测、校准和 Worker 状态已经以 PostgreSQL 为唯一事实源；在同一事务边界内可使用行锁、handler advisory lock、expected version 和 partial unique active index 证明发布顺序，并保留 exact previous policy。
**Trade-offs**: 首版审批身份是运维审计标签，依赖数据库/主机权限；不提供独立 Web 审批 UI，也不自动调整 Worker 副本。

### 影响范围

- `operations.capacity_governance/application`, `storage.capacity_governance/models`
- `workers.application`, `cli`, `config`, Alembic `f2a7d9c4e681`
- 容量治理领域/CLI/PostgreSQL/运行时测试、Compose、README、技术设计、运行手册和 ADR 0022

### 撤销条件

多主机配置传播、组织级审批或外部控制器经实测无法由 PostgreSQL/CLI 满足，且替代系统能维持单一权威发布顺序、审计和精确回滚时重新评估；容量观测、校准和 Worker 任务事实仍保留在 PostgreSQL。

## Project Convention: 容量策略发布与回滚边界

**类型**: 架构约束 / 并发安全 / 运维规范
**适用范围**: 容量策略、变更请求、运行时阈值解析、CLI、迁移和 PostgreSQL 集成测试

### 规范内容

所有容量策略写操作必须按 handler version 隔离，校验 expected version，并在发布、复核和回滚时持有 handler 级 advisory lock。切换 partial unique active 身份时，必须先让旧 active 策略退出并 flush，再让目标策略进入 active。窗口或复核样本/跨度不足不得推进状态；回滚只能恢复当前发布策略记录的 exact previous policy。发布不得修改 Worker 副本、Compose、`.env` 或外部控制器。

### 示例

**正确**: `superseded old -> flush -> insert active new`；回滚使用 `published_policy.previous_policy_id` 与请求基线交叉校验后恢复。

**错误**: 在同一次 flush 中同时保留旧 active 并插入新 active，或根据最新策略版本猜测回滚目标，或审批后自动改 Compose 副本数。

### 发现来源

2026-08-25 v0.21 版本化容量阈值变更治理、PostgreSQL 并发反例和生产发布门禁。

## Glossary Addendum: RBAC 容量治理与策略漂移

### 容量治理 Principal

- **规范名称**：容量治理 Principal（Capacity Governance Principal）
- **定义**：属于配置治理 tenant、当前 active、由未撤销未过期 Token 认证、具备所需细粒度权限、`all_agents=true` 且无 agent grant 的 PostgreSQL API Principal。

### 策略漂移告警

- **规范名称**：策略漂移告警（Policy Drift Alert）
- **定义**：最近有界容量观测的阈值指纹与当前 active policy（或 Settings fallback）指纹不一致，并达到最小样本后按 expected/observed 指纹去重的治理记录。

### 治理告警确认

- **规范名称**：治理告警确认（Governance Alert Acknowledgement）
- **定义**：值守人员已接手 open 告警的审计状态；确认不代表漂移恢复，只有更新观测证明回到当前 expected 后才能 resolved。

## Decision Record: RBAC 审批控制台、策略漂移检测与治理告警

**日期**: 2026-08-25
**问题**: 如何把容量策略 CLI 审计标签升级为可即时撤权的真实身份，并把持久化阈值观测形成可去重、确认、恢复和审计的治理闭环。

### 选项分析

| 选项 | 优势 | 劣势 | 复杂度 |
|---|---|---|---|
| 独立 IAM、SPA 和外部告警平台 | 组织功能完整 | 多事实源、部署和恢复边界显著扩大 | 高 |
| CLI 角色字符串与日志告警 | 改动小 | 身份不可验证、撤权不即时、生命周期不可靠 | 低 |
| PostgreSQL API Token RBAC + 原生控制台 + PostgreSQL 告警 | 事务、审计、迁移和回滚统一 | 首版无 SSO、外部通知和组织工作流 | 中 |

### 决策

**选择**: PostgreSQL API Token RBAC + 原生控制台 + PostgreSQL 漂移告警。
**理由**: Principal、Token、容量策略、观测和发布状态已经由 PostgreSQL 权威保存，可在状态变更事务内重验当前身份与权限，并用同一锁和版本边界维护告警生命周期。
**Trade-offs**: 安全管理员仍需通过认证管理 API 分发最小权限 Token；首版没有 OIDC/SSO、外部通知、自动扩缩容或复杂审批编排。

### 影响范围

- `auth/operations.capacity_control/storage.authorization/capacity_control`
- `api.capacity_governance/capacity_console/app`, `cli`, `config`, Compose Secret
- Alembic `2d6f8b1c4a90`、生产发布门禁、README、技术设计、运行手册和 ADR 0023

### 撤销条件

组织审批、跨主机策略传播或通知 SLA 经生产证据证明当前控制面无法满足，且替代系统能保留即时撤权、单一发布顺序、append-only 审计、expected-version 并发保护和 exact rollback 时重新评估。

## Project Convention: 容量治理授权与漂移告警边界

**类型**: 安全规范 / 状态机约束 / 前端安全
**适用范围**: 容量治理 API、控制台、漂移扫描、告警/审计模型、迁移和生产部署

### 规范内容

控制台永远不是授权依据。所有治理写动作必须在数据库状态事务内重验治理 tenant、active Principal、Token 当前状态、细粒度权限和 global scope；operator 只取 Principal subject。告警确认不等于恢复，无新观测不得关闭。判断当前漂移时必须同时绑定 expected 和 observed 指纹；active policy 切换后，有更新观测时关闭旧 expected 告警并为当前 expected 独立去重。Token 只允许当前标签页 sessionStorage，禁止 URL、Cookie、localStorage、DOM 文本、日志和 API 响应泄漏。

### 示例

**正确**: `Bearer -> transaction revalidation -> expected-version state change + same-transaction success audit`；`new observation + old expected -> resolve old alert -> open/dedupe current expected alert`。

**错误**: 依据控制台按钮可见性授权、信任认证时旧权限快照、确认后直接关闭告警、仅按 observed 指纹保留旧策略告警、把 Token 放入 URL/localStorage。

### 发现来源

2026-08-25 v0.22 RBAC 审批控制台、策略漂移告警闭环、安全代码审查和生产发布门禁。

## Decision Record: 治理事件处置审批与恢复验证

**日期**: 2026-08-25
**问题**: 内部治理事件被确认后，如何形成可审计的处置闭环，同时避免任意命令执行、同人自批和人工伪造恢复。

### 决策

采用 PostgreSQL 版本化处置单。事件信号固定映射 Playbook，每个 `incident_id + reopened_count` 周期最多一条；
request/approve/execute/verify 权限独立，请求人不能审批，执行人不能验证。执行只记录枚举结果与 Playbook 对应的
安全证据码；verified 必须读取执行后更高版本、恢复时间更晚的 resolved 事件事实。

### 规范

**正确**: `acknowledged incident -> fixed playbook -> independent approve -> bounded execution evidence -> newer resolved fact -> independent verify`。

**错误**: 接受任意 shell/SQL/日志正文作为处置内容，允许请求人自批或执行人自验，或仅凭按钮/人工声明把处置标记 verified。

### 影响范围

- `operations/storage/api capacity_control`、控制台、Alembic `9f4e7c2d1a60`
- 独立 remediation RBAC、严格 cursor、append-only 审计、ADR 0026、生产门禁
- PostgreSQL 继续是唯一事实源；系统不自动执行生产变更

### 撤销条件

外部编排器只有在能够保留 PostgreSQL 权威状态、即时撤权、职责分离、expected version 和新恢复事实验证时才可接入；
外部工单、通知或执行日志不得替代事件/处置事实。

## Decision Record: 治理知识质量快照、隔离与受控恢复

**日期**: 2026-08-25
**问题**: 已发布治理知识被报告为不安全或低质量后，如何立即停止检索、保留完整证据，并在误报得到独立确认后安全恢复。

### 决策

采用 PostgreSQL 不可变质量快照与版本化恢复请求。确认安全反馈时，同知识版本其余待复核反馈原子进入 `superseded`，复盘原子隔离且统一检索立即排除；快照绑定精确 postmortem/knowledge/version、反馈终态和独立证据指纹，禁止 UPDATE。恢复只接受结构化 `false_positive`，至少隔离 24 小时，请求人、审批人、原安全报告人和原确认人四方分离；批准恢复、生成新知识版本、更新发布状态和成功审计同事务提交。

### 规范

**正确**: `confirmed safety feedback -> atomic quarantine -> immutable quality snapshot -> retention -> independent false-positive approval -> new published knowledge version`。

**错误**: 物理删除反馈/知识、修改历史快照、原报告人或确认人批准恢复、在旧 knowledge version 上直接改回 published，或把 RAG 命中当作恢复证据。

### 影响范围

- `operations/storage/api capacity_control`、治理知识检索、控制台、Alembic `c7a4d2e9f610` 与 `d8f1c2a4b730`
- ADR 0028/0029、生产 ground truth、运行手册和 PostgreSQL 回滚/反例测试
- v0.28 可只读消费这些不可变事实生成质量趋势和复发风险，不建立第二质量事实源

### 撤销条件

只有替代系统能够保留精确版本谱系、不可变证据、即时检索隔离、四方职责分离、事务原子性和 PostgreSQL 可审计恢复顺序时才重新评估；外部评测或 RAG 结果不得替代这些事实。

## Glossary Addendum: 治理知识质量与恢复

### 质量快照

- **规范名称**：治理知识质量快照（Governance Knowledge Quality Snapshot）
- **定义**：绑定精确复盘、知识版本、反馈终态与证据指纹的 PostgreSQL 不可变评测事实，assessment 为 insufficient/healthy/degraded/unsafe。

### 恢复后再次隔离

- **规范名称**：恢复后再次隔离风险（Post-Restoration Requarantine Risk）
- **定义**：`restore_count >= 1` 且当前复盘再次处于 quarantined 的复发质量风险；恢复申请或确认动作本身不构成质量恢复证据。

## Decision Record: PostgreSQL 治理知识质量趋势与复发风险

**日期**: 2026-08-25
**问题**: 如何从不可变质量快照和隔离/恢复历史形成可复用趋势与复发风险，同时避免第二事实源和不完整扫描误报健康。

### 选项分析

| 选项 | 优势 | 劣势 | 适用场景 |
|---|---|---|---|
| 控制台临时聚合 | 无 Schema 改动 | 无统一边界/cursor、无法成为 monitor 证据、易无界扫描 | 仅原型 |
| 外部时序/告警系统 | 长期趋势和通知生态完整 | 第二事实源、凭据、同步、租户隔离和恢复成本 | PostgreSQL 经实测不足后 |
| PostgreSQL 有界趋势 + 复用 incident 状态机 | 事务、RBAC、审计、迁移和恢复统一 | 增加聚合/索引成本，首版无外部通知 | 当前项目 |

### 决策

**选择**: PostgreSQL 不可变事实、有界 `date_trunc` 趋势和既有 incident 状态机。
**理由**: 质量快照、postmortem、恢复历史、事件和权限已经由 PostgreSQL 权威保存；同一边界可实现 UTC 窗口、筛选绑定 cursor、stable/evidence fingerprint、truncated 失败关闭和新事实恢复。
**Trade-offs**: PostgreSQL 承担额外趋势聚合与 captured-time 索引；首版不提供跨年时序仓库、外部通知或自动修复。

### 影响范围

- `operations/storage/api capacity_control`、控制台、Settings、Compose 和 Alembic `e9a2f4c6b810`
- README、技术设计、运行手册、ADR 0030、生产 ground truth 和迁移/生产门禁
- v0.29 可基于这些质量事实继续做再认证和受控退役，但不得把趋势或 RAG 命中当作授权

### 撤销条件

真实负载的 EXPLAIN/P95、表增长、备份恢复或组织通知 SLA 证明 PostgreSQL 不能满足目标，且替代系统经过租户隔离、即时撤权、幂等同步、断线恢复、状态一致性和回滚演练时，可接入外部时序/告警后端；PostgreSQL 不可变快照与授权事实仍保留。

## Glossary Addendum: 治理知识质量趋势与风险

### 质量趋势桶

- **规范名称**：治理知识质量趋势桶（Governance Knowledge Quality Trend Bucket）
- **定义**：在 UTC 有界窗口内由 PostgreSQL `date_trunc(hour/day)` 聚合的不可变质量快照统计点，包含四类 assessment 和 distinct postmortem 数。

### 截断失败关闭

- **规范名称**：质量风险截断失败关闭（Quality Risk Truncation Fail-Closed）
- **定义**：当快照、postmortem、候选或既有事件读取超过配置上限时标记 `truncated`，并停止不完整的质量风险创建与恢复判断，禁止解释为健康。
