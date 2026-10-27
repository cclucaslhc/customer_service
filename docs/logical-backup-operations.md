# 加密逻辑备份与隔离恢复

状态：本机隔离环境实现与验收中，2026-10-07。此工具不授权读取生产数据，也不代表 S1/S7 完成。主备份仍需 pgBackRest/托管 PITR、WAL 连续归档、异地副本与自动调度。

## 原理与边界

`app/backup.py` 在 PostgreSQL REPEATABLE READ 只读事务内导出 snapshot ID，`pg_dump --snapshot` 使用相同快照；全部当前业务表按主键流式读取，形成行数和语义 SHA256 摘要，另记录 Schema、序列状态和数据库快照时间。未知表或 Schema head 不一致拒绝备份，不静默忽略新数据类型。

只备份快照中 documents/knowledge_assets 引用的原文和图片，不扫描整个 data、密钥目录、未发布 staging、导入输入目录或其他上传文件。每个文件必须与数据库尺寸/SHA256 相符。原文、图片、custom-format dump 和包含路径的 manifest 分别 AES-256-GCM 加密，随机 nonce 与 blob 身份 AAD 防止文件替换；READY 最后生成，失败删除本次新包，不覆盖已有备份。READY 本身不是签名；密文/manifest 的 GCM 才提供完整性。

数据库 snapshot **不能冻结文件系统或 PostgreSQL sequence**。执行前必须暂停 API 写入、全部 Worker、解析和文件清理，确认排空；工具的 maintenance confirmation 是操作员声明，不是自动停写系统。若输入缺失或变化，备份失败，不标记成功。大库扫描/导出仍需容量、锁时长、scratch 空间与超时评测。

当前连接限 loopback、显式 `postgresql+psycopg` URL，无 URL query/libpq 选项。子进程剥离历史 PG* 配置，再注入本次明确参数；不自动读取 `.pgpass`、历史模型密钥或应用 SecretStore。远程 TLS/CA/专用备份角色与生产端点配置尚未接入，不应通过隧道将此限制当作生产认证方案。

## 使用前准备

- 由授权操作员提供 PostgreSQL 官方 `pg_dump/pg_restore/psql` 的绝对 binary-dir，不自动下载/安装或寻找 PATH 中的程序；三项客户端的**主版本必须与服务端相同**。备份/恢复开始前逐项核验并失败关闭，避免较新客户端生成服务端不认识的恢复 SQL；仍需按环境批准具体补丁版本和来源。
- 备份密钥是独立的 32-byte 随机二进制文件，由授权的密钥供应流程提供；与模型主密钥无关，不能把 key 放进备份包或同一目录。KMS 包装、轮换及双人恢复仍未实现。
- 所有路径绝对指定。拒绝本仓库 data 子树、secrets、`.env*`、磁盘根目录和跨根/符号链接的 manifest 文件。包目录必须不存在，恢复根必须不存在且互不包含。
- scratch 必须在受限加密卷或受控 tmpfs 上；其中会暂存**解密后的 dump/manifest/文件**。POSIX 临时目录使用 0700；Windows chmod 不等同于 ACL，操作员需预配置私有 ACL。本批仅使用合成内容，不证明生产磁盘权限合规。进程被 kill 后的残留需受控清理，不自动扫描/删除全局 Temp。
- 数据库备份包含加密模型配置、0016 对象凭据配置及会话摘要等敏感数据；不包含包装主密钥。主密钥/KMS 另行授权恢复，不自动调用任何模型或使用源对象凭据。

## 命令入口

### 对象文件函数与显式 CLI 入口

0015 后对象引用清单还覆盖全部 knowledge_job_items.input_object，包括 pending 和终态仍保留的输入；不扫描原始本地导入目录。备份记录 job_id/position，恢复按复合主键锁定和映射，不能把同一 job 的多个文件折叠成一个引用。所有映射仍与原文/图片同一个短事务验证，恢复队列保持 hold，不重放。

`create_backup(..., object_store=source_store)` 支持从快照引用下载确切 VersionId，校验数据库 size/hash/kind 后加密为文件 blob；不使用本地缓存、latest 或公开地址。对象凭据显式注入，无环境凭据链。存在对象时 manifest format 为 2，记录加密的 tenant/table/row ID/源引用；纯本地包仍使用 format 1 并向后兼容。

`restore_backup(..., object_store=restore_store)` 需不同于源桶的空私有版本桶，拒绝现有版本、delete marker 或分页不完整的空判定。全部 GCM 文件/大小/摘要验证完成后，才将对象按新 attempt key 上传、读回验证；不依赖源桶仍存在。随后原始 dump 与 hold 同事务恢复，先核对原 snapshot 表/序列/引用，再于另一个短事务复查 hold、锁定原引用行、更新全部原文/资产引用并核对新引用 manifest；失败回滚全部映射，保留原恢复库的 hold，绝不启用服务。

本地原文仍重定位路径；对象原文保留旧来源指针作 provenance，以新对象引用读取，不伪造本地文件。报告分开给出 `objects`、`objects_remapped`、`local_document_paths_relocated`；`document_paths_relocated` 仅表示至少一个本地原文路径被重定位。引用映射是明确的恢复后变换，不宣称新数据库所有行摘要等于源库。

上传回执不明/SQL或映射失败时保留远程版本，不按失败请求盲删可能引用的对象；不得向同一非空桶自动重试，先隔离对账或选另一个新桶。SQL 开始前失败保持数据库空、清理本次文件根；SQL 后失败保留恢复库/文件及 hold。旧队列和身份不自动重新执行或解除保护。

CLI 已增加成组 `--storage-policy`/`--storage-target`/`--storage-credentials-stdin`，由受信运维流程在私有 stdin 管道提供独立 source/restore 凭据，不从 AWS_*、模型主密钥或应用 SecretStore 发现凭据。先确认离线维护/隔离恢复，再读取凭据；成功/失败都关闭 SDK，输出不回显密钥。没有显式供应时仍拒绝对象引用；一次只支持一个源桶，不跳过其他桶。备份包私有版本归档/取回已增加独立 CLI，见 [远程归档](remote-backup-operations.md)。自动停写调度、IAM/KMS/TLS、远程 DB 角色与生产恢复验收仍未完成。

### 本地文件 CLI

仅在授权隔离环境中设置 `BACKUP_DATABASE_URL`，不在 Git 或命令行输出明文 URL。下面的占位符需替换为已核实的绝对目录，不能直接复制为生产命令：

```powershell
.\.venv\Scripts\python.exe scripts\logical_backup.py create `
  --binary-dir <PostgreSQL-bin绝对目录> --package <不存在的备份包目录> `
  --knowledge-root <共享原文根> --asset-root <共享图片根> `
  --key-path <独立备份key绝对路径> --scratch-root <受限临时根> `
  --confirm-offline-maintenance

# BACKUP_DATABASE_URL 此时应改为新建的、空的隔离数据库，不能指向源库。
.\.venv\Scripts\python.exe scripts\logical_backup.py restore `
  --binary-dir <PostgreSQL-bin绝对目录> --package <已完成备份包目录> `
  --knowledge-root <不存在的新原文根> --asset-root <不存在的新图片根> `
  --key-path <独立备份key绝对路径> --scratch-root <受限临时根> `
  --confirm-isolated-empty-restore
```

CLI 成功仅输出备份 ID、Schema、文件数、快照时间、耗时及恢复验证状态，不输出用户/原文/密钥/路径。失败只给脱敏说明，子进程 stderr 不透传。耗时是工具耗时，**不是完整业务 RTO**；快照时间不是 WAL 最新可恢复点。

## 恢复验证与失败处理

0017维护门控：create_backup可接收maintenance_guard，可信CLI成组owner/fence可进入数据库封存，在pg_dump/文件处理/READY边界复查租约；过期/替换不产生成功包，不自动release全局hold。恢复先验证原maintenance_state语义摘要，再与引用映射/rebind同事务清除旧维护owner/期限，fence保留，独立recovery_hold仍强制阻断；失败回滚清除。此为新的受控恢复变换，不宣称每行仍等于源。操作与自动调度未完成项见 [维护](maintenance-operations.md)。

0016 受控变换：源业务语义验证通过后，在对象引用映射/验证的同一事务，将所有恢复的 `storage_configs.state` 改为 `recovery_rebind`，报告 `storage_rebind_required` 数量。纯本地恢复也执行此保护。不篡改密文、不自动解密或把源桶凭据应用到恢复桶；此 state 变化与 hold/路径重定位都是明确的恢复后变换，不宣称每行仍等于源快照。resolve 在解密前拒绝待绑定配置。映射/验证失败时 state 变换同事务回滚且 hold 保留。

在独立授权恢复对账完成、受控解除 hold 后，管理员需要选择与本租户所有恢复对象引用 bucket 一致的批准目标并显式提供新凭据；不匹配拒绝保存。普通 UI 不能解除 hold，当前受控启用工具仍未完成。包装密钥不可恢复时，现有 SecretStore 供应/替换也需独立授权，不可从加密备份包推导。此批管理/Worker 配置入口不等于备份 CLI 或多租户备份角色配置。

恢复先拒绝非空 public 表/视图/序列或其他用户 Schema。逐 blob 解密并核对全部文件，再将 custom dump 用 pg_restore 导出无 owner/ACL 的 SQL，通过 `psql --no-psqlrc --single-transaction --set=ON_ERROR_STOP=on` 在同一事务执行恢复 SQL 和 recovery hold SQL；不会先写一半数据库再发现图片被篡改，也不会提交未持 hold 的恢复数据库。psql 不读取用户启动配置。SQL 后用一致只读事务核对所有业务表/行数/摘要、序列及文件引用；recovery_state 是明确的受控变换，单独验证 singleton、blocked 和本次 backup_id，不用源 marker 覆盖新恢复保护。验证通过后才在独立事务将 documents.source_path 重定位到新托管副本；快照中的原来源路径保存在加密 dump。重定位和 hold 是明确的恢复后数据变换，不宣称新库逐字节等于源库。

解密或文件校验失败时删除本次新文件根，数据库保持空。开始 SQL 后发生失败时保留隔离数据库/文件用于诊断，**不删除或自动重试/覆盖目标**。恢复 SQL 或 hold 写入失败由单事务回滚；验证或重定位失败不意味着已恢复数据已回滚，数据库 hold 保留。重试必须选择另一空库和新文件根。

工具不启动 API、模型或 Worker，不重写/恢复 pending job、Run 或外部业务操作，不自动恢复应用角色/ACL。不保证恢复点后的撤销、删除和外部操作状态仍存在。0014 新增数据库 recovery_state，恢复时强制 blocked=true；API 启动与 HTTP 请求、聊天/知识 Worker 领取、知识管理/发布及 Run 租约写入检查此事实。报告返回 `activation_requires_reconciliation=true,recovery_hold=true`。保持网络隔离，由授权流程前向补齐撤销/删除、处理旧租约/排队任务、核对未知工具结果，恢复角色/KMS/发布配置后再审阅启用。未提供自动或租户管理端解除 hold 的入口；完整前向对账及受控解除流程仍待实现。拥有数据库维护权限的操作员可以改表，应用层保护不是数据库 RLS，也不能保护旧版本/绕过服务直接运行 SQL 的程序。

## 尚未完成

0018 更新自动化状态：[调度 Worker](backup-scheduler-operations.md) 已接通本地计划、唯一执行租约、续租/排空/封存和策略绑定的可选本地实际恢复验证。恢复在原始摘要验证后同事务禁用恢复计划、把 running 备份任务变为 needs_review、清除旧维护owner并保留 recovery hold。此处更新下文历史“scheduler/门控未实现”描述；完整对象自动恢复、catalog/回执灾备、freshness/告警、保留/PITR及生产角色仍未完成。

未显式供应 object_store 的调用仍拒绝 `_private_object`，即使有同名缓存也不得使用。函数及 CLI 对象备份/恢复映射、远程加密包归档/取回已接通；多源桶最小权限供应、自动调度和生产验收仍需补齐，见 [远程归档](remote-backup-operations.md)。

自动 scheduler/唯一执行租约与实际停写门控、保留策略/删除保护、远程目录/回执灾备、生产 TLS/最小权限角色、KMS、备份指标/失败告警、自动恢复/freshness gate、在线文件协调、PITR/WAL、跨区域和生产规模恢复、恢复后授权/操作前向对账。不能以手工归档/恢复通过替代完整自动备份/灾备目标。

关联：[迁移备份](postgresql-migration.md)、[灾备 Runbook](deployment-dr-runbook.md)、[验收记录](production-verification.md)。
