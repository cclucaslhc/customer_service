# PostgreSQL 生产角色与授权

状态：隔离 PostgreSQL 已验证；未在生产数据库执行。此处是 S1 的最小权限基础，不等于 RLS、生产 TLS/IAM 或备份恢复验收。

运行期启动门控：远程 PostgreSQL 目标由 `PostgreSQLRepositories` 默认检查实际连接身份，且不能通过 `strict_role=False` 关闭；URL中的host/hostaddr/service覆盖也视为远程。校验拒绝超级用户、数据库或对象所有者、可创建数据库/角色/Schema、BYPASSRLS、继承/切换到对象所有者、TRUNCATE/触发器权限，以及缺少关键业务写权限或拥有审计/用量账本危险改写权限的角色。API lifespan 以 `runtime_role='api'` 检查；聊天/知识 Worker 以 `runtime_role='worker'` 检查，互用对方身份时失败。对回环数据库或本机代理，应显式用 `PostgreSQLRepositories.from_url(..., strict_role=True)` 装配 API，并给两个 Worker CLI 加 `--require-app-role`；隔离合成测试可保留默认回环模式。不要将本机代理地址当成生产角色校验的自动启用证据。

## 四种独立身份

由数据库管理员在受控变更中创建四个**互不继承、互不作为对方成员**的 LOGIN 角色。迁移角色是目标数据库及迁移对象的所有者，四个角色均不得为 superuser，也不得有 CREATEDB、CREATEROLE、REPLICATION 或 BYPASSRLS。密码/证书只由部署密钥管理系统供应；不要放进仓库、命令行参数或客服管理页。

| 身份 | 用途 | 允许 | 明确禁止 |
| --- | --- | --- | --- |
| 迁移角色 | 独立 Alembic 发布任务 | 拥有目标数据库与迁移对象，执行受审阅的 DDL | 运行客服 API/Worker |
| API 角色 | 客服 HTTP/API 进程 | 业务表常规 SELECT/INSERT/UPDATE/DELETE，序列 USAGE/SELECT | 数据库/Schema CREATE、表所有权、ALTER/DROP/TRUNCATE；审计/费用不可变表的改写或删除 |
| Worker 角色 | 独立聊天与知识 Worker | 领取跨租户任务所需的业务/队列 DML；仅查询会话校验需要的用户列 | 数据库/Schema CREATE、表所有权、ALTER/DROP/TRUNCATE；写供应商配置、身份会话、价格版本及预算策略；读取用户密码摘要 |
| 备份角色 | 只读 `pg_dump` 与核验 | 所有业务表和序列 SELECT | DML、DDL、TRUNCATE、应用运行 |

迁移完成、应用切流之前，以迁移角色连接**明确选定的目标数据库**，运行 [授权脚本](../ops/postgresql/grant-runtime.sql)：

```powershell
psql -X -v ON_ERROR_STOP=1 `
  -v migration_role=cs_migrator `
  -v app_role=cs_app `
  -v worker_role=cs_worker `
  -v backup_role=cs_backup `
  -f ops/postgresql/grant-runtime.sql
```

上例角色名只是占位；连接目标与认证必须由受控 `PGSERVICE`/密钥管理系统供应，不在命令中写密码。非回环目标必须使用 `sslmode=verify-full` 并验证可信 CA、服务端证书与主机名。脚本不创建角色、不读取或生成凭据，执行前要求当前用户和数据库所有者均为指定迁移角色；不满足角色属性、成员关系或授权后禁止条件时在事务内失败。它撤销数据库 PUBLIC 连接及 public Schema 的 PUBLIC CREATE，授予现有对象和迁移角色未来对象的基线权限，并收紧审计/费用证据表。对同一 head 重复执行是幂等的；每次新迁移后仍必须复审新表，尤其新增不可变账本，不可依赖通用默认授权自动满足特殊限制。

运行前审阅 PUBLIC 连接撤销对监控、维护和其他业务客户端的影响，分别供应所需最小权限；不得在共享数据库中盲执行。`pg_dump` 角色可读取敏感业务数据和加密密文，备份包的访问控制、加密、留存及异地保护另按备份 Runbook 验收。

## 隔离验收证据与边界

`tests/test_pg_runtime_roles.py` 仅接受显式指定的 `::1:55433` 合成测试集群。它创建独立数据库及四种临时角色，以迁移角色实际运行 Alembic 与授权脚本；验证错误执行者和备份角色预存写权限使脚本失败且事务回滚、重复运行、未来新表默认授权、API 真实身份/配置写入、Worker 配置写入和密码摘要读取拒绝但会话校验仍可用、DDL/TRUNCATE/审计改写拒绝、备份角色数据 `pg_dump`、撤销 SELECT 立即拒绝及重新授权。测试结束删除确切临时数据库与角色，不能用于生产数据库。

后续启动验收又覆盖了权限漂移：临时向 API 角色授予 Schema CREATE 或向 Worker 角色授予密码摘要 SELECT 后，启动检查拒绝，撤销后恢复；实际 FastAPI lifespan 只接受 API 角色，聊天/知识 Worker `--once --require-app-role` 只接受 Worker 角色，双方互用、迁移角色、备份角色和超级用户均启动失败，且错误输出不回显合成口令。该检查是启动时的有效权限快照，不持续监控 DBA 在启动后的授权变化；也不对每张表逐列证明最小权限。

该脚本不能防超级用户、数据库所有者或被另行授予权限的角色绕过；当前 API 与 Worker 角色仍可直接 SQL 读取本库各租户的表，**RLS 尚未实现**，必须继续依赖 Repository 的租户过滤与数据库凭据隔离。Worker 必须跨租户领取任务，因此不能直接给它套用 API 的单租户上下文。单纯 `SET LOCAL app.tenant_id` 可由持有数据库凭据者伪造，不能作为“直接 SQL 不可跨租户”的证明。尚需可信租户上下文、Worker 权限边界、生产账号供应、TLS 握手、连接预算、混合版本发布及备份恢复实测；不能把隔离测试当作上线批准。

关联：[迁移方案](postgresql-migration.md)、[RLS 实施契约](postgresql-rls.md)、[实施清单](production-roadmap.md)、[灾难恢复 Runbook](deployment-dr-runbook.md)。
