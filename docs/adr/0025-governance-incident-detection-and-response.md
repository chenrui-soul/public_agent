# ADR 0025：治理异常检测与内部事件响应闭环

- **状态**：Accepted
- **日期**：2026-08-25
- **关联**：ADR 0023、ADR 0024、`operations.capacity_control`、`storage.capacity_control`、capacity-monitor

## 背景

v0.23 已具备 append-only 治理审计、安全查询、告警 SLA 和只读演练，但值守人员仍需人工关联 denied/conflict、
SLA breached、告警复发和演练失败。直接接入外部通知平台会新增凭据、网络、重试、恢复和第二状态权威边界。

## 决策

1. PostgreSQL 新增 `reflection_capacity_governance_incidents`，保存 warning/critical、
   `open/acknowledged/resolved`、version、命中次数、复发次数和安全 evidence。
2. 首版规则固定为四类：审计失败有界时间桶、未确认告警 SLA breached、告警重复 reopen、只读演练检查失败。
   每类规则有稳定 rule version；事件 fingerprint 绑定 tenant/handler/rule/目标，evidence fingerprint 绑定可变化事实。
3. capacity-monitor 在容量观测和漂移扫描后串行执行事件扫描。扫描使用 PostgreSQL advisory lock、有界样本和唯一
   fingerprint；重复扫描不重复建事件，并在达到容量上限时显式返回 truncated。
4. 确认只表示人工接手。恢复必须观察到晚于 `last_evidence_at` 的 alert/catalog 事实，或进入新的审计 bucket；
   无新事实不得关闭。相同 fingerprint 在 resolved 后出现新证据时重开并清除旧确认。
5. 新增独立 `operations.capacity_incidents:read/manage`。所有查询和确认在事务内重验治理 tenant、active Principal、
   Token、权限和 global scope；成功确认与状态同事务审计，拒绝/冲突独立追加。
6. API 和控制台只投影白名单 evidence、operator subject 与生命周期时间；不返回内部 Principal/Token ID、
   Authorization、数据库 URL、未知 metadata 或原始异常正文。外部通知、自动撤权、Schema 修复、策略回滚和扩缩容
   均不在本阶段。

## 反选方案

- **直接让告警平台成为事件状态源**：引入第二事实源，恢复、撤权和重放边界不一致。
- **把事件合并进漂移告警表**：审计桶和演练失败没有 expected/observed policy 语义，会破坏现有告警模型。
- **确认即关闭**：把“已接手”误当作“已恢复”，会隐藏持续异常。
- **无界全表扫描**：数据增长后不可预测，且不满足生产容量治理要求。
- **自动修复或自动撤权**：扩大写入和业务影响，缺少独立授权与恢复证据。

## 后果

### 正面

- 四类治理信号形成统一、可分页、可确认、可恢复和可审计的内部事件队列。
- 指纹、唯一约束和 advisory lock 同时提供重复扫描与并发扫描幂等。
- auditor、incident viewer 和 incident operator 保持职责分离，撤权即时生效。
- 外部通知后续可作为内部事件消费者接入，而不改变 PostgreSQL 状态权威。

### 代价

- PostgreSQL 增加事件状态和扫描读取压力；达到配置上限时只处理有界集合并报告 truncated。
- 审计突增按固定桶建事件，桶边界附近可能拆成两个独立事件。
- 首版没有值班排班、通知升级、WORM 导出或跨系统关联。

## 安全与回滚

- migration `6b9d2f4a8c71` 可 downgrade 到 `e3c8a1f7b920`；先停止 API/capacity-monitor 并导出事件事实。
- downgrade 先删除 audit incident FK/列，再删除事件索引和表；既有告警和审计行保留。
- 控制台不是授权依据；后端事务重验、expected version、行锁和 append-only 审计仍是唯一安全边界。
- 本阶段不调用收费 API、不发送真实通知、不自动修改副本或生产策略。

## 撤销条件

当事件量、扫描延迟或跨区域要求经生产证据证明 PostgreSQL 队列无法满足，且替代事件平台能保留同事务 Outbox、
幂等 consumer、即时撤权、安全投影、版本化状态和 PostgreSQL 最终权威时，可迁移事件分发；检测规则、状态事实与
审计仍须保持可回放和可恢复。
