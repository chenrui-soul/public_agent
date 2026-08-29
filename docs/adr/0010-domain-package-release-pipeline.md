# ADR 0010：内容寻址的领域能力包版本发布闭环

- 状态：Accepted
- 日期：2026-08-25

## 决策点

技能、策略、工作流和领域能力包如何形成一套生产可用的版本发布机制，使专业能力可以快速构建，
但任何内容都不能绕过路径安全、回归评测、人工审批、并发控制、审计和回滚直接成为活跃智能体配置。

## 候选方案

| 方案 | 优势 | 劣势 | 适用场景 |
|---|---|---|---|
| 文件目录覆盖 + 重启加载 | 实现最少 | 无不可变版本、审批证据、并发原子性和可靠回滚 | 本地原型 |
| 技能/策略/工作流分别建版本和发布器 | 每类资产可独立发布 | 重复状态机，包级兼容需要跨表协调和半发布恢复 | 大规模共享资产平台 |
| 领域包版本聚合 + 类型化不可变资产 | 一次评测、审批和事务原子发布完整能力集合 | 单资产首版不能独立晋升 | 当前模块化单体 |

## 决策

选择“领域包版本作为发布聚合，技能、策略、工作流和评测文件作为类型化不可变资产”。

### 1. 构建阶段先于持久化

`DomainPackageLoader.build()` 在访问 PostgreSQL 前完成严格清单验证和安全读取。清单、instructions、
内联 policies 与声明资产只接受受限 UTF-8 文本；绝对路径、`..`、Windows drive、符号链接逃逸、目录、
缺失文件和容量超限全部拒绝。文本统一为 Unicode NFC 和 LF。

每个资产保存类型、逻辑键、包内相对路径、媒体类型、规范化内容、字节数和 SHA-256。规范化清单与排序后
的资产摘要生成包 SHA-256，绝对路径和创建机器不影响结果。同一内容在不同目录和换行格式下得到相同哈希。

### 2. 领域包版本是不可变发布聚合

`domain_package_versions` 使用 `tenant_id + agent_id + domain_id + version` 唯一约束。同版本同内容重复
创建返回原记录；哈希、清单或资产不同则拒绝，并要求提升语义版本。`domain_package_assets` 使用包版本、
资产类型、逻辑键和相对路径唯一约束保存独立指纹。

首版不拆独立 `skill_releases`。技能、策略和工作流必须作为同一个兼容集合接受领域回归评测，避免某个
单资产已经激活、其依赖仍停留在旧版本的半发布状态。

### 3. 评测和人工审批是两个独立门禁

状态机为：

```text
draft -> evaluating -> awaiting_approval -> approved -> active
                        |                  |
                        +-> rejected       +-> deprecated / rolled_back
```

评测报告的 suite、数据集版本、通过状态、分数、摘要和 metrics 规范化后生成报告哈希；相同报告保存幂等，
哈希绑定不同内容时拒绝。评测失败进入 `rejected`。只有通过评测的版本进入 `awaiting_approval`；人工决定
追加写入 `domain_package_approvals`，批准后才进入 `approved`。

### 4. 发布和回滚锁定智能体聚合

发布事务先以 `FOR UPDATE` 锁定 `agents`，再锁定目标包版本及旧包版本。事务重新检查通过评测和批准证据，
创建或复用内容完全一致的不可变 `agent_versions`，追加 activate 记录，最后切换
`agents.active_version_id`。旧领域包版本只标记为 `deprecated`。

发布幂等键在租户与智能体作用域唯一，并绑定包版本、动作、操作者和备注。并发相同请求只产生一个有效
activate。任意异常会回滚旧版本状态、agent version 创建、发布审计和 active version 切换。

回滚只允许当前活跃且由最后一次 activate 直接激活的包；它切回 activate 记录保存的前一 agent version，
把前一领域包恢复为 active，把当前包标记为 rolled_back，并追加 rollback 记录。回滚不会删除版本、资产、
评测、审批或发布事实。

## 反选论证

- 不选目录覆盖：文件变化无法证明评测和批准的是当前内容，进程重启也不能提供事务性回滚。
- 不选每类资产独立发布：首版没有共享技能目录和兼容依赖求解器，拆分只会复制门禁并产生跨资产半发布。
- 不选 Redis 保存发布状态：缓存故障或淘汰不能影响当前活跃版本，PostgreSQL 必须是唯一事实来源。
- 不选评测通过后自动激活：模型或规则评测不能替代高影响能力升级的人类责任边界。
- 不选覆盖旧 `agent_versions`：历史运行通过外键引用具体版本，原地修改会破坏审计和可重放性。

## 接受的代价

- 单个技能要修改时需要发布新的领域包版本并重跑包级回归评测。
- 资产内容首版直接保存在 PostgreSQL；大型知识文件继续走独立 RAG 文档链路，不进入领域包资产表。
- 当前接口是 Python 应用服务，认证、租户角色授权和管理 API 留到后续 API Wave。
- 已由 rollback 恢复的旧版本不能再次执行“回滚的回滚”；要改变能力应发布新版本，保持历史语义清晰。

## 撤销条件

- 当多个领域包稳定共享同一技能，或技能独立发布频率显著高于包级发布时，从现有类型化资产模型拆出
  `skills/skill_versions/skill_releases`，但包发布仍需保存解析后的兼容版本集合和单事务激活快照。
- 当单包文本资产接近 8 MiB 或需要二进制模型资产时，将内容迁移到对象存储；PostgreSQL 继续保存不可变
  哈希、大小、媒体类型和对象版本，发布门禁不改变。
- 当授权 API 上线时，在调用发布服务前增加租户角色和审批职责分离；数据库作用域与审计模型继续复用。

## 验证

- 单元测试覆盖 CRLF/LF 与目录无关哈希、资产变化、重复键/路径、路径穿越、解析后逃逸、缺失、目录、
  非 UTF-8 和容量限制。
- PostgreSQL 集成测试覆盖同版本同内容幂等、同版本异内容拒绝、跨租户隔离、失败评测、缺失审批、
  幂等键冲突和活跃规格还原。
- 并发相同发布请求只产生一个 activate；第二版本发布原子废弃旧版本；回滚恢复旧版本并保留全部审计。
- Alembic downgrade/upgrade/current/check、全量 Ruff、Mypy 和 PostgreSQL Pytest 作为最终门禁。

## 相关实现

- `src/public_agent/domains/models.py`
- `src/public_agent/domains/loader.py`
- `src/public_agent/storage/models.py`
- `src/public_agent/storage/domain_packages.py`
- `migrations/versions/b42e6f8a1c30_add_domain_package_release_pipeline.py`
- `tests/test_domain_loader.py`
- `tests/test_domain_package_publishing.py`
