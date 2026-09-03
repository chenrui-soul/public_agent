# public_agent 生产部署与容量治理运行手册

## 1. 适用范围

本手册适用于 `docker-compose.production.yml` 的单主机生产拓扑。PostgreSQL 是 Outbox、租约、Worker
heartbeat、重试和容量判断的唯一事实来源。容量检查只输出建议，不会自动扩缩容或修改任务。

当前 `public-agent serve` 运行生产管理应用，装配 `/health/*`、认证管理、反思任务运维、容量治理 API 和
`/console/capacity-governance`。知识、运行和成长等业务路由仍需由具体业务部署显式装配。

## 2. 发布前准备

生产主机需要 Docker Engine 和 Docker Compose。发布产物应使用不可变镜像 digest；仓库内 Dockerfile 默认
使用 `python:3.12-slim-bookworm`，受限网络可通过 `PUBLIC_AGENT_PYTHON_IMAGE` 指定可信镜像代理。

在仓库根目录创建不纳入版本控制的 `secrets/`，并由 Secret Manager 或受控发布流程写入以下文件：

```text
secrets/
├── PUBLIC_AGENT_POSTGRES_PASSWORD
├── PUBLIC_AGENT_DATABASE_URL
├── PUBLIC_AGENT_SECRET_KEY
├── PUBLIC_AGENT_API_TOKEN_PEPPER
└── PUBLIC_AGENT_OPENAI_API_KEY
```

要求：

- 文件只含值本身，不加变量名、引号或换行说明；
- `PUBLIC_AGENT_DATABASE_URL` 使用容器网络主机名 `postgres:5432`；
- Secret 文件权限仅授予部署账号；
- 不把 Secret 值写入 `.env`、命令行参数、镜像层、工单或日志。

## 3. 发布门禁

```powershell
$env:PUBLIC_AGENT_RUN_DB_TESTS = "1"
$env:PUBLIC_AGENT_PYTHON_IMAGE = "python:3.12-slim-bookworm"
python scripts/test_production_deployment.py
```

门禁验证容量/CLI/PostgreSQL 反例、Compose 配置、生产镜像、非 root 用户、CLI 入口和 Alembic head。收费模型
API 不会被调用。若构建网络受限，可把 `PUBLIC_AGENT_PYTHON_IMAGE` 指向经过审核的镜像代理，但正式发布应
记录最终基础镜像 digest。

数据库迁移必须先在可恢复的预发布数据库执行：

```powershell
alembic upgrade head
alembic current
alembic check
```

本版本 head 为 `f1b3c7d9e2a4`（父版本 `e9a2f4c6b810`）。Wave 4 新增再认证事实表、退役审计字段及其约束/索引。

## 4. 首次启动

```powershell
docker compose -f docker-compose.production.yml up -d `
  --scale reflection-worker=1 `
  postgres redis migrate api reflection-worker capacity-monitor
```

检查状态：

```powershell
docker compose -f docker-compose.production.yml ps
docker compose -f docker-compose.production.yml logs --tail 100 migrate api reflection-worker
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

发布必须满足：migrate 退出码为 0，API ready 返回 `{"status":"ready"}`，Worker 日志出现安全
`reflection_worker.starting` 事件且无配置失败机器码。

## 5. 容量观测、趋势与校准

```powershell
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-check
$LASTEXITCODE
```

| 退出码 | 操作 |
|---:|---|
| 0 | 保持当前容量，继续常规观测 |
| 4 | 检查 `reasons`、最老任务年龄和 Worker 最近心跳；确认后按 `recommended_workers` 调整 |
| 5 | 视为严重告警；先恢复至少一个 active Worker，再检查数据库、dead-letter 和积压年龄 |
| 1 | 容量检查本身失败；检查数据库连通、迁移状态和 Secret 挂载 |
| 2 | 配置失败；停止发布并修正阈值或 handler version |

显式扩缩容：

```powershell
docker compose -f docker-compose.production.yml up -d --scale reflection-worker=4 reflection-worker
```

扩容后再次运行 `capacity-check`。缩容前确认没有持续增长的 processing/ready backlog；Compose 停止信号会让
Worker 停止领取新任务并在 30 秒 drain 窗口内收敛，45 秒 stop grace 后仍未完成的任务依赖数据库 lease
过期后由其他 Worker 接管。

建议由外部调度器每分钟运行一次容量检查并采集 JSON，但自动执行 `--scale` 前必须增加持续窗口、冷却时间、
最大变化步长和回归验证。本版本不提供自动控制器。

生产 Compose 默认常驻 `capacity-monitor`，按
`PUBLIC_AGENT_REFLECTION_CAPACITY_SAMPLE_INTERVAL_SECONDS`（默认 60 秒）生成报告并幂等保存观测。检查日志：

```powershell
docker compose -f docker-compose.production.yml logs --tail 100 capacity-monitor
```

查询最近七天小时趋势或三十天日趋势：

```powershell
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-trend
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-trend `
  public-agent capacity-trend --hours 720 --bucket day --limit 30 --pretty
```

从真实终态 Outbox 处理耗时生成校准建议：

```powershell
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-calibrate `
  public-agent capacity-calibrate --lookback-hours 168 --minimum-samples 30 `
  --maximum-samples 10000 --target-drain-seconds 300 --target-utilization 0.70 --pretty
$LASTEXITCODE
```

校准计算 P50/P95/P99、观察吞吐和建议阈值，只写校准历史，不修改环境变量、Compose 副本或运行时设置。
退出码 `6` 表示样本不足，应继续采样而不是降低门槛伪造结论。建议至少覆盖一个完整业务周期，并同时复核
dead-letter 比例、供应商限流和数据库锁等待后，才进入下述受控策略发布流程。

## 5.1 容量阈值变更审批、发布与回滚

默认治理参数由以下环境变量提供，可在命令行显式覆盖：

- `PUBLIC_AGENT_REFLECTION_CAPACITY_POLICY_WINDOW_SECONDS=3600`
- `PUBLIC_AGENT_REFLECTION_CAPACITY_POLICY_MINIMUM_OBSERVATIONS=60`
- `PUBLIC_AGENT_REFLECTION_CAPACITY_POLICY_COOLDOWN_SECONDS=3600`

状态流为 `pending_window → awaiting_approval → approved → cooling_down → effective|ineffective`；拒绝进入
`rejected`，显式回滚进入 `rolled_back`。不得跳步，也不得复用旧 `expected_version`。

```powershell
# 1. 从 calibration_id 创建请求；首次会把当前 Settings 等值登记为数据库 active 基线
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-policy `
  public-agent capacity-policy create --calibration-id <calibration-id> `
  --operator requester@example.com --pretty

# 2. 等待持续窗口与观测跨度满足后验证，成功后 version 1 -> 2
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-policy `
  public-agent capacity-policy validate --request-id <request-id> --expected-version 1 --pretty

# 3. 具名人工审批，成功后 version 2 -> 3；拒绝则把 approve 改为 reject
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-policy `
  public-agent capacity-policy approve --request-id <request-id> --expected-version 2 `
  --operator reviewer@example.com --pretty

# 4. 显式发布，成功后 version 3 -> 4，并进入冷却期
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-policy `
  public-agent capacity-policy publish --request-id <request-id> --expected-version 3 `
  --operator publisher@example.com --pretty

# 5. 冷却期结束后复核，成功后 version 4 -> 5
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-policy `
  public-agent capacity-policy review --request-id <request-id> --expected-version 4 `
  --operator reviewer@example.com --pretty
```

查询 active 策略或请求：

```powershell
public-agent capacity-policy show --pretty
public-agent capacity-policy show --request-id <request-id> --pretty
```

退出码 `7` 表示状态冲突、窗口未到、冷却未结束、样本或跨度不足；数据库不会推进请求。退出码 `8` 表示目标
不存在于当前 handler version。任何失败都先重新 `show` 获取最新 version，不得盲目重放旧命令。

复核为 `ineffective` 时，或冷却期内出现需要紧急撤销的回归，执行：

```powershell
docker compose -f docker-compose.production.yml --profile ops run --rm capacity-policy `
  public-agent capacity-policy rollback --request-id <request-id> --expected-version <current-version> `
  --operator publisher@example.com --reason "post-release regression" --pretty
```

回滚仅在本请求的 published policy 仍为 active 且 base policy 仍是其 exact previous policy 时成功。它只切换
数据库阈值，不修改 Worker 副本。是否按 `recommended_workers` 调整副本仍是独立人工运维动作。

## 5.2 RBAC 审批控制台与漂移告警

打开：

```text
http://<api-host>:8000/console/capacity-governance
```

只能使用由认证管理流程签发的 Bearer Token。治理 Principal 必须属于
`PUBLIC_AGENT_REFLECTION_CAPACITY_GOVERNANCE_TENANT_ID`，设置 `all_agents=true`，且没有 agent grant。推荐角色：

| 角色 | 权限 |
|---|---|
| viewer | `operations.capacity:read`, `operations.capacity_alerts:read` |
| proposer | `operations.capacity:read`, `operations.capacity:request` |
| approver | `operations.capacity:read`, `operations.capacity:approve` |
| publisher | `operations.capacity:read`, `operations.capacity:publish` |
| reviewer | `operations.capacity:read`, `operations.capacity:review` |
| rollback_operator | `operations.capacity:read`, `operations.capacity:rollback` |
| alert_operator | `operations.capacity_alerts:read`, `operations.capacity_alerts:manage` |
| auditor | `operations.capacity_audit:read` |
| incident_viewer | `operations.capacity_incidents:read` |
| incident_operator | `operations.capacity_incidents:read`, `operations.capacity_incidents:manage` |
| remediation_viewer | `operations.capacity_remediations:read` |
| remediation_requester | `operations.capacity_remediations:read`, `operations.capacity_remediations:request` |
| remediation_approver | `operations.capacity_remediations:read`, `operations.capacity_remediations:approve` |
| remediation_executor | `operations.capacity_remediations:read`, `operations.capacity_remediations:execute` |
| remediation_verifier | `operations.capacity_remediations:read`, `operations.capacity_remediations:verify` |
| postmortem_viewer | `operations.capacity_postmortems:read` |
| postmortem_requester | `operations.capacity_postmortems:read`, `operations.capacity_postmortems:request` |
| postmortem_reviewer | `operations.capacity_postmortems:read`, `operations.capacity_postmortems:review` |
| knowledge_feedback_viewer | `operations.capacity_knowledge_feedback:read` |
| knowledge_feedback_reporter | `operations.capacity_knowledge_feedback:read`, `operations.capacity_knowledge_feedback:report` |
| knowledge_feedback_reviewer | `operations.capacity_knowledge_feedback:read`, `operations.capacity_knowledge_feedback:review` |
| knowledge_quality_viewer | `operations.capacity_knowledge_quality:read` |
| knowledge_quality_assessor | `operations.capacity_knowledge_quality:read`, `operations.capacity_knowledge_quality:assess` |
| knowledge_recovery_viewer | `operations.capacity_knowledge_recovery:read` |
| knowledge_recovery_requester | `operations.capacity_knowledge_recovery:read`, `operations.capacity_knowledge_recovery:request` |
| knowledge_recovery_reviewer | `operations.capacity_knowledge_recovery:read`, `operations.capacity_knowledge_recovery:review` |

不要给单一人工账号默认合并全部角色。请求人不能批准自己的请求；客户端不能提交 operator，审计身份只取
数据库 Principal subject。Token 仅保存在当前标签页，关闭或点击“断开”后清除。401 表示 Token 缺失/错误/
过期/撤销；403 表示当前数据库权限或 global scope 不满足；409 表示 expected version 或状态已变化，刷新后重试。

`capacity-monitor` 在每次新观测持久化后扫描漂移。也可由具备 `operations.capacity_alerts:manage` 的值守人员在
控制台显式触发。检查告警时：

1. 比较 expected/observed 指纹、样本数、最近观测时间和当前 active policy；
2. `acknowledged` 仅表示已接手，不表示漂移恢复；
3. 不得通过直接改表关闭告警；新观测恢复到当前 expected 后系统自动 `resolved`；
4. 相同漂移复发会重开并累加 `reopened_count`；策略切换后旧 expected 告警在新观测到达时关闭；
5. critical 告警先排查错误镜像、旧配置进程和跨主机未同步配置；本系统不自动扩缩容或发送外部通知。

审计表 `reflection_capacity_governance_audit_events` 为 append-only；成功动作与状态同事务，拒绝/冲突独立追加。
如 UPDATE 被数据库 trigger 拒绝，这是预期保护，禁止停用 trigger 绕过。

审计员在控制台“治理审计历史”中筛选 actor subject、action 和 outcome；API 还支持 `occurred_from`、
`occurred_to`、`limit` 和 cursor。不要给 auditor 合并任何请求、审批、发布、回滚或告警管理权限。响应不包含
Token ID；分页 cursor 不能跨 actor、handler 或筛选条件复用。

未确认告警的响应时限由以下配置决定：

```text
PUBLIC_AGENT_REFLECTION_CAPACITY_ALERT_RESPONSE_WARNING_SECONDS=900
PUBLIC_AGENT_REFLECTION_CAPACITY_ALERT_RESPONSE_CRITICAL_SECONDS=3600
```

warning deadline 后状态为 `due`，critical deadline 后为 `breached`；确认后为 `acknowledged`，恢复后为
`resolved`。这些状态只用于值守排序，不会自动确认、关闭告警或发送通知。

治理知识质量趋势与风险扫描使用以下生产变量：

| 变量 | 默认值 | 允许范围/约束 |
|---|---:|---|
| `PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_QUALITY_RISK_WINDOW_SECONDS` | 604800 | 3600-2592000 秒；缩短会降低历史覆盖，扩大前先验证索引和扫描耗时 |
| `PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_UNSAFE_WARNING_COUNT` | 2 | 至少 2，且不得大于 unsafe critical |
| `PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_UNSAFE_CRITICAL_COUNT` | 3 | 至少 2，且不得大于 maximum snapshots |
| `PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_DEGRADED_WARNING_COUNT` | 2 | 至少 2，且不得大于 degraded critical |
| `PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_DEGRADED_CRITICAL_COUNT` | 4 | 至少 2，且不得大于 maximum snapshots |
| `PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_QUALITY_MAXIMUM_SNAPSHOTS` | 1000 | 2-100000，必须覆盖两类 critical 阈值 |
| `PUBLIC_AGENT_REFLECTION_CAPACITY_KNOWLEDGE_QUALITY_MAXIMUM_TREND_BUCKETS` | 366 | 1-3660；API 自身仍限制单页 `limit <= 366` |

修改这些变量后必须先运行配置测试、PostgreSQL 定向测试和生产门禁，再重启 API 与 capacity-monitor。阈值变化
不会追溯修改快照，也不会自动生成快照、恢复知识或执行外部动作；若扫描返回 `truncated=true`，先缩小窗口或经
容量验证后提高上限，不能把截断结果解释为健康。

每周和发布后运行一次控制台“运行只读演练”，或调用：

```text
GET /v1/operations/capacity-governance/drill-report
Authorization: Bearer <auditor-token>
```

报告应为 `passed=true`，并覆盖 `current_actor_revalidated`、`role_separation`、`audit_append_only`、
`alert_lifecycle_constraints`、`audit_query_indexes`、`incident_lifecycle_constraints`、`incident_query_indexes`、
`remediation_lifecycle_constraints`、`remediation_query_indexes`、`postmortem_lifecycle_constraints`、
`postmortem_query_indexes`、`knowledge_feedback_lifecycle_constraints`、`knowledge_feedback_query_indexes`、
`knowledge_quality_snapshot_controls`、`knowledge_quality_query_indexes`、
`knowledge_recovery_lifecycle_constraints`、`knowledge_recovery_query_indexes`、
`knowledge_recertification_lifecycle_constraints` 和 `knowledge_recertification_query_indexes`。质量项必须证明快照 UPDATE 拒绝、
assessment/count/version/evidence 约束和 `(tenant_id, handler_version, captured_at, id)` 趋势索引存在；恢复项必须
证明单活动申请、生命周期/version CHECK 和状态/postmortem 查询索引存在。失败时先停止治理发布并核对迁移 head；不要通过手工建索引、
停用 trigger 或跳过 CHECK 来伪造通过。该演练只读，不创建临时 Principal/Token，也不修改请求、告警或审计行。

治理事件由 capacity-monitor 自动扫描，也可由具备事件权限的人员在控制台只读查看和确认。事件队列覆盖：

1. 固定时间桶内 denied/conflict 审计达到 warning/critical 阈值；
2. 未确认漂移告警进入 SLA `breached`；
3. 告警 `reopened_count` 达到重复复发阈值；
4. 只读演练任一 catalog/职责检查失败；
5. 风险窗口内持续出现独立 `unsafe` 质量证据，且最新快照仍为 unsafe；
6. 风险窗口内重复出现独立 `degraded` 质量证据，且最新快照仍为 degraded；
7. 已恢复过的 postmortem 再次进入 `quarantined`，且新隔离时间晚于最近恢复时间。

capacity-monitor 同时执行只读知识生命周期扫描，并在采样 JSON 的 `knowledge_lifecycle` 字段输出
`current/due/overdue/quarantined/retired` 聚合。到期只代表需要人工发起再认证，不会自动认证、隔离、退役、
发送通知或改变 RAG；`retired` 保留完整内容、向量、反馈和谱系，但 KnowledgeRetriever 永久排除该状态。
再认证请求必须绑定当前 postmortem 版本和质量证据指纹，request/review/retirement 使用独立权限，重复请求由
幂等键收敛。生产排障时先查看生命周期聚合，再到再认证队列确认 awaiting_review；不得通过修改时间戳或直接改表绕过审批。

`acknowledged` 只表示 incident operator 已接手，禁止人工直接改表为 resolved。系统只有在新 alert 版本、后续
演练事实、新审计 bucket、更新质量快照或更新 postmortem 隔离/恢复历史证明规则不再命中时才关闭；复发后自动
重开并清除旧确认。时间流逝、确认、恢复申请或 RAG 命中不是恢复证据。事件查询 cursor 绑定当前
Principal、handler、signal、severity 和 status，不能跨身份或筛选重放。事件响应和审计安全投影不得包含 Token ID、
Authorization、数据库 URL 或原始异常正文。当前没有邮件、飞书、钉钉、Webhook、PagerDuty 或 SIEM 通知；不得
把外部系统当作事件状态事实源。

质量趋势可由具备 `operations.capacity_knowledge_quality:read` 的人员在控制台“治理知识质量趋势”面板查询，或调用：

```text
GET /v1/operations/capacity-governance/knowledge-quality-trend
  ?captured_from=<UTC>&captured_to=<UTC>&bucket=hour|day
  &assessment=insufficient|healthy|degraded|unsafe&limit=<1-366>
Authorization: Bearer <knowledge-quality-token>
```

控制台只提供 24/72/168 小时窗口；API 的 `captured_from/to` 必须是 UTC 且前小于后。响应按桶返回 total、四类
assessment 数和 distinct postmortem 数，空桶补零。cursor 绑定 actor、handler、bucket、assessment 和完整窗口，
更换任一筛选条件后必须从第一页重新查询。403 只表示趋势读取权限不足，不影响事件、反馈、快照或恢复队列。

风险排障按信号执行：`knowledge_unsafe_persistent` 先确认当前知识是否仍 quarantined、最新 unsafe 快照和独立证据数；
`knowledge_degraded_repeat` 复核反馈分类、多个 evidence fingerprint 和最新 assessment；`knowledge_requarantined` 复核
restore count、最近恢复/隔离时间及新 knowledge version。三类风险只能通过新的质量或隔离事实恢复，不得手工关闭、
删除旧快照、缩短保留期或把 advisory-only RAG 结果当作证据。

事件确认后，由 remediation requester 在控制台创建固定 Playbook 处置单。不得修改 Playbook 映射或把命令、脚本、
日志正文、Token 写入处置字段。处置步骤如下：

1. requester 创建 `awaiting_approval`，同一 `incident_id + reopened_count` 只能创建一次；
2. 独立 approver 批准或拒绝，请求人不能自批；
3. executor 在外部完成获批人工动作后，只记录 `completed/failed` 和受限证据码；系统本身不执行生产变更；
4. 独立 verifier 只能在事件于执行后产生新版本并自动 resolved 后验证，执行人不能自验；
5. 未恢复、旧版本、事件再次复发或权限已撤销时返回 409/403，刷新事实后处理，禁止直接改表。

处置 API 为 `GET /remediations`、`POST /incidents/{id}/remediations`、`POST /remediations/{id}/approve|reject`、
`POST /remediations/{id}/execution` 和 `POST /remediations/{id}/verify`。外部工单或自动化未来只能消费获批记录，
不得替代 PostgreSQL 的事件与处置状态。

处置进入 `verified` 后，postmortem requester 可以创建治理复盘。操作规则：

1. 每个 remediation 最多一份复盘，创建时保存 incident cycle/version 和 remediation version 快照；
2. 根因、影响、预防措施必须使用控制台提供的 Playbook 兼容枚举；摘要限 10-1000 字，禁止凭据、连接串、
   代码块、Shell/SQL/容器编排命令；
3. 独立 postmortem reviewer 批准或拒绝，请求人不能自审；批准前会重新鉴权、锁定记录并重验来源版本；
4. 批准与知识发布同事务完成；拒绝、撤权、旧 version 或来源漂移不会留下词法/向量知识；
5. 发布知识固定为 `operations.governance.postmortems` namespace，仅供值守参考，不是授权、恢复证据或执行指令。

复盘 API 为 `GET /postmortems`、`GET /postmortems/{id}`、`POST /remediations/{id}/postmortems` 和
`POST /postmortems/{id}/approve|reject`。治理 RAG 调用方必须同时使用 domain `operations-governance` 和 access tag
`operations.governance:advisory`；缺少任一项时检索结果应为空。上线后抽样核对命中 metadata 中
`advisory_only=true`、`authorization_source=false`、`recovery_evidence=false` 和
`execution_instruction=false`，并确认引用保留 incident/remediation/version/content fingerprint 谱系。

## 6. Outbox 分区归档与安全清理

默认命令只预览，不修改数据：

```powershell
docker compose -f docker-compose.production.yml --profile ops run --rm outbox-maintain
```

先只归档，不物理清理：

```powershell
docker compose -f docker-compose.production.yml --profile ops run --rm outbox-maintain `
  public-agent outbox-maintain --execute --archive-after-days 7 `
  --purge-after-days 90 --batch-size 500 --maximum-batches 10 --pretty
```

完成归档核验和数据库备份后，才允许清理：

```powershell
docker compose -f docker-compose.production.yml --profile ops run --rm outbox-maintain `
  public-agent outbox-maintain --execute --prune --archive-after-days 7 `
  --purge-after-days 90 --batch-size 500 --maximum-batches 10 --pretty
```

安全不变量：

- 只处理当前 handler version 的 `succeeded/dead_letter` 终态记录；
- 归档身份是 `job_id + completed_at + version`，归档批次使用 `FOR UPDATE SKIP LOCKED`；
- 清理前必须存在当前版本的精确归档副本；
- 有 `reflection_job_retry_requests` 引用的任务永不物理清理；
- `--prune` 没有同时提供 `--execute` 时配置失败，退出码为 2；
- 归档父表没有指向运行表的外键，源任务清理后历史快照仍保留。

每次执行先记录 dry-run 的 `archive_eligible`、`purge_eligible` 和
`purge_blocked_by_retry_requests`，执行后对比 `before/after`。单次最大处理量为
`batch_size * maximum_batches`；需要继续时重复运行，不要临时扩大为无界事务。

当前迁移创建 `pre_2020`、`2020_2030`、`2030_2040` 和 `post_2040` 范围分区。新增更细粒度分区、导出到
对象存储或制定归档表自身保留期前，必须另做迁移、恢复演练和查询性能验证。

## 7. 告警与巡检

默认阈值：

| 指标 | Warning | Critical |
|---|---:|---:|
| ready backlog | 100 | 500 |
| oldest ready age | 300 秒 | 1800 秒 |
| dead-letter | 1 | 10 |
| Worker stale | 任意大于 0 即 Warning | 有积压且 active=0 时 Critical |

每日巡检：

- API live/ready；
- capacity-check 状态、原因码、recommended workers 和 scale delta；
- capacity-monitor 最近采样时间、warning/critical 趋势和观测表增长；
- 最近质量风险扫描的 `truncated`、扫描快照/复盘数和七类 incident 变化；
- PostgreSQL 容量、连接、慢查询、备份状态和 WAL/磁盘增长；
- dead-letter 数和人工重试审计；
- Worker stale/errored 数与最后心跳；
- Redis 内存和淘汰量。Redis 故障不能改变任务正确性。

每周巡检：

- 随机恢复一次 PostgreSQL 备份到隔离环境；
- 检查 `requirements.lock`、基础镜像和依赖漏洞；
- 检查日志轮转、磁盘水位和容器资源上限；
- 复核容量阈值是否与实际处理速率匹配；
- 运行一次 `capacity-calibrate` 并与上次校准比较，样本不足时记录但不调整阈值；
- 预览 Outbox 维护，核对待归档、可清理和被 retry history 阻塞的数量；
- 检查归档分区大小、索引膨胀和恢复抽样。
- 以 auditor Token 运行只读治理演练，复核审计分页、SLA breached 告警和 append-only 保护。
- 抽样复核已发布 postmortem 的来源版本、内容指纹、GIN/HNSW 索引命中和 advisory-only metadata。
- 复核 quarantined postmortem 的最新 `unsafe` 质量快照、24 小时保留窗口、活动恢复申请和职责分离；不得通过改表缩短窗口。
- 查询 7 天日桶与 72 小时小时桶质量趋势，复核 unsafe/degraded 数、distinct postmortem、空桶补零和 cursor 筛选绑定。
- 抽样复核三类知识质量风险的 stable fingerprint、evidence fingerprint、固定 Playbook 和仅由新事实恢复的生命周期。

## 8. 常见故障

### 有积压但 active Worker 为 0

1. 查看 Worker 配置失败事件；
2. 检查 `PUBLIC_AGENT_OPENAI_API_KEY`、数据库 URL 和 Secret 文件权限；
3. 检查 migrate 是否成功；
4. 启动至少一个 Worker，再运行容量检查。

### stale Worker 增长

检查容器是否被 OOM、CPU 限流、网络隔离或长时间卡在供应商调用。不要直接修改 heartbeat 表。确认旧容器停止
后重建副本；相同 Worker ID 的旧进程会被 instance token fencing。

### dead-letter 增长

先通过安全运维 API 查看机器错误码和任务作用域，禁止查询或打印原始运行正文。修复根因后使用
expected-version + Idempotency-Key 人工重试，保留审计链。

### capacity-check 返回 1

该状态表示报告不可用，不代表容量健康。检查数据库 ready、迁移 head 和容器 Secret 挂载；禁止把失败降级为
healthy。

### capacity-calibrate 返回 6

样本窗口内没有达到 `minimum_samples` 个真实已完成任务。保持现有阈值，检查 handler version 与时间窗口是否
正确，继续由 capacity-monitor 积累事实。禁止把最小样本数降到无法代表业务周期的水平。

### capacity-policy 返回 7 或 8

`7` 是失败关闭：检查请求最新状态/version、持续窗口、冷却截止、观测数量/跨度和当前 active policy；不要通过
直接改表或缩短窗口绕过。`8` 表示 request/calibration 不属于当前 handler version 或不存在，先检查命令作用域。

### outbox-maintain 归档或清理失败

先保留原表，不重试清理。检查迁移 head、目标分区、数据库锁等待和安全机器码。若
`purge_blocked_by_retry_requests > 0`，这是预期保护，不得删除 retry request 绕过审计保留要求。归档失败时
源记录仍保留；清理只在精确归档存在时发生。

### 治理知识恢复返回 409

先刷新 postmortem、质量快照和恢复申请，核对当前 postmortem version、knowledge version、证据指纹、
`last_quarantined_at` 和是否已有 `awaiting_review` 申请。24 小时保留期未到、快照不是最新 `unsafe`、反馈事实变化、
expected version 过旧或重复活动申请都会失败关闭。不得直接 UPDATE 快照、复盘或反馈绕过；应生成新快照或等待
保留期结束。批准失败时确认审批人不是请求人、原安全报告人或确认人，并检查 Token 是否仍 active 且具有独立
review 权限。

### 质量趋势返回 422 或风险扫描 truncated

422 通常表示时间不是 UTC、窗口无序、bucket/assessment 非法、limit 超过 366 或 cursor 与当前筛选不一致。重新用
明确 UTC 窗口从第一页查询，不得复用旧 cursor。`truncated=true` 表示快照、postmortem、候选或既有事件超过扫描
上限；此时系统故意停止不完整的质量风险创建/恢复判断。先检查 captured-time 索引、窗口范围、表增长和查询耗时，
再决定缩小窗口或按容量证据提高上限；禁止直接把事件改为 resolved。

### 知识质量风险事件持续或复发

先按 incident `signal` 查询对应快照/postmortem 新事实，并确认 handler 与 tenant 一致。unsafe/degraded 只有最新
assessment 改变且 captured time 晚于事件证据后才可能恢复；requarantine 只有 postmortem 在新版本上出现晚于隔离
证据的恢复/状态事实才可能恢复。确认动作、等待时间、恢复申请和外部工单不会关闭事件。若相同 stable fingerprint
在新 evidence 下重开，这是预期 recurrence 行为，不得删除历史事件或处置单。

## 9. 回滚

应用回滚：把 `PUBLIC_AGENT_IMAGE` 切回上一不可变 digest，再执行：

```powershell
docker compose -f docker-compose.production.yml up -d --no-build api reflection-worker
```

数据库回滚只在确认旧镜像不理解新 Schema 时执行。Wave 4 的 `f1b3c7d9e2a4` 增加再认证事实和退役字段；
先停止 API、capacity-monitor、reflection-worker 并导出再认证/退役事实，再执行：

```powershell
docker compose -f docker-compose.production.yml stop api capacity-monitor reflection-worker
alembic downgrade e9a2f4c6b810
alembic upgrade f1b3c7d9e2a4
```

回滚演练必须在预发布数据库完成 upgrade → downgrade → upgrade 往返，并核对 `alembic current` 为
`f1b3c7d9e2a4`。生产门禁会拒绝旧 head、缺少再认证 ground-truth case 或静态 Compose/Dockerfile 契约不完整。
v0.28 的 `e9a2f4c6b810` 扩展七类事件、Playbook/证据码
CHECK，并新增质量快照 captured-time 趋势索引。先停止 API 与 capacity-monitor，导出三类质量风险 incident、
关联 remediation 和处置审计，再回退到 v0.27 head：

```powershell
docker compose -f docker-compose.production.yml stop api capacity-monitor
alembic downgrade d8f1c2a4b730
```

该操作删除 `ix_capacity_knowledge_quality_tenant_captured`，并把 incident、Playbook 和 execution evidence CHECK
缩回 v0.27 的四类事件集合；不会删除质量快照、反馈、恢复申请、postmortem 内容、向量或恢复谱系。回滚前必须
处理或导出所有三类新风险事件及其处置事实，否则旧应用无法解释这些枚举值。

v0.27 Wave 4 的 `d8f1c2a4b730` 新增不可变质量快照、
隔离恢复申请和复盘恢复历史。先停止 API，导出快照、恢复申请、反馈和 postmortem 谱系，再回退到 Wave 3：

```powershell
docker compose -f docker-compose.production.yml stop api
alembic downgrade c7a4d2e9f610
```

该操作删除质量快照与恢复申请表及其索引/触发器，并移除复盘恢复历史列；不会删除反馈、复盘内容、向量、事件、
处置或审计事实。所有 `awaiting_review` 恢复申请必须先导出并停止审批。v0.27 Wave 3 的 `c7a4d2e9f610`
新增治理知识反馈表，并允许已发布复盘进入 `quarantined`。确认没有需要保留的隔离恢复流程后，再回退到 v0.26：

```powershell
docker compose -f docker-compose.production.yml stop api
alembic downgrade b6d8e1f3a420
```

该操作删除反馈表，并要求所有复盘均已退出 `quarantined`；迁移不会删除复盘内容、事件或处置事实。v0.26 的
`b6d8e1f3a420` 新增治理复盘、全文/向量索引和
审计 `postmortem_id` 外键。先停止 API、导出复盘与来源谱系，再回退到 v0.25：

```powershell
docker compose -f docker-compose.production.yml stop api
alembic downgrade 9f4e7c2d1a60
```

该操作删除复盘表和治理知识索引，不删除事件、处置、告警、策略或既有审计事实。v0.25 的
`9f4e7c2d1a60` 新增处置表；继续回滚前导出处置事实并停止 API，再降级到 v0.24：

```powershell
docker compose -f docker-compose.production.yml stop api
alembic downgrade 6b9d2f4a8c71
```

该操作删除处置表，不删除治理事件。v0.24 的 `6b9d2f4a8c71` 新增治理事件表和审计
`incident_id` 外键。先停止 API 与 capacity-monitor，导出事件事实，再回退到 v0.23：

```powershell
docker compose -f docker-compose.production.yml stop api capacity-monitor
alembic downgrade e3c8a1f7b920
```

该操作删除治理事件表和审计 `incident_id` 列，不删除既有告警或审计行。v0.23 的 `e3c8a1f7b920` 只增加审计过滤索引，先回退
应用后可安全降级到 v0.22：

```powershell
alembic downgrade 2d6f8b1c4a90
```

该操作不删除审计或告警事实，只移除 v0.23 查询索引。继续回滚 v0.22 时，`2d6f8b1c4a90` 新增容量漂移告警
和追加治理审计；先停止 API 与 capacity-monitor 并导出这两张表，再 downgrade 到 v0.21 head：

```powershell
docker compose -f docker-compose.production.yml stop api capacity-monitor
alembic downgrade f2a7d9c4e681
```

该操作删除 v0.22 告警与治理审计。继续回滚时，`f2a7d9c4e681` 新增版本化容量策略与变更请求；先导出
这些治理事实，再 downgrade 到 v0.20 head：

```powershell
alembic downgrade c9f4e2a7b613
```

该操作删除 v0.21 策略/审批/复核历史，应用会回退 Settings 阈值。继续向下回滚时，`c9f4e2a7b613` 会创建容量历史与分区归档表，并给
`outbox_jobs` 增加处理耗时列；downgrade 会删除这些表、分区、历史观测、校准结果和耗时列，因此必须先完成
逻辑备份或导出并停止 capacity-monitor/outbox-maintain：

```powershell
docker compose -f docker-compose.production.yml stop capacity-monitor
alembic downgrade b7e2c4a9d610
```

回滚前必须有最新可恢复备份并评估 DDL 锁等待。回滚后旧版 `capacity-check` 仍可用，但 trend、calibrate、
outbox-maintain 和持久观测不可用；运行 API ready、Worker 启动和容量检查三项烟雾测试。

## 10. 已知限制

- Compose 是单主机编排，不提供跨可用区调度；
- 校准使用应用记录的真实任务处理耗时，但不会单独建模供应商速率限制、时段季节性或数据库锁等待；
- 容量趋势已持久化，但尚未提供自动保留、预测模型或自动扩缩容控制器；
- 容量策略治理已提供 Web 审批控制台，但尚无 OIDC/SSO、组织工作流编排、外部通知或自动扩缩容控制器；
- 审计查询和只读演练已内置，但尚未接入 SIEM、WORM 导出或外部通知；SLA breached 只在控制台/API 展示；
- 治理复盘检索默认使用离线确定性嵌入并作为 advisory-only 参考；已支持安全反馈隔离、质量快照和人工恢复，
  质量趋势和三类复发风险，但尚未提供自动定时快照、自动恢复、自动重建、外部治理通知或独立时序仓库；
- 控制台不提供 Token 签发或权限管理向导，安全管理员仍需通过认证管理 API 按最小权限建立 Principal/Token；
- 效果判定采用 warning/critical 比例及原始 ready/age/dead-letter 回归护栏；
- Outbox 已提供原生范围分区和受保护清理，但尚未导出对象存储或自动创建更细粒度未来分区；
- 默认 API 容器装配生产管理控制面；知识、运行和成长等面向业务的完整服务仍需独立生产装配。
