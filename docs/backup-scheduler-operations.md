# 自动逻辑备份与隔离恢复验证（0018）

状态：隔离实现，不是上线许可。PITR、异地容灾、完整对象自动恢复、生产权限及保留策略仍未交付。

## 执行边界

迁移不创建或启用计划。可信全库运维人员显式提供计划和 `BACKUP_DATABASE_URL`，不能使用租户管理员权限代替全库授权。连接仅支持 loopback PostgreSQL；不发现历史密钥或凭据。计划只记录非秘密配置及独立加密密钥的绝对路径，不把密钥正文放入 JSON。

`backup_schedules` 持久记录策略指纹、间隔和下次时间；`backup_jobs` 记录唯一时间槽、owner/fence/数据库时钟租约及有界报告。部分唯一索引保证全局至多一个 running。错过的时间槽合并，不回放积压。任何 needs_review 阻止后续领取和配置；期限过后须显式调用有界 reaper 标记待审，不自动重试未知结果。

领取 → 每秒续租 → 请求全库维护 → 有界等待排空 → 持独占封存锁生成实际 pg_dump/文件加密包 → 全包认证 → 解除源维护 → 可选远程归档 → 可选实际隔离恢复 → 写 complete。备份期间 guard 检查任务租约和封存许可；远程上传分块和发布前也检查任务租约。失败不自动解除仍持有的维护，不重放命令、不删除包或孤儿对象。

只有本地包认证的 complete **不代表恢复已验证或异地备份已完成**。分别检查 local_verified、remote_verified、restore_verified。恢复验证失败时源维护可能已正常解除，但目录任务仍 needs_review，不能把它解释成源服务仍停写。

这两张表是可信控制平面，刻意不进入 0017 业务表写保护，允许在封存期间续租/记录任务；正式角色必须禁止普通 API/租户连接修改控制表，当前生产角色供应未验收。受信程序报告不是抵御数据库管理员伪造的认证凭据。

## 计划与 CLI

计划 JSON 必须是绝对路径、非符号链接且不超过 16KiB，字段为：

| 字段 | 约束 |
| --- | --- |
| schedule_id | 显式 UUID，不自动生成生产计划 |
| interval_seconds | 60–604800 |
| binary_dir | 已存在的 PostgreSQL 工具目录 |
| knowledge_root、asset_root | 已存在的受管本地原文/资产根 |
| package_root、scratch_root | 已存在的私有输出/临时根 |
| key_path | 独立 32 字节备份密钥的绝对路径 |
| drain_timeout | 1–600 秒，默认 120 |
| lease_seconds | 符合维护租约范围，默认 60 |
| restore_policy | 默认 null；需要自动恢复验证时填验证器 policy_fingerprint |
| object_policy | 默认 null；对象源填 S3ObjectStore.policy_fingerprint |
| archive_policy | 默认 null；远程归档填 S3ObjectStore.policy_fingerprint |
| archive_namespace | 与 archive_policy 成对，规范化 UUID 字符串 |
| catalog_policy | 默认null；独立目录store目标指纹，必须有archive且不能同目标 |

执行 `scripts/backup_worker.py --plan <绝对路径> --configure --confirm-global-backup` 显式启用；`--once` 尝试领取一次，否则每 5 秒检查。不迁移、不隐式配置；配置的策略不可原地变更，变化需审阅迁移。SIGINT/SIGTERM 停止下次轮询，不保证立即中断当前数据库命令或当前备份。

自动恢复验证必须同时提供 `BACKUP_VERIFICATION_ADMIN_URL`、`--verification-root`、`--confirm-isolated-verification`。批准计划的 restore_policy 必须等于 `LocalRestoreVerifier(...).policy_fingerprint`；该属性只哈希实现版本、去除密码的目标 URL 和规范化恢复根，不读取密码文件或密钥正文。配置阶段与执行阶段都检查一致性：遗漏验证器、换恢复根/目标或擅自增加验证器均在领取前拒绝；改计划删除 restore_policy 也会被数据库中的计划指纹拒绝。环境密码轮换不改变策略目标。

CLI 已支持独立对象源与归档凭据：`--storage-policy <批准策略绝对路径>`、`--source-target <批准源名>`／`--archive-target <批准归档名>`、`--storage-credentials-stdin`。源与归档角色必须和计划 object_policy/archive_policy 完全匹配，归档 namespace 从批准计划读取，不从临时参数更换。两类存储的指纹绑定实现类型、endpoint、bucket、region，独立凭据轮换不改目标。换目标、遗漏/添加角色在领取前拒绝；更改 JSON 删除角色也不能匹配持久计划。旧版0018计划因字段扩展指纹不匹配，失败关闭；不可直接改数据库hash，需要显式审阅迁移/重新批准计划。

安全管道一次供应最大32KiB JSON envelope，只允许已配置source/archive/verification角色，例如两种角色时结构为 `{"source":{"access_key":"<独立源备份凭据>","secret_key":"<独立源凭据>"},"archive":{"access_key":"<独立归档凭据>","secret_key":"<独立归档凭据>"}}`；session_token 可选，不放在命令参数、计划、环境或日志。所有角色/凭据在客户端构造前校验；构造部分失败关闭已拥有的客户端，Worker退出关闭全部客户端。此入口是可信全库运维工具，不复用租户存储密文/历史AWS凭据/模型密钥。批准策略使用HTTPS，不提供生产HTTP豁免。源/归档client在维护前check_ready；verification是保留创建前缀，不当成已有bucket检查。

对象源目前一个批准bucket，多源bucket仍失败关闭；对象引用不读本地缓存。对象自动恢复入口见下节，但真实PG/S3联动仍需可用服务验收；离线客户端/CLI测试只证明参数和资源关闭，不证明S3语义。有对象源的计划不能以恢复失败或无restore_policy冒充对象恢复已验证，不能把该入口当作完整生产灾备。

## 恢复验证与失败处置

对象恢复验证新增 `--verification-object-target <批准目标名>` 与 `--confirm-isolated-object-verification`，必须同时明确已有PG验证URL/root/隔离确认和非空restore_policy，才读取verification角色独立凭据。批准目标的bucket字段在此角色表示4–30字符的安全创建前缀（小写字母/数字/连字符），不是现有桶；IAM仅允许隔离账户/环境中该前缀下CreateBucket、版本/公共访问封禁、列表与确切版本读写，不授予源桶写/删除能力。禁止用源/归档凭据代替，实际账户/IAM联合验收仍待完成。

ObjectRestoreTargets按`<批准前缀>-<attempt UUID hex>`为每次验证生成独立bucket，非404/未知HEAD失败关闭，已存在目标拒绝，不接管或清空；明确新建后开启版本，生产目标设置四项PublicAccessBlock并确认私有/空版本桶。非us-east-1明确LocationConstraint；所有网络阶段前后复查任务guard。创建ack未知、权限设置失败或失租不删除资源，不重试该attempt。验证器策略指纹包含对象创建目标，换前缀/endpoint/region或省略factory无法匹配批准plan；配置/构造阶段不创建桶，仅领取后实际恢复创建。

实际restore_backup消费独立空bucket，按加密包恢复确切对象版本/摘要、原子重映射引用及配置rebind，完整业务/文件校验和hold不启用服务。成功report增加restore_bucket供运维精确对账，目录/metrics不把bucket加入标签。成功只删除确切验证数据库，**对象桶/版本和文件仍保留**，下次验证用新的attempt bucket，不复用或自动GC。失败状态可由计划前缀+attempt精确定位，待授权审阅。SDK客户端由Worker统一关闭，factory生成的store借用client，不重复关闭或删除source。AWS us-east-1的已拥有bucket创建语义不能当作原子排他创建；UUID/持久attempt目录和预检查不代替正式IAM/跨进程对抗验收。

验证器需显式确认隔离环境和私有根，创建精确 `cs_backup_verify_<attempt UUID hex>` 新数据库及新文件根。已有同名资源不覆盖。实际解密/pg_restore、业务摘要/序列/文件校验及 recovery hold 检查成功后，只删除本次确切验证数据库；不启动 API/Worker，不解除 recovery hold。

失败或未知结果保留确切数据库/文件供授权复核；成功验证的明文文件也保留，**尚无自动 GC**，运维需按批准保留期与隐私规则处理，Windows 根 ACL 需平台配置。不得按宽泛前缀删除其他任务资源。检查 catalog、维护状态、包/回执和确切验证资源后，再单独授权处置；目前无自动 needs_review 解除入口。

恢复业务包先校验原始数据库摘要，再同事务清除旧维护 owner、禁用所有恢复计划、把恢复的 running 备份任务标记 needs_review，独立 recovery hold 保持阻断。恢复源 catalog 中的 running 不等于恢复环境可以重新执行该任务。

## 新鲜度、指标与告警（只读增量）

`scripts/backup_health.py` 使用显式 `BACKUP_DATABASE_URL`、`--schedule-id`、`--policy-fingerprint`、`--max-age-seconds` 和 `--confirm-global-catalog-read`。固定批准计划与指纹防止其他计划的近期备份掩盖目标缺失。上限60秒至30天；默认同时要求local和restore，`--require-remote`增加远程要求；只有显式`--local-only`才豁免restore，不能把豁免称为恢复验证已通过。

监控以数据库时钟和单个REPEATABLE READ只读快照检查：计划存在/启用/指纹、全局needs_review/过期running、维护/恢复hold，以及该计划最近100条complete中的local/remote/restore证据。按源`snapshot_at`计算年龄，不按任务finished_at；未来/无时区/坏时间/坏标记报告使健康失败。只读取有界目录报告，不读取业务正文/密钥/包/对象，不修复、续租、重试或解除hold。history_truncated提示历史取样上限；较旧记录不被扫描，可能保守地报告证据缺失。维护期间仍可读，maintenance_hold单独输出，recovery_hold使健康失败。

退出码：0满足明确要求，2成功采集但保护不健康，1采集/配置失败。JSON失败只返回collection_success=false/healthy=false；`--format prometheus`失败只输出collection_success=0，不沿用上次健康快照。成功指标只有固定kind(local/remote/restore)标签，不包含计划/用户/Run ID、URL、凭据、报告正文。指标反映**目录中历史验证的恢复点**，不代表当前包/密钥/网络仍可用、完整PITR、生产RPO或实际RTO。

Prometheus规则位于 `ops/prometheus/backup-alerts.yml`，目标scrape job必须配置为customer-service-backup；部署方填实际owner/环境和通知渠道，规则不发送消息。包含scrape失败/采集缺失/失败/超过300秒未刷新、所需恢复点过期、失败/失租待审和综合保护不健康。固定字段见app/backup_health.py。平台每60秒采集，可用`--format prometheus --textfile <私有目录绝对路径>/customer-service-backup.prom`同卷原子发布给textfile exporter：fsync新文件后replace，只接受固定专用名称/现存安全父目录，拒绝符号链接，不删除其他文件。退出码2也更新不健康指标；授权后的采集失败发布collection_success=0。发布失败保留旧文件并返回1，程序崩溃留下旧成功文件仍由collection_timestamp超时规则告警。一个私有目录/独立scrape target对应一个固定批准计划，避免多个计划往同一文件写。输出直接使用UTF-8/LF字节，Windows文本管道CRLF不符合Prometheus文本协议。不要暴露无鉴权管理HTTP端点或直接把凭据放监控进程命令中。

实际Prometheus规则验证入口：`promtool check rules ops/prometheus/backup-alerts.yml` 与 `promtool test rules ops/prometheus/backup-alerts.test.yml`；测试含健康静默、local成功但restore过期、采集失败、失租触发/恢复、旧成功文件冻结及多个目标中一台down不被健康目标掩盖。另通过promtool check metrics验证成功/失败指标和实际CLI失败管道。pytest需显式提供TEST_PROMTOOL_PATH才运行，缺工具跳过，不把YAML解析成功冒充PromQL验收。这里尚无已部署Prometheus/Alertmanager/真实测试receiver、自动textfile安装、实际通知和实时远程包探测，完整S2/S7仍未完成。

## 剩余生产化缺口

目录/回执检查点增量：Worker已有独立catalog角色、策略绑定、加密上传/认证读回、固定版本anchor与不依赖源DB的取回入口；独立anchor分发/全目录清单/实时最新点选择、真实S3验收和完整灾备仍缺，不把本机anchor当异地目录。详见 [目录与回执取回](backup-catalog-recovery.md)。更新此前“无catalog/receipt入口”的历史状态，不宣称目录灾备已全部完成。

catalog/receipt 独立灾备、自动对象恢复真实联动验收、多源bucket供应、监控平台/receiver/实时包检查、保留/隐私 GC、Object Lock/KMS、生产最小权限/TLS、在线 WAL/PITR、HA 与容量/RPO/RTO 验收仍待实施。最新测试证据见 [验收记录](production-verification.md)，全部 S0–S7 目标保持未完成。
