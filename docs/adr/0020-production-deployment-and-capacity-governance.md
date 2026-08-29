# ADR 0020: 生产部署编排与 Reflection Worker 容量治理

- 状态：已接受并实现
- 日期：2026-08-25
- 决策范围：生产镜像、Docker Compose 拓扑、Reflection Worker 容量判级与发布门禁

## 背景

v0.18 已经提供 PostgreSQL Outbox、租约、Worker heartbeat/fencing、安全运维 API 和
`public-agent reflection-worker`，但部署方仍需自行构建镜像、安排迁移、分配资源、注入 Secret，并根据积压和
Worker 最近状态判断是否扩容。若每个部署脚本各自实现这些规则，会产生三类风险：

1. 任务状态与部署平台副本数形成两个互相冲突的事实源；
2. 连接串、Token 或供应商异常正文进入命令行、日志或 Compose 环境；
3. 仅按当前队列长度扩缩容，忽略最老任务年龄、dead-letter、失联 Worker 和处理器版本隔离。

## 决策

### 1. PostgreSQL 保持容量事实唯一来源

`PostgresReflectionJobStore.capacity_snapshot()` 在同一数据库会话中读取当前 handler version 的 Outbox 聚合和
Worker heartbeat 聚合。Redis 仍只能作为非权威唤醒提示，不能决定任务状态、Worker 活性或副本建议。

容量报告只包含计数、时间、handler version、机器原因码和阈值，不包含任务、输出、轨迹、payload、lease
token、数据库 URL、API Key 或原始异常正文。

### 2. 三级判级和有界副本建议

`public-agent capacity-check` 输出单个 JSON 文档，并使用稳定退出码：

| 状态 | 退出码 | 含义 |
|---|---:|---|
| healthy | 0 | 当前容量和 Worker 事实未触发告警 |
| warning | 4 | 达到预警阈值、存在 stale/errored Worker 或建议扩容 |
| critical | 5 | 达到严重阈值，或有工作量但没有 active Worker |
| error | 1 | 数据库、运行或清理失败 |
| invalid | 2 | 配置或装配失败 |

推荐 Worker 数为：

```text
ceil((pending + retry_wait + processing) / target_jobs_per_worker)
```

结果始终限制在 `minimum_workers..maximum_workers`，`scale_delta = recommended_workers - active_workers`。
该值只作为运维建议，不直接调用 Docker、Kubernetes 或云平台扩缩容 API。

### 3. 首版生产编排使用 Dockerfile + Docker Compose

生产镜像采用多阶段构建、精确依赖约束、非 root UID/GID `10001:10001`，运行层只保留应用 Wheel、Alembic
配置和迁移。应用容器使用只读根文件系统、`no-new-privileges`、移除 Linux capabilities、有限 PID/CPU/内存
和轮转日志。

`docker-compose.production.yml` 定义 PostgreSQL、Redis、一次性 migrate、API、可横向扩容的
reflection-worker，以及 `ops` profile 下的一次性 capacity-check。API、Worker 和容量检查必须等迁移成功后
启动；Worker 的 `stop_grace_period` 大于默认 drain timeout。

Compose 不设置 `PUBLIC_AGENT_REFLECTION_WORKER_ID`。每个扩容容器使用应用默认的 hostname + PID 标识，
避免多个副本互相替换注册并触发 fencing。

### 4. Secret 只通过挂载目录注入

生产进程设置 `PUBLIC_AGENT_SECRETS_DIR=/run/secrets`。Secret 文件名必须与 Pydantic Settings 的带前缀字段
一致：`PUBLIC_AGENT_DATABASE_URL`、`PUBLIC_AGENT_SECRET_KEY`、`PUBLIC_AGENT_OPENAI_API_KEY`。
PostgreSQL 自身密码使用独立 `PUBLIC_AGENT_POSTGRES_PASSWORD` 文件。

## 候选方案与反选论证

### 方案 A：立即采用 Kubernetes/HPA

优点是原生滚动发布、Secret、探针和扩缩容；缺点是当前没有集群、指标适配器和多可用区负载证据。HPA 仅凭
CPU 无法表达 handler version、dead-letter 和最老任务年龄，仍需本 ADR 的 PostgreSQL 容量事实。

不选择原因：会在容量规则尚未经过真实负载校准前引入额外控制面，并不能替代本次纵向链路。

### 方案 B：引入 Celery/Kafka/SQS 作为队列和扩缩容事实

优点是成熟的消费生态和独立扩展；缺点是与现有 PostgreSQL Outbox、租约、fencing、人工重试和审计形成双写及
一致性问题。

不选择原因：当前 PostgreSQL 领取与索引尚未被容量证据证明不足，过早引入第二事实源会扩大故障面。

### 方案 C：Docker Compose + PostgreSQL 容量建议（选择）

优点是复用现有状态机、部署门槛低、可完整回滚，并保留未来接 Kubernetes 或云平台的接口边界；代价是首版
扩缩容需要运维显式执行，Compose 不提供跨主机调度和自动滚动发布。

## 回滚

1. 应用镜像回退到上一不可变 digest；
2. 将 Worker 副本缩回上一稳定数量；
3. `b7e2c4a9d610` 仅增加两个索引，可在维护窗口 downgrade 到 `a4d6f8b2c510`；
4. 移除 `capacity-check` 不影响 Outbox、Worker、租约、heartbeat 或安全运维 API 的正确性。

## 撤销条件

出现以下任一证据时重新评估本决策：

- PostgreSQL claim/capacity 查询的 P95 或锁等待无法通过索引和批量参数满足目标；
- 单机 Compose 无法满足可用区、滚动发布或故障恢复要求；
- 已有稳定指标适配器可把本报告转换为经过回归验证的自动扩缩容信号；
- 跨区域消费或吞吐证明需要独立消息平台。

## 验证证据

- `tests/test_capacity.py`、`tests/test_capacity_cli.py` 和 PostgreSQL handler 隔离/heartbeat 聚合反例；
- Alembic `a4d6f8b2c510 -> b7e2c4a9d610 -> a4d6f8b2c510 -> head` 往返；
- `docker compose -f docker-compose.production.yml config --quiet`；
- `scripts/test_production_deployment.py` 构建镜像并验证非 root、CLI 和容器内 Alembic head。

