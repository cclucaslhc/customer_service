# S2 私有 Collector 实机证据

2026-10-29最新安全状态：运行40791886920在官方0.164.0镜像发现`CVE-2026-116587`（go-jose/v4 4.1.5，修复4.1.6），本机独立新库扫描同样单项HIGH。因此下文官方镜像0 HIGH仅为旧库时间点，当前不再批准。`ops/otel/Dockerfile`以官方完整Contrib manifest重建，保留所有组件，固定源commit、OBI源码哈希、Go编译器和模块校验；构建尚未完成，不声称已修复。验证脚本现在必须同时显式提供`--app-image`与`--collector-image`，不自动回退旧镜像。

本机首轮因国内镜像校验数据库EOF失败，现明确指定官方校验数据库URL，未关闭GOSUMDB或取消哈希；使用独立命名BuildKit缓存，重试不丢弃已验证依赖。实际验收会在无网络/只读/无capabilities下分别运行官方参考与重建版`components`静态清单，比较全部分类、组件名/供应器scheme、模块版本和稳定性；只忽略dist buildinfo版本。官方清单共255项，移除oidc认证或改变模块的负向测试拒绝；新二进制尚待构建后实际比对。这不启动旧参考的采集服务或批准其高危版本。

Go1.27.3重建首版实际编译和扫描exit0，但运行`--version`/`components`在`SermoDigital/jose/crypto`的RegisterHash初始化panic。归档`collector-security-20261029.tar`、报告`collector-security-20261029.json`及manifest `sha256:e70fc02fd1fcd150ac17a1fe7ab6babdf93097f06a69cda286adcb885106b66b`仅是失败候选，0 HIGH不代表可用。已用go version读取官方原版确认Go1.26.10，并从go.dev当前稳定列表确认其仍为受支持1.26最新补丁；Dockerfile现固定官方镜像`sha256:23ce71337b726608afb1bc00986e630a05034a87a87ea1fc2cd1ae5cf33387eb`。不篡改crypto标准库、不删除旧组件，仍升级go-jose4.1.6。新候选标签`s2-security-go126-20261029`正在重建，尚未取得功能/扫描通过证据。

2026-10-29 CI权限补验：Docker默认Collector UID10001与Linux宿主私有0700输出目录所有者不匹配。脚本现于POSIX显式使用同一非root宿主UID/GID，拒绝root调用；Windows继续默认镜像身份。没有改宽目录权限或使用root Collector。真实Linux volume重现旧UID permission denied，新UID1001在0700目录持久化三层真实Span（exported3/failed0）；首次过早检查空输出文件失败保留，实际producer触发后才算通过。完整Collector过滤/暂停恢复再次通过，仓库外证据`cs-s2-collector-b8277f73969e-faubpwvj`；新CI仍待验证。

## 当前版本增量（优先于下述0.140.0历史记录）

官方Collector0.164.0固定摘要 `sha256:340c9ca6a36617084c6661bdc4f0fe92b7cda5a805d61673ec8db375fddc522b` 已由固定Trivy完整扫描exit0、0 HIGH/CRITICAL；报告为实验室 `s2-r2-output-20261028/collector-current-vulnerabilities.json`。新版实际校验现有配置、三层Span关联、隐私过滤、pause时3项导出失败及恢复后新trace落盘均通过，证据 `C:/Users/56226/AppData/Local/Temp/cs-s2-collector-7a31f74ef45e-yl2iwkfa/`。脚本现必须显式指定 `--app-image`，不再自动复用旧应用镜像。

已从该固定官方镜像提取原生二进制，SHA256 `57911a9d902cfe2c287dac09fcee4b054630b4ec248fa190a3115d860a32f81f`；通过一次性 `linux_lab_collector_upgrade.py` 在授权node2原位升级。脚本检查旧/新hash、拒绝已有backup/pending，先validate再停服务，保留旧二进制；启动失败自动恢复旧文件的负向测试通过。实际升级健康通过，保留 `/opt/cs-s2-otel/otelcol-contrib.before-0.164.0`、原配置、0700状态及历史记录；**不要重跑create-only安装或一次性升级**。

升级后新建合成客服对话 Run `68e8445b-d0af-4927-8a53-31b5f37d9dee`、Trace `cd1ba748571355563b1af6605d498c21`。定向root审计确认257 Span，HTTP/Worker/Agent/model/db齐全，Worker parent为API、request_id一致、恰1模型attempt、目录0700。为固定node2执行而临时停node1 Worker，已在finally恢复并确认active；未改生产模型门禁，也未调用真实在线模型。该轮为node2单侧HTTPS/SSE/重放复验，不冒充新一次跨guest故障切换。CI已加入固定镜像扫描和真实Collector演练步骤，尚无远端CI运行证据。

日期：2026-10-28。配置：`ops/otel/collector.yaml`；专用OS账户模板：`ops/systemd/customer-service-otel.service.template`；可重复验收：`scripts/verify_collector.py`。本轮是实际 Collector 运输/过滤/持久出口验证，不是 API/Worker 正式平台部署完成。

## 配置边界

- OTLP/HTTP仅监听127.0.0.1:4318，health仅127.0.0.1:13133，不启用公网/任意gRPC入口或debug exporter。接收体1MiB；memory_limiter192MiB、spike32MiB；batch最大256。
- 只接受已注册的固定Span名称；Collector对资源/Span属性再做白名单过滤，清空status.message，去掉事件。应用SafeTracing仍承担UUID/固定值/长度校验、最多16属性、零事件/links；Collector不是任意不可信OTLP内容的完整语义审计器，允许字段不能当作可发送原文的通道。
- 文件出口20MiB轮转、3份backup、backup max_days7。**max_days只管理轮转文件，不保证活动文件内每条Span在7天到期删除**。当前文件出口不替代正式Trace平台检索/严格留存和权限验收。
- 正式模板将状态目录设为专用用户、0700、UMask0077、受限文件系统/capabilities；模板未自动创建真实账户或批准版本。

## 实际运行

Collector Contrib0.140.0镜像来自官方仓库，摘要固定为 `sha256:ffc818ee108d0b934fd14207fd87ff247c9b64f344ed349d0b66c166c18a2312`。实际二进制 `validate` 成功；固定版本不是漏洞扫描批准。

测试Collector无Docker网络、无宿主端口；合成应用producer加入同一network namespace，只连loopback。应用运行时采用旧验证镜像依赖并只读挂载当前app源码及测试fixture，因此不冒称已重建最新应用镜像。没有公司文档、业务请求或模型API调用。

最终批次证据目录：`C:/Users/56226/AppData/Local/Temp/cs-s2-collector-10cdb93df758-cnsp9cr5`，保存实际Collector JSON及结果，不提交运行数据。验证：

1. 当前PrivateOtlpTracing发送http.request→agent.run→model.request；文件中恰3个Span、同trace_id，逐项确认parentSpanId及request_id/run_id/attempt_id。
2. 独立合成protobuf输入包含额外资源属性、db.statement、status.message、事件和未注册Span名称中的敏感哨兵。落盘只剩注册db.query；事件为空，文件无该哨兵。它不证明任意恶意允许字段值都会被Collector识别。
3. 真实docker pause冻结Collector而保留网络namespace。应用3个Span导出记录failed、exported=0；finally解除暂停。失败是**未获确认**，并非证明服务端永远没有接收/持久化；不把失败后重发当无重复保证。
4. 恢复后新的三层trace再次落盘，父子/ID断言通过；本批Collector精确清理，证据文件保留。没有全局prune或重启其他容器。

最终恢复前trace=`06fd2e623b613dbbe7e8553c46915565`，恢复后=`398ad11739eec485506ae483b830f7b4`。这些都是合成ID，不是生产请求。

## 尚未完成

### 后续 node2 原生服务/API→Worker 实际接线

从上述官方固定摘要镜像提取原生二进制，Windows提取件及guest安装前 SHA256均核对为 `3530d0bd121cc54b2390e00a1b11e2fdfd56c528584d8b4cbd2bfb7b082bcf5f`。未下载不明二进制。create-only `linux_lab_collector.py` 固定hostname/marker/测试库与空端口检查后，新建专用无登录OS用户 `cs-s2-otel`；二进制root所有、只读安装路径，状态目录 `/var/lib/cs-s2-otel` 为服务用户所有且0700。真实binary validate、systemd-analyze verify、Collector健康检查均通过。独立 `cs-lab-otel` 单元未enable开机启动，不声称无人值守部署完成。

既有API经独立ExecStart drop-in增加显式loopback OTLP endpoint；原HTTPS、数据库API角色、日志namespace与认证边界不变。既有合成Worker fixture增加可选OTLP参数，用相同生产ChatWorker及PrivateOtlpTracing执行，不改生产模型/密钥门禁。先停node1 Worker以确定本轮由node2消费，跑真实跨guest TLS/Cookie/CSRF、入队、SSE及重放。

run=`f933864e-900d-4fe3-867a-91d23e2708ca`，真实生成仅1助手。node2 root-only UUID定向审计文件确认trace=`3a42b7a49bde5f5033816c7afa3163c7`，包含http.request、worker.chat、agent.run、model.request、db.query，共257 Span；Worker parentSpanId精确对应API http.request，所有选定Span request_id一致，worker run_id匹配，model attempt_id是合法UUID、model.request恰1个，事件/links为空。审计只输出合成ID/类型/计数，不输出参数或正文。257是该合成执行样本，不是生产Span预算/性能已达标。

完成后node1 Worker已恢复active；node2 API/Worker/Collector保持运行以供后续S2工作。未接Trace查询平台，不把root读取文件等同普通平台用户权限验收。早先“仅直接合成scope”适用于前批Docker证据，本段补齐当前API/队列/独立Worker的实际运输；知识Worker、多机平台、采样/容量和严格留存仍需验证。

后续权限直接复查：状态目录700、owner/group均cs-s2-otel，非提权labops不能读取traces.json；三个服务active。部署/实验室相关测试24 passed，0.23秒；完整本地后端520 passed / 370 skipped，29.90秒。未配置的外部测试仍非通过。当前未创建S2完成提交。

相关OTLP/追踪/部署测试20 passed，2.11秒；部署配置额外核对与应用注册名称/属性集一致，防止漂移。完整本地后端518 passed / 370 skipped，27.22秒；跳过项仍未验收。实际Collector运行不依赖这些跳过项，其证据由上述独立脚本取得。

同机服务部署与API/独立Worker接入正式Collector、跨节点TLS/mTLS、Trace平台存储与查询/严格留存、采样/持续压力与内存拒绝演练、Collector自身指标告警和真实值班渠道仍需证据。Exporter有界队列和Collector限制不等于已经通过生产容量验收。S2不能据本轮单项勾选完成。
