# v0.29 治理知识再认证与受控退役闭环计划

## 当前状态

已在 v0.28 全部生产门禁通过后自动启动。Wave 1 已完成领域契约、到期投影、独立职责权限和领域失败测试；
当前 Wave 3 已完成：API、再认证队列、控制台和移动端静态验收。

## Wave 4 实施状态

- `capacity-monitor` 已串接只读 `scan_knowledge_lifecycle`，每轮采样输出
  `knowledge_lifecycle` 聚合（current/due/overdue/quarantined/retired）；不会自动认证、隔离、退役或通知。
- 只读治理演练新增再认证表的生命周期/决定/原因/版本/幂等约束及活动请求、租户状态、复盘索引检查；角色职责分离
  对共享 read 权限保持兼容，state-changing 权限仍要求唯一角色归属。
- 生产 ground truth 新增再认证闭环、退役 RAG 排除、Wave 4 迁移往返三项；门禁 head 更新为 `f1b3c7d9e2a4`。
- README、TECHNICAL_DESIGN、DATABASE_SCHEMA、OPERATIONS_RUNBOOK 和 ADR 0031 已同步再认证、退役、monitor、
  迁移回滚与排障规则。
- 验证：定向测试与全量 Pytest 通过，Ruff/Mypy 通过，Alembic upgrade → downgrade → upgrade 通过；Docker 构建
  可启动但生产脚本在当前网络环境的依赖 wheel 下载阶段长时间无输出，未将该项记为通过。

## Wave 3 验收证据

- 新增治理 API：`GET /knowledge-recertifications`、`POST /postmortems/{id}/recertifications`，以及 review/approve/reject/retire 动作路由。
- API DTO 强制绑定 postmortem/version、knowledge version、内容指纹、质量快照和证据指纹；支持 `Idempotency-Key` 透传。
- 控制台新增再认证队列、状态筛选、评审/退役操作、局部 403 降级和断开清理；加载链路使用 `Promise.allSettled`，不会因再认证权限缺失阻断其他面板。
- 390px 浏览器插件访问本机服务被 `ERR_BLOCKED_BY_CLIENT` 阻断；已完成仓库静态移动端契约检查、DOM/脚本节点检查和无横向溢出 CSS 继承复核，未伪造浏览器实测结果。
- API/控制台定向测试、Wave 1 领域测试、PostgreSQL 治理/RAG 回归、全量离线 Pytest、Ruff 和 Mypy 通过。

## Wave 2 验收证据

- 新增 `reflection_capacity_governance_knowledge_recertifications` 追加事实表，绑定 postmortem/knowledge/质量快照及证据指纹。
- 请求使用 tenant 级幂等键和唯一的 awaiting_review 索引；请求、评审、退役均使用 handler advisory lock、行锁和 expected version。
- certify 仅接受当前 published 且 healthy 的最新质量证据；reject 不改变知识事实；retire 在同一事务将 postmortem 标记为 `retired` 并保留内容、向量、反馈、快照和谱系。
- `PostgresGovernanceKnowledgeRetriever` 继续只召回 `published`，退役提交后立即排除；没有删除历史数据或改变 advisory-only 边界。
- Alembic `e9a2f4c6b810 -> f1b3c7d9e2a4 -> e9a2f4c6b810 -> f1b3c7d9e2a4` 往返通过，当前为 `f1b3c7d9e2a4 (head)`。
- Docker PostgreSQL 健康；完整 Pytest（含数据库）通过，Wave 1 定向测试通过；Mypy 定向源码通过。Ruff 仅剩既有导入排序/新增长行格式问题，未影响功能验证。

## 初始范围

1. 只消费 PostgreSQL 当前 published/quarantined postmortem、最新不可变质量快照、恢复历史和既有 incident 事实。
2. 为已发布治理知识定义有界再认证窗口，派生 `current/due/overdue/quarantined/retired` 运营状态；时间到期只生成
   内部待办或风险事件，不自动修改知识状态。
3. 再认证请求必须绑定精确 postmortem/knowledge version、内容指纹和最新质量证据；请求、独立评审和退役权限分离。
4. 通过再认证只追加新的认证事实和有效期；退役必须人工批准并原子停止 RAG 召回，不物理删除内容、向量、反馈、
   快照、恢复、事件、处置或复盘谱系。
5. RAG 继续是 advisory-only 不可信参考；认证、退役和恢复不能由模型输出、RAG 命中、外部工单或控制台状态决定。

## 行为契约

1. 到期时间必须由版本化策略、最近发布/恢复/认证事实和 UTC 时间确定性派生，客户端不能提交最终状态。
2. `due/overdue` 不等于 unsafe，也不能自动 quarantined/retired；只有受限人工决定可以继续认证或退役。
3. 再认证审批必须重验最新质量快照、当前 published 状态、expected version 和职责分离；陈旧证据或并发变化失败关闭。
4. 退役后统一治理知识检索立即排除当前版本；重新启用必须创建新的受控发布/恢复事实，禁止原地改回 published。
5. 查询、扫描和控制台必须有窗口、样本、返回数和 cursor 上限；截断不得生成“已认证”或“可继续使用”结论。

## Wave

1. 再认证策略/DTO、到期状态、独立权限、受限决定与领域反例。
2. PostgreSQL 认证事实、可逆迁移、并发幂等、退役原子性、RAG 排除和只读演练扩展。
3. API、控制台再认证队列、独立 403、断开清理和 390px 浏览器验收。
4. monitor/事件串接、容量与索引验证、生产 ground truth、ADR、运行手册、全量门禁和发布镜像。

## Wave 1 验收证据

- 新增版本化 `CapacityGovernanceKnowledgeRecertificationPolicy`，窗口为 1 天至 1 年，到期提醒必须短于认证窗口。
- 新增 `current/due/overdue/quarantined/retired` 生命周期状态投影；投影只读，不写入 PostgreSQL，不把到期自动转为隔离或退役。
- 生命周期锚点只接受最近认证、恢复或发布事实；published 缺失锚点、未来时间、naive 时间和非 published/quarantined 状态均失败关闭。
- 新增 `certify/reject/retire` 受限决定 DTO，强制绑定 postmortem/version、knowledge version、内容指纹、质量快照 ID 和证据指纹；决定与理由不兼容时拒绝。
- 新增独立 `read/request/review/retirement` 权限和最小权限角色；请求人不拥有 review，reviewer 不拥有 retirement。
- 新增领域反例测试 `tests/test_knowledge_recertification.py`；定向 Wave 1 + 既有治理测试 38/38，全量离线回归 252 passed / 48 skipped。
- Ruff 目标文件通过；Mypy 91 个源码文件通过。PostgreSQL 集成套件因当前环境无可用数据库未计入本轮通过证据。

## 影响面与规划门禁

- **领域/存储**: `operations.capacity_control`、`storage.capacity_control/models`、治理知识检索和既有 incident 状态机。
- **权限/API**: 新权限不得复用质量评测、恢复或 postmortem review 权限；所有动作继续事务内即时撤权和 global scope 重验。
- **数据库**: 认证与决定使用追加事实；退役状态可逆迁移，不删除历史；downgrade 前导出活动请求和退役决定。
- **依赖**: 无新增第三方库；仓库无 `scripts/build_graph.py`，继续以 `rg` 复核全部模型、检索器、monitor、API 和测试调用方。
- **安全**: 不调用收费 API、不发送真实通知、不自动认证/退役/恢复、不执行生产修复或扩缩容。
- **验证**: 领域反例、PostgreSQL 并发/RAG、API/控制台、390px、迁移往返、全量 Pytest、Ruff、Mypy 和生产门禁。

# v0.28 治理知识质量趋势与复发风险闭环计划

## 当前状态

已完成。Wave 1-4 已通过领域契约、PostgreSQL 有界趋势、三类风险事件扫描、API、控制台、390px、迁移往返、
全量质量门禁、ADR/运行手册和生产镜像验收；v0.29 已自动启动。

## 初始范围

1. 只消费 PostgreSQL 中既有不可变质量快照、postmortem 隔离/恢复历史和当前发布状态，不读取查询正文、提示词或模型输出。
2. 提供按 tenant、handler、assessment 和 UTC 时间窗有界的小时/天质量趋势，游标或桶边界必须绑定全部筛选条件。
3. 使用稳定 rule version 与独立证据指纹识别持续 `unsafe`、重复 `degraded` 和恢复后再次隔离三类风险，并复用现有内部治理事件队列。
4. 新风险只能生成/更新 PostgreSQL 内部事件和只读趋势，不自动生成质量快照、不恢复知识、不撤权、不发布/回滚策略或调整 Worker。
5. API 与控制台按质量读取、事件读取/管理权限独立降级；RAG 仍只把当前 `published` 知识作为 advisory-only 不可信参考。

## 行为契约

1. 趋势查询最多覆盖有界时间窗并限制桶数；空窗返回零值，不得无界扫描全部历史。
2. `unsafe` 风险绑定最新 unsafe 快照；`degraded` 风险要求多个独立证据快照；复发隔离要求 `restore_count >= 1` 且当前再次 quarantined。
3. 规则指纹只绑定稳定规则目标，证据指纹绑定实际快照/版本/时间事实；相同证据幂等，新证据可升级、恢复或复发重开。
4. 事件确认只表示人工接手；只有更新质量事实证明规则不再命中才能恢复，不得把确认或恢复申请当作质量恢复证据。
5. 趋势与事件响应不得返回内部 Principal/Token ID、向量、查询正文、数据库 URL、命令或任意异常正文。

## Wave

1. 质量趋势 DTO、三类风险规则、规则/证据指纹、配置边界和失败测试。
2. PostgreSQL 有界聚合、事件扫描、并发去重、恢复/复发和只读演练扩展。
3. API、控制台趋势面板、事件筛选、独立 403、断开清理和 390px 浏览器验收。
4. monitor 串接、容量/索引验证、Alembic 往返、生产 ground truth、ADR、运行手册和全量门禁。

## 影响面与规划门禁

- **领域/存储**: `operations.capacity_control`、`storage.capacity_control/models`、现有治理事件状态机和只读演练。
- **API/控制台**: `capacity_governance.py`、`capacity_console.py`；趋势只读，事件动作继续事务内即时撤权。
- **数据库**: 优先复用质量快照、postmortem 和治理事件表；只有索引或规则证据无法安全表达时才新增可逆 Schema。
- **依赖**: 无新增第三方库；仓库无 `scripts/build_graph.py`，继续以 `rg` 复核模型、扫描器、monitor、API、控制台和测试调用方。
- **安全边界**: 不调用收费 API、不发送真实通知、不自动生成快照或执行恢复；PostgreSQL 继续是唯一治理事实源。
- **验证**: 领域反例、PostgreSQL 集成、只读演练、API/控制台、390px、全量 Pytest、Ruff、Mypy、Alembic 和生产发布脚本。

## Wave 1 验收证据

- 新增小时/天质量趋势 query/point/report DTO；时间必须带时区且窗口有序，桶数由客户端 `limit <= 366` 与服务端配置双重限制，零快照桶可显式返回零值。
- 新增 `knowledge_unsafe_persistent/v1`、`knowledge_degraded_repeat/v1`、`knowledge_requarantined/v1` 三类规则及固定 Playbook/证据码映射。
- unsafe/degraded 在 7 天有界窗口内按独立 evidence fingerprint 计数，stable fingerprint 绑定 tenant/handler/rule/postmortem，evidence fingerprint 绑定实际快照集合；最新匹配快照作为 source。
- 恢复后再次隔离仅在 `restore_count >= 1`、当前 quarantined 且 `last_quarantined_at > last_restored_at` 时命中，规则指纹跨新版本稳定，证据随新隔离事实变化。
- Settings 增加风险窗口、unsafe/degraded warning/critical、最大快照数和最大趋势桶数边界；领域与配置测试 43/43，Ruff 与 Mypy 定向通过，无新增第三方依赖。

## Wave 2 验收证据

- PostgreSQL 趋势使用 `date_trunc(hour/day)` 对有界窗口聚合，Python 仅在服务端最大桶数内补零；cursor 绑定 bucket、assessment、from/to 和 actor scope，筛选漂移失败关闭。
- incident 扫描在既有 handler advisory lock 与同一事务中读取不可变快照和恢复历史；超过最大快照/复盘数时标记 truncated 并停止质量风险创建与恢复判断。
- unsafe 可从 warning 升级 critical、由更新 healthy 快照恢复并在新 unsafe 事实下复发重开；requarantine 与 degraded 分别由恢复后新隔离事实和多个独立 degraded 快照创建。
- 成功审计失败时风险事件整体回滚，质量快照、反馈、恢复和 postmortem 事实保持不变；重复扫描相同证据不更新版本。
- 新迁移 `e9a2f4c6b810` 扩展 incident/playbook/evidence CHECK，并新增 `(tenant_id, handler_version, captured_at, id)` 趋势/扫描索引；`e9 -> d8 -> e9`、current/check 通过。
- 领域/配置/模型/PostgreSQL 定向测试 60/60，Ruff 通过，Mypy 91 个源码文件通过。

## Wave 3 验收证据

- 新增 `GET /knowledge-quality-trend` 控制台纵向接入，提供 hour/day、assessment 和 24/72/168 小时安全有界筛选；客户端生成 UTC from/to 和不超过 169 的桶上限。
- 趋势面板以纯 DOM 表格呈现 bucket start、total、unsafe、degraded、healthy、insufficient 与 distinct postmortems，不引入第三方图表或新的前端状态事实。
- 事件筛选、固定 Playbook、执行证据与复盘默认分类已扩展到 `knowledge_unsafe_persistent`、`knowledge_degraded_repeat` 和 `knowledge_requarantined`。
- 趋势读取 403 只清趋势面板，不影响质量快照或事件；disconnect 后趋势、事件、快照与处置卡片均清零。
- API/控制台测试 6/6，Ruff、源码 Mypy、Node.js `--check` 通过；390px 实测 `innerWidth=390`、页面 `scrollWidth=375`、趋势正常渲染，独立 403 与断开清理通过，console 零 warning/error。

## Wave 4 最终验收证据

- `.env.example`、生产 Compose 与 Settings 使用一致的 7 个质量风险/趋势变量；27 条唯一生产 ground truth 已加入
  UTC 有界趋势、stable/evidence fingerprint、truncated 失败关闭、新事实恢复和固定 Playbook 契约。
- 只读演练覆盖 `knowledge_quality_snapshot_controls`、captured-time `knowledge_quality_query_indexes`、
  `knowledge_recovery_lifecycle_constraints` 和 `knowledge_recovery_query_indexes`；PostgreSQL 定向回归通过。
- README、技术设计、运行手册和 ADR 0030 已说明七类事件、趋势 DTO/`date_trunc`/补零/cursor、三类风险、阈值、
  排障、巡检、`e9 -> d8` 回滚、反选理由和撤销条件。
- 定向领域/配置/模型/API/PostgreSQL 测试 72/72；全量 PostgreSQL Pytest 291/291；生产子集 116/116。
- Ruff 全仓通过；Mypy 91 个源码文件通过；外置控制台 JavaScript `node --check` 通过；Compose config 通过。
- Alembic `e9a2f4c6b810 -> d8f1c2a4b730 -> e9a2f4c6b810`、current 和 check 通过。
- 干净生产镜像通过 UID/GID 10001、`pip check`、全部 CLI 和容器 Alembic head；镜像 digest
  `sha256:74ca38d5b43d961ba9795c0e5e3db45b1f102f480adc3bae26e89b2575402964`。构建期间 PyPI SSL EOF 自动有限重试后成功，
  未调用收费 API、真实通知或生产自动修复。

## 验收标准

1. 越界时间窗/桶数、跨 tenant/handler、陈旧 cursor、重复证据和无权限全部失败关闭。
2. 三类风险可确定性创建、去重、升级、恢复与复发重开；无新质量事实不能关闭事件。
3. 趋势查询有界并命中索引；扫描失败不修改知识、反馈、快照或恢复事实。
4. 控制台趋势与事件面板独立降级、断开清理且 390px 无横向溢出；生产门禁不调用付费 API 或真实通知。

# v0.27 治理知识质量反馈与安全隔离闭环计划

## 当前状态

已完成。Wave 1-4 全部通过 PostgreSQL、控制台、浏览器、迁移往返、全量质量门禁和生产镜像验收。已落地
`已发布治理知识 -> 受限反馈 -> 独立复核 -> 原子隔离 -> RAG 立即排除`
纵向链路，并补充隔离时同版本待复核反馈原子 `superseded`、事务失败整体回滚以及 feedback 数据库控制只读演练。
控制台反馈队列、移动端局部权限降级和浏览器验收已完成；下一步实现质量评测快照、隔离保留/恢复治理、生产门禁和文档收口。

## 初始范围

1. 反馈只能绑定当前 tenant、当前 handler 下已发布的治理复盘及其精确知识版本和内容指纹。
2. 反馈使用受限信号与原因分类，不保存查询正文、提示词、模型输出、凭据或任意命令。
3. 反馈报告人与复核人职责分离；确认安全问题时，反馈确认与复盘知识隔离必须在同一 PostgreSQL 事务完成。
4. 被隔离的复盘保留完整来源谱系和知识内容，但统一 `KnowledgeRetriever` 必须立即排除，不能继续进入运行时上下文。
5. 延续独立 RBAC、即时撤权、expected version、严格分页、安全投影、追加审计和可逆迁移。

## Wave

1. 反馈领域契约、独立权限、受限分类、PostgreSQL 模型和失败测试。
2. 独立复核、职责分离、确认后原子隔离和 RAG 排除。
3. API、控制台反馈队列、移动端局部降级与浏览器验收。
4. 质量评测快照、保留/恢复治理、生产门禁、ADR 和运行手册。

## Wave 1-3 验收证据

- PostgreSQL 纵向子集 30/30、全量 PostgreSQL Pytest 277/277、控制台 API 静态测试 6/6。
- Ruff 全仓、Mypy 91 个源码文件、内联 JavaScript `node --check`、Alembic current/check 全部通过；当前 head `c7a4d2e9f610`。
- 桌面与窄屏控制台覆盖反馈提交、`safety_concern -> unsafe_content` 受限原因、反馈面板独立 403、断开清理和零横向溢出。
- 生产发布脚本通过容量治理子集、Compose、干净镜像、非 root UID/GID 10001、`pip check`、CLI 与容器 Alembic head；镜像摘要 `sha256:75ec4197450a7506b4852f95aad76f06209866997b8576928f603bd25d0d3406`。

## Wave 4 行为契约与规划门禁

1. 质量评测快照必须绑定精确 postmortem/knowledge/version/fingerprint，并对反馈 ID、版本和终态生成独立证据指纹；同一证据重复评测幂等返回同一快照。
2. 评测状态仅由受限反馈聚合派生：确认安全问题为 `unsafe`，确认负面多于正面为 `degraded`，无已确认质量反馈为 `insufficient`，否则为 `healthy`；快照不可更新或删除。
3. 隔离知识至少保留 24 小时，内容、向量、来源谱系和确认安全反馈均不物理删除；恢复只能以结构化 `false_positive` 理由绑定当前隔离版本及其最新质量快照发起。
4. 恢复请求人与审批人必须分离；审批人还不得是原安全反馈报告人或确认人。批准、恢复为 `published`、生成新 knowledge version、递增 postmortem version/restore count 与成功审计必须同事务提交。
5. 恢复后旧反馈和旧快照继续保留，新反馈只能绑定恢复后的新 postmortem/knowledge version；RAG 仅重新检索当前 `published` 版本。

- **影响面**: `operations.capacity_control`、`storage.models/capacity_control/governance_knowledge`、治理 API/控制台、Alembic、PostgreSQL/API/静态测试、生产 ground truth 与文档。
- **数据库回滚**: downgrade 先删除恢复/快照索引和表，再移除 postmortem 隔离来源列并恢复 v0.27 约束；不触碰既有反馈、复盘内容或审计事实。
- **依赖/安全**: 无新增第三方库；仓库无 `scripts/build_graph.py`，以 `rg` 复核全部调用方；所有写动作 expected-version、行锁、handler advisory lock、事务内即时撤权和追加审计失败关闭。
- **验证路径**: 先写领域与 PostgreSQL 反例，再实现迁移/存储/API/控制台，随后执行迁移往返、只读演练、RAG、全量 Pytest、Ruff、Mypy、浏览器与生产发布脚本。

## 验收标准

1. 未发布、跨 tenant、旧知识版本/指纹、重复报告、自复核、撤权和旧 expected version 全部失败关闭。
2. 驳回反馈不改变检索；确认安全反馈与知识隔离原子提交，事务失败不得留下半隔离状态。
3. 隔离前可检索、隔离后同一 namespace/domain/access tag 下立即零命中；历史内容与来源谱系不物理删除。
4. API/审计不返回 Token/Principal 内部 ID、查询正文、提示词、模型输出、数据库 URL 或命令正文。

## Wave 4 最终验收证据

- 全量 PostgreSQL Pytest 282/282；生产容量治理子集 107/107；Ruff 全仓与 Mypy 91 个源码文件通过。
- Alembic `d8f1c2a4b730 -> c7a4d2e9f610 -> d8f1c2a4b730` 往返、current 和 check 通过。
- 控制台 API 6/6、模型 16/16、内联 JavaScript `node --check` 通过；质量与恢复面板各自支持独立 403 和断开清理。
- 390px 浏览器实测 `innerWidth=390`、页面 `scrollWidth=375`，质量/恢复卡片正常渲染且 console 零 warning/error。
- 26 条生产 ground truth、Compose、非 root UID/GID 10001、pip check、CLI 和容器 Alembic head 全部通过。
- 生产镜像 digest：`sha256:9ffe68b5c66facbc02ce427d766c55f64532dc55d2777cda40b375b298ab9808`。

# v0.26 治理事件复盘与知识沉淀闭环计划

## 当前状态

已完成。verified 处置事实已能转化为结构化复盘候选，经独立评审后原子发布为可检索但不自动执行的治理知识资产。

## 初始范围

1. 仅允许 verified 处置单创建复盘；根因、影响、预防措施采用受限分类和有界安全摘要。
2. 复盘请求人与评审人分离，复盘不能修改事件、处置、策略或权限事实。
3. 经评审的复盘发布到独立治理知识 namespace，保留 incident/remediation/version 来源谱系和内容指纹。
4. RAG 只能作为值守参考，不能把历史处置文本当作授权、恢复证据或自动执行指令。
5. 延续 PostgreSQL 唯一事实源、即时撤权、expected version、追加审计、严格分页和可逆迁移。

## 行为契约

1. 复盘请求必须绑定 `verified` 处置单、同一事件复发周期，以及创建时的 incident/remediation 版本快照；来源事实变化后评审失败关闭。
2. 每个处置单最多一条复盘；内容指纹覆盖来源谱系、受限分类与规范化摘要，重复提交不能产生第二份知识资产。
3. 根因、影响和预防措施只能使用枚举分类；摘要限制长度并拒绝凭据、Authorization、连接串、代码块、Shell/SQL/编排命令等任意执行指令。
4. 复盘请求人与评审人必须是不同 Principal；评审通过与治理知识发布在同一 PostgreSQL 事务内完成，拒绝不发布知识。
5. 治理知识固定进入 `operations.governance.postmortems` namespace，采用中文词法索引与离线确定性向量；检索必须显式携带治理 advisory access tag。
6. 治理知识命中只返回值守参考和来源引用，metadata 明确 `advisory_only=true`、`authorization_source=false`、`recovery_evidence=false`、`execution_instruction=false`。

## Wave

1. 领域契约、权限角色、受限分类、安全摘要和内容指纹失败测试。
2. PostgreSQL 复盘模型、可逆迁移、状态机、并发去重与来源版本重验。
3. 独立 `KnowledgeRetriever` 混合检索、来源谱系与运行时非指令边界测试。
4. API、响应式控制台、只读演练约束、浏览器和生产发布门禁。

## 影响面与规划门禁

- **领域/权限**: 扩展 `operations.capacity_control` 与默认可管理权限；不复用处置审批权限，避免隐式授权。
- **存储/迁移**: 新增单表复盘与知识索引，向治理审计增加 postmortem 外键；只新增结构，downgrade 先删索引/约束再删表和列。
- **RAG**: 新增独立治理知识检索器，不改变现有 agent/domain 知识表；调用方必须显式选择 namespace、domain 和 advisory tag。
- **API/控制台**: 新增有界列表、详情、创建、批准发布、拒绝端点及独立 403 局部降级；响应不投影 Principal/Token 内部 ID 或向量。
- **验证**: 选定领域、PostgreSQL、API、RAG、浏览器、Alembic 往返、Ruff、Mypy、全量 Pytest 和生产脚本。
- **依赖分析**: 仓库无 `scripts/build_graph.py`，本 Wave 使用 `rg` 对模型、权限、服务、API、控制台、迁移和测试引用逐项复核。

## 验收标准

1. 非 verified、旧版本、错误周期、自评审、重复复盘、敏感摘要和任意指令全部失败关闭。
2. 评审通过后复盘与知识索引原子发布；拒绝、冲突或撤权不留下可检索知识。
3. RAG 仅在固定 namespace/domain/access tag 下返回同 tenant 已发布内容，并带完整 incident/remediation/version/fingerprint 谱系。
4. 迁移可逆、游标绑定筛选和 actor scope、审计追加写、移动端无横向溢出，生产门禁不调用收费 API或真实外部通知。

## 最终验收证据

- 全量 PostgreSQL Pytest 276/276；生产容量治理子集 101/101；Ruff 全仓与 Mypy 91 个源码文件通过。
- Alembic `b6d8e1f3a420` 为当前 head，`alembic check` 无漂移；此前往返 `b6d8e1f3a420 -> 9f4e7c2d1a60 -> b6d8e1f3a420` 通过。
- 390px 控制台覆盖创建复盘、自评审/只读批准局部 403、独立 reviewer 发布、断开清理、零横向溢出和零 console error/warn。
- Compose、24 条唯一 ground truth、非 root UID/GID 10001、pip check、容器 CLI 与 Alembic head 全部通过。
- 生产镜像 digest：`sha256:6b910dfd1ff64fbc3abdd88d009bc57e3d11784752c0b53a5402ceeecabff2cc`。

# v0.25 治理事件处置审批与恢复验证闭环计划

## 当前状态

已完成。v0.24 的内部事件检测、确认、恢复与复发重开已完成浏览器和生产发布门禁；本阶段继续把已确认事件
推进为 PostgreSQL 权威的结构化处置单，并已形成
`事件确认 -> 固定 Playbook 请求 -> 职责分离审批 -> 安全执行结果码 -> 新恢复事实验证 -> 追加审计`
纵向闭环。

## 本阶段目标与边界

1. 每个事件复发周期最多一个处置单；Playbook 与事件信号固定映射，客户端不能提交任意命令或脚本。
2. 独立 read/request/approve/execute/verify 权限；请求人不能审批，执行人不能验证。
3. 执行步骤只记录枚举结果与安全证据码，不自动撤权、改 Schema、发布/回滚策略、扩缩容或调用外部系统。
4. 只有事件在执行后出现更高版本的新恢复事实并进入 resolved，处置单才能 verified；确认或人工声明不能代替恢复。
5. API、控制台、严格 cursor、即时撤权、expected version、行锁、handler advisory lock、追加审计和可逆迁移全部纳入。

## Wave

1. 领域契约、固定 Playbook、权限角色和状态机反例。
2. PostgreSQL 模型、迁移、并发与恢复事实验证。
3. API、控制台处置队列和各权限面板局部降级。
4. 全量回归、Alembic 往返、浏览器、生产门禁、ADR/运行手册与记忆收口。

## 验收标准

1. 错误 Playbook、未确认事件、旧版本、自审批、自验证、未恢复或旧恢复事实全部失败关闭。
2. 同一事件周期并发创建不重复；事件复发进入新 cycle 后可新建处置单。
3. 响应与审计不包含 Token/Principal 内部 ID、命令正文、Authorization、数据库 URL 或异常正文。
4. PostgreSQL 继续是唯一事实源；不调用收费 API、不发真实通知、不自动执行生产变更、不提交或推送。

## 最终验收证据

- 全量 PostgreSQL Pytest 273/273；生产容量治理子集 98/98；Ruff 全仓与 Mypy 90 个源码文件通过。
- Alembic `9f4e7c2d1a60 -> 6b9d2f4a8c71 -> 9f4e7c2d1a60` 往返、current/check 通过。
- 浏览器覆盖 390px 请求/审批/执行/验证四个最小权限身份、局部 403、未恢复 409、恢复后 verified 和零新 console error。
- Compose、ground truth、非 root UID/GID 10001、pip check、容器 Alembic head 全部通过。
- 生产镜像 digest：`sha256:a96bb3a6c6802d9bef2705dd8b84927a4c6b4948c1065f21a6a3c280e2175c00`。

# v0.24 治理异常检测与内部事件响应闭环计划

## 当前状态

已完成。完成 v0.23 验收后的现状审计与范围收敛，并通过领域、PostgreSQL、API、控制台、迁移和生产发布门禁。

## 本阶段目标

在不接入邮件、短信、Webhook 或外部事件平台的前提下，把 v0.23 的 append-only 审计、告警 SLA、复发次数和
只读演练证据聚合为 PostgreSQL 内部治理事件队列，形成
`有界扫描 -> 规则命中 -> 指纹去重 -> 分级 -> 认领/确认 -> 新事实恢复 -> 自动关闭/复发重开 -> 追加审计`
纵向闭环。

## 默认范围

### In scope

1. 检测有界时间窗内 denied/conflict 审计突增、SLA breached 告警、重复 reopen 和演练检查失败。
2. 独立治理事件表、规则/证据指纹、warning/critical、open/acknowledged/resolved、版本和复发计数。
3. 独立 `operations.capacity_incidents:read/manage` 权限，事务内重验当前 Principal/Token/global scope。
4. 有界、actor/filter-bound keyset API 和控制台内部事件队列；安全投影不包含 Token、异常正文或原始凭据。
5. 成功状态变更同事务审计，拒绝/冲突独立追加；扫描幂等，确认不等于恢复，无新证据不得关闭。
6. 可逆迁移、PostgreSQL 反例、浏览器烟雾、生产门禁、ADR 和运行手册。

### Out of scope

- 真实外部通知、值班排班、PagerDuty/飞书/钉钉/Slack/SIEM 接入。
- 自动撤权、自动修复数据库对象、自动回滚/发布策略或自动扩缩容。
- 物理删除审计、告警或事件历史，以及外部 WORM/对象存储导出。

## 行为契约

1. 每条事件必须绑定可验证的 PostgreSQL 事实窗口和稳定规则版本，不能由控制台或客户端直接声明异常。
2. denied/conflict 突增按 tenant + handler + rule + bounded bucket 指纹去重；SLA/reopen/drill 事件绑定目标事实 ID。
3. 确认只表示人工接手；只有更新事实证明规则不再命中时才自动 resolved，相同信号复发时重开。
4. 扫描和查询必须有最大窗口、最大样本和最大返回数；不得全表无界聚合。
5. 事件管理权限不隐式包含容量请求、审批、发布、回滚、告警管理或审计读取权限。
6. PostgreSQL 继续是唯一事实源；外部通知后续必须消费内部事件，不得成为事件状态权威来源。

## 影响面与规划门禁

- **领域与权限**：在 `operations.capacity_control` 延续既有容量治理术语，新增事件信号、状态、严重度、查询、扫描报告、稳定规则版本与独立 `operations.capacity_incidents:read/manage` 权限；auditor 不自动获得事件权限。
- **存储与迁移**：新增 PostgreSQL 治理事件表、唯一指纹、生命周期 CHECK、查询索引和审计事件 `incident_id` 外键；迁移只新增结构并提供完整 downgrade，不删除既有事实。
- **扫描与并发**：复用 handler 级 PostgreSQL advisory lock；审计使用固定时间桶，告警使用持久化 version/updated_at，演练使用本次检查时间作为新事实；所有读取有上限。
- **API 与控制台**：严格 cursor 绑定 actor、handler、status、severity、signal；响应不投影 token/principal 内部 ID、原始异常正文或未知 evidence 键；各面板按权限独立降级。
- **运行时**：`capacity-monitor` 在持久化观测、漂移扫描之后执行事件扫描；不发送真实通知，不自动撤权、修复 Schema、发布/回滚策略或调整副本。
- **验证**：领域反例、PostgreSQL 状态机/并发/撤权、API 安全投影、控制台静态与浏览器烟雾、模型、配置、Alembic 往返/current/check、Compose、全量 Pytest、Ruff、Mypy 和生产门禁。
- **结构分析降级**：仓库无 `scripts/build_graph.py`；已用 `rg` 复核容量领域、认证 allowlist、模型/迁移、API 装配、控制台、monitor 和测试调用方，后续以 grep 结果为准。
- **回滚**：先停止 API 与 capacity-monitor，回退应用镜像隐藏事件入口，再 downgrade 删除审计外键/事件表；既有告警与审计事实不删除。
- **安全边界**：无新依赖、无外部系统、无生产数据写入；DDL 已包含在用户确认的 v0.24 自动实施范围内，且只在本地测试库执行往返验证。

## Wave 执行计划

1. **Wave 1 / 规则契约与反例**：定义事件 DTO、规则版本、指纹、阈值和职责权限；先补边界/去重/权限测试。
2. **Wave 2 / PostgreSQL 状态机**：迁移、索引、扫描、去重、升级、确认、恢复、重开和追加审计。
3. **Wave 3 / API 与控制台**：安全分页、事件详情/确认、控制台队列和局部权限降级。
4. **Wave 4 / 生产治理**：monitor 接入、容量上限、并发扫描、迁移往返、只读演练扩展和生产门禁。
5. **Wave 5 / 审查与沉淀**：全量验证、ADR/运行手册/记忆收口；通过后自动进入 v0.25。

## 验收标准

1. 四类信号均有正反例；重复扫描不重复建事件，新事实可升级/恢复/复发重开。
2. 事件读写权限独立，撤权即时生效，失败动作不推进版本；cursor 不能跨 actor/filter/signal 重放。
3. 响应与审计不含 Token ID、Authorization、数据库 URL、原始异常正文或无界证据正文。
4. 扫描使用有界 PostgreSQL 查询和索引；并发相同扫描不产生重复 open 事件。
5. Ruff、Mypy、全量 PostgreSQL Pytest、Alembic 往返/current/check、Compose、浏览器和生产镜像门禁通过；
   不调用收费 API、不发真实通知、不自动扩缩容、不提交或推送。

# v0.23 治理审计查询、告警 SLA 与运营演练闭环计划

## 当前状态

已完成。v0.22 已具备追加审计、RBAC 控制台和漂移告警，但审计事实只能由数据库人员查询，告警没有显式响应
时限状态，生产值守也缺少一个不修改业务数据的治理演练入口。v0.23 将补齐
`审计事实 -> 最小权限查询 -> 控制台复核 -> SLA 升级 -> 只读演练报告` 纵向链路。

## 项目启动信息

- **项目类型**：生产治理审计 API、原生 Web 控制台、PostgreSQL 只读演练
- **业务目标**：让独立审计员和告警值守人员无需数据库权限即可复核治理动作、识别响应超时并验证关键控制面
- **运行环境**：Python 3.11+、FastAPI、PostgreSQL、Docker Compose、现有 API Token RBAC
- **终极功能**：审计查询不泄露 Token；游标不可跨筛选/actor 重放；告警 SLA 可判级；只读演练能失败关闭
- **技术约束**：PostgreSQL 继续为唯一事实源；不发送真实外部通知；不修改 Worker 副本；不调用收费 API
- **自主续推**：本阶段验收通过后直接定义并启动 v0.24，不再逐阶段请求确认

## 默认范围

### In scope

1. 新增独立 `operations.capacity_audit:read` 权限和 auditor 最小权限角色，不隐式授予治理写权限。
2. 新增按 actor subject、action、outcome 和 UTC 时间窗过滤的有界审计 API；严格 keyset cursor 绑定当前 actor、
   handler version 和全部筛选条件。
3. 审计响应仅投影 actor subject、动作、结果、目标资源 ID、白名单安全元数据和时间；不返回 Token ID、摘要、
   Authorization、数据库 URL 或内部异常正文。
4. 告警响应根据 first_seen/acknowledged/resolved 事实派生 `within_sla/due/breached/resolved` 状态和响应截止时间；
   SLA 阈值由有界 Settings 配置，状态不作为授权或自动关闭依据。
5. 控制台增加 SLA 标记和审计历史筛选面板；保持原生 DOM API、无 `innerHTML`、Token 仅 sessionStorage。
6. 新增受审计权限保护的只读治理演练报告，验证当前 actor 即时重验、角色职责分离、审计 append-only trigger、
   告警 lifecycle CHECK 和关键索引存在；任一证据缺失则报告失败，不自动修复。
7. 为审计过滤增加可逆 PostgreSQL 索引，补领域/API/PostgreSQL/浏览器静态/生产门禁测试和运维文档。

### Out of scope

- 邮件、短信、钉钉、飞书、PagerDuty、Webhook 等真实通知发送。
- 自动撤销 Token、自动修复约束、自动扩缩容、自动发布或回滚容量策略。
- 对象存储/WORM 导出、SIEM 接入、OIDC/SSO 和跨系统组织审批编排。

## 行为契约

1. auditor 只能读取治理审计与只读演练，不能创建、审批、发布、复核、回滚请求或确认告警。
2. 每次审计查询和演练均在当前 PostgreSQL 事务内重验 tenant、active Principal、Token、permission 和 global scope。
3. 审计游标必须绑定 actor principal、handler version、actor/action/outcome/time filters；非规范 Base64、未来时间窗、
   `from > to`、跨筛选或跨 actor 重放必须失败关闭。
4. actor 筛选使用完整 subject 精确匹配，action 使用受限长度精确匹配；所有 SQL 参数化，列表最多 100 条。
5. 审计安全投影不包含 `actor_token_id`；`safe_metadata` 只允许既有标量白名单，未知键不返回。
6. SLA 状态只根据持久化时间事实和服务端当前 UTC 计算：resolved 始终 resolved；已确认但未恢复为 within_sla；
   未确认告警在 warning deadline 前 within_sla、之后 due、critical deadline 后 breached。
7. 演练报告是只读证据快照；不得创建临时 Principal/Token、修改告警/请求、禁用约束或声称已发送通知。
8. 控制台不是授权依据；按钮和筛选只改善 UX，后端 RBAC 仍是唯一判定。

## 影响面与规划门禁

- **领域/API**：`operations.capacity_control`、`api.capacity_governance`、`api.capacity_console`。
- **存储/配置**：`storage.capacity_control/models`、`config`、`api.app`、Alembic 新 head。
- **权限**：`auth.management` 通过容量权限 allowlist 自动纳入新权限；既有 Principal 不自动获得权限。
- **验证**：领域、配置、API、PostgreSQL、模型、迁移、控制台静态安全和生产发布脚本。
- **结构分析降级**：仓库无 `scripts/build_graph.py`；已使用 `rg` 复核容量控制、认证审计查询、模型、迁移、
  API 装配、控制台和测试调用方。
- **回滚**：先回退应用镜像隐藏新 API/控制台字段，再 downgrade 删除新增索引；既有审计和告警事实不删除。
- **安全门禁**：无外部系统和新依赖；不执行生产数据写入；迁移仅新增可逆索引；游标、权限、敏感投影、XSS、
  SQL 参数化、并发读取和 catalog 演练均纳入反例。

## Wave 执行计划

1. **Wave 1 / 契约与测试**：新增审计/SLA/演练 DTO、权限角色和失败测试，固化游标、投影和职责分离契约。
2. **Wave 2 / PostgreSQL 审计链路**：实现 actor join、过滤、严格 cursor、安全元数据投影和审计查询索引。
3. **Wave 3 / SLA 与控制台**：配置有界 SLA 阈值，派生告警运营状态，增加审计筛选和 SLA 视觉标记。
4. **Wave 4 / 只读治理演练**：实现 catalog 证据检查与 API，覆盖撤权重验、缺失约束/索引失败关闭和零业务写入。
5. **Wave 5 / 安全审查与交付**：全量测试、Ruff、Mypy、Alembic 往返/current/check、Compose、浏览器烟雾、
   生产门禁、ADR/运行手册/记忆收口；通过后自动进入 v0.24。

## 验收标准

1. 无 audit 权限返回 403 且不泄露记录；撤销/禁用后同一 Token 下一次查询立即失败。
2. 审计分页有界稳定，actor/action/outcome/time/cursor 任一篡改不能跨作用域读取；响应不含 Token ID 或敏感键。
3. SLA 边界覆盖 warning 前、warning 后、critical 后、acknowledged 和 resolved，时区统一为 UTC。
4. 控制台能安全筛选审计并显示 SLA；无 `innerHTML/localStorage`，小屏无新增横向页面溢出。
5. 演练报告能证明角色分离、当前 actor 重验、append-only trigger、lifecycle CHECK 和索引存在；证据缺失时
   `passed=false`，且整个演练不改变治理业务表。
6. Ruff、Mypy、全量 PostgreSQL Pytest、Alembic downgrade/upgrade/current/check、Compose 和生产镜像门禁通过；
   不调用真实收费 API、不发真实通知、不自动扩缩容、不提交或推送代码。

## 最终验收证据

- 全量 PostgreSQL Pytest：262/262 通过；生产容量治理门禁：87/87 通过。
- Ruff 全仓通过；Mypy 90 个源码文件通过；生产 Compose config 通过。
- Alembic `e3c8a1f7b920 -> 2d6f8b1c4a90 -> e3c8a1f7b920` 往返通过，`current` 为 head，
  `alembic check` 无漂移。
- PostgreSQL 反例覆盖 auditor 职责分离、撤权即时生效、actor/action/outcome/time 过滤、cursor filter/scope
  篡改、Token ID 与未知 metadata 脱敏、只读演练零业务写入和 catalog 证据全部通过。
- SLA 单元边界覆盖 within_sla、due、breached、acknowledged、resolved；配置顺序失败关闭。
- 浏览器烟雾覆盖默认桌面与 390px 小屏，无页面级横向溢出、无 console error；审计筛选、SLA、只读演练、
  弹窗焦点和按权限局部降级通过；无 `innerHTML/localStorage`。
- 干净生产镜像以 UID/GID 10001 运行，`pip check` 无断依赖，容器 Alembic head 为 `e3c8a1f7b920`；本地
  镜像 digest `sha256:d7b8026ecb707c746d8b226f88607019e6325f9ca13f1e26742ffa04f9d2e68e`。
- 未调用收费 API、未发送外部通知、未自动扩缩容、未提交/推送代码或镜像。

# v0.22 RBAC 审批控制台、策略漂移检测与治理告警计划

## 当前状态

已完成。v0.22 已把 CLI 审计标签升级为真实 Bearer Principal 权限控制，交付可直接运行的响应式审批控制台，
并利用容量观测中持久化的实际阈值快照形成
`检测 -> 去重告警 -> 人工确认 -> 恢复验证 -> 自动关闭/复发重开` 闭环。

## 项目启动信息

- **项目类型**：生产管理 API、Web 审批控制台、后台治理监测任务
- **业务目标**：让安全管理员、审批人、发布人和告警值守人员在不接触数据库的情况下治理容量策略
- **运行环境**：Python 3.11+、FastAPI、PostgreSQL、Docker Compose、现有 API Token 认证
- **终极功能**：越权动作失败关闭且不改变状态；控制台可完成审批主链路；漂移告警可去重、确认和自动恢复
- **技术约束**：PostgreSQL 继续为唯一事实源；不引入前端构建链或外部告警服务；不调用真实收费 API
- **默认循环轮次**：3；**安全最大轮次**：6；**每轮最大改动点数**：3

## 默认范围

### In scope

1. 容量治理细粒度权限和推荐角色模板；每个动作在事务内重新验证 tenant、Principal、Token 和权限。
2. 容量策略请求/告警 JSON API，以及无前端构建依赖的响应式 Web 审批控制台。
3. 基于容量观测 `thresholds` 快照和当前 active 策略指纹的漂移扫描、去重、升级、确认、恢复和重开。
4. 生产 `serve` 入口装配认证管理、反思任务运维和容量治理控制面；Token pepper 使用独立 Secret。
5. 可逆迁移、审计、配置、Compose、测试、运行手册、ADR 和生产发布门禁。

### Out of scope

- 邮件、短信、钉钉、飞书、PagerDuty 等外部通知通道。
- 自动修改 Worker 副本、Compose、`.env`、Kubernetes 或外部配置中心。
- OIDC/SSO、独立前端构建系统和组织审批工作流引擎。

## 行为契约

1. 权限拆分为容量只读、请求、审批、发布、复核、回滚，以及告警读取和管理；客户端不能提交 operator 身份。
2. 容量治理只允许配置的治理 tenant，且 Principal 必须为 `all_agents`；每个写动作在同一事务内重新验证
   active tenant/Principal、未撤销未过期 Token 和当前权限。
3. 请求人不能审批自己的请求；审批、发布、复核和回滚继续复用 v0.21 expected version、锁和状态机。
4. 控制台不把 Token 放入 URL、Cookie、日志或持久存储；仅在当前标签页会话中保存，并以 Bearer 调用 API。
5. 漂移定义为最近有界窗口内观测到的阈值指纹与当前 active 策略（无 active 时为 Settings fallback）不一致。
6. 漂移少于最小样本不告警；达到门槛后按 expected/observed 指纹确定性去重，持续样本可升级严重度。
7. 告警支持 `open -> acknowledged -> resolved`；漂移消失且存在新观测时自动 resolved，复发时重开并累加次数。
8. 告警列表和请求列表必须有界并使用绑定 actor/filter 的 keyset cursor；响应不含 Token、digest、数据库 URL
   或内部异常正文。
9. 控制台必须覆盖 loading、empty、error、403、409、键盘焦点和危险动作二次确认。

## 架构决策

| 方案 | 优势 | 代价 | 结论 |
|---|---|---|---|
| 新建独立 IAM/前端/时序告警系统 | 生态完整 | 三个新事实源、部署和恢复面过大 | 反选 |
| 仅给 CLI 增加角色字符串 | 改动小 | 身份不可验证、撤销不即时、无法防越权 | 反选 |
| 复用 API Token RBAC + PostgreSQL 观测/告警 + 原生 Web 控制台 | 事务、审计、部署边界统一 | 首版无 SSO 和外部通知 | 采用 |

- **依赖方向**：`api -> operations contracts -> storage`; 控制台只调用公开 HTTP API，不访问数据库。
- **数据归属**：容量请求/策略继续归 `capacity_governance`；漂移告警和治理审计归新增 control-plane 存储模块。
- **回滚**：先回退应用镜像并停用控制台/漂移扫描；导出新增告警和审计后 downgrade 到 v0.21 head。
- **撤销条件**：组织审批、跨主机配置传播或通知 SLA 无法由当前控制面满足，且替代系统能保留单一发布序列、
  即时撤权、审计和 exact rollback 时重新评估。

## Wave 执行计划

1. **Wave 1 / 契约与迁移**：权限、角色模板、漂移/告警 DTO、模型、索引、不可更新审计和可逆迁移。
2. **Wave 2 / 授权控制面**：事务内 actor 重验、请求/告警有界查询、治理动作包装和安全错误码。
3. **Wave 3 / 漂移闭环**：观测指纹扫描、去重告警、严重度升级、确认、自动恢复和 monitor 接入。
4. **Wave 4 / API 与控制台**：FastAPI 路由、生产管理应用装配、无构建依赖控制台和安全响应头。
5. **Wave 5 / 反例与交付**：越权/撤权竞态、自审批、cursor 篡改、漂移去重/恢复/重开、迁移和生产门禁。

## 验收标准

1. 缺失/错误/撤销/越权 Token 分别安全返回 401/403，任何失败动作不推进容量请求或告警版本。
2. proposer、approver、publisher、reviewer、rollback operator 和 alert operator 权限互不隐式包含。
3. 控制台能完成可用的审批纵向链路，并且不展示或持久化敏感内部字段。
4. 漂移告警在重复扫描时不重复创建；确认不等于恢复；恢复后自动关闭；相同漂移复发时重开。
5. 查询有界、SQL 参数化、审计追加不可更新；Ruff、Mypy、全量 PostgreSQL Pytest、Alembic 往返/check、
   API/浏览器烟雾和生产镜像门禁通过。

## 最终验收证据

- 全量 PostgreSQL Pytest：259/259 通过；容量治理生产门禁子集：84/84 通过。
- Ruff 全仓通过；Mypy 90 个源码文件通过；生产 Compose config 通过。
- Alembic `2d6f8b1c4a90 -> f2a7d9c4e681 -> 2d6f8b1c4a90` 往返通过，`current` 为 head，
  `alembic check` 无漂移；两个 lifecycle/policy CHECK 与 append-only trigger 已实查。
- 浏览器烟雾覆盖桌面与 390px 小屏、认证空态/401、无横向溢出、无 console error、弹窗初始焦点和关闭后焦点恢复。
- 安全审查覆盖事务内即时撤权、all_agents/no grants、自审批、expected version、cursor actor/filter/kind 绑定、
  敏感字段投影、无 innerHTML/localStorage、告警无新观测不关闭，以及策略切换后旧 expected 告警关闭。
- 干净生产镜像以 UID/GID 10001 运行，`pip check` 无断依赖，容器 CLI 和 Alembic head 通过；未调用收费 API，
  未发布/推送镜像，未自动扩缩容或接入外部通知。

# v0.21 容量阈值变更治理闭环计划

## 当前状态

已完成。v0.21 已交付并验收
`校准建议 → 持续窗口验证 → 人工审批 → 策略发布 → 冷却期 → 效果复核 → 安全回滚` 纵向闭环；
容量阈值治理、运行时 active 策略解析、迁移、CLI、生产编排和发布门禁均已关闭，无阻塞项。

## 本阶段目标

PostgreSQL 继续作为容量观测、校准、策略、审批和发布状态的唯一事实源。Settings 只作为无已发布策略时的
安全默认值；所有容量检查与校准运行时解析当前 active 策略。策略发布必须显式人工审批，只改变阈值，不
修改 Worker 副本、Compose 或环境文件。

## 行为契约

1. 校准记录可创建一个版本化变更请求；系统先把当前 Settings 安全默认值登记为 active 基线策略，但不改变
   实际阈值语义。
2. 变更请求初始为 `pending_window`；持续窗口未到期、观测数量不足或观测跨度不足时不得进入审批。
3. 只有 `awaiting_approval` 可由具名人工审批为 `approved`；旧 expected version、跨 handler version 或重复
   决策冲突必须失败关闭。
4. 只有 `approved` 可发布；发布事务必须确认 base policy 仍是当前 active 策略，并阻止同一 handler version
   在冷却期内发布第二个策略。
5. 发布后请求进入 `cooling_down`，新策略 active、上一策略 superseded；`capacity-check`、monitor 和 calibrate
   无需重启即可读取 active 策略。
6. 冷却期结束后，复核读取发布后的持久化观测，并与发布前窗口证据比较；样本或跨度不足不推进状态。
   无明显回归为 `effective`，否则为 `ineffective`。
7. `cooling_down` 或 `ineffective` 可显式回滚，但仅当本请求发布的策略仍是当前 active；回滚后精确恢复
   previous policy，当前策略标记 `rolled_back`，请求标记 `rolled_back`。
8. 所有状态变更在 PostgreSQL 单事务内使用行锁、handler 级 advisory lock 和整数 version；不调用模型或
   真实收费 API。

## 架构决策

### 候选方案

| 方案 | 优势 | 代价 | 结论 |
|---|---|---|---|
| 直接改 `.env`/Compose | 实现快 | 无事务、无审批事实、无法精确回滚，多实例漂移 | 反选 |
| 外部配置中心/控制器 | 发布生态成熟 | 引入第二事实源和新运维面，当前无规模证据 | 暂不采用 |
| PostgreSQL 版本化策略与变更请求 | 复用既有事务、锁、迁移、审计和趋势数据 | 增加两张治理表与显式 CLI 流程 | 采用 |

### 选择、代价与撤销条件

- **选择**：新增 `reflection_capacity_policies` 与 `reflection_capacity_change_requests`；运行时 active 策略优先，
  Settings 作为无数据库策略时的 fallback。
- **反选理由**：文件发布不能证明并发顺序或精确前一版本；外部控制器会在当前阶段形成双事实和恢复成本。
- **接受的代价**：治理动作需要数据库连接与人工命令；首版不提供 Web 审批 UI，也不自动扩缩容。
- **撤销条件**：多主机配置传播或组织审批流程经实测无法由 PostgreSQL/CLI 满足，且已有可验证的外部配置
  控制器时重新评估；容量任务与观测事实仍保留在 PostgreSQL。

## 影响面

- **领域与应用**：`operations.capacity/capacity_history/capacity_governance/application`
- **存储**：`storage.models/capacity_history/capacity_governance`
- **运行时**：`workers.application` 的 active 策略解析，`cli` 的治理命令
- **数据库**：新 Alembic head，两张表、状态/版本约束、handler active partial unique index
- **验证**：领域、CLI、PostgreSQL 并发/状态机、模型、迁移、生产发布脚本和运维文档
- **结构分析降级**：仓库无 `scripts/build_graph.py`，使用 `rg` 复核构造器、协议、模型、迁移、CLI 和测试引用。

## Wave 执行计划

1. **Wave 1 / 契约与数据**：新增治理领域 DTO、错误码、ORM 模型和可逆迁移；先补领域/模型失败测试。
2. **Wave 2 / PostgreSQL 状态机**：实现基线登记、请求创建、窗口验证、审批/拒绝、发布、复核和回滚；覆盖
   行锁、advisory lock、expected version、handler 隔离和精确 previous policy。
3. **Wave 3 / 运行时与 CLI**：active 策略解析接入 capacity-check/monitor/calibrate；增加显式治理命令与安全
   JSON/退出码，不接受凭据，不自动修改副本。
4. **Wave 4 / 反例与迁移门禁**：覆盖未完成窗口、未审批发布、冷却期重复发布、旧版本并发、样本不足、
   跨 handler 隔离和精确回滚；执行 Ruff、Mypy、Pytest、Alembic 往返/current/check 和真实 CLI。
5. **Wave 5 / 发布与沉淀**：更新 README、技术设计、运行手册、ADR、ground truth、生产发布脚本、项目记忆
   和 handoff；干净镜像门禁不得调用真实收费 API。

## 验收标准

1. 状态机禁止跳步；所有写操作校验 expected version，并发冲突不产生双 active 策略。
2. active 策略按 job type + handler version 隔离，未发布时精确回退 Settings。
3. 窗口和效果复核均使用有界、持久化 PostgreSQL 观测；样本/跨度不足失败关闭。
4. 回滚只恢复本次发布的 exact previous policy；后续策略已生效时拒绝旧请求回滚。
5. Ruff、Mypy、全量 PostgreSQL Pytest、Alembic downgrade/upgrade/current/check、CLI 和生产镜像门禁通过；
   不调用真实收费 API、不自动扩缩容。

## 最终验收证据

- 全量 PostgreSQL Pytest：242/242 通过。
- Ruff：全仓通过；Mypy：85 个源码文件通过。
- Alembic：`f2a7d9c4e681 -> c9f4e2a7b613 -> f2a7d9c4e681` 往返通过；`current` 为
  `f2a7d9c4e681 (head)`；`alembic check` 无漂移。
- 生产发布门禁：容量/CLI/PostgreSQL 子集 67/67、Compose config、干净镜像构建、UID/GID 10001、
  `pip check`、`capacity-policy` CLI 和容器 Alembic head 全部通过。
- 生产镜像 digest：`sha256:2ae49b3d946aecc145d7851fcfc6b4ac4edd66d2e6909baefe28538f05266911`。
- 真实 CLI 已验证无 active 策略时 `capacity-policy show --pretty` 返回 `null`；缺少 create 参数时安全失败并退出码 2。
- 全过程未调用真实收费 API；策略发布只改变 PostgreSQL 阈值，不修改 Worker 副本、Compose 或环境文件。

# v0.20 真实负载校准、Outbox 分区归档与容量趋势治理计划

## 当前状态

功能、迁移、文档和生产发布门禁已全部完成。2026-08-25 14:34 从 Docker Hub 成功拉取精确
`python:3.12-slim-bookworm` 基础镜像并重新执行完整生产发布脚本；容量治理测试 55/55、Compose config、
干净镜像构建、非 root 身份、依赖完整性、全部 v0.20 CLI 和容器内 Alembic head 均通过。

## 本阶段目标

在 PostgreSQL 继续作为任务、租约、heartbeat、重试、终态和容量唯一事实源的前提下，使用真实已完成任务
处理耗时生成可审计但不自动应用的容量建议，持久化小时/天容量趋势，并交付默认 dry-run、精确版本匹配、
重试历史保护和有界批处理的 Outbox 分区归档/清理链路。

## 已完成

1. Worker 领取、完成、失败和租约过期路径持久化本次与累计真实处理耗时。
2. `capacity-check` 自动持久化容量观测，新增常驻 `capacity-monitor`。
3. `capacity-trend` 提供 handler version 隔离、小时/天有界聚合。
4. `capacity-calibrate` 从真实终态 Outbox 历史计算 P50/P95/P99、观察吞吐和阈值建议；样本不足退出码 6，
   不调用模型且不自动修改配置或副本。
5. PostgreSQL 原生范围分区 `outbox_job_archives`；快照身份为 `job_id + completed_at + version`，不回指运行表。
6. `outbox-maintain` 默认 dry-run；`--execute` 才归档，`--execute --prune` 才清理；只删除精确当前版本归档已
   存在且没有 retry request 引用的终态任务。
7. 迁移 `c9f4e2a7b613`、Compose 常驻 monitor 与 ops 治理入口、ground truth、单元/PostgreSQL/部署门禁测试。
8. README、技术设计、运行手册和 ADR 0021 已同步。

## 影响面与回滚

- **直接模块**：`operations.capacity_history/outbox_retention/application`、`storage.capacity_history/outbox_retention/outbox/models`、CLI、Compose、Alembic、测试与运维文档。
- **结构分析降级**：仓库无 `scripts/build_graph.py`，按规则使用 `rg` 复核 CLI、模型、仓储、迁移、Compose 和测试引用。
- **数据库回滚**：停止 capacity-monitor/outbox-maintain 并备份新增历史后 downgrade 到 `b7e2c4a9d610`；该操作会删除 v0.20 观测、校准、归档和耗时字段。
- **应用回滚**：切回 v0.19 镜像；既有 Outbox/Worker/运维状态机不依赖 v0.20 治理入口。

## 验收标准

1. Ruff、Mypy、全量 PostgreSQL Pytest 和 Worker/Operations 串行专项通过。
2. Alembic downgrade/upgrade/current/check 通过，实际归档主键包含 `id + completed_at + version`。
3. 真实 CLI 验证 capacity-check、trend、校准样本不足退出码 6 和 outbox-maintain 默认 dry-run。
4. 生产发布门禁验证 Compose、镜像、非 root、精确依赖、CLI 和容器内 migration head。
5. 不调用真实收费 API；校准不自动应用阈值，清理不绕过归档和重试历史保护。

## 最终验收证据

- Ruff：通过；Mypy：83 个源码文件通过。
- Pytest：230/230；Worker 专项 33/33；Operations 专项 26/26。
- 容量治理/部署子集：55/55；Compose config 与静态生产契约通过。
- Alembic：`c9 -> b7 -> c9` 往返、current/check 通过；实际归档主键 `{id,completed_at,version}`。
- 真实 CLI：capacity-check warning/4、trend 0、calibrate 样本不足 6、outbox-maintain dry-run 0。
- 干净生产镜像：基础镜像 digest `sha256:b64e9d3a71eddaa1b3f80c04abf292b3139e3b7c4dd272d19c31dc1f91194d1b`；
  产物 manifest list `sha256:b48cae2b3cb003ffa6fdec33bbb88767b5c1f1ae9b3e7d59cef392de4d442dc3`。
- 容器 UID/GID 10001、`pip check`、全部 v0.20 CLI、`c9f4e2a7b613 (head)` 通过；未调用真实收费 API。

## 下一阶段

- 积累多个生产周期后建立阈值变更审批、持续窗口和冷却策略。
- 评估归档表自身保留、对象存储导出、更细粒度分区和容量预测。
- 需要跨主机高可用或自动扩缩容时，把既有容量报告接入外部控制器，不引入第二任务事实源。

## v0.19 历史基线

## 当前状态

已完成并验收。

## 本阶段目标

在不引入第二任务事实源、不绑定 Kubernetes 的前提下，交付 PostgreSQL Worker 容量快照、三级判级、有界
副本建议、`public-agent capacity-check`、生产镜像、Compose 拓扑、Secret/资源/日志边界和可执行发布门禁。

## 已完成

1. `ReflectionCapacitySnapshot` 聚合 handler version 隔离的 Outbox 和 Worker heartbeat 事实。
2. healthy/warning/critical 判级、稳定原因码、推荐 Worker 数和 `scale_delta`。
3. `public-agent capacity-check`：退出码 0/4/5，配置/运行错误 2/1，单一安全 JSON，且不需要 OpenAI Key。
4. 迁移 `b7e2c4a9d610` 增加 Outbox/heartbeat 容量查询索引，并通过 downgrade/upgrade 往返。
5. 多阶段非 root Dockerfile、精确 `requirements.lock` 和生产 Compose：migrate、API、可 scale Worker、ops
   capacity-check、Secret、read-only、cap drop、PID/CPU/内存和日志轮转。
6. 容量/CLI/PostgreSQL/配置/模型/部署测试、ground truth 和 `scripts/test_production_deployment.py`。
7. ADR 0020、生产运行手册、README、技术设计和项目记忆。

## 验收证据

- Ruff：全量通过。
- Mypy：78 个源码文件通过。
- 容量发布门禁：40/40，通过 Compose config、镜像构建、UID/GID 10001、CLI 和容器 Alembic head。
- Alembic：`a4d6f8b2c510 -> b7e2c4a9d610 -> a4d6f8b2c510 -> b7e2c4a9d610`，`check` 无漂移。
- 实际 `capacity-check` 在无 OpenAI Key 情况下读取 PostgreSQL 并返回 warning/退出码 4。

## 下一阶段

- 使用真实吞吐和处理时延校准 target jobs/worker 与持续窗口。
- 实现 Outbox 历史归档/分区、容量趋势和保留策略。
- 需要跨主机高可用或自动扩缩容时，将相同容量报告接入外部控制器；在证据触发前不引入第二事实源。

## v0.18 历史基线

## 历史阶段状态

已完成

## 当前进度

- Wave 1-4 第一条纵向链路已完成：`ReflectionWorkerRunner`、停止信号、有限 drain、PostgreSQL Worker
  heartbeat、同 worker ID instance token fencing、handler version 积压快照和迁移 `9c3e5a7b1d40`。
- Wave 5-7 已完成：安全任务投影、严格筛选绑定 keyset、dead-letter expected-version retry、actor 当前事实
  重验证、独立运维权限、不可变幂等请求和追加审计，迁移 `a4d6f8b2c510`。
- Wave 5-7 最终验收已完成：Ruff、Mypy、179 个 PostgreSQL 全量测试、Worker 14/14、Operations
  专项 21/21、Alembic downgrade/upgrade/current/check、领域包、计算器和中文 RAG 全部通过。证伪审查修正
  人工 retry 新轮次错误继承历史总 attempts 退避的问题，退避与耗尽预算现统一使用 `attempts_in_cycle`。
- Wave 8 已完成：生产 application 装配、`public-agent reflection-worker`、跨平台信号、安全 JSON 事件、
  稳定退出码、配置失败关闭和资源生命周期已落地。
- 最终验收已完成：Ruff、Mypy 77 个源码文件、195 个 PostgreSQL 全量测试、Worker 30/30、Operations
  21/21、Alembic current/check、领域包、计算器、中文 RAG 和真实 Windows SIGBREAK 子进程停止均通过；
  全程未调用真实收费 API。

## Wave 8 执行切片

- **生产装配**：新增环境变量驱动的 Worker 参数，将 Database、OpenAI ReflectionEngine、PostgreSQL
  LearningStore/Publisher、Outbox Store、ReflectionWorker 和 Runner 装配为单一应用生命周期。
- **CLI 与信号**：`public-agent reflection-worker` 不接收密钥命令行参数；SIGINT/SIGTERM（Windows 额外
  SIGBREAK）只设置停止事件，Runner 停止领取并在有限 drain timeout 内收敛。
- **失败关闭**：缺少 OpenAI API Key、参数越界、数据库不可达或装配异常使用安全机器码和非零退出码，
  不打印连接串、Token、供应商异常正文或运行轨迹。
- **验证**：覆盖配置边界、依赖装配、信号安装/恢复、成功停止、drain timeout 与安全错误输出；专项脚本
  和全量 PostgreSQL 回归不得调用真实收费 API。

### Wave 8 实施结果

- `ReflectionWorkerApplication` 统一装配 Database、OpenAI `ReflectionEngine`、PostgreSQL 学习/发布仓储、
  Outbox Store、Worker 与 Runner；启动 ping，退出时独立尝试关闭 Provider 与 Database。
- CLI 不提供 API Key 参数；缺密钥、非法配置或装配异常返回安全机器码和退出码 2，运行/清理异常返回 1，
  drain timeout 返回 3，正常停止返回 0。
- SIGINT/SIGTERM 和 Windows SIGBREAK 只设置 `asyncio.Event`，上下文退出恢复原处理器；真实子进程 smoke
  使用独立 handler version、0 个任务和假测试 Key 验证停止路径，未产生模型请求。
- 全量回归发现并修复 `workers.__init__` eager 导出 application 导致的 storage/outbox 循环导入；生产装配
  改为显式 `workers.application` 子模块边界，核心 Worker 包不反向依赖 storage。

### Wave 8 影响面与回滚

- **直接模块**：`config`、`cli`、`workers.application/__init__`、CLI/Runner 测试、Worker ground truth、
  README、技术设计和 ADR 0019。
- **依赖方向**：CLI → Worker application → Runner/ReflectionWorker → PostgreSQL stores/growth pipeline；
  底层 Worker 和 storage 不依赖 CLI。
- **结构分析降级**：仓库仍无 `scripts/build_graph.py`，使用 `rg` 复核 CLI 入口、构造器、导出和测试调用方。
- **回滚点**：移除 `reflection-worker` 子命令和 Worker application 装配即可；无数据库迁移和外部状态变更。

## Wave 5-7 执行切片

- **Wave 5 / 数据与领域契约**：为 Outbox 增加独立 `version` 和单轮尝试计数，新增幂等重试请求与
  append-only 运维审计表；迁移以 `9c3e5a7b1d40` 为 down revision，可完整 downgrade。
- **Wave 6 / 纵向实现**：新增 `operations.jobs:read` / `operations.jobs:retry`、事务内 actor 当前事实复核、
  tenant/agent/handler-version 安全查询、绑定筛选条件的严格 Base64 keyset，以及仅允许
  `dead_letter -> pending` 的人工重试。
- **Wave 7 / 反例验证**：覆盖跨租户、无 agent grant、权限分离、认证后撤销/禁用、旧 version、相同与不同
  幂等键并发、非死信拒绝、旧 lease fencing、游标篡改和敏感字段泄漏。

### 影响面与回滚

- **直接模块**：`storage.models/outbox/operations`、`operations` 领域 DTO、`api.operations/app`、认证权限
  allowlist、Alembic、PostgreSQL/API 测试和运维文档。
- **上游复核**：`create_app` 的所有调用方保持可选参数兼容；Worker 的 claim/fail/complete/heartbeat 必须统一
  递增版本并改用单轮尝试计数，现有 Worker 协议和测试输入不变。
- **结构分析降级**：仓库不存在 `scripts/build_graph.py`，本 Wave 使用 `rg` 对上述符号和调用方做实码复核。
- **回滚点**：Alembic downgrade 回到 `9c3e5a7b1d40`；新 API 仅在显式注入 operations service 时安装，
  不改变默认健康检查应用。

## 目标

把 v0.17 的进程内 `ReflectionWorker.process_step` 提升为可长期运行、可安全观测和可人工恢复的生产运维
纵向链路：提供常驻 Worker 入口、优雅关闭、PostgreSQL 心跳与积压快照、租户/agent 作用域任务查询、
dead-letter 安全重试、独立运维权限和追加审计，同时保持 PostgreSQL 为唯一事实来源。

## 默认假设

- PostgreSQL 继续决定任务状态、租约、重试、worker 心跳和终态；Redis 只可作为非权威唤醒提示。
- 常驻 Worker 使用有界 poll interval、随机 jitter 和批量上限；SIGINT/SIGTERM 停止领取新任务并等待当前
  job 在有限时间内收敛，不能强行把本地内存状态当作完成事实。
- 运维查询不返回 task、output、trace、candidate 内容、payload 扩展、checkpoint、provider state、Token、
  Authorization header 或原始异常，只返回安全状态、计数、时间、机器错误码和作用域 ID。
- 人工重试只允许 `dead_letter -> pending`，必须校验 expected version/updated_at、当前 actor、tenant、agent
  grant 和独立权限；相同请求幂等，不同请求冲突。
- 任务历史和审计不物理删除；归档/保留策略留给后续合规 Wave。

## Wave

1. 审查 CLI、应用装配、认证管理重验证、Outbox 状态机和现有健康检查边界。
2. 设计 worker heartbeat、积压快照、安全任务投影、dead-letter retry 和审计模型。
3. 新增 PostgreSQL 模型、迁移、索引和作用域/状态约束。
4. 实现常驻 Worker runner、优雅关闭、poll/jitter、有限 drain 和进程心跳。
5. 实现运维仓储与 FastAPI：stats、严格 keyset 任务列表/详情、dead-letter retry。
6. 增加 `operations.jobs:read`、`operations.jobs:retry` 权限，管理动作事务内重验证 actor 当前事实并追加审计。
7. 补并发重试、跨租户/agent、旧 expected version、敏感字段、worker 失联和退出恢复反例。
8. 全量门禁、CLI/离线演示、ADR 0018/0019、技术文档和项目记忆。

## 验收标准

1. `public-agent reflection-worker` 可在无真实收费 Provider 的测试装配下长期轮询，收到停止信号后不再领取
   新任务，并在配置的 drain timeout 内安全退出。
2. PostgreSQL 可查询 pending/retry/processing/dead-letter 数量、最老可用任务年龄和 worker 最近心跳；
   Redis 故障不影响正确性。
3. 任务管理查询严格按认证 tenant + agent grant + permission 过滤，使用稳定 keyset，不泄漏运行正文或凭据。
4. dead-letter retry 在行锁事务中验证最新状态和 expected version，只允许当前 actor 可管理作用域；并发相同
   请求幂等，不同请求/旧版本冲突，旧 lease token 不能复活。
5. 运维写操作追加安全审计，只保存 actor/target ID、动作、结果、状态和机器码，不保存 payload/异常正文。
6. Ruff、Mypy、全量 PostgreSQL Pytest、Alembic downgrade/upgrade/current/check、领域包/计算器/中文 RAG、
   v0.17 Worker 和 v0.18 运维专项全部通过，且不调用真实收费 API。
