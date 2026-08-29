# ADR 0012：安全文档解析、持久知识导入与管理 API

- 状态：Accepted
- 日期：2026-08-25

## 决策点

如何把用户提供的文本、HTML、PDF 和 DOCX 安全地接入现有 PostgreSQL 混合 RAG，同时满足大文件有界
处理、崩溃恢复、并发幂等、跨租户授权、稳定错误、文档分页和归档后停止召回。

## 候选方案

| 方案 | 优势 | 劣势 | 适用场景 |
|---|---|---|---|
| 上传请求内同步解析、嵌入和发布 | 接口少、实现直接 | 外部嵌入和大文档占住 HTTP，超时后难判断是否发布，无法安全接管 | 小型原型 |
| 只把任务放 Redis，由缓存决定进度 | 吞吐高、队列接入快 | Redis 故障、过期或淘汰会丢事实，无法与知识版本和归档原子约束 | 不采用 |
| PostgreSQL Init-Step-Poll + staging + 最终原子发布 | 可恢复、可审计、可重试，复用租户和版本事实来源 | 增加任务表、staging、租约和客户端轮询 | 当前生产基线 |

解析依赖比较：自写 PDF/DOCX 可以减少依赖，但格式边界、ZIP 安全和文本提取正确性风险高；选择
`pypdf + python-docx + BeautifulSoup` 负责格式读取，框架在调用前后负责全部媒体、容量和安全门禁。

## 决策

选择“安全 allowlist 解析器、PostgreSQL 持久导入任务、分阶段 staging、有限 Step 租约与可替换认证主体”。

### 1. 文档始终是不可信数据

首版只支持 UTF-8 plain text/Markdown、UTF-8 HTML、PDF 和 DOCX。单文件最大 8 MiB，提取文本最大
2,000,000 字符；媒体类型必须和安全叶子文件名扩展一致。PDF 校验 magic、加密状态、页数和可提取文本；
DOCX 校验 ZIP traversal、条目数、解压总体积、单条目/总体压缩比、加密标志和宏文件。HTML 删除
script/style/template，不访问网络、不执行脚本或外部实体。

解析结果统一 NFC、LF 和无 NUL 文本，保存原始文件 SHA-256、parser profile 和有界结构统计。
外部 metadata 不能覆盖 `filename/media_type/source_hash/parser_profile/title` 等保留字段。错误只返回稳定代码
和安全消息，不保存或透传正文、原始二进制、供应商响应或内部路径。

### 2. Init-Step-Poll 是持久状态机

Init 只做上传容量、媒体/扩展、作用域和请求幂等校验，然后创建 `queued/parsing` 任务。同一
`tenant + agent + idempotency_key` 使用事务级 advisory lock；同请求重放返回原任务，异请求冲突拒绝。

Step 在行锁下签发随机 token 和有限租约，按 `parsing -> embedding -> publishing -> completed` 推进：

```text
source bytes
→ bounded parse
→ staging chunks
→ bounded embedding batches
→ immutable document publish
→ succeeded + document_id
```

活动租约拒绝第二个 worker；租约过期后新 worker 可以接管。所有阶段提交都同时校验 token 和到期时间，
旧 worker 只能收到 ownership-lost，不能写进度或把新 owner 的任务标记失败。解析成功或终态失败时清除
原始字节。数据库约束保证 running 必须同时持有 token/lease，succeeded/completed/document ID 一致。

### 3. 发布和文档治理复用现有知识不变量

嵌入先写 `knowledge_ingestion_chunks`，只有全部向量完整且维度/有限值验证通过后才发布。发布器继续使用
来源 advisory lock 和 `tenant + agent + domain + namespace + source_key + version` 唯一约束：同版本同内容
重放返回原文档，同版本异内容拒绝，新版本原子 supersede 旧 active 版本。

文档列表使用 `created_at + id` 降序 keyset 游标，最大页大小 100，游标采用严格 URL-safe Base64、UTF-8、
JSON 精确字段、时区时间和 UUID 校验。归档使用文档行锁，只允许 active -> archived；重复归档幂等，
superseded 版本拒绝归档。RAG 查询只选择 active 文档，因此归档立即停止召回但保留历史和 chunks。

### 4. HTTP 认证和错误边界失败关闭

知识路由只有在知识服务与认证依赖同时配置时才注册。依赖返回 `KnowledgePrincipal(subject, tenant_id,
allowed_agent_ids, permissions)`；tenant 只取该主体，客户端 header/body 不参与 tenant 授权。读操作要求
`knowledge:read`，写操作要求 `knowledge:write`，agent 必须在 allowlist。

API 使用 multipart Init、JSON Step、GET Poll/列表和 POST archive。验证、权限、未找到、幂等冲突、Step
竞争/所有权丢失、文档状态和内部异常均映射到稳定机器码；未知异常只返回通用 500，不泄漏实现细节。
未配置具体认证供应商时，宿主可以注入已有 SSO/API Gateway 主体；框架不会提供不安全的默认身份。

## 反选论证

- 不选同步上传：嵌入延迟、客户端断线和代理超时会形成无法安全判断完成状态的窗口。
- 不选 Redis 作为任务事实来源：任务、进度、文档版本和归档必须由 PostgreSQL 一致决定。
- 不选扩展名或 Content-Type 单独判断：二者均由客户端控制，必须组合解析器预检和格式内部边界。
- 不选自动 OCR 或外部转换服务：首版无网络解析；扫描 PDF 明确失败关闭，避免隐式费用和数据外发。
- 不信任客户端 tenant header：认证主体必须由服务端验证并绑定 tenant/agent/permission。
- 不物理删除归档文档：保留版本、引用、评测和审计证据，检索通过 status 停止召回。

## 接受的代价

- 原始文件在 parsing 前短暂保存在 PostgreSQL 任务行；生产大规模或合规场景需要迁移到加密对象存储，
  PostgreSQL 只保留对象版本和哈希。
- 首版 Step 由 HTTP 客户端或外部 worker 触发，没有队列自动调度、取消端点和租约续期。
- `pypdf` 不提供 OCR 和复杂版面高保真结构；扫描 PDF、表格和图片语义不在首版范围。
- 认证契约已经安全关闭，但具体 Bearer/API Token、用户、角色和撤销存储留到下一阶段。
- Multipart 总请求体上限还需由反向代理/网关设置，应用读取本身严格限制为 8 MiB + 1 字节。

## 撤销条件

- 上传规模、合规或数据库膨胀证明 bytea 不适合暂存时，迁移到加密对象存储；任务状态机和来源哈希不变。
- 接入 PostgreSQL Outbox/队列 worker 后，HTTP Step 变为可选触发器；租约、fencing 和幂等语义保留。
- 领域评测证明 OCR/版面结构是召回瓶颈时，引入隔离解析 worker；`DocumentParser` 输入输出契约保留。
- 多租户规模需要数据库强隔离时增加 PostgreSQL RLS；应用层主体和作用域过滤继续保留为双保险。

## 验证

- 单元测试覆盖四类文档、扩展冲突、非 UTF-8、NUL、容量、加密/超页/空 PDF、ZIP traversal、压缩炸弹和保留 metadata。
- PostgreSQL 集成覆盖并发 Init 幂等、异请求冲突、多批 embedding、唯一发布、跨租户同名 agent、游标分页、superseded 归档拒绝、重复归档和归档后无 RAG 召回。
- 活动 Step 租约拒绝并发 owner；过期租约可接管，旧 token 失败且不能把任务二次标记失败。
- FastAPI 测试覆盖未配置路由 404、可信主体 tenant、读写权限、稳定验证/冲突错误和真实 PostgreSQL Init-Step-Poll。
- Alembic `e95f2c7a6b31` downgrade/upgrade/current/check 通过，数据库实查任务/分块约束和索引完整。
- Ruff、Mypy 56 个源码文件、145 个 PostgreSQL 全量 Pytest、领域包、计算器和中文 RAG 离线示例通过。

## 相关实现

- `src/public_agent/knowledge/parsing.py`
- `src/public_agent/knowledge/ingestion.py`
- `src/public_agent/knowledge/errors.py`
- `src/public_agent/storage/knowledge_management.py`
- `src/public_agent/storage/models.py`
- `src/public_agent/api/knowledge.py`
- `src/public_agent/api/app.py`
- `migrations/versions/e95f2c7a6b31_add_knowledge_ingestion_jobs.py`
- `tests/test_document_parsing.py`
- `tests/test_postgres_knowledge_management.py`
- `tests/test_knowledge_api.py`
