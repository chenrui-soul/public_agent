# ADR 0007: 版本化中文分词与候选重排

- 状态：Accepted
- 日期：2026-08-25

## 背景

现有 PostgreSQL `pg_catalog.simple` 会把连续中文文本视为难以复用的词法单元，查询端逐字构造
`tsquery` 也无法表达领域词语。混合 RAG 虽有向量召回，但缺少稳定中文全文召回与独立重排层，无法
通过领域词典提升专业术语，也无法在不改仓储契约的情况下替换交叉编码器或外部重排服务。

## 决策

### 1. 应用层版本化中文分词

首版使用 MIT 许可的 `jieba` 搜索模式。`JiebaChineseSegmenter`：

- 关闭 HMM，保持离线结果可复现；
- 只输出汉字、字母、数字和下划线词项，去重并限制最大词项数；
- 支持最多 10000 个、单项最长 100 字符的领域自定义词条；
- profile 绑定实现版本、参数与规范化词典哈希。

`knowledge_chunks` 新增 `lexical_text` 和 `lexical_profile`。生成式 `search_vector` 改为基于
`lexical_text`，并继续使用 PostgreSQL GIN。迁移以冻结的默认分词逻辑回填已有分块。词典变化后，
`reindex_lexical` 按租户和智能体作用域，以小批次、行锁和 profile 比较幂等重建派生字段。

### 2. 词法与语义 profile 独立过滤

公共作用域与 ACL 过滤继续同时应用于两条召回分支。全文分支额外匹配当前 lexical profile；向量分支
只匹配 embedding profile 和固定 384 维，不受词典重建进度影响。这样词典升级期间旧词法索引不会混入，
同时仍保留语义召回降级路径。

### 3. 可替换候选重排与安全降级

新增 `KnowledgeReranker` 协议。仓储先对全文和向量候选做 RRF，再将最多 100 个候选交给重排器。
默认 `ChineseHybridReranker` 确定性组合：

- 正文中文词项覆盖率 40%；
- 标题词项覆盖率 20%；
- 语义相似度 20%；
- 原始 RRF 融合分数 20%。

重排有独立超时。仓储验证返回数量、候选唯一性、身份、内容和 `[0,1]` 分数；任何超时、异常、重复、
越界或篡改均降级到原 RRF 顺序，只记录安全错误类型。最终由仓储重新分配 `[Kx]`，运行事件与 RAG
评测结果保存 lexical/reranker profile、重排分数和降级状态。

## 备选方案

### 继续逐字构造 tsquery

不采用。单字召回噪声高，无法稳定表达领域复合词，且查询与文档分词规则无法版本化对齐。

### 直接在仓储 SQL 中写死重排权重

不采用。会把候选召回、排序策略和未来模型重排耦合，难以独立设置超时、质量评测和故障降级。

### 立即引入 Elasticsearch 或专用向量数据库

不采用。当前 PostgreSQL 已是事实来源，现有规模没有证据支持新增集群、一致性、备份和权限成本。

### 首版使用收费交叉编码器 API

暂不采用。首版先建立协议、评测和降级边界；领域回归证明确定性重排不足时再替换实现。

## 影响

正向影响：

- 中文查询与文档使用相同、可审计的分词 profile；
- 领域词典可独立升级并批量重建，不改变 `KnowledgeRetriever`；
- 重排策略可替换，故障不会中断 RAG；
- 运行轨迹和评测报告能够定位词典、召回和重排版本。

代价与限制：

- 摄取和重建增加本地 CPU 成本；
- jieba 对新词、实体和复杂行业文本仍可能不足；
- 迁移需要重建生成列与 GIN 索引，生产执行需维护窗口；
- 默认重排器不是深度语义交叉编码器。

## 撤销条件

- 中文领域评测无法达到召回、MRR/NDCG 或延迟门槛时，在相同协议下替换分词或检索后端；
- 候选池 CPU 或 P95 延迟不可接受时，减少候选、下推特征或接入专用重排服务；
- 领域回归证明本地规则重排不足时，新增版本化交叉编码器实现，但保留候选校验和 RRF 降级；
- PostgreSQL GIN 在真实规模下无法满足写入和查询 SLA 时，再评估独立搜索服务。

## 相关实现

- `src/public_agent/knowledge/segmentation.py`
- `src/public_agent/knowledge/reranking.py`
- `src/public_agent/storage/knowledge.py`
- `migrations/versions/d7b3a1e9f240_add_chinese_segmentation_and_reranking.py`
- `tests/test_segmentation_reranking.py`
- `tests/test_postgres_rag.py`
- `tests/test_postgres_rag_evaluation.py`
