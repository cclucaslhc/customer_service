# 知识解析共享容量

Schema `0029_knowledge_admission` 在 PostgreSQL 中保存全局策略，默认同时执行 2 个导入任务、每租户 1 个，每个任务逐文档解析。领取时持事务锁，检查所有保留占用后原子写入 owner、fence 和占用标记；繁忙租户不阻塞其他租户。API/Worker 只能读策略，迁移 owner 可修改。此限额约束数据库协调域中的执行授权数；每台机器的内存、CPU、磁盘仍需部署层 cgroup/容器限制。

`0030_knowledge_actions` 将 durable 模式的 HTTP 上传和重解析也纳入同一 queued 队列及容量限制，返回 202 任务回执；API 只固定输入，不执行解析。兼容模式仍同步执行，不属于生产容量保证。重解析固定已发布的不可变原文、文档 ID 和预期版本；外部源文件的变化须另行导入，排队后目标变更或删除会失败而非覆盖/复活文档。

升级至 0030 前排空并停止旧 Worker。迁移锁任务表，拒绝存在 running queued 任务；新动作清单由数据库触发器要求当前 Worker 能力标记，避免旧 Worker 将重解析误作普通导入。此标记用于版本兼容，不替代数据库角色鉴权。升级后启动新版 Worker；不支持降级丢弃固定目标。待处理本地上传输入及其哈希纳入备份，跨主机输入应使用私有版本化对象存储。

管理员可在管理页取消 durable 导入，也可调用 `POST /api/admin/knowledge/jobs/{id}/cancel`。取消立即撤销后续发布，保留已提交文档；运行任务的容量仍保留到 Worker 停止解析并确认释放。`cancelled` 并不表示物理进程已退出。失联 Worker 的 cancelled 占用也须先确认旧进程停止，再通过现有 owner release 命令释放；不得以取消成功回执作为停进程证据。重复取消幂等，完成后的取消不撤销已完成结果。兼容本地线程模式不支持该入口。

## 升级

知识 Worker 同时要求解析运行环境满足[Linux 解析隔离](parser-isolation.md)；启动时先解析合成样本，再领取任务。旧内核、不允许安装策略或依赖加载失败时拒绝启动，不把队列中的真实任务逐个消耗为失败。物理 cgroup/临时磁盘/PID 预算仍须独立部署和验收。

暂停新导入，完成旧 Worker 排空和失租核对，再停止旧 Worker。`0029` 对任务表加锁，拒绝仍有 queued/running 任务的升级；升级后重新执行经审阅的 `ops/postgresql/grant-runtime.sql`，再启动新 Worker。旧版本不带容量预留的 pending→running 更新会被数据库拒绝，迁移不提供丢弃容量证据的降级。

## 正常退出与失联

Worker 在解析停止、发布结束和心跳退出后，以原 job/owner/fence 确认释放容量。回执不明时保留占用并记录日志。租约过期仅终结任务，**不释放容量**；旧进程状态未知时，新任务等待。保留占用没有自动 TTL，需要核对旧主机、容器和解析进程组，不能只根据 lease_until 过期认定进程已停止。

正常 Worker 的过期维护把任务标为 failed/lease_expired；确认任务终态且进程组已停止后，操作员才可释放。保留原文档发布结果，不重新执行旧任务。该策略可能降低故障期间可用性，避免状态不明时增加执行授权。

## 操作

使用绝对路径的私有迁移 owner URL 文件，不在生产命令行中放 URL/密码：

```bash
python scripts/configure_knowledge_admission.py --database-url-file /private/migration-owner.url inspect
python scripts/configure_knowledge_admission.py --database-url-file /private/migration-owner.url configure --capacity 2 --tenant-capacity 1
python scripts/configure_knowledge_admission.py --database-url-file /private/migration-owner.url release-abandoned --job-id <uuid> --owner <uuid> --fence <n> --confirm-parser-stopped
```

`inspect` 只显示策略及最多 33 个占用的任务 ID、租户、owner/fence、状态和租约时间。改限额要求全部占用排空；变更和人工释放同事务追加审计，释放核对终态及 owner/fence，重复释放无副作用。确认标志代表操作员已核对远端进程，程序不代替现场核对。扩大容量前须核对每台机器实际解析内存及 Worker 分布。

## 备份恢复

策略与占用随当前 Schema 的加密备份保存，恢复库保持 recovery hold，不自动删除旧占用或恢复旧解析。恢复负责人核对旧部署停止后，再按恢复 runbook 处理任务状态和容量。只读备份角色保持 SELECT，API/Worker 不得获得策略写权限。

本机隔离测试覆盖并发领取、租户限额、排队回补、过期保留、fence 拒绝、人工释放、owner 权限及加密恢复。跨主机失联、数据库 HA、分区与物理资源限额仍需正式验收。
