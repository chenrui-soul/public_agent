## Handoff Checkpoint

**更新时间**: 2026-09-03
**当前目标**: v0.29 治理知识再认证与受控退役闭环
**当前阶段**: v0.28 已完成全部验收；v0.29 Wave 1 已完成，进入 Wave 2
**完成度**: v0.28 100%；v0.29 Wave 1 100%，Wave 2 待实现

### 已完成

- v0.28 UTC 小时/天质量趋势、assessment/时间窗/actor 绑定 cursor、PostgreSQL `date_trunc` 与有界补零
- 持续 unsafe、重复 degraded、恢复后再次隔离三类风险，stable/evidence fingerprint、truncated 失败关闭和七类事件状态机
- 控制台趋势面板、独立 403、断开清理、390px 无溢出；固定 Playbook 和受限证据码
- Alembic head `e9a2f4c6b810`；ADR 0030、README、技术设计、运行手册和 27 条生产 ground truth
- 定向 72/72、全量 PostgreSQL Pytest 291/291、生产子集 116/116、Ruff、Mypy 91、Node、迁移往返/current/check、Compose
- 生产镜像 digest `sha256:74ca38d5b43d961ba9795c0e5e3db45b1f102f480adc3bae26e89b2575402964`
- v0.29 Wave 1：版本化再认证策略、`current/due/overdue/quarantined/retired` 投影、独立 read/request/review/retirement 权限、受限决定 DTO 和领域反例测试
- 验证：定向 38/38；全量离线 252 passed / 48 skipped；Ruff 与 Mypy 91 个源码文件通过

### 未完成

- v0.29 Wave 2：PostgreSQL 认证事实、可逆迁移、并发幂等、退役原子性、RAG 排除和只读演练扩展

### 关键边界

- PostgreSQL 是治理事实唯一来源；RAG 只是不可信参考，不是授权、认证、恢复或执行证据。
- 到期只产生内部待办/风险，不自动认证、退役、隔离、恢复或发送通知。
- 退役必须人工批准并停止召回，但不删除内容、向量、反馈、快照、恢复、事件、处置或复盘谱系。
- 仓库无 `scripts/build_graph.py`，影响面以 `rg` 和实码回读复核。

### 恢复入口

- `_plan.md` 顶部 v0.29；`docs/adr/0030-governance-knowledge-quality-trends-and-recurrence-risks.md`
- `src/public_agent/operations/capacity_control.py`; `src/public_agent/storage/capacity_control.py`; `src/public_agent/storage/models.py`
- `src/public_agent/storage/governance_knowledge.py`; `tests/test_capacity_control.py`; `tests/test_postgres_governance_postmortems.py`
- 首步先审计 postmortem 状态、治理知识检索过滤、默认角色和 incident 信号扩展点，再写领域失败测试。

---

## Handoff Checkpoint

**更新时间**: 2026-08-25 21:42
**当前目标**: v0.28 治理知识质量趋势与复发风险闭环
**当前阶段**: v0.27 已完成全部验收；v0.28 已完成范围和 Wave 规划，Wave 1 进行中
**完成度**: v0.27 100%；v0.28 规划完成

### 已完成

- v0.27 受限反馈、独立复核、原子隔离、RAG 即时排除、不可变质量快照和 false_positive 恢复新版本闭环
- 控制台质量快照与恢复审批独立面板、独立 403、断开清理和 390px 无溢出
- PostgreSQL 反例覆盖快照不可变、陈旧/旧版本、重复 active recovery、四方职责分离及审计失败整体回滚
- Alembic head `d8f1c2a4b730`；ADR 0029、README、技术设计、运行手册和 26 条生产 ground truth
- 全量 PostgreSQL Pytest 282/282；生产子集 107/107；Ruff；Mypy 91；迁移往返/current/check；生产镜像 `sha256:9ffe68b5c66facbc02ce427d766c55f64532dc55d2777cda40b375b298ab9808`

### 未完成

- v0.28 Wave 1：审计质量快照/事件/配置/monitor 调用方，实现有界趋势 DTO、三类风险规则和稳定 rule/evidence fingerprint，先补领域失败测试。

### 关键边界

- PostgreSQL 是治理事实唯一来源；RAG 只是不可信参考，不是授权、恢复证据或执行指令。
- 不调用收费 API、不发真实通知、不自动生成快照、不恢复知识、不执行生产变更。
- 仓库无 `scripts/build_graph.py`，影响面以 `rg` 和实码回读复核。

### 恢复入口

- `_plan.md` 顶部 v0.28；`docs/adr/0029-governance-knowledge-quality-and-recovery.md`
- `src/public_agent/operations/capacity_control.py`; `src/public_agent/storage/capacity_control.py`; `src/public_agent/storage/models.py`
- `tests/test_capacity_control.py`; `tests/test_postgres_governance_postmortems.py`

---

## Handoff Checkpoint

**更新时间**: 2026-08-25 19:05
**当前目标**: v0.26 治理事件复盘与知识沉淀闭环
**当前阶段**: v0.24 与 v0.25 已完成并验收；v0.26 已自动启动并完成初始范围
**完成度**: v0.25 100%；v0.26 规划启动

### 最新完成

- v0.24 四类治理异常检测、事件指纹/证据指纹、确认/恢复/复发重开、独立事件 RBAC、API、控制台和 monitor
- v0.25 固定 Playbook 处置单；每个 incident recurrence cycle 唯一；read/request/approve/execute/verify 独立权限
- 请求人与审批人、执行人与验证人职责分离；执行只保存枚举结果/证据码，不运行任意命令
- verified 必须依赖执行后更高版本的 resolved 事件事实；未恢复、旧版本和复发 cycle 失败关闭
- 控制台新增处置审批队列和局部 403；390px 四身份 E2E、未恢复 409、恢复后 verified、零新 console error
- Alembic head `9f4e7c2d1a60`；ADR 0026、README、技术设计、运行手册和生产 ground truth
- 全量 PostgreSQL Pytest 273/273；生产子集 98/98；Ruff；Mypy 90；迁移往返/current/check；Compose
- 生产镜像 digest `sha256:a96bb3a6c6802d9bef2705dd8b84927a4c6b4948c1065f21a6a3c280e2175c00`

### 当前未完成

- v0.26：verified 处置的结构化复盘、独立评审、治理知识 namespace 发布和 RAG 安全引用链路。

### 安全边界

- PostgreSQL 继续是身份、授权、事件、处置、复盘、审计和知识发布的唯一事实源。
- 不调用收费 API、不发送真实通知、不自动执行修复/撤权/发布/回滚/扩缩容。
- RAG 复盘知识只能作为参考，不能成为授权、恢复证据或可执行指令。

### 最新恢复入口

- `_plan.md` 顶部 v0.26；`docs/adr/0026-governance-remediation-approval-and-verification.md`
- `src/public_agent/operations/capacity_control.py`; `src/public_agent/storage/capacity_control.py`
- `src/public_agent/api/capacity_governance.py`; `src/public_agent/api/capacity_console.py`
- 首步定义 postmortem DTO、受限根因/影响/预防分类、独立评审权限和失败测试。

---

## Handoff Checkpoint

**更新时间**: 2026-08-25 16:55
**当前目标**: 实施 v0.24 治理异常检测与内部事件响应闭环
**当前阶段**: v0.23 已完成并验收；v0.24 已自动启动并完成现状审计、范围和 Wave 计划
**完成度**: v0.23 100%；v0.24 规划完成，Wave 1 待实现

### 最新完成

- 独立 `operations.capacity_audit:read` auditor 角色、安全审计 API、actor/action/outcome/UTC 时间过滤和严格 cursor
- 审计投影排除 Token ID，safe metadata 白名单；撤权即时生效，auditor 不具备容量治理写权限
- 告警 `within_sla/due/breached/acknowledged/resolved` 派生状态和有界 Settings 配置
- 控制台审计面板、SLA 标记、只读演练入口及按权限局部降级；断开身份清空旧视图
- 只读 catalog 演练验证当前 actor、职责分离、append-only trigger、lifecycle CHECK 和查询索引
- Alembic head `e3c8a1f7b920`；README、技术设计、运行手册和 ADR 0024
- 全量 PostgreSQL Pytest 262/262；生产门禁 87/87；Ruff；Mypy 90；Alembic 往返/current/check；Compose
- 桌面/390px 浏览器无页面横向溢出、无 console error；本地镜像 digest `sha256:d7b8026ecb707c746d8b226f88607019e6325f9ca13f1e26742ffa04f9d2e68e`

### 当前未完成

- v0.24 Wave 1：治理事件 DTO、规则版本、指纹、阈值、独立事件权限和失败测试。

### v0.24 边界

- 只生成 PostgreSQL 内部事件队列；不发送真实外部通知。
- 不自动撤权、修复 Schema、回滚/发布策略或调整副本。
- 信号来源限定为 denied/conflict 审计突增、SLA breached、重复 reopen 和演练失败。

### 最新恢复入口

- `_plan.md` 顶部 v0.24；`docs/adr/0024-governance-audit-sla-and-read-only-drills.md`
- `src/public_agent/operations/capacity_control.py`; `src/public_agent/storage/capacity_control.py`; `src/public_agent/api/capacity_console.py`
- 首步先写 v0.24 领域与权限反例，再新增迁移或状态机。

---

**更新时间**: 2026-08-25 16:43
**当前目标**: 构建可快速专业化、可记忆、可受控成长并可量化验证的生产级通用智能体框架
**当前阶段**: v0.22 RBAC 审批控制台、策略漂移检测与治理告警闭环已完成
**完成度**: 100%；功能、迁移、浏览器烟雾、安全审查、文档、全量回归和生产发布门禁全部完成

### 已完成

- 容量治理权限拆分与推荐角色模板；事务内重验治理 tenant、active Principal、Token、权限、all_agents/no grants
- 容量请求/告警有界 API、actor/filter/kind 绑定 keyset cursor 和安全响应投影
- 原生响应式审批控制台；Token 仅 sessionStorage；CSP/no-store/nosniff/no-referrer；无 innerHTML/localStorage
- 漂移扫描、expected/observed 指纹去重、critical 升级、确认、自动恢复、复发重开和策略切换旧告警关闭
- `reflection_capacity_governance_alerts`、append-only 审计与 Alembic `2d6f8b1c4a90`
- `public-agent serve` 生产管理入口、API-only Token pepper Secret、Compose 与生产 ground truth
- 浏览器桌面/390px 小屏、401、空态、焦点恢复和无 console error 烟雾通过
- 全量 PostgreSQL Pytest 259/259；Ruff；Mypy 90 个源码文件；Alembic 往返/current/check；生产门禁 84/84
- 生产镜像 UID/GID 10001、pip check、CLI 和容器 Alembic head 通过；digest `sha256:1afac849325ffa3855ed743255ddbe990e85c4e4c7cde08c9c87627e5c04055b`

- Worker 领取、完成、失败、租约过期接管和耗尽路径保存本次/累计真实处理耗时
- capacity-check 自动持久化观测；常驻 capacity-monitor；小时/天 capacity-trend
- capacity-calibrate 从真实终态历史计算 P50/P95/P99、观察吞吐和建议；样本不足退出码 6，不调用模型、不自动应用
- PostgreSQL 原生范围分区 Outbox 归档，身份为 `id + completed_at + version`，父表不回指运行表
- outbox-maintain 默认 dry-run；显式 execute 归档；execute+prune 清理；精确版本和 retry history 双重保护
- 容量观测、校准历史、归档分区和处理耗时迁移 `c9f4e2a7b613`
- Compose 常驻 monitor 与 ops trend/calibrate/outbox-maintain；README、技术设计、运行手册、ADR 0021
- 代码审查修正归档主键遗漏 version，实查 PostgreSQL 主键为 `{id,completed_at,version}`
- 回归修正时间敏感测试使用过期固定墙钟的问题
- Ruff 通过；Mypy 83 个源码文件；Pytest 230/230；Worker 33/33；Operations 26/26
- 容量治理/部署子集 55/55；Compose config、静态生产契约和 Alembic c9→b7→c9/current/check 通过
- 2026-08-25 14:25 重新执行生产发布脚本并完成两次有限网络重试；三次运行均再次通过容量治理测试 55/55 与 Compose config
- 2026-08-25 14:34 成功拉取 `python:3.12-slim-bookworm`，完整生产发布脚本退出码 0；干净镜像、UID/GID 10001、pip check、全部 v0.20 CLI 和容器 Alembic head 通过
- 真实 CLI：capacity-check=4 warning、trend=0、calibrate=6 insufficient、outbox-maintain=0 dry-run
- 离线生产镜像：UID/GID 10001、pip check、v0.20 CLI 和 c9 head 通过；未调用真实收费 API
- 版本化容量策略、变更请求、持续窗口证据、职责分离审批、发布冷却、效果复核和精确上一策略回滚
- capacity-check、capacity-monitor、capacity-calibrate 每次运行解析 PostgreSQL active 策略；无 active 策略时使用 Settings fallback
- `capacity-policy show/create/validate/approve/reject/publish/review/rollback` CLI 与 Compose ops 入口
- Alembic `f2a7d9c4e681` 往返/current/check 通过；全量 Pytest 242/242、Ruff、Mypy 85 个源码文件通过
- 生产发布门禁容量/CLI/PostgreSQL 子集 67/67；生产镜像 digest `sha256:2ae49b3d946aecc145d7851fcfc6b4ac4edd66d2e6909baefe28538f05266911`

### 未完成

- 无未完成交接项。

### 关键决策

- 控制台不是授权依据；所有容量治理动作必须在数据库事务内重验当前 actor 事实
- 治理 Principal 必须来自配置 tenant，具备 all_agents 且没有 agent grant；operator 只取 Principal subject
- 漂移告警绑定 expected/observed 双指纹；无新观测不关闭；策略切换后关闭旧 expected 告警
- 成功审计与状态同事务，失败审计独立追加，数据库 trigger 拒绝 UPDATE
- API Token pepper 独立于应用 Secret，只挂载到 API 管理入口

- PostgreSQL 继续作为任务、租约、heartbeat、重试、终态、容量、校准和归档唯一事实源
- 校准只产生并持久化建议，不自动修改阈值或部署副本
- 容量观测按 handler version 隔离，趋势查询有界；长期自动保留与预测留后续治理
- 归档快照以 `job_id + completed_at + version` 唯一标识，且不设置回指运行表外键
- 物理清理失败关闭：必须 execute+prune、精确当前版本归档存在且无 retry request 引用
- 测试禁止调用真实收费 API；共享默认 handler version 的数据库套件保持串行
- PostgreSQL 是容量观测、校准、策略、审批、发布、复核与回滚的唯一事实源；Settings 仅为无 active 策略时的 fallback
- 发布只改变容量阈值，不修改 Worker 副本、Compose、`.env` 或外部控制器；首版不自动扩缩容
- active partial unique index 切换时先让旧策略退出并 flush，再激活目标策略，避免同事务唯一约束竞态

### 恢复入口

- **首读文件**: `_plan.md`, `docs/OPERATIONS_RUNBOOK.md`, `docs/adr/0023-rbac-capacity-console-policy-drift-alerts.md`, `src/public_agent/storage/capacity_control.py`, `src/public_agent/api/capacity_governance.py`, `src/public_agent/api/capacity_console.py`
- **关键命令**: `.\.venv\Scripts\python.exe -m ruff check .`; `.\.venv\Scripts\python.exe -m mypy src`; `$env:PUBLIC_AGENT_RUN_DB_TESTS='1'; .\.venv\Scripts\python.exe -m pytest -q`
- **生产发布复验**: `$env:PUBLIC_AGENT_PYTHON_IMAGE='python:3.12-slim-bookworm'; .\.venv\Scripts\python.exe scripts\test_production_deployment.py`
- **迁移验证**: `.\.venv\Scripts\python.exe -m alembic current`; `.\.venv\Scripts\python.exe -m alembic check`

### 阻塞项

- 无。
