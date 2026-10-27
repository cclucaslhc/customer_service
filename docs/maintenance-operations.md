# 全局维护、停写与备份封存（0017）

0031 更新：审批意向表也加入触发器/封存清单，当前为 44 张业务表、4 张协议控制表；下段 43 表数字是 0030 修复批次的历史范围。

2026-10-25 封存清单修复：当前 schema 的 43 张业务表都进入 `maintenance_current.PROTECTED_TABLES` 的显式 SHARE 锁清单；补齐后来迁移已安装写保护触发器的 embedding_profiles、knowledge_index_versions、knowledge_chunk_versions、knowledge_embeddings_1024、knowledge_admission_policy。不可变 0017 的历史输入不改。maintenance_state、recovery_state、backup_schedules、backup_jobs 四张协议控制表保留明确例外，供维护/恢复/调度协议本身使用。静态回归要求“当前 metadata 全部表减这四个例外”与显式清单完全一致且无重复，新增审批等业务表必须同步决定边界。

真实隔离 PG 已核对触发器清单与上述 43 表一致，并在本轮自有测试事务临时设置 session_replication_role=replica 后，确认五张新增表的空 DELETE 仍被封存 SHARE 锁阻止（lock timeout），事务结束恢复 origin。这是独立表锁层的验证，不是授权生产客户端绕过触发器。当前修复未新增迁移，head 仍为 0030；完整 pg_dump/恢复本批未重跑，CI 已加入整个维护测试文件并要求真实组零跳过，托管尚未执行。

状态：2026-10-07，隔离实现/验收。是自动备份的必要门控基础，不是 scheduler/完整自动备份交付；不授权生产访问或切流。

## 原理与边界

maintenance_state 是独立于 recovery_state 的全库单例。默认表为空、不启用维护；blocked/owner/fence/lease_until 必须成对有效，fence 正且递增。维护状态由明确数据库运维权限控制，没有租户管理员 HTTP 开关。维护覆盖所有租户，不应由单租户管理员触发。

0017 在30张现有业务/身份/任务/配置表安装 BEFORE STATEMENT 的 INSERT/UPDATE/DELETE/TRUNCATE 保护。写事务先取得共享 transaction advisory lock，再读持久 blocked；维护请求取得同一键的排他事务锁，等待此前写入 commit/rollback 后才发布 blocked。因此即使请求先经过 HTTP 检查、随后才开始 SQL，维护状态也在数据库层复查，不以页面提示代替停写。写事务必须 READ COMMITTED，旧 REPEATABLE READ/SERIALIZABLE 快照写入被拒绝，避免从旧快照读到“未维护”。读取/导出仍可用一致只读快照。

API启动与HTTP、Worker领取、知识/Run运行边界通过 require_operational 看到维护时失败关闭；HTTP返回503 maintenance_hold/no-store，提醒稍后刷新、不要重复提交。已有生产执行者须支持新head，不能混跑不认识新迁移的应用。SQL保护抛SQLSTATE55000；竞态中部分调用方仍可能返回普通失败而非专用维护提示，统一异常分类/前端维护展示待补齐，不宣称全链路交互已验收。

request 返回随机 owner UUID + fence，租约由数据库时钟决定、10–3600秒。renew/release 检查当前 blocked/owner/fence/期限；**期限过期也不自动开放服务**。失联后必须显式 request takeover_expired，且旧期限已过，fence递增；旧owner不能续租或解除。lease字段不是数据库角色鉴权，操作员仍必须拥有明确全库运维授权和最小权限凭据。

sealed(permit) 用排他 try transaction advisory lock 保证只有一个封存连接，再对全部受保护业务表持 SHARE 锁。在封存进入和退出以及备份guard边界复查owner/期限。活动running聊天/知识任务仍有有效租约，或running没有租约时拒绝封存；不会猜测这些工作已结束。过期任务仅表示没有执行授权，不证明残留进程已经退出；fencing/不可变attempt路径防止迟到结果发布，解析进程资源回收仍待完善。

封存连接持锁期间，其他业务 SQL 写入也受表锁阻止，不依赖每个Repository都调用了应用级检查。控制表允许续租，不被业务表锁覆盖。直接 nextval、DDL、禁用trigger/superuser和未迁移的新表不属于这项应用门控的保护范围；生产角色必须禁止绕过这些控制。未来新表需明确添加对应保护迁移与验收，不能修改0017冻结表清单。全表锁会影响可用性，锁等待/容量/规模停写窗口未验收；不是在线PITR替代品。

## 运维入口

scripts/maintenance.py 仅使用明确 MAINTENANCE_DATABASE_URL（当前限定loopback PostgreSQL，无自动读取应用DB/密钥），所有动作需要 confirm-global-maintenance。没有默认账号/服务启动、自动接管或解除恢复保护。

```powershell
.\.venv\Scripts\python.exe scripts\maintenance.py request `
  --lease-seconds 600 --confirm-global-maintenance
# 明确核对旧期限已过、旧执行者与资源后，才可额外使用 --takeover-expired。

.\.venv\Scripts\python.exe scripts\maintenance.py renew `
  --owner <当前owner UUID> --fence <当前fence> `
  --lease-seconds 600 --confirm-global-maintenance

# 成功验证源备份/停写窗口结束后由当前授权所有者显式解除：
.\.venv\Scripts\python.exe scripts\maintenance.py release `
  --owner <当前owner UUID> --fence <当前fence> --confirm-global-maintenance
```

owner/fence用于明确操作归属，不替代DB授权，不包含密钥。输出只含operation/owner/fence；错误不回显数据库URL或行内容。不是应用权限/audit ledger的完整身份记录，最小权限角色、独立操作审计和生产凭据供应仍需交付。

logical_backup.py create 可增加 --maintenance-owner/--maintenance-fence，完整成对、只能create、仍需要confirm-offline-maintenance。它按该permit进入封存，pg_dump后、文件处理前后和READY前复查guard；失租不产生成功包。源窗口退出只释放封存连接，不自动 release 持久hold。操作者负责有效续租、真实排空及失败后对账；现有手工离线备份入口仍兼容，不能把手工确认旗标当自动停写。

恢复先在独立隔离库按原快照校验包括maintenance_state在内的业务摘要，再在引用映射/rebind同一事务清除旧maintenance blocked/owner/期限，保留fence与独立recovery_hold。映射失败回滚此清除，恢复保护仍保持。旧维护permit不能启用恢复库；此变换不宣称每行逐字节等于源快照。恢复对账/受控启用工具仍未交付。

## 当前验收与未完成

0018 更新：自动续租/唯一调度键与catalog、有界排空等待及实际封存备份已接通，可选本地实际恢复验证绑定批准目标。操作与限制见 [自动备份](backup-scheduler-operations.md)。下文“还需scheduler”等为0017历史缺项；当前仍需完整对象自动供应/恢复、catalog/receipt灾备、freshness/告警、GC和生产权限/PITR/HA，不将局部调度实现视为完整生产化。

真实PG专项覆盖旧事务提交屏障、全部trigger清单、旧隔离级别拒绝、原始SQL零行写也拒绝、过期不开放/显式接管/旧owner失效、有效租约拒绝封存、单封存连接与续租、封存下真实pg_dump/隔离恢复、旧ownership清除/hold保留、显式permit CLI、备份途中失租无包、运行HTTP和独立Worker看到持久维护。浏览器本次被客户端拦截，不能用TestClient/真实TCP检查替代页面验收。

还需scheduler/自动续租/唯一调度键与备份catalog、排空等待/残留进程收敛、统一错误/前端维护状态、remote receipt灾备、自动恢复/freshness/告警、保留/GC、生产角色/TLS/IAM/KMS/WAL/PITR与HA演练。S0–S7全部目标不缩小、不标完成。
