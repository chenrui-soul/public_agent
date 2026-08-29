# public_agent

`public_agent` 是一个面向生产环境的通用智能体框架。它将稳定的运行内核与可安装的领域能力包分离，使同一套基础设施能够构建多个拥有独立知识、工具、流程、记忆、评测和成长路径的专业智能体。

## 核心目标

- 提供可替换模型供应商的异步运行内核。
- 支持结构化工具调用、权限判断、人工审批和结果验证。
- 提供工作记忆、长期记忆和独立领域记忆空间。
- 将运行经验转化为成长候选，经过评测、审批和版本发布后晋升。
- 使用 PostgreSQL、pgvector、Redis、迁移、审计和可观测性满足生产要求。
- 通过领域能力包快速生产专业领域智能体，而不是复制框架代码。

## 当前阶段

项目已完成 `v0.21` 的版本化容量阈值变更治理闭环，在真实负载校准、Outbox 分区归档和容量趋势治理之上，
加入持续窗口验证、人工审批、策略发布、冷却期、效果复核与精确回滚。详细方案见
[技术方案](docs/TECHNICAL_DESIGN.md)。

已经实现：

- 通用异步运行循环和模型供应商协议。
- 领域能力包加载、语义版本校验和路径隔离。
- `AgentFactory` 专业智能体实例化入口。
- 工具 Schema、超时、风险等级和人工审批暂停点。
- 分层记忆接口和租户/智能体/领域命名空间隔离。
- 成长候选的评测、审批、激活、废弃和回滚状态机。
- PostgreSQL + pgvector 生产数据模型和首个 Alembic 迁移。
- PostgreSQL 运行记录、不可变顺序事件、记忆、成长候选和评测仓储。
- 成功运行后的知识提取、作用域指纹去重和证据评测。
- 基于完整顺序轨迹的 `ReflectionEngine`，覆盖模型正文、工具调用与结果、验证和失败。
- 反思前秘密脱敏、单事件/总轨迹容量控制和不可信数据隔离。
- 反思模型结构化 JSON 契约、真实事件 ID 证据绑定和响应内去重。
- 失败运行可提出 `failure` 记忆，但仍必须经过评测与人工审批。
- 候选冲突分类为重复、兼容、矛盾或无冲突；不确定时保守地不合并。
- 显式候选合并会创建新的待审批候选，并保留来源候选、运行和事件证据谱系。
- PostgreSQL 候选指纹采用独立权威列和作用域索引，精确去重不再扫描 JSON。
- 同指纹并发创建使用事务级 advisory lock；并发合并使用来源行锁和确定性合并 ID。
- 合并发布会原子弃用来源候选、停用来源记忆；合并回滚会恢复发布前来源状态。
- 独立的外部知识文档和分块模型，不与受控成长记忆混用。
- 知识文档不可变版本、重复摄取幂等、新版本原子替代和访问标签过滤。
- PostgreSQL `tsvector` + GIN 全文检索与 pgvector + HNSW 向量检索。
- `jieba` 搜索模式中文分词、领域自定义词典、版本化 lexical profile 和幂等批量重建。
- 全文和向量结果使用 RRF 融合，再经可替换 `KnowledgeReranker` 做有界二次排序。
- 默认中文重排器组合词项覆盖率、原始 RRF 和语义相似度；超时、异常或非法返回自动降级。
- 检索来源按 `[K1]` 引用，实际呈现内容、分数和裁剪状态写入完整运行轨迹。
- RAG 内容按不可信数据隔离；领域要求引用时，无有效引用的答案会进入修订步骤。
- 生产级 `OpenAIEmbeddingProvider`，固定使用 384 维、批量输入、有限重试、显式超时和安全错误边界。
- YAML/JSONL 版本化 RAG 评测集、稳定内容哈希和重复案例检测。
- Hit Rate@K、Recall@K、MRR@K、NDCG@K、无关召回率、平均/P95 延迟指标。
- 可选答案引用评测、绝对质量阈值和与上一成功运行比较的回归门禁。
- PostgreSQL 持久化评测运行与逐案例结果，保留嵌入、检索配置、门禁和安全错误码。
- 显式人工审批后发布正式记忆，后续运行可召回并记录命中事件。
- 候选和正式记忆的事务性发布、审计记录与回滚停用。
- 候选显式过期时间、保护时间和 `expired` 软终态；自动治理不物理删除证据。
- 正式记忆召回次数和最近召回时间的并发安全统计。
- 版本化候选治理策略：有界 keyset 扫描、保护判定、价值评分、过期和低价值淘汰。
- 高风险、显式保护、高价值、评测/审批中或仍被活跃派生资产引用的候选禁止自动遗忘。
- 兼容活跃候选可生成确定性压缩候选，关系表保存来源版本和状态，并重新经过评测与人工审批。
- 压缩发布原子弃用来源并停用来源记忆；回滚恢复压缩前来源状态和召回能力。
- 独立候选治理动作表保存策略、原因、分数、幂等键、前后状态和替代候选。
- 生产级 `OpenAIModelProvider`，使用官方 Responses API、完整工具历史重放和 reasoning 隔离状态。
- function tool 使用原始输入/输出 Schema 与 strict 模式；未知工具、重复调用 ID、非法 JSON 和乱序工具结果失败关闭。
- SDK 内建重试关闭；适配器只对超时、429 和 5xx 使用稳定幂等键做有限重试，4xx 参数错误不重试。
- 生成模型异常统一脱敏，不透传 API Key、请求正文、供应商响应正文或原始传输错误。
- 严格领域包清单、类型化技能/策略/工作流/评测资产，以及路径、UTF-8 和容量安全边界。
- instructions、policies 和声明资产分别生成 SHA-256，规范化清单与资产哈希形成稳定包内容哈希。
- 同租户/智能体/领域/版本同内容重复建包幂等，同版本异内容拒绝原地覆盖。
- 领域包 `draft -> evaluating -> awaiting_approval -> approved -> active` 状态机，失败与拒绝均失败关闭。
- 通过评测且人工批准后，发布事务创建或复用不可变 `agent_versions` 并原子切换活跃版本。
- 评测、审批、activate 和 rollback 使用 PostgreSQL 追加审计；旧版本软废弃，回滚不删除历史。
- 发布幂等键、agent 行锁、租户作用域检查和并发重复激活防线。
- 活跃领域包可从 PostgreSQL 还原为 `AgentSpec`，文件构建与数据库发布使用同一清单契约。
- 高风险工具暂停时持久化完整消息、隔离供应商状态、当前及剩余工具调用、引用 ID 和 agent/tool 哈希。
- `PersistentAgentService.resume(...)` 支持批准后从不可变 checkpoint 继续，不重新检索记忆或 RAG。
- 批准只授权一个精确调用；同一响应中的后续高风险调用会产生新的独立审批。
- 审批工具必须声明幂等，执行时获得稳定 `run_id:tool_call_id` 幂等键。
- PostgreSQL 行锁、有限租约和 fencing token 保证并发恢复只有一个 owner；过期租约和旧 token 不能完成运行。
- 拒绝决定原子取消运行且不执行工具；重复相同决定和终态重放幂等，不同决定失败关闭。
- agent spec、工具版本/定义、approval/run/tenant 作用域或 checkpoint 漂移均拒绝恢复。
- resume token 不进入普通运行事件；数据库约束保证 token 与租约状态一致。
- UTF-8 文本/Markdown、HTML、PDF 和 DOCX 的有界安全解析，统一执行媒体/扩展、编码、页数、ZIP 路径、解压体积、压缩比和总容量门禁。
- 文档解析结果保存原始文件 SHA-256、版本化 parser profile 和结构统计；HTML 脚本/样式/模板内容不会进入知识正文。
- PostgreSQL 持久知识导入任务采用 `Init -> Step -> Poll`，解析、分批嵌入和原子发布均可重试且有界。
- 导入任务使用作用域幂等键、请求哈希、advisory lock、Step 租约和 fencing token；崩溃接管后旧 worker 不能覆盖任务状态。
- staging chunks 保存分块和嵌入进度，最终发布复用知识版本唯一约束，重复 Step 不会产生第二个文档版本。
- 知识文档管理支持稳定游标分页、active 归档和跨租户/同名 agent 隔离；归档后 RAG 立即停止召回但不物理删除历史。
- FastAPI 提供知识导入 Init/Step/Poll、文档列表和归档端点；认证主体由服务端可替换依赖提供，未配置认证或知识服务时路由不暴露。
- 管理端点只使用认证主体的 tenant，拒绝把客户端 tenant header 当作授权事实；写操作要求 `knowledge:write`，错误返回稳定机器码。
- PostgreSQL `api_principals`、agent grants 和 `api_tokens` 提供真实服务身份、tenant/agent 最小权限、显式 permissions、过期、撤销、禁用和最近使用状态。
- API Token 使用 256 bit 随机 secret、随机 prefix 和部署 pepper 的 HMAC-SHA256；明文只在签发时以 `SecretStr` 返回一次，数据库只保存 32 字节摘要。
- Token 验证先做严格格式检查，再按 prefix 定位并使用 constant-time digest 比较；未知、错误、过期、撤销、主体禁用和 tenant 停用统一返回 `401 authentication_required`。
- 每主体 active Token 上限、最长十年有效期、条件式 `last_used_at` 更新、并发主体幂等创建和 Token 撤销幂等已经落地。
- FastAPI 可直接注入 `PostgresAPIKeyService` 生成 HTTP Bearer 主体；认证数据库不可用返回安全 `503 authentication_unavailable`，不泄漏内部异常。
- `ActiveAgentAssembler` 只从 PostgreSQL 当前 active 领域包装配运行实例，并校验 agent key、清单、内容哈希和资产索引一致性。
- FastAPI 提供运行创建、查询、取消、审批查询和决定端点；权限分别为 `runs:write`、`runs:read` 和 `approvals:decide`。
- 运行创建使用 tenant 级幂等键绑定 agent、active version、任务和完整运行上下文；同请求重放同一 run，异请求稳定冲突。
- 运行与审批响应不返回 checkpoint、provider state、resume token、工具参数、工具定义哈希或原始内部错误。
- 取消在 PostgreSQL 行锁事务中清除 checkpoint/恢复所有权并取消 pending approval；旧 runtime/resume owner 不能覆盖 canceled 终态。
- FastAPI 提供正式记忆列表/搜索、成长候选列表/详情、可信评测、人工批准/拒绝、发布和回滚端点。
- 记忆和候选列表使用严格 URL-safe Base64 的 `created_at + id` keyset；管理查询不会修改正式记忆的召回统计。
- 候选评测结果只由服务端配置的 `CandidateEvaluator` 生成，客户端不能提交 `passed` 或评分绕过质量门禁。
- 人工批准与正式记忆发布在同一 PostgreSQL 行锁事务中完成；并发相同决定幂等，不同决定、版本或备注冲突。
- 候选管理响应只投影待审资产、证据 ID 和最新评测/审批摘要，不返回反思 prompt、provider state、checkpoint 或未脱敏事件正文。
- Principal/Token 管理、职责权限拆分、委派约束、最后安全管理员保护和追加认证审计。
- 运行成功、失败、取消或超时与反思 Outbox 任务在同一 PostgreSQL 事务提交，终态幂等重放会补偿入队。
- `ReflectionWorker` 使用 `FOR UPDATE SKIP LOCKED`、有限租约、自动 heartbeat、fencing、有界指数退避和 dead-letter。
- `ReflectionWorkerRunner` 支持常驻单任务领取、停止信号、有限 drain 和停止后不再领取；Worker 实例 token 与生命周期 heartbeat 持久化到 PostgreSQL。
- `public-agent reflection-worker` 以环境变量/受限 CLI 参数装配真实 PostgreSQL 与 OpenAI 反思链路，支持跨平台停止信号、安全 JSON 事件和稳定退出码。
- `public-agent capacity-check` 从 PostgreSQL 读取 handler version 隔离的积压和 Worker fleet 快照，输出 healthy/warning/critical、安全原因码、有界推荐副本数和 scale delta。
- `public-agent capacity-monitor` 按有界周期持续采样容量报告，并将观测幂等持久化到 PostgreSQL。
- `public-agent capacity-trend` 按小时或天查询有界容量趋势；`capacity-calibrate` 从真实已完成任务计算 P50/P95/P99 与阈值建议，样本不足以退出码 6 失败关闭，且不会自动修改配置。
- `public-agent capacity-policy` 把校准建议推进为版本化变更请求，强制持续窗口、具名人工审批、冷却期、效果复核和精确上一策略回滚；发布只激活 PostgreSQL 阈值策略，不自动修改 Worker 副本。
- Worker 领取、租约过期接管、完成与失败路径记录真实处理耗时，校准不调用模型供应商。
- `public-agent outbox-maintain` 默认只预览；显式 `--execute` 才归档，物理清理还必须同时提供 `--prune`。
- 终态 Outbox 快照进入 PostgreSQL 原生范围分区；归档身份为 `job_id + completed_at + version`，有重试请求引用的任务禁止物理清理。
- 生产镜像采用精确依赖约束、多阶段构建、非 root 用户；Compose 编排 migrate、API、可扩容 Worker、Secret、资源/日志上限和容量发布门禁。
- 可按 job type + handler version 查询 pending/processing/retry/succeeded/dead-letter 数量和最老可用任务时间。
- Worker 只从 Outbox 读取安全 schema 版本，再按 run ID 重新加载已提交轨迹；payload 不复制任务、输出、checkpoint、provider state 或凭据。
- 同一 run + handler version 幂等，处理器版本重放继续复用候选指纹、冲突、合并、评测和发布不变量。
- `runs(id, tenant_id)` 与 `outbox_jobs(run_id, tenant_id)` 复合外键在数据库层拒绝跨租户任务配对。
- Outbox 使用独立 `version`、历史总尝试和单轮尝试计数；人工重试保留历史 attempts，只重置当前重试轮次。
- FastAPI 提供 tenant/agent/handler-version 作用域的反思任务 stats、严格 keyset 列表、安全详情和 dead-letter 重试。
- 运维职责拆为 `operations.jobs:read` 与 `operations.jobs:retry`；重试事务重新验证 tenant、Principal、Token、权限和 agent grants 当前事实。
- dead-letter 重试使用 expected version、任务行锁、租户幂等锁和哈希幂等键；相同请求安全回放，不同请求或状态变化冲突。
- 幂等重试事实和每次运维尝试分别追加到不可变表；审计不保存任务、输出、payload、异常正文、Token 或 Authorization header。
- FastAPI 存活与数据库就绪检查。
- Docker Compose 开发基础设施、生产编排、离线示例和 PostgreSQL 端到端集成测试。

下一阶段：

- 在积累多个业务周期的生产观测后建立人工审批的阈值变更流程，并评估外部扩缩容控制器、长期分区维护和
  容量预测；不改变 PostgreSQL 任务、租约、heartbeat、重试与终态的权威事实边界。

## 知识沉淀闭环

当前纵向链路为：

```text
持久化运行与事件
→ 完整轨迹加载
→ 脱敏与容量控制
→ 结构化模型反思
→ 真实事件证据校验
→ 作用域指纹去重
→ 保守冲突检测与审计标记
→ 可选显式合并并保留来源谱系
→ 证据评测
→ awaiting_approval
→ 显式审批
→ 事务性发布正式记忆
→ 下一次运行召回
→ 回滚后停止召回或恢复合并前来源记忆
→ 生命周期治理扫描与使用统计
→ 软过期/低价值淘汰，或提出待审批压缩候选
```

`ReflectionEngine` 把轨迹内容视为不可信证据而不是指令，只接受严格 JSON，并拒绝缺失、
伪造或因容量裁剪而未呈现的事件 ID。它只会提出候选，不会绕过审批直接学习。
离线或低成本场景仍可使用 `SuccessfulRunKnowledgeExtractor` 安全基线。

冲突检测默认使用保守规则基线：精确指纹相同视为重复；同一作用域、相同记忆类型且高度相似的
同极性内容视为兼容；同一命题的相反极性内容视为矛盾。兼容和矛盾只写入候选审计元数据，
不会自动覆盖或发布。调用 `KnowledgeSedimentationPipeline.merge_candidates(...)` 才会生成合并候选，
且合并候选仍停在 `awaiting_approval`。

## 候选生命周期治理

`CandidateGovernanceService` 只处理一个有界作用域批次。它组合最近评测分数、记忆重要度、置信度、
召回次数和召回新鲜度计算价值分数；候选状态、显式保护、高风险、活跃来源引用和高价值记忆构成
不可绕过的保护门禁。过期和淘汰只把候选、正式记忆标记为 `expired`，不删除运行、事件、评测、
审批或来源谱系。

```python
from datetime import UTC, datetime

from public_agent.growth import (
    CandidateGovernancePolicy,
    CandidateGovernanceQuery,
    CandidateGovernanceService,
    EvidenceBasedCandidateEvaluator,
    LearningService,
)
from public_agent.storage import (
    PostgresCandidateGovernanceRepository,
    PostgresLearningStore,
)

learning_store = PostgresLearningStore(database.sessions)
governance = CandidateGovernanceService(
    repository=PostgresCandidateGovernanceRepository(database.sessions),
    learning=LearningService(learning_store),
    evaluator=EvidenceBasedCandidateEvaluator(),
    policy=CandidateGovernancePolicy(),
)

cursor = None
as_of = datetime.now(UTC)
while True:
    batch = await governance.run_batch(
        CandidateGovernanceQuery(
            tenant_id="tenant-a",
            agent_id="tax-agent",
            domain_id="tax-agent",
            as_of=as_of,
            after=cursor,
        )
    )
    cursor = batch.next_cursor
    if cursor is None:
        break
```

压缩器不会原地改写来源候选。默认 `DeterministicCandidateCompressor` 只处理每一对都被保守检测为
`compatible` 或 `duplicate` 的活跃候选，创建带确定性 ID 和完整来源关系的新候选；新候选仍需评测、
人工审批和事务发布。生命周期治理决策见 [ADR 0008](docs/adr/0008-candidate-lifecycle-governance.md)。

## OpenAI Responses 生成模型

生产生成链路使用官方异步 SDK 的 Responses API。运行时保存完整 assistant 工具调用，再按相同
`call_id` 追加 tool 输出；适配器会把 reasoning、`function_call` 和 `function_call_output` 作为
有序输入重放，因此同一 provider 实例可以安全服务多个并发运行，也能为后续审批检查点恢复保留事实。

```python
from public_agent.config import Settings
from public_agent.factory import AgentFactory
from public_agent.providers import OpenAIModelProvider

settings = Settings()
model = OpenAIModelProvider(
    api_key=settings.openai_api_key,
    model=settings.openai_model,
    max_output_tokens=settings.openai_max_output_tokens,
    timeout_seconds=settings.openai_timeout_seconds,
    max_retries=settings.openai_max_retries,
    retry_backoff_seconds=settings.openai_retry_backoff_seconds,
)

agent = AgentFactory().create(
    domain_path=domain_path,
    model=model,
    tools=tools,
)

try:
    result = await agent.run(task, context=run_context)
finally:
    await model.aclose()
```

默认模型为 OpenAI 当前平衡型 `gpt-5.6-terra`，可通过环境变量覆盖。API Key 仅通过
`SecretStr`/环境变量进入客户端；适配器不向 OpenAI 转发任意运行 metadata，使用 `store=false`，
并将输出上限、超时和重试次数设为显式硬边界。所有供应商契约测试都使用 Mock HTTP，不会调用收费 API。
实现决策见 [ADR 0009](docs/adr/0009-openai-responses-model-provider.md)。

## RAG 混合检索

RAG 外部知识与成长记忆保持独立：文档是可更新、可引用的参考资料，记忆是经过真实运行、评测和审批后发布的经验。当前可运行链路为：

```text
KnowledgeDocumentInput
→ TextChunker
→ EmbeddingProvider
→ knowledge_documents / knowledge_chunks
→ jieba 预分词 lexical_text + PostgreSQL GIN 全文召回
→ pgvector HNSW 向量召回
→ RRF 融合
→ KnowledgeReranker 有界重排（失败降级 RRF）
→ KnowledgeRetriever
→ AgentRuntime 不可信上下文
→ [Kx] 来源引用
→ knowledge.retrieved 完整轨迹事件
```

生产接入示例：

```python
from public_agent.config import Settings
from public_agent.knowledge import (
    JiebaChineseSegmenter,
    KnowledgeIngestionService,
    OpenAIEmbeddingProvider,
)
from public_agent.storage import PostgresKnowledgeRepository

settings = Settings()
embeddings = OpenAIEmbeddingProvider(
    api_key=settings.openai_api_key,
    model=settings.openai_embedding_model,
    dimensions=settings.openai_embedding_dimensions,
    timeout_seconds=settings.openai_embedding_timeout_seconds,
    max_retries=settings.openai_embedding_max_retries,
    batch_size=settings.openai_embedding_batch_size,
)
segmenter = JiebaChineseSegmenter(custom_terms=("智能体工程", "退款期限"))
knowledge = PostgresKnowledgeRepository(
    database.sessions,
    embeddings,
    segmenter=segmenter,
)
ingestion = KnowledgeIngestionService(writer=knowledge, embeddings=embeddings)

await ingestion.ingest(document)
# 词典/profile 变化后，对目标作用域执行可恢复的幂等批量重建。
await knowledge.reindex_lexical(
    tenant_id=document.tenant_id,
    agent_id=document.agent_id,
    domain_id=document.domain_id,
    namespace=document.namespace,
)
agent = AgentFactory().create(
    domain_path=domain_path,
    model=model,
    tools=tools,
    knowledge=knowledge,
)
```

领域包启用方式：

```yaml
memory_namespace: support-memory
knowledge_namespace: support-manuals
knowledge_top_k: 5
policies:
  require_citations: true
```

`DeterministicHashEmbeddingProvider` 只用于离线测试，不代表生产语义质量。当前生产适配器默认使用 `text-embedding-3-small`，显式请求 384 维；嵌入 profile 为 `openai:text-embedding-3-small`。切换模型或 profile 后必须重建对应知识向量，禁止在同一知识索引中静默混用。中文分词与重排决策见 [ADR 0007](docs/adr/0007-versioned-chinese-segmentation-and-reranking.md)。

## 知识文件管理 API

管理 API 只在同时注入知识服务和认证主体依赖时注册。tenant 始终来自认证主体，不读取客户端
`X-Tenant-Id` 作为授权事实。

```text
POST /v1/knowledge/ingestions
POST /v1/knowledge/ingestions/{id}/step
GET  /v1/knowledge/ingestions/{id}
GET  /v1/knowledge/documents
POST /v1/knowledge/documents/{id}/archive
```

创建导入使用 `multipart/form-data` 和 `Idempotency-Key`。支持文件上限为 8 MiB；`access_tags` 是 JSON
字符串数组，`metadata` 是 JSON 对象。Init 返回 `202 queued`，客户端或 worker 按有界批次调用 Step，
再通过 Poll 获取 `processed / total / percent / has_more`。生产部署还应在反向代理设置同等或更小的请求体上限。

```python
from public_agent.api import KnowledgePrincipal
from public_agent.api.app import create_app
from public_agent.storage import PostgresKnowledgeManagementService

async def authenticated_principal() -> KnowledgePrincipal:
    # 下一阶段会提供 PostgreSQL Bearer/API Token 实现；当前由宿主系统注入可信主体。
    return KnowledgePrincipal(
        subject="operator-id",
        tenant_id="tenant-slug",
        allowed_agent_ids=frozenset({"support-agent"}),
        permissions=frozenset({"knowledge:read", "knowledge:write"}),
    )

knowledge_management = PostgresKnowledgeManagementService(
    sessions=database.sessions,
    writer=knowledge,
    embeddings=embeddings,
)
app = create_app(
    database=database,
    knowledge=knowledge_management,
    knowledge_principal_dependency=authenticated_principal,
)
```

详细状态机、安全边界和权衡见 [ADR 0012](docs/adr/0012-safe-document-ingestion-and-knowledge-api.md)。

## API Token 认证

`PostgresAPIKeyService` 负责创建租户主体、签发一次性 Token、认证、撤销和禁用。签发返回的
`IssuedAPIToken.token` 是 `SecretStr`；调用方必须立即存入秘密管理器，之后无法从数据库恢复。

```python
from public_agent.auth import APITokenCodec, PrincipalCreateRequest
from public_agent.storage import PostgresAPIKeyService

api_keys = PostgresAPIKeyService(
    database.sessions,
    codec=APITokenCodec(settings.secret_key),
)
principal = await api_keys.create_principal(
    PrincipalCreateRequest(
        tenant_id="tenant-slug",
        subject="knowledge-ingestor",
        display_name="Knowledge Ingestor",
        permissions=("knowledge:read", "knowledge:write"),
        agent_ids=("support-agent",),
    )
)
issued = await api_keys.issue_token(
    principal_id=principal.id,
    tenant_id=principal.tenant_id,
    label="production-ingestor",
)
plaintext_once = issued.token.get_secret_value()
```

知识 API 可直接使用 Bearer 认证，不再需要手写主体依赖：

```python
app = create_app(
    database=database,
    knowledge=knowledge_management,
    api_keys=api_keys,
)
```

客户端发送 `Authorization: Bearer public_agent_<prefix>.<secret>`。生产 pepper 轮换会使旧 Token 失效，应先
通过下述认证管理 API 签发新 Token 并完成客户端切换。底层 Token 设计见
[ADR 0013](docs/adr/0013-postgresql-api-token-authentication.md)。

## 运行与审批管理 API

运行 API 只在同时注入运行管理服务和可信主体依赖时注册。使用 `api_keys` 时，Bearer Token 会被解析为
服务器可信的 tenant、agent grants 和 permissions；客户端 `X-Tenant-Id` 不参与授权。

```text
POST /v1/runs
GET  /v1/runs/{run_id}?agent_id=...
POST /v1/runs/{run_id}/cancel
GET  /v1/approvals/{approval_id}?agent_id=...
POST /v1/approvals/{approval_id}/decide
```

```python
from public_agent.application import AgentRunManagementService, PersistentAgentService
from public_agent.factory import ActiveAgentAssembler
from public_agent.storage import PostgresDomainPackagePublisher, PostgresRunPersistence

run_store = PostgresRunPersistence(database.sessions)
active_agents = ActiveAgentAssembler(
    specs=PostgresDomainPackagePublisher(database.sessions),
    model=model_provider,
    tools=tool_registry,
    knowledge=knowledge,
    memory=memory,
)
run_management = AgentRunManagementService(
    executor=PersistentAgentService(runs=run_store),
    runs=run_store,
    agents=active_agents,
)
app = create_app(
    database=database,
    runs=run_management,
    api_keys=api_keys,
)
```

创建运行必须携带 `Idempotency-Key`。审批批准会从不可变 checkpoint 精确恢复；拒绝不会执行工具；取消会
清除 pending checkpoint 和 resume lease，并通过数据库状态检查阻止旧执行者提交结果。API 仅返回安全
状态摘要和最终输出，失败原因使用稳定机器码，不透传内部异常。详细权衡见
[ADR 0014](docs/adr/0014-run-and-approval-management-api.md)。

## 记忆与成长候选管理 API

管理 API 继续只在同时注入服务和认证依赖时注册。tenant 固定来自 Bearer Principal，agent 还必须通过
grant 与独立权限检查：`memories:read`、`candidates:read`、`candidates:evaluate`、
`candidates:promote`。

```text
GET  /v1/memories?agent_id=...&domain_id=...
GET  /v1/candidates?agent_id=...&domain_id=...
GET  /v1/candidates/{candidate_id}?agent_id=...&domain_id=...
POST /v1/candidates/{candidate_id}/evaluate
POST /v1/candidates/{candidate_id}/decide
POST /v1/candidates/{candidate_id}/rollback
```

```python
from public_agent.growth import AgentGrowthManagementService, EvidenceBasedCandidateEvaluator
from public_agent.storage import (
    PostgresGrowthManagementRepository,
    PostgresKnowledgeAssetPublisher,
)

growth_management = AgentGrowthManagementService(
    repository=PostgresGrowthManagementRepository(database.sessions),
    evaluator=EvidenceBasedCandidateEvaluator(),
    publisher=PostgresKnowledgeAssetPublisher(database.sessions),
)
app = create_app(
    database=database,
    growth=growth_management,
    api_keys=api_keys,
)
```

管理搜索是无副作用查询，不复用会更新 `recall_count` 的运行时 `MemoryStore.search`。评测请求只提交
`expected_version`，实际 `passed/score/metrics` 由可信 evaluator 生成。人工 `approved` 决定会在一个事务中
校验最新通过评测、写审批、发布记忆并激活候选；`rejected` 和 rollback 同样使用版本保护与幂等重放。
详细权衡见 [ADR 0015](docs/adr/0015-memory-and-growth-management-api.md)。

## Principal、Token 与认证审计 API

认证管理路由只在同时注入 `auth_management` 服务和可信 Bearer 认证依赖时注册。管理职责拆分为
`auth.principals:read/write`、`auth.tokens:read/issue/revoke` 和 `auth.audit:read`；服务端会重新读取
actor 当前 Token、主体状态、权限和 agent grants，不能依赖认证时的旧快照扩大权限。

```text
GET  /v1/auth/principals
POST /v1/auth/principals
GET  /v1/auth/principals/{principal_id}
POST /v1/auth/principals/{principal_id}/status
GET  /v1/auth/principals/{principal_id}/tokens
POST /v1/auth/principals/{principal_id}/tokens
POST /v1/auth/tokens/{token_id}/revoke
GET  /v1/auth/audit-events
```

```python
app = create_app(
    database=database,
    api_keys=api_keys,
    auth_management=api_keys,
)
```

通过管理 API 创建主体时，请求权限必须同时属于 actor 当前权限和服务端 allowlist，agent scope 不能超过
actor。完整 Token 只在签发响应返回一次；Token 列表只返回 id、随机 prefix、标签和生命周期时间，不返回
明文或 HMAC digest。撤销和状态切换幂等且立即影响下一次认证。tenant 级 advisory lock 会阻止并发禁用或
撤销最后一个仍有可用 Token 的全租户安全管理员。

`authentication_audit_events` 追加记录已呈现/缺失凭据的认证结果以及管理写操作，数据库触发器禁止更新
审计行。审计仅保存主体/Token ID、动作、结果和服务端安全元数据，不保存 Token、digest 或原始
`Authorization` header。详细权衡见
[ADR 0016](docs/adr/0016-principal-token-management-and-authentication-audit.md)。

## PostgreSQL Outbox 与反思 Worker

`PostgresRunPersistence` 默认在运行进入 `succeeded`、`failed`、`canceled` 或 `timed_out` 时，于同一事务
写入唯一反思任务。在线运行服务应不再注入同步 `sedimentation`，由后台 Worker 重载已提交运行事实并执行
同一 `KnowledgeSedimentationPipeline`，从而避免反思模型延迟阻塞运行 API。

```python
from public_agent.growth import (
    EvidenceBasedCandidateEvaluator,
    KnowledgeSedimentationPipeline,
    LearningService,
    SuccessfulRunKnowledgeExtractor,
)
from public_agent.storage import (
    PostgresKnowledgeAssetPublisher,
    PostgresLearningStore,
    PostgresReflectionJobStore,
)
from public_agent.workers import ReflectionWorker

learning_store = PostgresLearningStore(database.sessions)
pipeline = KnowledgeSedimentationPipeline(
    learning=LearningService(learning_store),
    learning_store=learning_store,
    extractor=SuccessfulRunKnowledgeExtractor(),
    evaluator=EvidenceBasedCandidateEvaluator(),
    publisher=PostgresKnowledgeAssetPublisher(database.sessions),
)
worker_jobs = PostgresReflectionJobStore(database.sessions)
worker = ReflectionWorker(
    jobs=worker_jobs,
    sedimentation=pipeline,
    lease_seconds=300,
    heartbeat_seconds=60,
)
results = await worker.process_step(worker_id="reflection-worker-01", max_jobs=10)
```

需要常驻运行时，使用可由宿主信号处理器设置的停止事件；runner 收到停止信号后不会领取新任务，当前任务
最多在 `drain_timeout_seconds` 内收敛，超时后取消本地处理并等待数据库租约过期接管：

```python
import asyncio

from public_agent.workers import ReflectionWorkerRunner

stop_event = asyncio.Event()
runner = ReflectionWorkerRunner(
    worker=worker,
    lifecycle=worker_jobs,
    worker_id="reflection-worker-01",
    poll_interval_seconds=1,
    poll_jitter_seconds=0.25,
    drain_timeout_seconds=30,
)
summary = await runner.run(stop_event=stop_event)
```

生产部署应直接使用项目入口。API Key 只能通过 `PUBLIC_AGENT_OPENAI_API_KEY` 或受控 secret manager 注入，
命令行不提供密钥参数：

```powershell
$env:PUBLIC_AGENT_ENVIRONMENT = "production"
$env:PUBLIC_AGENT_SECRET_KEY = "<from-secret-manager>"
$env:PUBLIC_AGENT_OPENAI_API_KEY = "<from-secret-manager>"
$env:PUBLIC_AGENT_REFLECTION_WORKER_ID = "reflection-worker-01"
public-agent reflection-worker
```

可用参数包括 `--worker-id`、`--handler-version`、重试/租约/heartbeat、poll/jitter 和 drain timeout。
同一部署中的并发进程必须使用不同 worker ID。SIGINT/SIGTERM（Windows 额外支持 SIGBREAK）只设置停止事件；
Runner 停止领取新任务并有限 drain。退出码 `0` 表示安全停止，`1` 表示运行或清理失败，`2` 表示配置失败，
`3` 表示 drain timeout。stdout/stderr 只输出安全 JSON 事件，不包含连接串、Token、供应商异常正文或运行轨迹。

`lifecycle` 应使用与 Worker 相同的 `PostgresReflectionJobStore` 实例。重复注册相同 worker ID 会生成新的
instance token 并 fencing 旧进程；heartbeat 与停止状态保存在 `reflection_worker_heartbeats`。积压可通过
`await worker_jobs.backlog_snapshot()` 获取，结果按当前 handler version 隔离。

Outbox payload 固定为 `{"schema_version": 1}`。领取使用 `FOR UPDATE SKIP LOCKED`；heartbeat 只延长当前
fencing token 的租约；过期任务可由新 Worker 接管，旧 Worker 无法 complete/fail。失败仅持久化安全机器码，
按有界指数退避重试，耗尽后进入 `dead_letter`。处理器升级使用新的 `handler_version` 显式重放，同一版本
重复入队不会生成第二个任务。详细权衡见
[ADR 0017](docs/adr/0017-postgresql-reflection-outbox-worker.md)。
生产入口的应用生命周期、信号、密钥和退出码决策见
[ADR 0019](docs/adr/0019-production-reflection-worker-cli.md)。

## 生产部署与容量治理

生产发布使用 `Dockerfile`、`requirements.lock` 和 `docker-compose.production.yml`。先由受控流程创建
`secrets/PUBLIC_AGENT_POSTGRES_PASSWORD`、`PUBLIC_AGENT_DATABASE_URL`、`PUBLIC_AGENT_SECRET_KEY`、
`PUBLIC_AGENT_API_TOKEN_PEPPER` 和 `PUBLIC_AGENT_OPENAI_API_KEY`；Token pepper 必须独立于应用签名密钥，
Secret 值不进入 `.env`、命令行或镜像。

```powershell
docker compose -f docker-compose.production.yml up -d `
  --scale reflection-worker=1 `
  postgres redis migrate api reflection-worker capacity-monitor
```

生产 Compose 不固定 Worker ID；每个容器使用 hostname + PID 的默认标识，避免 scaled 副本互相 fencing。
运行容量检查：

```powershell
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-check
```

`capacity-check` 不需要 OpenAI API Key。退出码 `0/4/5` 分别表示 healthy/warning/critical，`1` 表示运行或
清理失败，`2` 表示配置/装配失败。报告的 `recommended_workers` 与 `scale_delta` 只提供建议；确认原因码和
持续窗口后再显式扩缩容：

```powershell
docker compose -f docker-compose.production.yml up -d --scale reflection-worker=4 reflection-worker
```

`capacity-monitor` 作为常驻服务每 60 秒采样一次；趋势和校准可通过 `ops` profile 按需运行：

```powershell
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-trend
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-calibrate
```

需要自定义窗口时，完整覆盖一次性容器命令：

```powershell
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-trend `
  public-agent capacity-trend --hours 720 --bucket day --limit 30 --pretty
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-calibrate `
  public-agent capacity-calibrate --lookback-hours 720 --minimum-samples 100 --pretty
```

校准返回 `calibration_id` 后，按显式状态机治理阈值：

```powershell
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-policy `
  public-agent capacity-policy create --calibration-id <calibration-id> `
  --operator requester@example.com --window-seconds 3600 --minimum-observations 60 --pretty
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-policy `
  public-agent capacity-policy validate --request-id <request-id> --expected-version 1 --pretty
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-policy `
  public-agent capacity-policy approve --request-id <request-id> --expected-version 2 `
  --operator reviewer@example.com --pretty
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-policy `
  public-agent capacity-policy publish --request-id <request-id> --expected-version 3 `
  --operator publisher@example.com --cooldown-seconds 3600 --pretty
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-policy `
  public-agent capacity-policy review --request-id <request-id> --expected-version 4 `
  --operator reviewer@example.com --pretty
```

`validate` 或 `review` 在窗口、样本或跨度不足时退出码为 7 且不推进状态。复核为 `ineffective`，或冷却期内
出现紧急回归时，可使用 `rollback --reason <原因>`；只有本请求发布的策略仍是 active 时才会精确恢复上一策略。

生产 `public-agent serve` 已装配 Bearer Token 认证管理、反思任务运维和容量治理控制面。容量治理控制台地址：

```text
http://<api-host>:8000/console/capacity-governance
```

控制台 Token 只保存于当前标签页 `sessionStorage`，不写 Cookie、URL 或 `localStorage`。治理 Principal 必须属于
`PUBLIC_AGENT_REFLECTION_CAPACITY_GOVERNANCE_TENANT_ID`，具备 `all_agents` 且不存在 agent grant；服务端在每个
动作的数据库事务内重验 active tenant/Principal、未撤销未过期 Token 和当前权限。请求、审批、发布、复核、
回滚、告警读取与告警管理使用独立权限；请求人不能审批自己的请求。

治理审计使用独立 `operations.capacity_audit:read` 权限。审计员可通过控制台或
`GET /v1/operations/capacity-governance/audit-events` 按 actor subject、action、outcome 和 UTC 时间窗执行最多
100 条的严格 keyset 查询；游标绑定当前 actor、handler version 和全部筛选条件。响应只包含 actor subject、
动作、结果、目标资源 ID、白名单安全元数据和时间，不返回 Token ID、Authorization 或内部异常正文。

`capacity-monitor` 在保存新观测后运行策略漂移扫描。告警按当前 expected/observed 阈值指纹去重，支持
`open -> acknowledged -> resolved`、持续样本升级为 critical、恢复后自动关闭和复发重开。策略切换后，存在
更新观测时旧期望指纹告警会关闭，新期望独立建警。控制台和 API 不自动扩缩容，也不发送外部通知。
告警同时根据 `first_seen_at` 派生 `within_sla/due/breached/acknowledged/resolved` 运营状态；默认 15 分钟进入
due、60 分钟进入 breached，可分别通过
`PUBLIC_AGENT_REFLECTION_CAPACITY_ALERT_RESPONSE_WARNING_SECONDS` 和
`PUBLIC_AGENT_REFLECTION_CAPACITY_ALERT_RESPONSE_CRITICAL_SECONDS` 配置。SLA 状态不改变告警事实或授权。

`GET /v1/operations/capacity-governance/drill-report` 是受审计权限保护的只读演练：它重验当前数据库身份，检查
职责分离、append-only trigger、告警/事件 lifecycle CHECK、质量快照不可变控制，以及审计/事件/质量趋势查询索引。
任一证据缺失时 `passed=false`，不会自动修复、创建测试 Token 或修改治理记录。

`capacity-monitor` 还会在漂移扫描后执行治理事件扫描，把七类有界信号写入 PostgreSQL 内部队列：denied/conflict
审计突增、未确认告警 SLA breached、告警重复 reopen、只读演练检查失败、持续 unsafe、重复 degraded，以及恢复后再次
quarantined。事件按稳定 rule version 和目标
指纹去重，支持 `open -> acknowledged -> resolved`；确认只表示人工接手，只有更新的 bucket、alert 或 catalog
事实，或更新质量快照/postmortem 隔离历史证明规则不再命中时才会恢复，相同信号复发会重开。时间流逝、确认动作或
恢复申请本身都不能替代恢复事实。事件读取与管理分别使用
`operations.capacity_incidents:read/manage`，API 为
`GET /v1/operations/capacity-governance/incidents`、`GET /incidents/{id}` 和
`POST /incidents/{id}/acknowledge`。控制台事件面板按最小权限独立降级，响应不包含 Token/Principal 内部 ID、
Authorization、数据库 URL 或原始异常正文。本阶段只维护内部事件，不发送外部通知、不自动撤权/修复/发布/
回滚策略，也不调整 Worker 副本。

已确认事件可以创建结构化处置单。七类事件分别固定映射到 `audit_failure_containment`、`alert_sla_recovery`、
`alert_reopen_stabilization`、`drill_control_repair`、`knowledge_safety_containment`、`knowledge_quality_review` 和
`knowledge_recurrence_review` Playbook；同一事件复发周期最多一条。处置使用独立
`operations.capacity_remediations:read/request/approve/execute/verify` 权限，请求人不能审批，执行人不能验证。
执行接口只记录 `completed/failed` 结果和受限证据码，不接受或运行任意命令；只有事件在执行后出现更高版本的
resolved PostgreSQL 事实，处置单才能 verified。API 位于 `/v1/operations/capacity-governance/remediations`，
控制台提供创建、审批、执行记录和恢复验证入口，但仍不自动修改生产系统。

verified 处置可以进一步创建结构化治理复盘。复盘使用独立
`operations.capacity_postmortems:read/request/review` 权限，每个处置单最多一份，请求人不能评审自己的复盘。
根因、影响和预防措施必须与固定 Playbook 的受限分类兼容；10-1000 字安全摘要会拒绝凭据、连接串、代码块、
Shell/SQL/编排命令。批准时系统会再次重验 incident/remediation 版本，并在同一 PostgreSQL 事务内发布到
`operations.governance.postmortems` namespace；拒绝、撤权、来源漂移或并发冲突不会留下知识向量。
`PostgresGovernanceKnowledgeRetriever` 仅在 domain `operations-governance` 且请求携带
`operations.governance:advisory` access tag 时执行中文全文检索与 pgvector RRF 融合。返回谱系包含事件、处置、
版本和内容指纹，并强制标记为 advisory-only，不得作为授权、恢复证据或执行指令。API 为
`GET /postmortems`、`GET /postmortems/{id}`、`POST /remediations/{id}/postmortems` 以及
`POST /postmortems/{id}/approve|reject`。

已发布治理知识支持受限质量反馈。独立
`operations.capacity_knowledge_feedback:read/report/review` 权限分别控制队列读取、报告和复核；反馈只保存
`helpful/not_helpful/safety_concern` 信号、受限原因、postmortem version、knowledge version 和内容指纹，
不保存查询、提示词或模型输出。报告人不能复核自己的反馈；确认 `safety_concern` 时，反馈确认与复盘
`quarantined` 在同一事务提交，同知识版本的其他待复核反馈原子进入 `superseded`，不会形成永久待办或伪造评审人；
`PostgresGovernanceKnowledgeRetriever` 随即排除该知识但保留内容和来源谱系。
API 为 `GET /knowledge-feedback`、`POST /postmortems/{id}/feedback` 和
`POST /knowledge-feedback/{id}/confirm|dismiss`。

隔离后的治理知识由独立质量评测与恢复 RBAC 管理。`operations.capacity_knowledge_quality:read/assess`
控制不可变质量快照的读取与生成；快照绑定当前 postmortem/knowledge version、内容指纹和独立反馈证据指纹，
只会派生 `insufficient/healthy/degraded/unsafe`。`operations.capacity_knowledge_recovery:read/request/review`
控制恢复队列；知识至少隔离 24 小时，且只有当前 `unsafe` 快照能够以结构化 `false_positive` 理由发起申请。
请求人与审批人必须分离，审批人还不能是原安全反馈报告人或确认人。批准、恢复为 `published`、生成新
knowledge version、递增 restore history 和成功审计在同一事务提交；旧反馈、旧快照、内容、向量和来源谱系
继续保留，RAG 只重新检索新的当前发布版本。API 为 `GET /knowledge-quality-snapshots`、
`POST /postmortems/{id}/quality-snapshots`、`GET /knowledge-recoveries`、
`POST /postmortems/{id}/recoveries` 和 `POST /knowledge-recoveries/{id}/approve|reject`。控制台的质量与恢复面板
各自独立处理 403，批准恢复前必须再次确认。

质量读取权限还可调用 `GET /v1/operations/capacity-governance/knowledge-quality-trend`，按 UTC `captured_from/to`、
`hour/day`、可选 assessment 和最多 366 个桶查询不可变快照趋势。服务端只在配置上限内补零，cursor 绑定当前 actor、
bucket、assessment 和完整时间窗。控制台提供 24/72/168 小时安全窗口，并与质量快照和事件面板分别处理 403。
默认风险窗口为 7 天：unsafe warning/critical 为 2/3 个独立证据，degraded 为 2/4；扫描最多读取 1000 个快照，
超出时标记 truncated 并停止创建或恢复质量风险事件。以上阈值可通过
`PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_QUALITY_RISK_WINDOW_SECONDS`、
`PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_UNSAFE_WARNING_COUNT/CRITICAL_COUNT`、
`PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_DEGRADED_WARNING_COUNT/CRITICAL_COUNT`、
`PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_QUALITY_MAXIMUM_SNAPSHOTS` 和
`PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_QUALITY_MAXIMUM_TREND_BUCKETS` 调整，但不会触发自动快照、自动恢复、
外部通知或生产修复。

Outbox 维护默认 dry-run；归档和清理必须分两次显式执行：

```powershell
docker compose -f docker-compose.production.yml --profile ops run --rm outbox-maintain
docker compose -f docker-compose.production.yml --profile ops run --rm outbox-maintain `
  public-agent outbox-maintain --execute --archive-after-days 7 --purge-after-days 90 --pretty
docker compose -f docker-compose.production.yml --profile ops run --rm outbox-maintain `
  public-agent outbox-maintain --execute --prune --archive-after-days 7 --purge-after-days 90 --pretty
```

清理只删除当前版本已有精确归档副本、且没有人工重试请求引用的终态任务。详细安全步骤见运行手册。

完整 Secret、发布、回滚、告警和巡检步骤见
[生产运行手册](docs/OPERATIONS_RUNBOOK.md)，方案权衡见
[ADR 0020](docs/adr/0020-production-deployment-and-capacity-governance.md) 与
[ADR 0021](docs/adr/0021-real-load-calibration-outbox-archives-and-capacity-trends.md)、
[ADR 0022](docs/adr/0022-versioned-capacity-policy-governance.md)、
[ADR 0023](docs/adr/0023-rbac-capacity-console-policy-drift-alerts.md)、
[ADR 0024](docs/adr/0024-governance-audit-sla-and-read-only-drills.md)、
[ADR 0025](docs/adr/0025-governance-incident-detection-and-response.md) 与
[ADR 0026](docs/adr/0026-governance-remediation-approval-and-verification.md)、
[ADR 0027](docs/adr/0027-governance-postmortem-knowledge-publication.md) 与
[ADR 0028](docs/adr/0028-governance-knowledge-feedback-quarantine.md) 与
[ADR 0029](docs/adr/0029-governance-knowledge-quality-and-recovery.md) 与
[ADR 0030](docs/adr/0030-governance-knowledge-quality-trends-and-recurrence-risks.md)。默认 `serve` 入口现在运行生产管理应用；
知识、运行和成长等非管理路由仍需按业务部署显式装配。

## 反思任务安全运维 API

运维路由只在同时注入 `PostgresReflectionJobOperations` 和可信认证依赖时注册；直接注入
`PostgresAPIKeyService` 时会自动生成 Bearer Principal：

```python
from public_agent.api import create_app
from public_agent.storage import PostgresReflectionJobOperations

operations = PostgresReflectionJobOperations(database.sessions)
app = create_app(
    database=database,
    api_keys=api_keys,
    operations=operations,
)
```

```text
GET  /v1/operations/reflection-jobs/stats
GET  /v1/operations/reflection-jobs
GET  /v1/operations/reflection-jobs/{job_id}
POST /v1/operations/reflection-jobs/{job_id}/retry
```

stats/list 支持 `handler_version` 和可选 `agent_id`；列表还支持状态过滤、`limit` 和严格 keyset `cursor`。
游标使用规范化、无 padding 的 URL-safe Base64，并绑定 handler、状态、agent 和 actor 当前 grant scope，篡改或
跨筛选条件复用返回 `400 invalid_cursor`。

查询 DTO 只返回 job/run/agent ID、handler version、状态、版本、尝试计数、时间和安全机器错误码，不返回
task、output、trace、payload、result metadata、worker ID、lease token、checkpoint 或 provider state。

重试请求必须同时提供：

```text
Idempotency-Key: <1..200 characters>
{"expected_version": 7}
```

服务在同一事务内重新验证 active tenant/Principal、未撤销且未过期 Token、当前
`operations.jobs:retry` 和 agent grants，再使用幂等 advisory lock 与 job 行锁检查最新状态。只允许
`dead_letter -> pending`；历史总 attempts 保留，`attempts_in_cycle`、lease/worker、错误码、完成时间和旧结果
摘要被安全重置。原始幂等键只在请求内使用，数据库和审计仅保存 SHA-256 哈希。

详细权衡见 [ADR 0018](docs/adr/0018-safe-reflection-job-operations.md)。

## RAG 评测

评测集使用稳定 `source_key` 作为相关性真值，支持 YAML 和带 `type=dataset` 元数据头的 JSONL。示例数据集见 [`examples/rag_eval/support-manuals.yaml`](examples/rag_eval/support-manuals.yaml)。

```python
from public_agent.evaluation import (
    RAGEvaluationDataset,
    RAGEvaluator,
    RAGQualityThresholds,
    RAGRegressionPolicy,
)
from public_agent.storage import PostgresRAGEvaluationStore

dataset = RAGEvaluationDataset.load("examples/rag_eval/support-manuals.yaml")
evaluation_store = PostgresRAGEvaluationStore(database.sessions)
evaluator = RAGEvaluator(
    retriever=knowledge,
    embedding_profile=embeddings.profile,
    store=evaluation_store,
    max_concurrency=4,
)
report = await evaluator.run(
    dataset,
    thresholds=RAGQualityThresholds(
        min_hit_rate_at_k=0.9,
        min_recall_at_k=0.8,
        min_mrr_at_k=0.75,
        min_ndcg_at_k=0.75,
        max_irrelevant_retrieval_rate=0.4,
        max_p95_latency_ms=2_000,
    ),
    regression_policy=RAGRegressionPolicy(max_quality_drop=0.03),
    retriever_config={"rrf_k": 60, "minimum_semantic_similarity": 0.15},
)
assert report.gate.passed
```

提供 `RAGAnswerProvider` 时，评测还会计算有效引用率、引用精确率/召回率、来源覆盖率和无引用断言率。未提供答案供应商时只评检索质量，避免为离线检索回归引入额外模型调用。

## 本地开发要求

- Python 3.11+
- Docker Desktop 或兼容的 Docker Compose 环境
- PostgreSQL 16 + pgvector
- Redis 7+

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
docker compose up -d
pytest
```

运行包含 PostgreSQL 的完整知识沉淀、RAG 和评测集成测试：

```powershell
$env:PUBLIC_AGENT_RUN_DB_TESTS="1"
pytest
```

验证 Outbox/Worker 的事务入队、并发领取、heartbeat、过期接管、fencing、重试/dead-letter、跨租户外键和
敏感错误脱敏：

```powershell
python scripts/test_reflection_worker.py
```

验证 Wave 5-7 运维查询、权限、严格游标、expected-version 重试、并发幂等、当前 actor 复核、旧 lease
fencing 和安全追加审计：

```powershell
python scripts/test_reflection_operations.py
```

验证 v0.22 容量判级、阈值治理、RBAC 控制面、漂移告警、真实耗时校准、趋势持久化、分区归档、安全清理、Compose、生产镜像、非 root 用户和容器内迁移：

```powershell
$env:PUBLIC_AGENT_PYTHON_IMAGE = "python:3.12-slim-bookworm"
python scripts/test_production_deployment.py
```

验证领域能力包和运行离线示例：

```powershell
public-agent validate-domain examples/domain_packs/calculator
python examples/run_calculator.py
```

`validate-domain` 同时输出规范化包内容哈希、总容量以及每个 instructions/policy/skill/workflow/evaluation
资产的独立哈希，可作为创建发布草稿前的离线构建证据。

## 设计原则

1. 核心稳定，成长受控。
2. 原始运行记录不可变，记忆和技能全部版本化。
3. 专业智能体由领域能力包生成。
4. 高风险行动和能力升级必须经过审批。
5. 无评测证据，不发布新能力。
