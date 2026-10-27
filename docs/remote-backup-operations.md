# 私有远程备份归档与取回

状态：2026-10-07，隔离实现/验收。不是完整自动备份、PITR 或生产灾备交付。不授权读取生产数据、使用历史密钥或公网发布。

## 已实现的链路

目录/回执增量：独立批准catalog store保存加密计划/执行/制品报告与receipt，固定版本anchor认证取回后可消费既有archive fetch，无源DB依赖。发布前认证receipt对应远程加密index同一恢复点，guard/排他anchor保持失败待审；不是全目录发现或最新点证明，独立anchor分发和实际S3仍待验收。见 [目录恢复](backup-catalog-recovery.md)。

0018后续供应更新：scheduler CLI已接通单对象源/独立远程归档的显式角色凭据envelope，目标与归档namespace绑定批准计划，退出关闭客户端。两个新真实PG/S3调度联动测试已添加但服务不可用时跳过，不宣称它们通过。自动对象恢复、catalog/receipt灾备、freshness/GC/PITR/生产IAM仍缺。详见 [自动备份操作](backup-scheduler-operations.md)。下文“scheduler CLI尚无供应”为此前历史状态。

0018 更新：本地scheduler已有唯一租约、续租/排空封存、可选本地实际恢复验证；函数级归档可注入并在分块/发布前复查任务guard。当前scheduler CLI尚无远程store凭据供应，也没有自动对象恢复目标，因此本批不能证明自动远程灾备链路。catalog/receipt独立灾备、freshness和生产IAM/PITR仍缺，见 [自动备份](backup-scheduler-operations.md)。

离线逻辑备份 → 全包认证 → 私有版本桶分块归档 → 加密索引/固定版本回执 → 从回执取回确切版本 → 全包认证 → 新空库/新空对象桶恢复与 hold。

`app/backup_archive.py` 不访问数据库、不运行模型、不发现应用凭据。逻辑包保留原 AES-GCM 密文；归档前解密验证 manifest、dump 和每个文件的 size/SHA256，并检查完整文件集合，未引用文件/目录或损坏拒绝上传。临时解密只发生在显式私有 scratch，不将业务明文上传。Windows LF/CRLF 完成标记兼容，其余密文逐字节保持不变。

每块最多 4MiB，最多 8192 块（32GiB 密文字节上限，文件边界会使实际可用量更低），索引最多 16MiB；逻辑文件 blob 最多 10000 个。此为实现资源上限，不是已验收生产容量。文件 SHA256 在验证前后、上传过程中及索引发布前核对，包变化不产生成功回执，已上传版本保留待对账。

块写到独立授权 UUID namespace 的 `tenants/<namespace>/backup/...`；每块上传后按确切 VersionId 读回核对。索引保存 archive_id、backup_id、snapshot_at、文件尺寸/密文 SHA256 和块引用，用独立备份密钥 AES-GCM 加密，AAD 绑定 archive_id，最后上传并读回。没有 mutable latest 指针或公开 URL。

本地回执只包含 format/archive_id/namespace 和加密索引的完整版本引用，不含用户内容或密钥。通过同卷临时文件、flush/fsync 和排他 hard-link 发布完整回执；目标名已存在时拒绝覆盖。receipt 是恢复定位依赖，必须另行可靠保存；目前没有远程目录/catalog、receipt 自动复制或签名 freshness 服务。没有回执不能凭最新对象猜测恢复点。

取回核对 namespace、索引 AAD、文件名、块唯一性/前缀/桶/版本/尺寸和总预算，只读确切版本。先在私有临时根重建并认证全包，backup_id/snapshot_at 一致才排他创建新目标，READY 最后发布。缺版本、损坏或绑定错误不回退 latest，不返回成功；已有目标不覆盖。不启用任何 API/Worker，不解除 recovery hold。

## 备份对象凭据供应

逻辑备份与远程归档 CLI 均可通过批准 policy、目标名称和显式 `--storage-credentials-stdin` 获取独立运维凭据。批准文件格式见[对象存储](object-storage-operations.md)，生产只接受 HTTPS；不提供测试 loopback 豁免参数。

stdin 是受控 secret-provider/操作员流程创建的私有管道，读取最多 16KiB UTF-8 JSON，仅含 access_key/secret_key/可选 session_token。**不要在可回显终端粘贴凭据、写命令行 JSON、保存进 Shell 历史、Git 或日志。** 工具不使用 AWS_*、模型主密钥、管理端历史凭据或应用 SecretStore，也不自动获取新 IAM 权限。凭据错误输出固定脱敏说明，SDK 总在 finally 关闭。

这是受信运维入口，不是租户管理员 HTTP 接口；逻辑数据库 dump 包含所有租户，操作员必须具有明确全库备份授权，不能将本租户 UI 权限视为该授权。各凭据权限由部署 IAM/Secret Provider 供应与审计，代码不能凭字段证明 IAM 最小权限：

- 源读取角色：指定源桶/租户前缀的 GetObjectVersion，以及版本化/PublicAccessBlock 检查；不授予 Put/Delete。
- 恢复角色：指定新空桶的 PutObject/GetObjectVersion、ListObjectVersions、版本化/私有状态检查；不自动 CreateBucket/Delete。
- 归档角色：独立备份桶/namespace 的 PutObject/GetObjectVersion 与版本化/私有状态检查；没有删除接口。
- 取回角色：独立备份桶/namespace 的 GetObjectVersion；不因取回自动授予数据库或业务系统访问。

CLI 当前一次选择一个源桶，所有引用必须能在该桶校验；不同租户使用不同源桶时失败关闭，不跳过外桶或读缓存。多源桶路由、全库最小权限角色、管道供应平台/KMS、凭据轮换、生产 TLS/公开访问阻断与生产规模仍待交付和外部验收。

## 命令入口

以下仅非敏感占位参数，需授权操作员核实路径和目标。stdin 必须由明确授权的安全供应流程提供；命令自身不会寻找凭据。逻辑备份依旧要求实际停写/排空确认，不得在定时器中无条件加确认旗标。

```powershell
# logical_backup.py create/restore 的其余显式路径与维护确认保持原规则；可额外提供：
--storage-policy <绝对批准JSON文件> --storage-target <批准源或恢复目标名> --storage-credentials-stdin

.\.venv\Scripts\python.exe scripts\backup_archive.py archive `
  --package <已完成的加密包绝对目录> --receipt <不存在的回执绝对文件> `
  --key-path <独立备份密钥绝对文件> --scratch-root <私有临时根> `
  --namespace <部署批准的UUID> --storage-policy <绝对批准JSON文件> `
  --storage-target <批准归档目标名> --storage-credentials-stdin

.\.venv\Scripts\python.exe scripts\backup_archive.py fetch `
  --package <不存在的取回包绝对目录> --receipt <既有回执绝对文件> `
  --key-path <独立备份密钥绝对文件> --scratch-root <私有临时根> `
  --namespace <与回执一致的UUID> --storage-policy <绝对批准JSON文件> `
  --storage-target <批准归档目标名> --storage-credentials-stdin
```

归档报告 remote_verified 表示固定版本上传/读回及索引发布通过；不是数据库恢复验证、跨区域持久性、费用预算或 RPO/RTO 达标。取回 verified 表示全包认证通过，不代表 PostgreSQL 恢复成功。随后必须执行逻辑 restore，在新隔离库/不同新空业务对象桶核对业务摘要、引用和 hold/rebind，再完成权限/删除/外部操作前向对账。生产解密卷/Windows ACL 要独立配置，合成临时文件验收不证明权限合规。

## 失败与未完成项

远程请求回执丢失时可能已有版本存在。工具不删除、不自动重试归档，也不以失败状态认定版本不存在；保留对象和源包，后续受控引用/catalog 对账。取回失败不发布新包；最终文件发布失败只清理本次排他创建的包，不覆盖已有目录。

仍需自动 scheduler/唯一租约、实际停写/排空门控、远程 catalog 与 receipt 灾备、保留/GC/Object Lock、自动隔离恢复验证、freshness gate/指标告警、KMS/最小权限角色、WAL/PITR/异地故障演练和容量性能。完整 S0–S7 目标保持进行中。
