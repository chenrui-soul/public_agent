# ADR 0006: 真实嵌入与 RAG 评测体系

- 状态：Accepted
- 日期：2026-08-25

## 背景

PostgreSQL 全文检索与 pgvector 混合 RAG 已经接入运行时，但原有确定性哈希嵌入只适合离线测试，
无法代表生产语义质量。同时项目缺少可版本化、可持久化、可回归比较的领域 RAG 评测资产，无法在
更换嵌入模型、检索参数或知识版本后判断质量是否退化。

## 决策

### 1. 真实 EmbeddingProvider

实现 `OpenAIEmbeddingProvider`，使用官方异步 Python SDK 调用 Embeddings API。默认模型为
`text-embedding-3-small`，显式传入 `dimensions=384` 与 `encoding_format=float`，保持现有
PostgreSQL `vector(384)` 不变。嵌入 profile 为 `openai:text-embedding-3-small`。

适配器负责：

- 批量输入和本地批次上限；
- 按响应 `index` 恢复输入顺序；
- 拒绝缺失、重复、越界 index、错误维度和非有限数；
- 显式超时和最多有限重试；
- 通过 `SecretStr`/环境变量接收 API Key；
- 将供应商异常转换为不包含响应正文和凭证的安全错误。

`EmbeddingProvider` 增加 `embed_many`，摄取服务使用有界并发批量嵌入。离线测试继续使用
`DeterministicHashEmbeddingProvider`，测试禁止调用真实收费 API。

### 2. RAG 评测资产与指标

评测集使用 YAML 或带 `type=dataset` 头的 JSONL，稳定 `source_key` 作为相关性真值。规范化数据集
内容生成 SHA-256 哈希；案例 ID 必须唯一，文件大小、案例数、top-k 和并发均有上限。

首版确定性指标包括：

- Hit Rate@K、Recall@K、MRR@K、NDCG@K；
- 无关召回率、平均延迟和 P95 延迟；
- 可选有效引用率、引用精确率/召回率、来源覆盖率和无引用断言率。

`RAGEvaluator` 支持绝对质量阈值，并可与同一数据集哈希、嵌入 profile 和维度下的上一成功运行比较。
单案例失败被隔离，只保存安全错误类型。没有 `RAGAnswerProvider` 时只评检索；答案指标阈值不得启用。

### 3. PostgreSQL 是评测事实来源

新增：

- `rag_evaluation_runs`：不可变运行摘要、配置、指标、门禁和基线；
- `rag_evaluation_case_results`：逐案例真值、召回、指标、答案、延迟和错误码。

运行 ID 与完整报告绑定：相同 ID、相同报告哈希重复保存幂等；相同 ID、不同报告拒绝。Redis 不保存
正式评测状态。评测文档和答案不能直接成为正式成长记忆。

## 备选方案

### 在摄取服务中直接调用 OpenAI

不采用。供应商重试、密钥、响应顺序和错误校验会扩散到知识业务逻辑，破坏可替换协议。

### 使用 1536 维默认向量

不采用。会要求迁移现有 `vector(384)`、重建索引并扩大存储；当前模型支持显式降维，384 维可保持
兼容。若领域评测证明质量不达标，再通过新 profile 和完整重建评估更高维度。

### 只在测试代码中写固定断言

不采用。无法保存评测资产版本、历史基线、失败案例和检索配置，也无法形成发布门禁。

### 首版使用模型裁判

暂不采用。模型裁判引入成本、随机性和裁判版本漂移。首版先用稳定真值与确定性引用规则；复杂答案
语义正确性有明确领域需求时，再增加版本化、可校准的裁判协议。

## 影响

正向影响：

- 生产摄取与检索共享真实语义嵌入；
- 嵌入模型和检索参数升级具有可审计质量证据；
- 领域能力包可以携带独立评测集并作为发布门禁；
- 供应商故障和案例错误不会泄露密钥或中断整批评测。

代价与限制：

- 生产嵌入产生外部 API 延迟和费用；
- 单部署仍固定 384 维，切换 profile 必须重建向量；
- 当前引用评测是确定性规则，不判断复杂语义事实性；
- `pg_catalog.simple` 的中文分词与重排质量仍需领域评测推动后续优化。

## 撤销条件

- 统一模型网关提供等价的批量、重试、审计和配置指纹能力时，可用同一协议替换 OpenAI 实现；
- 领域回归证明 384 维质量不足时，以新 profile、迁移和全量重建方式评估更高维度；
- PostgreSQL 的评测明细规模或分析负载不可接受时，可导出分析仓，但 PostgreSQL 继续保存权威摘要；
- 确定性引用指标与人工判断偏差不可接受时，引入版本化模型裁判，同时保留确定性检索指标。

## 资料来源

- OpenAI Embeddings 指南：https://developers.openai.com/api/docs/guides/embeddings
- OpenAI Embeddings API：https://developers.openai.com/api/reference/resources/embeddings/methods/create
- OpenAI API 错误处理：https://developers.openai.com/api/docs/guides/error-codes
- `src/public_agent/knowledge/embeddings.py`
- `src/public_agent/evaluation/rag.py`
- `src/public_agent/storage/evaluations.py`
- `migrations/versions/c31d8e7f4a62_add_rag_evaluation_system.py`
- `tests/test_embeddings_openai.py`
- `tests/test_rag_evaluation.py`
- `tests/test_postgres_rag_evaluation.py`
