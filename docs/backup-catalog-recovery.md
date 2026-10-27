# 加密备份目录检查点与回执取回

状态：隔离实现，真实PG/S3链路尚待服务验收。检查点不是完整灾备完成声明，不恢复生产服务或执行队列。

## 为什么单独保存

源数据库中的backup_jobs和本机receipt可能与源磁盘一起丢失；远程密文包本身不包含“最后一次备份完成后才生成”的回执。新增检查点把计划/策略指纹、attempt/owner/fence、已验证恢复点报告和远程receipt一起AES-GCM加密，上传到**与archive不同批准目标**的私有版本store。AAD绑定namespace/record UUID，上传后按确切VersionId/摘要读取、认证解密比对，再原子排他发布anchor。

每条检查点至多64KiB，receipt至多4KiB，报告字段白名单拒绝任意凭据正文。它记录的是经过批准流程验证的备份制品，不声称源DB中该任务已complete：上传/anchor之后、源catalog终态提交之前仍可能崩溃。未知结果保留待审，不自动重放。

archive和catalog不同逻辑目标不等于物理异地；区域/账户/IAM/删除保护/KMS/监控仍须平台配置并验收。没有全目录自动发现、按latest选择、完整源catalog数据库重建、抗删除/回滚证明或自动保留/GC。

## 显式启用

BackupPlan增加catalog_policy（S3ObjectStore目标指纹），必须同时有archive_policy/archive_namespace且与archive目标不同。Worker提供`--catalog-target <批准目录目标名>`，和storage-policy、storage-credentials-stdin一起供应独立catalog角色凭据。目标角色和指纹必须匹配计划；遗漏/更换失败关闭。字段扩展使旧0018策略hash不匹配，需要显式审阅重新批准，不能自动改数据库hash。配置/构造不上传检查点。

执行顺序：封存备份/包认证 → 远程归档 → 可选实际恢复验证 → 认证receipt的固定版本加密index（必须同archive_id/backup_id/snapshot） → 加密上传目录记录/认证读回 → 发布`<attempt>.catalog.json`anchor → 源catalog终态提交。guard在I/O和anchor发布前复查；失败不删除远程孤儿版本、重写anchor或重放任务。成功report含catalog_verified/catalog_record_id/catalog_reference，数据库只存有界固定版本引用；不包含凭据。

## 必须独立保存的引导材料

必须通过已批准的独立介质保存anchor、独立备份密钥、批准catalog/archive目标策略、所选schedule UUID/策略指纹和namespace。anchor只是固定版本定位信息，不含密钥；密钥不与数据副本放在同一故障域。本代码不会偷偷发现环境密钥、云凭据或源租户配置。

**仅在源package_root保留anchor不构成灾备。** 独立分发/持久化anchor、全目录清单、不可回滚时间/版本检查点、灾难后的最后有效恢复点选择和外部权限/业务前向对账仍未实现。旧合法anchor能认证旧恢复点，不能证明这是最新一条。需要核对批准恢复点/新鲜度/独立记录，不能自动选择unversioned latest。

## 隔离取回流程

### 批准清单与恢复点选择

新增`scripts/backup_recovery_points.py`读取独立批准的清单，不扫描源目录/云latest。JSON结构为`{"format":1,"anchors":[{"path":"<独立anchor绝对路径>","sha256":"<anchor原始字节SHA256>"}]}`，最多100项、64KiB；必须通过另一个可靠批准渠道提供整个清单原始字节的SHA256（`--manifest-sha256`）。清单同时固定路径和每个anchor摘要，不只固定可被替换的文件名。当前不自动生成、分发或签发清单，摘要可信来源/防回滚仍依赖部署方。

参数除已有namespace/计划/策略/密钥/独立catalog凭据外，还必须给出带时区`--not-after`和60秒至30天的`--max-gap-seconds`，可给出`--min-snapshot`限制已批准最早恢复点。cutoff不得在本机当前UTC之后，需要部署时钟同步。gap是请求恢复时间与所选snapshot之差，不是当前backup freshness，也不是系统实测RPO。仅选cutoff之前、gap以内、minimum之后的记录；默认要求restore_verified=true及完整真实恢复字段。仅显式`--allow-unrestored`豁免先前恢复验证，不把豁免称为恢复已验证。

全部列出anchor与固定版本目录记录先认证；任何列出记录不可读、密钥/计划/策略/摘要错或未来时间都使整次选择失败，不隐藏缺项后自动回退。重复路径、重复record/attempt（分叉）拒绝；最新eligible时间相同也拒绝，不按UUID猜选。选择仅在已批准集合中取最新，输出`complete_catalog_proven=false`，不能证明未列出的全局目录/最后一次备份。合法旧集合的回滚需要外部不可回滚批准记录/WORM等，算法不能自行检测清单外遗漏。

使用`--storage-credentials-stdin --confirm-isolated-recovery-selection`明确批准，默认仅输出候选与选择。提供`--destination`才认证取回所选checkpoint：fetch前再次核对所选anchor摘要，避免选择/取回间文件替换，READY最后。不连接源DB、重放队列、取回整个业务包或启动API/Worker；仍需后续archive fetch、PG/对象restore/hold与前向对账。错误固定脱敏，关闭自己构造的client。现有真实PG/S3灾难取回测试已扩展此选择/显式豁免/摘要绑定路径，但服务缺失时跳过，不以合成AES/内存CLI测试代替实际云版本和完整DR验收。

### 单条固定版本取回

1. 用独立目录凭据调用`scripts/backup_catalog.py`，明确anchor、destination（不存在的绝对目录）、namespace、schedule-id、policy-fingerprint、key-path、scratch-root、storage-policy、storage-target，并提供storage-credentials-stdin及confirm-isolated-catalog-fetch。
2. 工具仅访问catalog store，不接受源DB URL、不读取业务数据或启动服务。固定版本get后认证namespace/record UUID/计划/策略/报告绑定，在新私有目录排他发布record.json/receipt.json，READY最后。错密钥/策略/记录ID、未知版本或已有目标拒绝；部分发布留待审，不盲删。
3. 使用取回的receipt.json和独立archive凭据调用既有`scripts/backup_archive.py fetch`，完整固定版本取回并认证备份包。目录verified只证明检查点认证，不代替包认证。
4. 用`scripts/logical_backup.py restore`恢复到独立新PG/新空业务bucket；数据库/序列/文件/对象引用校验后保持recovery hold，计划禁用，旧running任务待审，不启用Worker/API。
5. 对照独立记录、RPO/RTO、权限撤销/删除/外部业务未知操作进行前向对账，由另行批准流程处理资源保留/启用。当前没有生产自动解除hold入口。

输出目录和anchor在Windows需要由平台设置ACL。禁止把操作命令中的凭据/密钥正文放日志或共享MD；CLI仅固定脱敏失败消息，关闭自己构造的SDK client。实际通知、WORM/跨区域、全目录清单和完整DR报告仍待交付。

验证证据见 [实施记录](production-verification.md)，关联 [调度/新鲜度](backup-scheduler-operations.md)、[远程归档](remote-backup-operations.md)、[DR Runbook](deployment-dr-runbook.md)。
