# PostgreSQL 迁移与自动备份方案

2026-10-25 当前 head 为 `0031_rag_release_intents`。新增空的受保护审批意向表、同租户复合外键、冻结定义与严格版本递增触发器，并接入维护封存和恢复后的重新审阅。普通 API/Worker 角色必须撤销该表全部权限；按经审阅的 grant-runtime.sql 重授并通过角色门控。先停写/排空旧 API/Worker，再升级并启动新版本，不能把旧 0030 镜像用于新 head。本批仅审批存储结构，无审批服务/自动发布；见[审批边界](rag-release-approvals.md)。0030 上传/重解析固定清单和 Worker 能力约束继续有效。本项不授权生产迁移或切流。

状态：部分实现，2026-10-07。SQLAlchemy Repository、Alembic、隔离快照迁移器和显式 PostgreSQL 应用工厂已通过隔离验收；生产权限、对象存储与备份自动化尚未完成。本文件的其余目标命令须等对应工具和环境交付后执行，不授权读取生产数据库或密钥。

## 原理与选择

0028更新（2026-10-20）：当前精确 head 为 `0028_rag_vector`，metadata 共 46 表。`0027_rag_chunk_versions` 保存租户／文档版本／内容 hash 绑定的不可变切块快照，`0028_rag_vector` 增加固定 1024 维 pgvector 表及 HNSW 索引，要求迁移前由 DBA 安装 vector 扩展。真实隔离 pgvector 已验证升级、切块回填、幂等向量写入及按租户／active profile／当前文档版本的精确查询；查询目前有意在过滤后精排，HNSW 尚未用于线上 ANN。应用仍未启用向量召回或自动索引 Worker，不能据此宣称 S6 验收。以下旧 head 数字均为历史批次。

0026更新（2026-10-19）：当前精确 head 为 `0026_rag_metadata`，metadata 共 44 表。新增空的租户范围 Embedding profile 与索引版本 manifest 表；不启用向量路由。隔离 PostgreSQL 真实升级及 SQLite 约束测试通过。运行角色暂不允许写这两表，需完成写入合同、权限和向量一致性后再授权。下方“当前 head”表述为对应历史批次记录。

0025更新（2026-10-18）：当前精确 head 为 `0025_model_circuit`，metadata 共 42 表。新增逐租户/供应商/模型可变共享熔断状态表及维护写门控；旧库升级后初始为空，不改变既有合同或预算账本。隔离 PostgreSQL 八组联合 **74 passed / 0 skipped**，专用运行角色权限测试在独立 `::1:55433` 集群 **1 passed / 0 skipped**。应用仍要求精确 head，迁移前须受控停写/排空；新旧应用混合运行未验收，禁止 downgrade 以避免静默擦除运行协调状态。下方 0024/0023/0022 “当前 head”均为历史记录。

0024更新（2026-10-18）：当时精确 head 为 `0024_model_route_budget`。新增不可变的逐租户/供应商/模型备用 Token 合同表，并给预算预留增加可空 `route_policy_id` 外键；旧预留保持 NULL。当时 metadata 共 41 表。空库升级、预算/备用授权/用量/费用及真实加密恢复联合 **48 passed / 0 skipped**；专用运行角色权限测试在独立 `::1:55433` 集群 **1 passed / 0 skipped**。应用要求精确 head，迁移前须受控停写/排空，禁止 downgrade 以保留计费证据。

0023更新（2026-10-13）：当时精确 head 为 `0023_knowledge_trace`，仅给 `knowledge_job_queue` 增加可空内部 `trace_context` JSONB 与固定形状/类型/编号 SQL 约束；旧任务为 NULL，不回放或改变业务授权。真实隔离 PostgreSQL 相关回归 **56 passed / 0 skipped**。下方 0022 “当前 head”是历史记录。

0022更新（2026-10-08）：当前精确head为0022_model_budget，新增不可变model_budget_policies/model_budget_reservations；36表维护封存和statement guard、SQLite导入空预算表检查、全表备份恢复联动。旧0020/0021迁移不改，downgrade禁止。新旧应用混合运行未验收，须先受控停写/排空再迁移；不自动配置/开启预算。策略/额度/来源与软标志边界见 [预算操作](model-budget-operations.md)，后续早期head记录为历史增量。

PostgreSQL 通过事务、外键、唯一约束和行锁提供业务一致性，便于连接池、多实例和 PITR。使用 SQLAlchemy 2 + Psycopg 3，数据库访问收敛到 Repository。首阶段可保留同步 Repository，经明确的线程边界调用；不得在 async 热路径执行长时间阻塞查询。异步 Repository 改造需联动调用方，避免机械替换 sqlite3。

SQLAlchemy 管理查询与事务，Alembic 管理结构版本，数据搬迁脚本负责跨数据库转换；三者职责分开。生产 Schema 由独立迁移任务升级，应用启动只检查所需版本。应用角色不能 ALTER/DROP；迁移角色单独授权。

角色授权已增加可审阅的[迁移/API/Worker/备份角色操作说明](postgresql-roles.md)与事务化[授权脚本](../ops/postgresql/grant-runtime.sql)。真实隔离库验证 API/Worker 身份分离、业务 DML、迁移 DDL、备份数据导出及权限撤销；生产账号、TLS、RLS和容量尚待部署验收，不据此勾选完整 S1。

API/聊天Worker/知识Worker现另有[运行角色启动门控](postgresql-roles.md)：API 与 Worker 使用不同角色，远程目标默认强制、不可显式关闭；回环/本机代理须由部署方显式开启。门控发生在读取任务前，但不是RLS或持续权限监视。

2026-10-10连接保护增量：`build_engine` 对非 `localhost`/`127.0.0.1`/`::1` 的 PostgreSQL TCP 目标要求连接 URL 显式使用 `sslmode=verify-full`，拒绝 libpq 默认 `prefer`、`require`、`verify-ca` 和缺少主机的模糊目标；URL中的host/hostaddr/service覆盖也不得借回环主机名豁免。隔离回环测试可无TLS，但连接时明确钉住`127.0.0.1`或`::1`，不从PGHOSTADDR或hosts文件改变实际目的地。实际证书链、主机名、服务端配置及角色权限尚未在生产环境验收，不能将 URL 形状检查视为真实 TLS/最小权限证明。部署时须使用受信任 CA，并验证服务端证书与连接主机名匹配；不可通过降级 sslmode 绕过。

连接预算：所有 API/Worker 实例的 pool_size + max_overflow 总和，加上迁移、备份和运维连接，低于 PostgreSQL 安全连接上限。设置连接/语句/锁等待超时，监测排队；必要时增加 PgBouncer。模型和业务 HTTP 请求不持有数据库事务或行锁。

## 全部现有表的转换清单

| 现有表/结构 | PostgreSQL 处理 |
| --- | --- |
| provider_configs | 保留 provider/model、密文、nonce 与测试状态；二进制转 BYTEA；增密钥版本/包装引用 |
| app_settings | 保留配置；生产配置增版本与审计，敏感值仍加密 |
| chat_sessions | 保留 ID、状态、归档/删除、标签与负责人；增 tenant_id/owner_id |
| messages | 保留角色、正文、状态、引用、模型和反馈；引用转 JSONB；0002 增会话内递增 sequence |
| agent_runs | 保留运行、提示词关联和错误；增 attempt、lease、取消意图和工作流关联 |
| documents | 保留来源、哈希、版本、类型、警告、解析/切块版本；关联对象存储版本 |
| knowledge_chunks | 保留整数 ID、文档关系、内容、位置、tokenized、metadata；重设 identity 序列 |
| knowledge_assets | 保留图片 ID、尺寸、类型、位置和元数据；stored_name 映射对象键 |
| knowledge_jobs | 保留导入进度及结果；增租约、重试与任务阶段 |
| contacts | 保留与会话关联；权限隔离、保留期和字段保护 |
| prompt_versions | 保留版本、checksum、发布状态与时间；建立明确唯一约束 |
| session_notes | 保留内部备注，禁止出现在客户接口和模型上下文 |
| agent_run_tools | 保留记录，增 tool_call_id/attempt_id/operation_id；重设整数序列 |
| audit_logs | 保留历史 actor，不能将 local_admin 映射为真实认证管理员；新审计追加写入 |
| knowledge_fts/FTS5 triggers | 不迁移虚拟表、rowid 和触发器；从切块重建关键词/向量索引 |

新增表建议：users/roles/tenant_memberships、model_attempts/usage_ledger/pricing_versions、tool_operations/approval_requests、tasks/outbox_events、embedding_profiles/chunk_embeddings/index_versions。新表迁移与历史数据导入分批推进，不要求第一次切换即开放全部功能。

TEXT UUID 先检查合法性再转 UUID，错误记录中止导入；时间统一 TIMESTAMPTZ/UTC；所有 JSON 必须验证合法性，不能丢字段或把失败值静默改成空对象。布尔、NULL、默认值、排序与外键级联行为逐项确认。原始路径只用于受控迁移，不继续当作跨实例文件地址。

旧数据先归入受限 legacy 租户/所有者，由授权人员确认归属后再开放，不按手机号或会话 ID 自动关联新用户。

## Alembic 正式迁移

1. 从现有模型建立基线 revision，并补齐外键、索引和 CHECK 约束。
2. 自动生成后人工审阅；并发索引、JSON 转换、时间转换写明确迁移逻辑。
3. CI 验证空库到 head、支持的旧版到 head、只有一个 head、应用兼容窗口。
4. 发布执行一次 `alembic upgrade head`，并保存版本、耗时与锁等待。
5. 在扩大流量前验证 `alembic current`、Schema 与应用所需版本。

当前迁移命令（显式设置 `MIGRATION_DATABASE_URL`，连接凭据通过受控配置注入，不写到日志）：

```bash
alembic heads
alembic upgrade head
alembic current
```

采用 expand-contract：新增可兼容字段 → 双读/必要双写 → 分批回填 → 新路径验证 → 后续版本移除旧字段。回滚应用优先，破坏性 downgrade 不自动执行。长期锁和大表回填须拆分、可断点续跑。

### 已实现的消息顺序迁移

`0001_legacy_baseline` 保持不可变；消息序号由 `0002_message_order` 引入，当前单一 head 为 `0025_model_circuit`。运行 Schema 与历史基线分开管理，应用启动要求精确匹配当前版本，不在启动时隐式升级。新增 `messages.sequence` 为非空正 BIGINT，会话内唯一；写入时在会话事务锁内分配 MAX+1，消息与 Run 终态仍同事务。历史上下文、客户历史、管理详情和最新消息按该序号读取，不受相同时间戳、时钟倒退或 UUID 字典序影响。序号是内部排序，不是 SSE event_id，也不是 Worker fencing token。

0017 新增默认空 maintenance_state（blocked/owner/fence/lease_until），30个业务表BEFORE STATEMENT写保护与共享事务锁，request排他锁等待旧写事务结束。写要求READ COMMITTED，不允许旧快照绕过；sealed单封存连接/SHARE表锁、租约排空/ownerguard。降级禁止，旧导入/重放要求控制表为空。恢复原语义验证后与引用映射同事务清除旧维护owner/期限，独立recovery hold保持。状态/CLI/生产角色和规模限制见 [维护门控](maintenance-operations.md)。

0016 新增 storage_configs：tenant_id 主键/FK，target/target_fingerprint、正 revision、active/recovery_rebind 状态、AES-GCM 密文与 12-byte nonce、updated_at。迁移只新增空表，不读取主密钥、不填默认凭据，不可 downgrade。旧 SQLite 导入和幂等重放要求此表为空，避免覆盖后来产生的租户配置。管理保存绑定租户/fingerprint/revision，审计同事务；解析前后复查身份/版本。恢复在引用映射事务中将配置标为 recovery_rebind，hold 保持，原凭据不可复用；重新绑定需批准目标与恢复引用桶一致。详见 [存储供应与恢复](object-storage-operations.md)。

0015 增加可空 JSONB 排队输入引用及形状约束，0014 旧 item 升级保持 SQL NULL/原路径/hash/size，不自动转换或重放。对象模式入队与 Worker 使用不可变版本，备份纳入全部 item 引用并按 job_id/position 恢复映射；hold 和授权前向对账规则不变。降级禁止删除仍承担输入权威性的字段。独立进程对象配置供应尚未进入生产 CLI，验收使用隔离 harness。

`0009_run_leases` 增加 Run 的 lease_owner/lease_until/lease_fence/cancel_requested_at、租约字段完整性约束、同会话单一 leased running 部分唯一索引与过期扫描索引。既有无租约记录不被自动重放或改写，SQLite 导入仍保留其隔离状态；这些记录也不被租约清理器当作失租运行。迁移前应停掉旧版本执行者，升级后精确匹配新 Schema，不能混跑不认识租约的旧程序。回退删除租约/fencing 被禁止；本迁移不等于队列 Worker、检查点恢复或数据库 HA。

`0010_durable_chat` 新增 execution_mode/event_sequence、每会话单一活动 queued Run 索引、typed queue 和私有事件账本；通过复合外键绑定租户、Run、消息和 actor 会话摘要引用。升级不把旧 Run 变为排队任务。旧快照导入核对 execution_mode=direct、event_sequence/event_data_bytes/event_floor=0、queue/events/snapshots 为空，新增字段被篡改也拒绝重放。迁移不读取凭据；不可 downgrade 丢弃命令/事件。显式持久聊天要求独立 Worker；schema 升级本身不会启动 Worker 或切换默认服务。恢复必须包含数据库事件、终态快照及引用身份数据，不能单独恢复 queue。

`0011_run_snapshots` 新增 lifetime JSONB data 字节计数、event_floor、独立终态快照与部分保留索引。升级前停止旧 API/Worker 写入：按现有事件回填精确计数，已完成 Run 从 done 引用的同租户/同会话助手消息回填最终内容/引用；不是从 token 草稿猜测答案。缺少权威助手消息或活动 Run 超过 3MiB/9000 的完整性门控令整个迁移回滚，须先调查、协调终结异常运行，不可绕过门控。离线生成的 SQL 同样保留 DO 完整性检查；迁移不自动清理事件、downgrade 禁用。全表回填的锁时间与大库容量需另行评估。

保留策略默认关闭；审阅后显式配置 1–90 天。清理只删除有匹配终态快照的旧 queued Run 事件，同事务提高最低游标，保留消息/快照/Run/幂等身份引用和累计字节。客户端过期游标恢复见 [接口契约](api-contracts.md)。这是事件回收，不是完整隐私删除或备份删除政策。

`0012_chat_admission` 新增默认空的全局策略表、Run 启动时刻与预留有效期的部分索引。历史 Run 两个准入字段均为 null，旧快照导入核对并拒绝字段篡改。迁移不会自动启用 Redis 或创建策略，不可 downgrade 丢失限额/启动记录。先停写/排空并升级，再显式创建不可变策略，所有 Worker 使用一致 namespace/容量/启动速率和受控 Redis 端点；已启用策略的直接 PG 执行路径及未配置 Worker 失败关闭。PG 领取全局事务锁在 session/Run 锁之前，续租不持全局锁；抢占仍保留旧预留直到退出确认/到期。恢复时同时恢复策略和 Run 水位，不从空 Redis 推断无限容量。真实 Redis、规模性能与切主验收仍待完成，不能据此启用生产。

`0013_knowledge_queue` 增加导入 execution_mode/owner/until/fence、typed queue 和逐文件发布 ledger；同租户 job 复合外键、可撤销身份摘要引用、固定相对路径/长度/SHA256。旧任务默认 local、无租约/fence=0，旧快照导入检查这些字段及新 queue/items 必须为空；不把历史任务变为待执行命令。迁移不默认启用 API/Worker；不可 downgrade 丢失发布或身份记录。明确 `durable_knowledge=True` 与独立 Worker 要求相同可信共享文件根；60 秒 DB-clock 租约，元数据发布与逐文件结果/计数同事务。过期仅有界终结，不自动重放解析；对象存储、GC、备份和解析 sandbox 尚未完成。

原文与资产采用 attempt 独立路径，已发表文件不指向可变源文件。输入文件不被版本更新清理删除；SQLAlchemy/传输错误可能是提交回执丢失，此时保留 staging 文件，不能删除可能已被已提交元数据引用的内容。恢复/GC 必须核对 DB manifest 与共享文件，不能只恢复 queue 或直接按目录年龄删文件；逻辑备份/PITR 与对象版本一致性仍需后续演练。

新 SQLite 快照导入必须先把空目标升级至当前 head，再导入。导入器按源 `created_at,rowid` 保留每个会话原有顺序；报告增加仅含计数与摘要的 `message_order`，导入和重放同时验证内容及顺序。不可再向 0001 目标导入。若已有数据只存在于 PostgreSQL 0001，升级沿用该库此前的 `created_at,id` 顺序；原 SQLite rowid 未保存，不能凭空恢复同时间戳的真实插入次序，需授权操作员根据隔离源快照核对。这种旧库回填不宣称恢复已丢失的信息。

0002 downgrade 被禁止，回滚只能使用兼容 sequence 和当前 Schema 的应用版本；旧版应用直接写消息缺少序号会失败，不能跨过兼容性 gate。当前回填是事务内整表操作，大表上线前须评估锁时间并制定分批回填迁移；目前只完成隔离小规模验收。

### 已实现的身份底座（尚未接入业务接口）

0003 新增 `identity_tenants`、`identity_users`、`identity_memberships`、`identity_sessions` 和 `identity_events`。迁移不创建默认账户、不生成管理员密码、不给旧业务记录自动分配所有者。角色为 customer/support/admin，成员关系以 `(tenant_id,user_id)` 为主键，登录会话通过复合外键关联合法成员关系。会话仅存随机 256-bit 令牌的 SHA256 摘要，使用数据库时间判定过期；用户 auth_version 与实时成员/租户状态共同控制有效性。

内部 IdentityRepository 支持显式账户供应、登录/退出、改密和停用；密码使用随机盐 scrypt（N=131072、r=8、p=1、32-byte 输出），不保存明文。KDF 在事务外执行，登录提交前锁定用户并复查密码摘要与 auth_version，防止改密竞态。身份事件与账户/会话/改密/成员变更同事务，事件失败则回滚；这不替代业务审计/Outbox 或独立审计归档。

Principal 为服务端构造的用户/租户/角色，未知权限默认拒绝，客户只能访问本人资源，客服/租户管理员的扩展权限仍限同租户；租户 admin 没有自动获得全局供应商配置权限。退出、改密、成员变更、用户或租户停用使旧会话失效，重新启用不恢复已撤销令牌。该底座接入 PostgreSQLRepositories，尚无公开认证端点、登录 UI、HTTP 权限依赖、登录限流/MFA/SSO或完整业务 Repository 的租户过滤；不能据此开放外网。后续必须贯通 API/Agent/检索/资产/任务所有边界。0003 禁止移除撤销状态的 downgrade。

### 会话租户隔离（0004，尚未贯通 HTTP）

0004 为 chat_sessions/messages/agent_runs/agent_run_tools 添加 tenant_id，chat_sessions 增 owner_id。旧记录归入固定的停用 legacy 租户且 owner 为 NULL；新活动租户会话必须有本租户成员作为所有者。复合外键保障消息/运行与会话、工具记录与运行的 tenant_id 一致，不能仅凭全局 UUID 把子记录挂到外租户。当前导入器要求 0004，保持旧字段摘要，并验证四类记录仍在 legacy 隔离区；不自动认领旧会话。身份 API 禁止为 legacy 供应登录账户、登录或启用该租户，resolve 也排除 legacy。

`ChatRepository.authenticated(token)` 返回独立绑定实例，角色/租户/所有者不接受模型或调用方声明。每次方法调用使用当前数据库连接重新校验会话，避免池容量为 1 时嵌套获取连接；没有请求间共享角色变量。客户查询和变更仅本人会话，客服/租户管理员可访问本租户会话；普通会话更新禁止修改 id/tenant_id/owner_id，管理字段另需租户会话权限。工具记录和 Run 终态更新也检查其父会话归属。无令牌的 PostgreSQL ChatRepository 仅可访问 legacy，不能读取活动租户数据；此路径仅服务本地兼容测试，不代表匿名生产权限。

这是会话 Repository 的访问过滤与结构约束，不是数据库 RLS，也尚未接入 HTTP/ChatOrchestrator 身份传递。AdminRepository、联系人、知识库/资产、任务及模型/Prompt 配置的数据层边界分别在 0005–0007 引入；应用整体继续仅本机访问，不能上线。数据库最小权限、RLS 防御层和全链路越权验收仍未完成。旧记录的授权认领工具也尚未实现；0004 禁止丢弃归属的 downgrade。

## SQLite 切换操作

### 知识库隔离（0006，尚未贯通 HTTP/Agent）

documents/chunks/assets/jobs 增 tenant_id，文档记录 created_by、任务记录 requested_by；复合外键约束切块/资产与父文档租户一致。文档 SHA256 唯一约束改为租户内唯一，同一内容可在不同租户独立上传；来源路径去重也按租户查询。旧数据保持 legacy，不自动分配给新用户。新增 knowledge_settings，以 `(tenant_id,key)` 为主键；仅复制 app_settings 的 knowledge_ 前缀到 legacy，模型配置不进入此表。导入器复制并校验 legacy 知识设置、知识元数据归属及原字段/消息顺序摘要；重放时能检测仅知识设置被篡改的情况。

令牌绑定 KnowledgeRepository 每次调用重新校验身份。客户/客服仅可读本租户检索候选、字符回退和图片；文档来源/解析详情、发布/重解析/删除、重建、术语写入及导入任务管理需 knowledge.manage（租户管理员）。过滤发生在 SQL WHERE，先过滤再排序/LIMIT，不能只在返回后隐藏外租户候选。发布拒绝调用方提供 tenant_id/created_by/requested_by，检测外租户文档 ID 时拒绝覆盖；重解析保留初始创建者。无令牌 PG 路径仅查询 legacy，没有全局知识/术语回退。

绑定 KnowledgeService 的原文、图片和旧上传目录按租户 UUID 分目录。解析/重解析/删除及目录扫描在文件读取与 OCR 前校验管理权限；绑定模式只允许本租户托管目录内的源路径，扫描排除逃逸的符号链接，资产读取先校验数据库归属再查文件。客户不能借重解析读取文件或触发 OCR。原本的本机兼容目录导入行为保留，仅供本地管理模式；生产目录导入的独立授权根/对象存储源仍待实现。该本机分目录不是 S3 兼容对象存储，也不代替文件 manifest、备份或共享 Worker。

管理概览现可统计本租户知识文档/切块；模型配置指标在 0007 补齐，见下节。显式认证 API、前端登录与 HTTP/Agent 身份已接通；RLS、向量检索与队列 Worker 尚未完成，不能据此上线。0006 禁止回退到全局哈希唯一与全局知识设置。

### 管理与联系人隔离（0005，已接入显式认证模式）

contacts/session_notes/audit_logs 增 tenant_id，分别记录 owner_id/author_id/actor_id。联系人同时验证本租户成员所有者、父会话租户和父会话所有者；备注验证父会话租户及已知作者的成员关系；新活动租户审计必须有真实成员 actor_id。已存在的联系人/备注继承已经明确归属的父会话，不通过手机号或邮箱认领；legacy 父会话仍保持停用隔离。历史备注无身份记录时保留 author_id=NULL，不冒充客户或管理员。历史独立联系人与无法归属的审计仍为 legacy。

令牌绑定 AdminRepository 对管理列表、筛选数量、详情/导出来源、内部备注、联系人列表/修改、审计列表和运营指标执行角色与租户校验。客户可提交本人会话或本人独立联系请求，不能读取管理详情、内部备注、管理联系列表或更改跟进状态；客服可处理本租户会话与联系人，全租户审计列表另需管理员 audit.read。代办联系请求的 owner 是会话所有者，审计 actor 是实际客服；两者不混用。无令牌管理路径只查询 legacy 会话、联系人和审计。

联系人创建/跟进修改、内部备注与对应审计同事务；HTTP 路由不再追加第二条 contact.update/session.note 审计。只记录动作与字段名，不在这些审计内复制联系方式、联系摘要或内部备注正文。审计写入失败时业务变更回滚。其他会话/配置/知识业务变更、Outbox、拒绝访问审计、数据库审计角色与独立归档仍待实现，不能把这三类原子写视作 S4 完成。

令牌绑定概览只统计本租户会话/运行/联系人/文档/切块；0007 后管理员可查看本租户供应商配置状态，客服的 providers 仍为空、active_provider 为未知 NULL，不泄露管理配置。此模式已接到显式认证 HTTP 和前端入口；默认 SQLite、本机兼容 HTTP 响应保持原有字段，未自动启用认证。

### 模型配置与 Prompt 隔离（0007，已接入显式 HTTP/Agent）

provider_configs/app_settings/prompt_versions 增 tenant_id。供应商和设置主键分别改为 `(tenant_id,provider)`、`(tenant_id,key)`；Prompt 版本号仅在租户内唯一，部分唯一索引确保每租户只有一个 published 版本。Run 与 Prompt 建复合租户外键，外租户 Prompt 不能被引用，即使调用方绕过 Repository 也会导致整个开始运行事务回滚。草稿、发布和回滚由事务锁串行化；回滚创建新的版本/ID，不修改原正文。

旧配置、密文、nonce、Prompt 和全局设置迁至停用 legacy 租户。`key_aad_version=0` 表示旧 provider-only AAD，迁移原样保留二进制，不读取主密钥、不解密或自动重包装。新活动租户密文必须 `key_aad_version=1`，AES-GCM AAD 为 `cs-provider:v1:{tenant_uuid}:{provider}`。密文被搬到另一租户后不能解密。当前还是显式 SecretStore 文件主密钥，不等于 KMS、密钥轮换或灾备完成。导入校验除旧列摘要外，还验证配置处于 legacy 且 AAD 版本为 0，防止修改新字段后误报重放成功。

令牌绑定 ConfigurationRepository 的配置元数据、写入及 Prompt 管理需 config.manage（租户管理员）；runtime active_provider/active_prompt 分别需 model.use/prompt.use。客户不通过管理接口获取密文或明文；运行时明文仅在服务器内部构造模型。ModelGateway 在加密前鉴权，在建立 HTTP client 前拒绝外租户 ProviderSnapshot；每次重新校验可撤销会话，不自动读取环境历史密钥。无令牌 PG 路径仍仅访问 legacy。

管理员显式 initialize_tenant 只初始化未配置的 DeepSeek 和默认业务 Prompt；幂等且不复制旧密钥/设置/Prompt，不新增 OpenAI 配置。认证管理界面录入密钥是唯一供应商密钥入口，不读取环境历史密钥。真实 PostgreSQL 测试覆盖 0006→0007 保留旧密文、新租户无回退、格式约束、权限/撤销、密文移植、并发版本/发布及跨租户 Prompt FK；不调用在线模型。0007 禁止可能丢失租户边界的 downgrade。

### 登录准入底座（0008，已接入显式认证链路）

新增 identity_login_buckets 和 identity_login_slots；迁移只新增表和两个空闲密码校验槽，不创建用户、会话或供应商配置。跨实例事务 advisory lock 串行预算检查/扣除/领取，数据库 clock_timestamp 决定窗口和租约，不依赖应用进程时钟。默认全局 60 次/分钟、来源 20 次/分钟、账号 5 次/5 分钟；预算针对所有尝试，不仅失败尝试。被后续因素/并发容量拒绝时，之前已扣预算仍提交，不因抛出限流异常回滚。过期桶每次有界清理最多 500 条；全局预算先检查，避免任意账号输入无限扩张存储。

来源必须由服务端传输层提供 IP，不接受客户端 payload 或未经验证的 X-Forwarded-For。IPv6 按 /64 合并。账号键使用规范化全局用户名，不包含调用方选择的 tenant_id，避免攻击者切换租户 ID 绕过同一密码的账号预算。持久化只保存带用途前缀的 SHA256 摘要、计数和过期时间；可猜测用户名/IP 的摘要仍是可关联的假名，不是匿名化数据，仍需访问与保留策略。

两个数据库槽限制并发 scrypt 校验；120 秒租约用于崩溃释放，随机 UUID owner 防止旧请求释放重新领取的槽。KDF 在事务外执行；签发登录令牌前重新锁定并验证槽 owner 和数据库过期时间，失租请求不能签发。LoginService 在 finally 中按 owner 释放；数据库不可用时失败关闭，不回退进程内限流。该登录租约不等于 Agent/Worker 租约，也未实现 Redis 限流、HTTP 超时、边缘 WAF 或完整故障切换。

PostgreSQLRepositories.login 提供内部受限登录入口。IdentityRepository.login 仍供受控供应/测试调用，认证 HTTP 已使用 LoginService，不直接绕过准入。显式登录端点、前端登录和 HTTP/Agent 身份绑定已实现，但账户供应、TLS 部署、MFA/SSO 与全链路生产安全验收尚未完成。策略为保守开发默认值，生产容量与账号锁定策略需在 S0 审阅后统一版本化调整；不得让不同实例各自放宽。0008 禁止删除共享预算的 downgrade。

推荐第一次切换采用短停写窗口。生产访问和备份由授权操作员在受控环境执行，仓库只保存脱敏校验报告。

1. 隔离环境完整演练导入，确认目标库为空或指定 migration_batch。
2. 暂停新消息、联系登记、配置和导入写入，排空 Run/后台任务；关闭旧服务自动恢复行为。
3. 使用 SQLite backup API 或 sqlite3 `.backup` 获得一致快照；WAL 活跃时不能只复制 app.db。
4. 记录快照哈希、全部文件/资产 manifest 和密钥版本引用；加密保存，不输出原文/密钥。
5. 将空目标升级至当前 head，运行 `scripts/migrate_sqlite.py`，按 FK 顺序导入并保留主键。以语义快照摘要和批次记录校验重放，不使用 upsert 覆盖已有业务数据；源快照只读，内容与消息顺序均校验。
6. 同步对象文件，逐对象核对大小/SHA-256/版本；数据库与文件迁移失败不发布 ready 状态。
7. 校验每表行数、外键、NULL、JSON、时间范围、状态分布、密文长度、序列 nextval 和业务字段规范化摘要。
8. FTS5 重建为选定关键词索引，pgvector 在后续 RAG 阶段回填；两种检索不做等价保证。
9. 在目标应用运行会话历史、管理权限、提示词、资产、模拟工具和检索冒烟。用临时测试凭据验证密钥流程，禁止导入器自动调用真实模型。
10. 原子切换数据库配置，逐步恢复写入；观察错误、连接池、数据一致性。

API Key 密文迁移必须保留原 nonce 和 provider AAD；密文搬过去不代表新 KMS 可以解密。主密钥通过专门的授权密钥流程转移或重新包装，密钥丢失则要求用户重新录入。完成报告仅记录验证结果。

切换前失败：丢弃隔离目标批次，旧库继续服务。切换后产生新数据：禁止直接回到旧 SQLite 快照；保持停写，将增量完整回灌并校验后才切换，或优先前向修复 PostgreSQL。恢复数据库到过去可能造成外部操作重复，须对账后再开放写工具。

## 自动备份设计

0014 增加 recovery_state 单例保护事实，迁移空表不阻断新库；逻辑恢复在恢复 SQL 的同一事务强制写入 blocked=true，API/Worker 拒绝启动/处理请求/领取任务，知识管理/发布和租约写入也复查。不恢复旧队列执行，不提供未经对账的解除入口。SQLite 导入器要求该表为空。它不是 RLS，生产权限/撤销/外部操作前向对账及受控解除流程仍待完成。

当前已增加 `scripts/logical_backup.py` 的加密逻辑备份/空库隔离恢复：同一个 exported snapshot 的 pg_dump 和业务摘要、原文/图片 hash manifest、GCM 加密与恢复后全表/序列/文件验证；确认成功后重定位文档来源至新托管副本。不自动调度、不恢复服务/权限/业务操作，不替代下面的 WAL/PITR。当前仅 loopback，需显式离线维护、隔离恢复与私有 scratch；生产 TLS/角色/KMS/调度/异地桶/恢复前向对账未完成。完整边界与操作入口见 [逻辑备份操作](logical-backup-operations.md)。

推荐自管 PostgreSQL 使用 pgBackRest，托管 PostgreSQL 使用云 PITR；选定一种主备份事实源。pg_dump 为逻辑导出补充，不能代替 WAL 和物理备份。

| 内容 | 初始计划 | 验证与告警 |
| --- | --- | --- |
| 物理备份 | 每周全量、每日增量；全量链保留至少 4 周 | 校验可恢复链与备份大小 |
| WAL | 连续归档；低流量归档时限建议 ≤ 60 秒 | 最新可恢复时间落后 > 5 分钟告警 |
| 逻辑备份 | 每日 pg_dump 自定义格式，30 天；月度 12 个月 | pg_restore 到隔离空库并运行校验 |
| 文件/图片 | 私有版本化桶，增量备份与异地副本 | manifest 哈希/版本校验，目标 RPO ≤ 5 分钟 |
| 配置/制品 | 镜像 digest、Schema、Prompt/路由/索引版本 manifest | 每次发布保存且可检索 |
| 密钥 | 独立 KMS 恢复/轮换策略，双人受控访问 | 隔离解密演练，不与数据备份明文打包 |

上述保留期与数据删除政策共同审阅。异地副本和归档具备加密、最小权限、审计和删除保护；不能全部放在数据库同一磁盘。独立 scheduler 执行备份，禁止每个 API 实例各跑一次。

建议整体 RPO ≤ 5 分钟、RTO ≤ 60 分钟，必须覆盖数据库、对象、配置和密钥依赖。副本用于可用性，误删除会复制到副本，因此不能替代备份。

每周自动恢复最新逻辑备份，每月做物理 PITR 和跨存储演练；在隔离无出站环境验证表/外键/资产/检索和模拟工具。失败告警并保留最后成功恢复时间，禁止只凭备份任务退出码证明可恢复。

## 验收与关联

0021新增不可变model_price_versions、pre-call同事务model_price_quotes及追加model_cost_assessments。tenant复合FK、publication/request/receipt/revision唯一约束、明确Decimal单位/币种/金额/nullable形状CHECK；新三表不可改写/删除/清表，旧用量表加TRUNCATE保护但不改0020历史定义。维护清单34表、SQLite导入空费用门控及全表备份恢复已联动。旧0020行不伪造报价/费用历史，无quote的后续终結保留unknown；无默认真实报价/外部收费操作。downgrade禁止，需受控停写/排空/备份后升级，旧包用匹配schema恢复制品，不是混合版本零停机证明。operator_reported仅管理员声明，不是供应商认证账单；预算/费用界面/正式权限仍待实现。详见 [费用边界](model-usage-operations.md)。

0020新增model_usage_attempts：计费调用前独立短事务提交started/unknown，固定租户/Run/actor/租约/配置模型，终结SDK观测用量及状态；多列FK/非负Token/时间和状态约束，终结后SQL禁止改写/删除。新增表statement维护写guard，运行封存清单由maintenance_current扩展为31表，不改不可变0017定义；SQLite导入/重放要求用量表为空，不伪造历史账单。现有全表manifest自动纳入用量表，并有实际加密备份/恢复摘要与不可改写trigger复验。downgrade禁用；旧备份必须使用匹配schema的受控恢复制品，不能直接用0020运行时恢复0019包。账本只是SDK观测，无价格/金额/预算/对账及Embedding/Rerank/连接测试全覆盖，详见 [用量操作边界](model-usage-operations.md)。

0018 增加 backup_schedules/backup_jobs 与单全局 running 部分唯一索引，不自动启用计划。控制表允许封存中续租，不受0017业务写trigger保护，必须由正式数据库权限隔离普通应用；权限供应尚未完成。显式 SQLite 导入/重放要求目标调度表为空。恢复先校验原始摘要，再禁用恢复计划并把 running 备份任务标记 needs_review，不回放源运行。详见 [自动备份操作](backup-scheduler-operations.md)。

0019只为chat_run_queue增加nullable内部trace_context JSONB和精确形状/类型/编号约束，旧行保持SQL NULL，不创建或回放任务。客户端不能提交此字段；它只用于诊断，不授予权限，不参与幂等指纹。首次提交同事务存储，重复请求保留首次carrier。downgrade禁用以保留运行关联证据；API/Worker仍要求精确head，发布前按受控停写/排空/迁移流程执行，不是混合版本零停机验收。旧备份需匹配其schema的批准恢复制品，不能直接把0018包当0019已恢复；本批回归创建/恢复的是当前schema隔离包。详见 [追踪边界](observability.md)。

导入报告需覆盖上表全部表、序列、外键、资产和敏感配置；回归需覆盖原 API/SSE 契约。验收确认生产应用无 sqlite3 写路径、空库/旧库迁移均通过、备份能恢复、应用回滚可兼容 Schema。

执行顺序见 [实施清单](production-roadmap.md)，故障恢复步骤见 [Runbook](deployment-dr-runbook.md)，关键词/向量转换见 [RAG](rag-upgrade.md)。
