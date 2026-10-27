# 私有版本对象存储：当前接入边界

状态：2026-10-07，部分实现，不是完整生产存储交付。

## 2026-10-27 隔离服务兼容性增量

后续恢复专项已补齐：定位到此前隔离 PostgreSQL 运行时的 pg_dump/pg_restore/psql，实际 pg_dump 版本16.10；显式提供 TEST_POSTGRES_BIN 和本批隔离 PG/S3 连接后，`test_real_restore_fences_old_config_and_requires_matching_rebind` 为 **1 passed / 9 deselected，7.16秒**。确认恢复标记要求重绑定、旧配置不可直接使用、错误目标拒绝、匹配目标重新绑定成功。该结果覆盖上一联合批次唯一跳过项，但不是整组已重新运行，也不解决远端TLS/PublicAccessBlock兼容性。

Docker 已恢复可用；仓库 compose.integration.yml 新增独立 digest 固定 MinIO 测试服务，端口仅绑定 Windows 回环 59000，数据在专用 tmpfs，显式合成凭据。没有读取或复用运行中的 fastgpt-minio 及其数据/凭据；本项目测试 PG/Redis 同样为独立容器。该服务仅供本机兼容性测试，不是给 guest 开放的生产 HTTPS 端点。

真实标准 SDK PutPublicAccessBlock 请求在当前 MinIO `RELEASE.2025-09-07T16-13-09Z` 返回 `MalformedXML`；不能仅凭该错误断言所有 MinIO 版本都不支持，也不能断言请求已生效。测试进一步用真实本机客户端、关闭 loopback readiness 豁免后确认生产 gate 拒绝该桶。该依赖注入只验证 gate，不证明远程 TLS。新增兼容性负向测试通过表示**拒绝不满足契约的服务**，不是生产存储批准。

因此本机版本化/匿名拒绝/完整性及队列组件测试可以继续，但跨guest正式装配尚未通过。不得开启远端 loopback 豁免、关闭证书校验或用模拟 PublicAccessBlock 响应绕过此限制。后续需要满足四标志契约的批准私有 S3 服务，或经过明确设计/审阅的等价供应商安全策略；不能将当前测试 MinIO 直接标为生产兼容。

## 原理与装配

对象存储负责原文、图片和未来备份 blob，不存模型凭据或替代数据库事务。`S3ObjectStore` 必须由受信部署配置显式提供 endpoint、bucket、region 和凭据；不调用 AWS 默认凭据链、不发现环境历史密钥、不生成公开 URL。对象凭据与模型密钥是不同权限域，不开放客户配置入口。

0016 增加管理员凭据供应/轮换 UI 与 Worker 配置入口。生产装配使用 `create_app(..., auth_policy=..., storage_policy=policy, secret_store=...)`，不能同时注入直接 object_store。管理员在知识库页面选择部署批准的目标并显式填写已有凭据；对象密钥与模型密钥均不自动读取环境。测试仍可直接注入 store，但不能把依赖注入当作生产凭据供应。

## 批准目标与凭据管理（0016）

部署方通过 `StoragePolicy.from_file(<绝对 JSON 路径>)` 提供非敏感批准列表。文件不允许符号链接，最大 16KiB、1–32 条唯一目标；目标格式如下（仅占位示例，不执行网络请求）：

```json
[{"name":"company-private","endpoint":"https://objects.example.invalid","bucket":"company-private","region":"us-east-1"}]
```

普通管理请求只能提交目标名称，不能指定 endpoint/bucket/region，避免将管理表单变为任意网络探测入口。部署文件、DNS/出站策略和批准端点仍须受信控制；此白名单不是完整基础设施 SSRF 防护或 IAM 验收。生产策略不接受明文 HTTP；loopback ACL 测试豁免没有生产 CLI/UI 开关。

GET/PUT `/api/admin/storage` 需要实时 `config.manage`、同源 Cookie/CSRF。GET 只返回 enabled/configured/target/revision/targets/policy_configured/recovery_rebind，不返回凭据、密文或 nonce。PUT 提交 target/revision/access_key/secret_key/可选 session_token；校验失败不回显请求。UI 保存前清空凭据，保存请求最多等候 45 秒，无自动重试；失败/超时只表示结果未确认，应刷新核对版本后再决定操作。

`storage_configs` 每租户一行。保存先鉴权与版本检查，再检查桶私有/版本化 readiness；网络结束后重查身份。AES-GCM 绑定租户、目标 fingerprint 与递增 revision；配置更新和 `storage.configure` 审计同事务，审计失败全部回滚。并发相同 revision 只允许一个成功，另一请求 409。首次配置后只能轮换同一目标的凭据；改变批准列表中的 endpoint/bucket/region 也会使绑定失效，不可静默切桶。

API 与 Worker 按当前请求/任务原 actor 解析本租户配置，`knowledge.read`/身份/配置版本实时复查，SDK 生命周期结束关闭。无配置行时可以继续本地模式；有行却缺 policy、主密钥、目标绑定或恢复 rebind 时失败关闭，不发现 AWS 默认凭据或读旧缓存。

`scripts/chat_worker.py` 和 `scripts/knowledge_worker.py` 增加成对参数 `--storage-policy <绝对批准文件>`、`--storage-master-key-path <绝对包装主密钥文件>`。API/Worker 必须使用一致 policy 和授权供应的包装密钥，服务凭据从本租户加密配置读取，不进入命令行。对象模式新任务不需共享源文件根；旧 NULL 输入任务仍需原可信共享根，所有根参数保持显式。备份 CLI 尚未接通此供应入口，不能混同。

恢复将 storage_configs 在引用映射同一事务置为 `recovery_rebind`；旧凭据不得解密/使用。hold 期间连管理操作也拒绝，必须先完成授权恢复对账/解除保护流程，再由管理员选择与该租户全部恢复对象桶匹配的批准目标并提供新凭据。错误桶拒绝保存。当前没有应用解除 hold 的入口，主密钥/KMS 恢复仍需独立授权；测试对合成数据库的维护 SQL 不属于生产操作方案。

每次发布生成 `tenants/<tenant UUID>/<knowledge|assets>/<attempt UUID>/<SHA256>`，必须启用 bucket versioning 并取得非空、非 null VersionId。上传后按该版本读回核对大小和 SHA256，不把 ETag 当摘要。生产 readiness 要求四个 PublicAccessBlock 标志均为 true；远程 endpoint 必须 HTTPS、验证证书。

引用为严格的 `{bucket,key,version_id,size,sha256}`，服务端保存为文档/资产 JSONB metadata 的保留字段 `_private_object`；解析器同名字段被移除。采用现有 metadata 让引用与文档版本、切块、资产及耐久 item/result 共享发布事务，而不是创建脱节账本。尚无独立对象目录、跨表引用约束或 GC 账本。

短数据库事务仍复查身份、租户、expected_version 和 Worker owner/fence/lease；对象上传不持数据库事务。失败或 COMMIT 回执不明时不删除版本，留待权威引用扫描、保留窗口和受控 GC 对账。目前没有远程删除接口；这不等于实现隐私删除或空间回收。

## 跨实例读取与失败关闭

- 已发布原文可在另一实例按引用重解析，不需要原机器 source_path。按 document ID / expected_version 替换，ID 不变、版本递增；并发更新拒绝。
- 图片 HTTP 返回鉴权后 PNG 字节，no-store、nosniff、sandbox，不给客户端 S3 地址/版本。读取前鉴权；下载后复查资产授权及当前发布内容，撤销或替换时不返回旧字节。
- 引用必须与文档/资产 size、sha256 和租户/kind 一致。版本缺失或损坏不能回退 latest、本地缓存或原导入文件。对象不可用返回脱敏 HTTP 503 `object_store_unavailable`；越权不存在仍 404，撤销仍 401。
- 管理列表与详情移除内部引用字段；权威引用只保存在数据库。
- 未配置 store 的实例不能重解析/读取对象文档。默认 SQLite/local 模式不启用远程对象。

## 验证环境与限制

组件真实 S3 测试涵盖覆盖后旧版本读取、匿名/错误凭据拒绝、删除指定版本后拒绝 latest。应用真实 PostgreSQL + S3 专项涵盖无本地文件的跨实例重解析和图片、版本缺失/未配置拒绝缓存、发布失败保留孤儿版本、外租户不触发下载、备份拒绝缓存及 HTTP 读取中撤销。

MinIO 官方二进制下载返回 410，本次没有使用非官方镜像。隔离真实服务使用官方 SeaweedFS 4.48 Windows release，归档 SHA256 `FE90C04C0620AD1A1C756F86CD5E1443773F56A446688077A4BB5EC04C3CC874`；只监听 127.0.0.1，数据与凭据均合成。

该服务未实现 PublicAccessBlock API，只有显式 loopback + `allow_loopback=True` 才允许 `test_skip_public_block_api=True`，改为核对私有 canonical-user ACL。此测试豁免禁止远程部署，不证明生产 PublicAccessBlock/TLS/IAM/KMS 验收。注入 client 的故障测试不是真实 S3 证据。

## 必须继续完成

1. 0015 对象排队输入及 0016 管理供应/Worker 配置已接通；独立进程消费加密配置的证据使用真实隔离 PG/S3、显式合成 wrapping key。生产 TLS/PublicAccessBlock/IAM/KMS 与规模验收仍待完成，不把 loopback 测试当作正式部署证据。
2. 对象逻辑备份/恢复函数及 CLI 已接通；CLI 可显式批准目标并通过私有 stdin 管道供应独立运维凭据，不复用管理配置/模型/环境密钥。另有加密备份包的分块固定版本归档/取回及全包认证。默认未供应仍拒绝对象引用；多源桶/生产最小权限供应、自动停写调度和恢复验证服务未完成，见 [逻辑备份](logical-backup-operations.md)与[远程归档](remote-backup-operations.md)。
3. 原文/资产迁移、对象目录与 GC、保留政策、审批删除和备份删除传播；跨租户 bucket IAM、加密/KMS、资源预算、流式传输和生产规模测试。
4. 自动备份 scheduler/租约、私有备份桶版本目录、freshness gate、异地备份及 WAL/PITR。
5. 继续补齐对象凭据最小权限、失效/轮换演练与 KMS、独立备份角色/IAM/安全管道平台供应；现有 CLI 显式输入不等于生产密钥灾备完成。不将真实密钥置于命令行、日志、文档或 Git。

测试须显式设置隔离 TEST_POSTGRES_URL / TEST_S3_ENDPOINT；没有服务时专项跳过计入缺口。完整 S0–S7 目标仍 active，不能据此上线。
