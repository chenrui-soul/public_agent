# ADR 0005: PostgreSQL 混合 RAG 与运行时接入

- 状态：Accepted
- 日期：2026-08-25

## 背景

专业领域智能体需要从可更新、可引用、受权限控制的外部资料中检索事实。现有 `MemoryStore` 保存的是经过运行、反思、评测和人工审批后发布的经验；如果把原始文档直接写入正式记忆，会混淆外部资料与已验证经验，并绕过成长治理边界。

系统已经以 PostgreSQL 作为事实来源并启用 pgvector，因此应在现有模块化单体中打通知识摄取、混合检索、来源引用和运行轨迹，而不是提前引入独立向量数据库。

## 决策

### 独立知识资产模型

新增 `knowledge_documents` 和 `knowledge_chunks`：

- 文档按 `tenant_id + agent_id + domain_id + namespace + source_key + version` 唯一。
- 相同版本内容不可变；重复发布幂等，发布新版本时原子地将旧活动版本设为 `superseded`。
- 文档保存来源、版本、内容哈希、访问标签和元数据。
- 分块保存字符位置、嵌入配置、固定 384 维向量和生成式 `tsvector`。

原始文档不会写入 `memories`，也不会直接进入成长候选。只有后续真实任务轨迹提炼出的经验，才继续走 ReflectionEngine、冲突检测、评测、审批和发布流程。

### 混合检索

PostgreSQL 内执行两路召回：

1. `tsvector @@ tsquery` 全文召回，使用 GIN 索引。
2. `vector <=> query_embedding` 余弦距离召回，使用 HNSW `vector_cosine_ops` 索引。

应用层使用 Reciprocal Rank Fusion 合并两路排名。选择两条简单只读查询而不是单条窗口函数 SQL，是为了让索引路径、测试和未来重排器替换更清晰。接受每次混合检索包含两次数据库查询的代价；如果真实负载证明连接占用或尾延迟不可接受，再以数据库内融合 POC 重新评估。

向量召回设置可配置最低相似度，避免任何查询都被迫得到无关知识。

### 统一运行时接口

运行内核只依赖：

```python
class KnowledgeRetriever(Protocol):
    async def retrieve(self, query: KnowledgeQuery) -> tuple[KnowledgeHit, ...]: ...
```

领域能力包通过 `knowledge_namespace` 和 `knowledge_top_k` 按需启用 RAG。未配置或未注入 retriever 时，现有智能体行为不变。

检索结果进入模型前必须：

- 按租户、智能体、领域、命名空间和访问标签过滤；
- 限制单片段和总上下文容量；
- 标记为不可信数据，禁止文档内容覆盖系统指令；
- 分配 `[K1]`、`[K2]` 等本次运行引用标识；
- 记录实际呈现给模型的来源内容、版本、分数和裁剪状态到运行事件。

领域策略 `require_citations=true` 且存在检索命中时，最终答案必须包含至少一个本次有效 `[Kx]` 引用，否则进入运行时修订步骤。

## 备选方案

### 复用 MemoryStore

未采用。记忆与外部文档在治理状态、版本、ACL、分块和引用方面生命周期不同，复用会造成未经审批的资料与已验证经验混杂。

### 独立向量数据库

未采用。当前规模尚未证明 PostgreSQL 无法满足召回需求，引入 Qdrant、Milvus 或 OpenSearch 会增加一致性、备份、权限和运维成本。

### 单条 SQL 完成融合

未采用。当前收益不足以抵消窗口排名、CTE 和 SQLAlchemy 方言耦合带来的复杂度。

## 后果与限制

- 单部署固定使用 384 维嵌入配置；更换维度需要迁移和重建索引。
- 当前确定性哈希嵌入器只用于离线测试和本地开发，生产必须注入真实嵌入供应商实现。
- PostgreSQL `pg_catalog.simple` 对中文专业分词能力有限；需要中文高质量全文召回时，应增加经过评测的分词配置或 trigram/搜索服务适配器，但保持 `KnowledgeRetriever` 契约。
- 当前访问标签依赖上层认证授权后写入 `RunContext.metadata.authorized_knowledge_access_tags`；管理 API 的身份认证和授权仍是后续工作。
- HNSW 为全局索引并在查询中执行作用域过滤；超大多租户规模下需根据 EXPLAIN 和延迟评估分区或专用检索服务。

## 验证

- 单元测试覆盖分块、嵌入维度、摄取容量、运行时不可信上下文和引用修订。
- PostgreSQL 集成测试覆盖重复摄取、不可变版本、新版本替代、全文与向量命中、访问标签、租户隔离和运行时引用。
- Alembic upgrade/downgrade/upgrade 与 drift check 通过。
- 实查数据库存在 `tsvector` GIN、访问标签 GIN 和向量 HNSW 索引。
