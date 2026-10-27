# PostgreSQL 租户行隔离实施契约

状态：**设计与表清单门控已建立，RLS 尚未安装**。现有 API/Worker 数据库角色仍能通过直接 SQL 读取其他租户的业务行；Repository 的租户条件只约束走正常代码路径的查询。本文是后续实施与验收约束，不是上线证明。

## 威胁边界

- 目标：拿到 API 数据库凭据或在该身份下执行任意 SQL 时，不能仅靠修改 WHERE 条件或会话变量读取/写入其他租户业务行；客户、客服和管理员仍须在租户内遵守资源所有者及 RBAC。
- 当前 `app.tenant_id` 一类自定义 GUC 可由运行角色直接 `SET LOCAL`，不能单独作为可信身份。`FORCE ROW LEVEL SECURITY`、不授 `BYPASSRLS`、运行角色不是表所有者也缺一不可。
- API 角色目前可直接写 `identity_sessions`、账号和租户表。若数据库策略仅用会话行证明身份，同一 API 凭据可伪造会话；须先收紧会话签发与身份管理写权限，将登录、供应、撤销转到受信任且可审计的边界。原始 bearer token 不得进日志、队列或自定义 GUC。
- Worker 必须跨租户领取队列，但领取权限不应自动变成任意租户全表读写。领取、续租、完成应由受控数据库能力绑定确切 job/Run、owner、fence、期限及原始授权；其他业务表访问应限制于已领取任务的租户和资源。独立备份/迁移身份按其用途保留受控豁免，不用于 API/Worker。
- 超级用户、迁移所有者、已授权备份身份及数据库主机失陷不在运行角色 RLS 防护之内，须用独立凭据、TLS、审计和运维控制保护。

## 当前数据清单

`tests/test_rls_inventory.py` 固定当前 40 张表分类：30 张有 `tenant_id`，10 张全局表。唯一 nullable 租户列是 `identity_events.tenant_id`，用于全局身份事件；其可见性不能按普通租户行默认放行。任何新表或租户列可空性变化都必须先更新分类并解释访问模型。

全局表为 `backup_jobs`、`backup_schedules`、`chat_admission_policy`、`identity_login_buckets`、`identity_login_slots`、`identity_tenants`、`identity_users`、`maintenance_state`、`migration_batches`、`recovery_state`。它们不是“所有租户可见”：身份目录、登录限流、恢复/维护和备份控制分别需要专用权限或安全函数。

## 实施顺序与验收门槛

1. 先完成身份边界：API 运行角色不能自行铸造有效会话/成员关系。可信签发方及受控管理函数必须以登录凭据或已验证管理员身份作授权，保持现有密码重置、撤销、预算和审计语义。用直接 SQL 伪造会话的测试必须失败。
2. 建立事务绑定的可信租户上下文。数据库应独立验证授权凭据或签名，且上下文不能被同一运行角色通过 `SET`、连接池复用、嵌套事务、并发请求或重放旧证明伪造；每次事务开始绑定，结束清空。身份撤销后新事务立即失败。
3. 对 30 张租户表按依赖顺序启用 `ENABLE` 与 `FORCE ROW LEVEL SECURITY`，覆盖 SELECT、INSERT、UPDATE、DELETE 及 `WITH CHECK`；不能把未知/空上下文映射到 legacy 租户。对全局表另定白名单和最小权限。迁移前后校验所有表、策略、角色、默认授权和 owner/BYPASSRLS 漂移。
4. 将 Worker 的跨租户队列领取改成最小特权的受控入口，并对后续每个读取/发布动作复查租约和资源归属。错误 owner/fence、过期租约、撤销会话、抢占旧 Worker 和并发领取均应失败关闭。
5. 真实隔离 PostgreSQL 验收至少包括：两租户相同资源名与跨租户直读/写入；伪造 GUC、会话、角色切换、旧事务/连接池污染；API 与 Worker 互用；合法登录/管理/聊天/知识/备份及恢复后 hold；迁移期间新旧版本混跑与回滚边界。仅 Repository 过滤测试、静态清单或角色分离均不能证明 RLS 完成。

在这些门槛全部通过之前，不得把 S1 的“租户数据库隔离”或生产上线项标为完成。
