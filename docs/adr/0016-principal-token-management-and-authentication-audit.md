# ADR 0016: Principal、Token 管理与认证审计

## 状态

已接受，2026-08-25。

## 背景

v0.13 已经提供 PostgreSQL 高熵 API Token、即时撤销/禁用和真实 Bearer 认证，但生产运维仍需要直接调用
内部 Python 服务创建主体、签发 Token 或修改状态。缺少 HTTP 管理面会导致部署脚本获得过大数据库权限，
同时无法统一实施职责权限、委派约束、最后管理员保护和认证审计。

完整 Token 是一次性凭据，任何列表、错误、日志或审计泄漏都会破坏既有安全边界。管理动作还存在两个
并发风险：调用方可创建权限或 agent scope 大于自己的主体；两个管理员可同时禁用自己或撤销最后的可用
Token，使租户永久失去安全管理入口。

## 决策

### 1. 独立管理权限与安全关闭路由

管理权限拆分为：

- `auth.principals:read`
- `auth.principals:write`
- `auth.tokens:read`
- `auth.tokens:issue`
- `auth.tokens:revoke`
- `auth.audit:read`

主体管理、Token 签发和 Token 撤销不共享写权限。只有 `auth_management` 服务和可信 principal dependency
同时存在时才注册 `/v1/auth/*` 路由。tenant 始终来自 Bearer Principal，客户端不能提交或覆盖。

### 2. 每个动作重新验证 actor 事实

管理仓储不信任认证依赖几毫秒前生成的权限快照。每次操作都按 actor principal/token ID 和 tenant 重新读取
PostgreSQL，验证 tenant active、principal active、Token 未撤销/未过期、所需权限和 agent grants。

创建主体时，请求权限必须同时满足：

1. 使用合法 `resource:action` 名称；
2. 属于部署时的服务端 permission allowlist；
3. 是 actor 当前拥有权限的子集。

全 agent scope 只能由 `all_agents` actor 授予；显式 agent grants 必须是 actor grants 的子集。读取、启停、
签发和撤销也只能操作 actor 可管理的 agent scope。跨租户或不可管理的目标统一隐藏为 404。

### 3. 最后安全管理员并发保护

以下权限定义为关键安全管理职责：

- `auth.principals:write`
- `auth.tokens:issue`
- `auth.tokens:revoke`

禁用全租户 (`all_agents`) 主体或撤销其最后可用 Token 前，事务取得 tenant 级 PostgreSQL advisory lock。
对目标持有的每个关键职责，必须存在另一个 active、`all_agents`、持有同一权限并具有至少一个未撤销、
未过期 Token 的主体。检查和写入在同一事务中完成，因此两个安全管理员并发自禁用时最多成功一个。

重复设置相同状态和重复撤销保持幂等；撤销和禁用提交后，下一次认证直接读取 PostgreSQL 并立即失败。

### 4. 一次性 Token 与安全 DTO

Token 签发响应显式把 `SecretStr` 转为明文一次。之后的 Token 列表只返回：

- token/principal ID；
- 12 字符随机 prefix；
- label；
- created/expires/revoked/last-used 时间。

列表、审计、错误和普通 DTO 不引用 `APITokenModel.secret_digest`，也没有 token/secret 字段。

### 5. 追加认证审计

新增 `authentication_audit_events`，记录 tenant、actor/target principal/token ID、动作、结果、安全元数据和
创建时间。认证成功、认证拒绝、主体创建/启停、Token 签发/撤销均追加事件；缺失 Bearer 也通过认证依赖
记录拒绝。未知凭据无法确定 tenant 时允许 tenant 为空。

成功管理写操作和审计事件在同一事务提交；被策略或最后管理员保护拒绝的动作在独立事务追加失败审计，
不回滚原始业务事实。`safe_metadata` 只由服务端常量和布尔/计数值构造，不接收 Token、digest、请求 header
或自由错误正文。数据库触发器拒绝 UPDATE，保留追加不可篡改语义；删除仅用于 tenant 清理和未来保留策略。

主体、Token 和审计列表都使用严格 URL-safe Base64 `created_at + id` keyset 游标。

## 备选方案

### 使用单一 `auth:admin` 权限

拒绝。无法把主体治理、Token 签发和紧急撤销交给不同运维角色，也无法单独审计职责。

### 信任认证依赖中的权限快照

拒绝。主体可能在认证完成后立即被禁用或撤权，写操作会使用过期授权继续执行。

### 仅统计 active 主体，不检查可用 Token

拒绝。最后一个主体若没有可用 Token，名义 active 不能恢复租户管理能力。

### 在 Redis 中维护管理员锁或审计

拒绝。Redis 不是事实来源，故障或淘汰会造成保护和审计漂移；PostgreSQL 事务已经覆盖状态写入。

### 在审计中保存 Token prefix 或 Authorization header 便于排障

拒绝。prefix 虽不是完整 secret，但 header 包含完整凭据；排障价值不足以抵消泄漏和长期保留风险。

## 影响

### 正向

- 生产运维不再需要直连认证表；
- 自提权、跨 agent scope 和跨租户目标在服务端失败关闭；
- 关键安全管理职责可以独立授予；
- 并发管理员状态变化不会删除最后恢复入口；
- Token 明文仍严格限制在一次性签发响应；
- 认证和凭据生命周期具有可查询、不可更新的安全审计链。

### 代价

- 每个认证请求新增一条审计写入，认证吞吐需要在生产容量测试中单独验证；
- 未知或缺失凭据会产生 tenant 为空的全局审计，首版管理 API 不向单租户暴露这些事件；
- 服务端 permission allowlist 需要在新增业务权限时同步更新；
- 数据库触发器只禁止 UPDATE，未来审计保留/归档仍需定义专用删除角色和策略。

## 撤销条件

- 接入 OIDC、mTLS 或 API Gateway 时可以替换 Token 验证器，但必须保留 Principal、职责权限、委派约束、
  最后管理员保护和安全审计契约；
- 认证审计写入成为明确吞吐瓶颈时，可改为同事务 Outbox 后异步归档，但成功授权决策的最小审计事实不得
  丢失，失败认证仍需限流和可追溯；
- 引入 PostgreSQL RLS 时可把 tenant 隔离下沉到数据库，但不能移除应用层目标隐藏和 agent scope 检查；
- 合规要求审计物理 WORM 时，将事件同步到专用不可变存储并保留当前表作为事务入口。
