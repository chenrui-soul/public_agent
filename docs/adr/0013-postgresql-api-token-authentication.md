# ADR 0013：PostgreSQL API Token 认证与租户授权

- 状态：Accepted
- 日期：2026-08-25

## 决策点

如何为管理 API 提供可直接部署、可撤销、跨租户失败关闭且数据库不保存明文凭据的服务身份认证。

## 候选方案

| 方案 | 优势 | 劣势 | 适用场景 |
|---|---|---|---|
| 数据库保存明文 API Key | 可恢复、查询简单 | 数据库泄漏即完全失陷，无法满足秘密边界 | 不采用 |
| Argon2/bcrypt Token 哈希 | 适合低熵密码、抗离线猜测 | 每请求成本高；256 bit 随机 Token 不需要慢哈希 | 用户密码，不是当前 Token |
| 随机 prefix + pepper HMAC-SHA256 | O(1) 定位、快速常量时间验证、数据库单独泄漏不可直接使用 | pepper 轮换需重新签发 Token | 当前基线 |
| 外部 OIDC/API Gateway | 集中身份和企业治理 | 增加外部依赖，不能作为框架本地最小基线 | 可替换生产后端 |

## 决策

选择 PostgreSQL 主体/授权事实与高熵 Bearer Token。Token 格式为
`public_agent_<12-char-prefix>.<256-bit-secret>`。签发使用 CSPRNG；数据库只保存 prefix 和
`HMAC-SHA256(SHA256(pepper), prefix.secret)` 的 32 字节摘要。完整 Token 包装为 `SecretStr`，只在签发
调用返回一次。

### 1. 主体和 agent 授权

`api_principals` 绑定一个 tenant、唯一 subject、active/disabled 状态和 1-100 个规范化 permissions。
普通主体通过 `api_principal_agent_grants` 获得最多 500 个显式 agent；租户管理员可使用 `all_agents`，
不能同时保存显式 grants。grant 同时引用 principal/agent 的 `(id, tenant_id)` 复合唯一键，数据库直接
阻止跨租户组合。

主体创建按 `tenant + subject` advisory lock 并发幂等：同配置返回原主体，异配置拒绝原地覆盖。
主体禁用不修改 Token 行，但后续每次认证都会失败，因此全部 Token 立即失效。

### 2. Token 生命周期

每次签发产生全新 secret，不提供签发幂等重放，因为服务端无法恢复已返回的明文。主体行锁保证并发
active Token 计数不越过默认 100；prefix 唯一约束和 savepoint 处理极低概率碰撞。过期时间必须是未来
时区时间且不超过十年。撤销使用 Token 行锁，首次写 `revoked_at`，重复撤销返回幂等结果。

认证在数据库访问前严格验证格式。格式合法后按 prefix 查询，未知 prefix 也执行一次 dummy
`compare_digest`；已知 Token 使用 `hmac.compare_digest`。摘要错误、未知、过期、撤销、主体 disabled、
tenant inactive 或非法 all_agents/grants 状态全部抛同一 `AuthenticationError`，不泄漏具体原因。
`last_used_at` 使用条件 UPDATE，默认五分钟最多写一次。

### 3. FastAPI 失败关闭

`create_app(..., knowledge=..., api_keys=...)` 自动安装 HTTP Bearer 依赖并生成可信
`KnowledgePrincipal`。tenant、agent grants/all_agents 和 permissions 全部来自 PostgreSQL；客户端
header/body 不能扩大。缺失、畸形或无效 Token 返回稳定 `401 authentication_required`，权限不足返回
`403 knowledge_forbidden`，认证存储异常返回不含内部错误的 `503 authentication_unavailable`。

未配置知识服务或认证后端时知识路由仍然不存在。框架也继续允许宿主注入已有 SSO/API Gateway 的
Principal dependency，保持认证后端可替换。

## 反选论证

- 不保存明文或可逆加密 Token：管理服务没有恢复旧 secret 的业务需求。
- 不使用 Redis Token 缓存作为事实来源：撤销、过期和禁用必须立即一致。
- 不把客户端 tenant header 纳入主体：认证身份必须由服务端数据库或外部可信 IdP 产生。
- 不为高熵 Token 使用慢密码哈希：增加每请求 CPU/延迟但不提高 256 bit secret 的实际抗猜测能力。
- 不区分“未知/过期/撤销/错误摘要”：差异化错误会帮助枚举 Token 状态。

## 接受的代价

- 当前 pepper 没有 key ID；轮换 pepper 会使旧 Token 统一失效。运维必须先签发新 Token、切换客户端，
  再轮换 pepper。需要无中断多 key 验证时再增加版本列和 KMS/HSM keyring。
- 首版没有公开 Principal/Token 管理 API 或 CLI；签发、撤销和禁用由可信部署/管理代码调用。
- Token 状态依赖每请求 PostgreSQL 查询，换取即时撤销；大规模负载需用只读副本/连接池实测后再评估
  短 TTL 缓存，缓存不能绕过撤销事实。
- 当前认证审计依赖主体/Token 行时间戳和 last-used，没有独立追加登录成功/失败事件表。

## 撤销条件

- 接入企业 OIDC、mTLS 或 API Gateway 时替换认证后端，但继续输出同一 `AuthenticatedPrincipal`。
- 需要无中断 pepper 轮换时增加 `pepper_version` 和多 key keyring，旧版本只在迁移窗口验证。
- 认证 QPS 证明 PostgreSQL 查询成为瓶颈时引入短 TTL 正缓存和撤销版本；失败、撤销和禁用仍以数据库为准。
- 出现职责分离或合规审计要求时增加 Token 管理端点、审批和追加认证事件表。

## 验证

- 单元测试验证 256 bit Token 格式、SecretStr 不泄漏、HMAC 摘要、修改 Token 失败和权限/agent 规范化。
- PostgreSQL 集成验证并发主体幂等、同 subject 异配置冲突、跨租户 agent/principal 失败、数据库无明文列、
  constant-time digest 结果、active 上限、过期、撤销幂等、主体禁用和 all_agents。
- 真实 FastAPI + PostgreSQL 验证缺失/错误 Bearer 统一 401、read-only 写入 403、有效 Token 创建可信 tenant
  知识任务，以及认证存储异常安全 503。
- Alembic `fa6c3d9e2b40` downgrade/upgrade/current/check 通过，认证表约束、索引和列实查通过。
- Ruff、Mypy 61 个源码文件和 153 个全量 PostgreSQL Pytest 通过；离线示例不调用真实收费 API。

## 相关实现

- `src/public_agent/auth/base.py`
- `src/public_agent/auth/tokens.py`
- `src/public_agent/storage/auth.py`
- `src/public_agent/storage/models.py`
- `src/public_agent/api/auth.py`
- `src/public_agent/api/knowledge.py`
- `src/public_agent/api/app.py`
- `migrations/versions/fa6c3d9e2b40_add_api_token_authentication.py`
- `tests/test_auth_tokens.py`
- `tests/test_postgres_auth.py`
- `tests/test_knowledge_api.py`
