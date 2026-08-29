# ADR 0019: reflection-worker 生产 CLI 与应用生命周期

## 状态

已接受，2026-08-25。

## 背景

ADR 0017 和 ADR 0018 已确定 PostgreSQL Outbox、租约 Worker、实例 heartbeat、积压查询和安全 dead-letter
治理，但既有代码仍缺少一个可由进程管理器直接启动的生产入口。部署方若自行拼装 Database、模型
Provider、反思管线和 Runner，容易产生配置漂移、密钥进入进程参数、信号处理绕过有限 drain，或异常正文
进入日志的问题。

生产入口必须保留既有不变量：PostgreSQL 继续决定任务、租约、heartbeat、重试和终态；CLI 不能建立第二套
任务状态机；缺少生成模型凭据时必须失败关闭；测试和发布验证不得调用真实收费 API。

## 决策

### 1. 单一应用装配边界

新增 `ReflectionWorkerApplication` 和 `build_reflection_worker_application(...)`。生产构造函数装配：

- `Database`；
- `OpenAIModelProvider` 与完整轨迹 `ReflectionEngine`；
- `PostgresLearningStore`、`PostgresKnowledgeAssetPublisher`；
- `KnowledgeSedimentationPipeline`；
- `PostgresReflectionJobStore`、`ReflectionWorker`、`ReflectionWorkerRunner`。

应用启动先执行数据库 ping，再进入 Runner。应用拥有自己创建的 Provider 和 Database；关闭时两个资源都要
尝试释放，即使其中一个清理失败也不能跳过另一个。注入的测试模型或数据库由调用方拥有，应用不越权关闭。

### 2. 环境变量优先的有界配置

Worker ID、handler version、尝试次数、退避、租约、heartbeat、poll、jitter 和 drain timeout 使用
`PUBLIC_AGENT_REFLECTION_*` Settings。CLI 仅允许覆盖这些非敏感参数，并执行单字段和跨字段校验：

- retry base 不得大于 retry maximum；
- heartbeat 必须短于 lease；
- jitter 不得大于 poll interval；
- 所有时间和计数都有明确上下界。

未显式配置 worker ID 时，使用规范化 hostname + PID 生成不超过 200 字符的进程标识。并发部署仍必须为
每个进程配置唯一 worker ID；同名注册会按既有 instance token 规则 fencing 旧进程。

### 3. 密钥不进入命令行

`public-agent reflection-worker` 不提供 `--api-key`。OpenAI API Key 只能来自 Settings 对应环境变量或部署
secret manager。缺失或空白 Key 在创建数据库连接和进入轮询前返回配置失败；日志只输出稳定机器错误码。

### 4. 信号只请求停止

CLI 安装 SIGINT 和 SIGTERM；Windows 可用时额外安装 SIGBREAK。信号处理器只通过事件循环设置一个
`asyncio.Event`，不直接修改任务、租约或 Worker heartbeat。Runner 观察事件后停止领取新任务，并允许当前
任务在有限 drain timeout 内收敛。上下文退出后恢复进程原有信号处理器。

### 5. 安全事件与退出码

stdout/stderr 使用单行 JSON 事件，并只包含 event、worker ID、handler version、处理计数、任务 ID 和安全
机器错误码。不得输出数据库 URL、Token、Authorization header、供应商异常正文、任务正文或运行轨迹。

退出码定义为：

| 退出码 | 含义 |
|---|---|
| 0 | 安全停止 |
| 1 | 运行或资源清理失败 |
| 2 | 配置或装配校验失败 |
| 3 | drain timeout，本地处理已取消，等待数据库租约接管 |
| 130 | 在信号处理器安装前发生原生 KeyboardInterrupt |

## 备选方案

### 由部署脚本直接拼装对象

拒绝。会复制应用装配逻辑，难以保证 ReflectionEngine、成长仓储、handler version 和资源所有权一致。

### 使用 Celery 或 Redis Worker 命令

拒绝。当前没有吞吐证据要求引入第二个任务事实来源，也不能让外部队列绕过 PostgreSQL 租约和 fencing。

### 通过 CLI 参数传 API Key

拒绝。进程参数可能进入 shell 历史、进程列表、诊断快照和部署审计，扩大凭据暴露面。

### 信号到达时立即取消所有任务并写失败终态

拒绝。进程信号不拥有任务终态；应先有限 drain，超时后依赖 PostgreSQL 租约过期和新 token 接管。

## 影响

### 正向

- 进程管理器获得稳定、可测试的生产启动命令；
- 所有部署复用同一反思与知识沉淀装配；
- 密钥、错误和信号边界集中治理；
- 退出码可由 systemd、Kubernetes、Windows Service wrapper 或其他 supervisor 观测；
- 离线替身可覆盖装配和停止逻辑，不产生真实模型费用。

### 代价与限制

- CLI 当前是单进程轮询入口，不负责自动扩缩容或 leader election；
- 数据库 ping 只能证明启动时可用，持续健康仍依赖 heartbeat、积压和外部监控；
- drain timeout 后本地协程被取消，任务必须等待数据库 lease 到期后才能被其他 Worker 接管；
- Provider 与数据库清理失败只对外暴露类型机器码，详细诊断需在不含秘密的内部可观测系统中补充。

## 回滚与撤销条件

该变更不包含数据库迁移。回滚时可移除 `reflection-worker` 子命令和 application 装配，既有 Runner、Outbox、
租约、heartbeat 和运维 API 不受影响。

若部署平台未来提供经过验证的统一 Worker 生命周期与 secret 注入控制面，可以替换 CLI 外壳；但必须继续
调用同一应用装配/Runner 协议，保留 PostgreSQL 事实、有限 drain、安全事件和密钥不入参数的约束。
