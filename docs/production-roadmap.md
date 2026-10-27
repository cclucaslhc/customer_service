# 生产化分阶段实施清单

双guest故障增量：真实SIGKILL证明pre-dispatch失联仅一次安全接管、model-attempt开始后失联不盲重放，旧租约迟到写入均拒绝；真实数据库时钟、未缩短租约，两侧服务已恢复并重验HTTPS对话。无服务全量485 passed/369 skipped；仍非真实供应商网络/自动LB/PG Redis HA结论，见[故障证据](two-node-runtime-acceptance.md)。

双VM运行补验：两guest已部署严格角色API、私有HTTPS代理及合成ChatWorker，共享verify-full TLS PostgreSQL；跨guest Cookie/CSRF、入队/SSE/重放及单条助手消息通过，单API停止后的存活入口和恢复通过。实测修复了旧local-only误拒绝代理客户端IP的问题，默认本地模式与鉴权不放宽。尚非自动LB或PG/Redis HA，详见[当前运行证据](two-node-runtime-acceptance.md)。

2026-10-26双guest环境补齐：用户授权创建的两台本机Hyper-V Ubuntu测试VM已SSH/NoCloud/密码禁用/双向连通验收，解除先前权限和环境阻塞；两台均2核4GiB/32GiB差分盘，未开放公网。应用部署与双节点故障演练尚未完成，同宿主guest不替代物理宿主容灾。见[VM环境](hyperv-test-lab.md)。

2026-10-26审批入口：owner发布review CLI已补approve/reject/check/execute，隐藏TTY凭据、禁止回显降级及旧回执门禁；真实PG审批组与入口单元32 passed，最终无服务466 passed/369 skipped。修复两项并发测试观察器缓存快照，不改授权锁。生产审核API/UI、可信质量来源及实际人工终端部署仍缺；见[控制入口](rag-control-cli.md)。

当前简版状态见[当前交付边界](production-current-status.md)。2026-10-26跨模块真实PG/Redis集成组210 passed/0 skipped/1显式本地模型deselected（508.10秒）；前端40 passed+build。仍非生产部署/真实模型或独立业务验收，不勾选S2–S6整体。

2026-10-26控制入口增量：owner显式灰度控制CLI已实现，独立进程真实PG证明推进/旧回执拒绝/暂停，阶段组6 passed，单元12 passed；本地全量458 passed/368 skipped。审批创建/双人交互与独立可信质量来源、生产UI和部署仍未完成，不将CLI算成全部管理流程验收。见[控制入口](rag-control-cli.md)。

2026-10-26恢复补验：head0037包含有效审批、running灰度、candidate run快照的加密pg_dump/pg_restore真实场景通过；恢复hold、审批失效、灰度归零及证据保留有断言。与既有逻辑备份组19 passed/0 skipped。下方“新schema完整加密恢复未验”为本次之前的限制；本次不包含外部对象存储/PITR/跨主机密钥恢复或正式RPO/RTO。见[验收记录](production-verification.md)。

2026-10-26 OCI补验：head0037同宿主缓存允许/显式no-cache独立构建逐blob与manifest一致，规范manifest `sha256:20d167f6472bd52568eba86af1d8c092b3de6cad3588dc49d0ea8d03bff175e9`；该OCI独立漏洞扫描0 HIGH/CRITICAL、SBOM14 Debian＋143 Python。下方“新镜像未无缓存比对”为本次补验前的历史状态；跨宿主、签名/发布、托管CI及正式部署仍缺，不勾选S2。

2026-10-26制品补验：当前head0037新镜像已重建，非root/只读/无网络导入、镜像内PG迁移及知识CLI、RAG审计/CAS和资源故障注入通过；更新漏洞库后HIGH/CRITICAL=0，SBOM14 Debian＋143 Python。新镜像尚未无缓存OCI重建比对/签名/托管CI或完整新灰度镜像内验收，不能上线；详细摘要见[验收记录](production-verification.md)。

2026-10-26回退增量：已实现completed rollout的owner回退候选准备，新UUID、旧manifest复验、同事务审计；通过新release及新双人审批的真实PG切回/旧在途答案拒绝，专项5 passed。该内部路径不等于可信管理入口、自动紧急回切或独立业务质量验收；见[回退边界](rag-reversion.md)。

2026-10-26当前增量：S6健康判定、原子异常暂停及显式单次/有界循环监控CLI已实现，真实PG健康专项9 passed（含独立进程）；本地完整后端445 passed/361 skipped。Linux监督模板已提供且静态契约通过，但未安装，告警handler/值班渠道、进程故障接管仍须真实Linux验收。下方各日期段为历史记录，不代表当前仍缺已实现的内部方法。剩余继续包括可信审批控制流程、成功发布后回退、完整恢复/新制品、真实业务质量/模型费用负载和双机验收；S2–S6整体不勾选完成。

2026-10-26：S6一步advance及最终提升内部事务已实现，扩大组15 passed，本地426 passed/352 skipped；仍非完整上线流程。下一关键项是可信控制入口/自动采样调度与不健康自动暂停、成功提升后受控回退；之后补新制品/恢复、真实模型质量费用负载及双机验收。见[推进边界](rag-rollout-advance.md)，总目标不勾选完成。

2026-10-26 head0037：数据库阶段时钟与候选操作观察已实现（专项39 passed，本地426 passed/347 skipped），只观察不切流。下一步将可信窗口重采样、审批/索引复查、阶段推进/最终提升放入同一事务，并补自动失败动作与控制入口；新镜像/完整恢复/真实业务质量/模型费用负载及双机验收仍缺，不勾选总目标完成。见[操作观察](rag-observation.md)。

2026-10-26：S6默认向量Worker的候选检索/暂停在途答案拒绝已补真实浏览器/SSE两轮及刷新验收，并修复耐久完成正文遗漏。真实PG25 passed、本地401 passed/343 skipped、前端40 passed+build；浏览器成功轮1 passed（非性能证据）。下一关键项是可信观察窗口与阶段推进/最终提升、正式审批/控制入口；仍需自动回切、完整恢复、新制品和真实模型/质量/负载/双机验收，整体不勾选完成。详见[证据](rag-browser-acceptance.md)。

2026-10-25 head0036：候选检索授权、默认向量Worker接线、实际模型/Prompt绑定与暂停在途答案拒绝已通过合成真实PG链路。最终专项25 passed，本地全量401 passed/341 skipped；跳过不算通过。剩余关键项为浏览器/SSE验收、真实审批/控制入口、可信观察窗口与阶段推进/最终提升、完整恢复/新制品、真实模型质量/负载及双机/业务验收，整体S6仍未完成。

2026-10-25 head0035身份锁提交防护已实现，真实PG扩大组39 passed/1本地模型deselected，本地后端401 passed/335 skipped；并发撤权两种顺序有真实锁等待证明。剩余仍为候选检索授权、默认Worker/Orchestrator连接和SSE验收、阶段观察/推进/最终提升及完整恢复/双机/业务验收。未构建匹配新head镜像，不能上线。

2026-10-25 head0034：run快照与仓库提交防护已实现（真实PG扩大组33 passed/1本地模型deselected），但默认Worker未接入。后续仍须受限候选检索、审批并发撤销锁、Worker/Orchestrator接线与SSE端到端、阶段观察/推进/最终提升和恢复/双机验收；不因仓库测试通过而勾选实际灰度。

2026-10-25 head0033受限路由投影已实现，真实PG4项通过（含候选/基线及最小权限），但未被Worker消费。下一关键链是run路由快照、受限候选SQL、提交锁内复查、观察窗口/推进/最终提升、实际暂停回切与端到端验收；控制表权限不放开，新制品尚未构建。投影函数不是答案提交许可，整体S6不勾选。

2026-10-25 灰度控制状态新增start/pause/rollback（owner内部、无运行时路由），专项28 passed（18单元+10真实PG）。剩余关键链仍是分桶/run快照、受限候选SQL、提交事务复查、窗口指标/阶段推进/最终提升及暂停/回切的端到端效果；不将控制表状态变化算作实际灰度验收。

2026-10-25 灰度计划审批绑定已实现：计划随release冻结、禁止绕过阶段一次性execute、双人有效审批后owner创建零流量staged记录；专项44 passed（18单元+26真实PG），本地全量399 passed/321 skipped。剩余仍含真实审批入口、分桶路由/run快照、候选SQL授权、提交复查、阶段观察推进、暂停/回切及生产验收，不计灰度完成。

2026-10-25 最新head0032：完成灰度状态表及权限/封存/恢复归零边界；真实PG扩大组35 passed/1 skipped，静态18 passed，后端381 passed/318 skipped。仍需灰度计划审批绑定、会话分桶、受限候选SQL、run快照、提交版本复查、阶段指标、暂停/回切、新镜像及完整恢复。此存储批次不计S6灰度完成，详见[灰度状态](rag-rollouts.md)。

2026-10-25 S6 续进展（覆盖下方旧记录）：已新增 owner 内部审批服务与一次性事务发布，含撤权复查和审计失败整体回滚。真实 PG 58项合并专项通过，最终代码17项复测通过，另5项并发撤权通过；各组重叠不累加。仍需受信证据来源、真实审批入口、批次灰度/暂停/回切、完整恢复和新镜像验收；整体 S6 仍未完成。最新无外部服务后端381 passed/311 skipped，跳过不算通过。见 [当前服务边界](rag-release-approvals.md)。

2026-10-25 S6 审批存储基础：head 0031，新表定义冻结、同租户引用、版本递增、角色隔离与恢复后重审已实现/测试。它不是审批服务；身份/证据真实性核验、审批入口、原子消费/发布、批次及回切仍未实现。PG 扩大组 35 passed/1 real-local-model deselected，最终小组 5 passed，后端全量381 passed/292 skipped。旧制品未包含此批，不以先前扫描作为0031制品证明。见[边界](rag-release-approvals.md)和[证据](production-verification.md)，整体S6不勾选。

2026-10-25 S2/S3 封存边界修复：补齐五张 RAG/容量表的显式 SHARE 锁清单，当前 43 业务表与触发器清单一致，增加防新表遗漏的静态回归。真实隔离 PG 在绕过触发器的测试事务中仍证明表锁拒绝写入；局部组 2 passed，静态组 1 passed；无服务全量 380 passed/289 skipped。未重跑完整备份恢复或托管 CI，未据此勾选阶段。耐久审批/灰度/自动回切仍待实现，见[证据](production-verification.md)。

2026-10-25 S6 门槛比较基础组件：新增显式策略/冻结上下文/生成事实审阅计数的纯计算比较器，拒绝检索评分冒充生成质量或 approved 布尔旗标。与既有盲测组 43 项通过；它不验证真实审阅身份/业务批准，不改变现有 CLI，也不授权上线。后续仍须受信证据核验、耐久审批与批次/自动回切控制面，本组件不可作为完整灰度门禁；S6 不勾选。

2026-10-25 S2/S6 新制品补验：包含发布审计/CAS 的完整镜像 3d0880... 已在无 app 源码挂载下通过真实 PG/知识 CLI 及 RAG 审计回滚、CLI 激活/回切，扫描为 0 HIGH/0 CRITICAL、SBOM 14 Debian+143 Python。补齐下方源码-only 制品缺口，但 RAG 批次审批/质量门槛/自动回切和实际灰度仍未实现，托管 CI/签名/双机等尚未验收。详见[记录](production-verification.md)。

2026-10-25 S6 发布审计/CAS 增量：索引生命周期/非空 prune 的状态与运维审计同事务，库级激活必须显式给出旧 active 预期值，并发只允许一个 CAS 成功。真实 PG 完整文件+新审计组 32 passed/1 real-local-model deselected；无服务全量 341 passed/288 skipped。还未实现批次审批、质量门槛、自动回切和真实灰度；本批代码尚未进入上一批已扫描镜像，不据旧制品证明本批，整体 S6 不勾选。见[证据](production-verification.md)。

2026-10-25 S3 提交临界点增量：真实镜像/PG 的 OOM、提交前 KILL、DBAPI commit 返回后 KILL 联合 3 passed/0 skipped。提交前文档/item/计数原子回滚，提交后整体保留且重提交跳过、不重复；均不重放旧任务，保留容量到确认停止后释放。文件 orphan/GC、对象未知回执、PG 断电和双机分区仍不在本批证明范围，整体 S3 未完成。CI 步骤已同步，托管未运行，见[证据](production-verification.md)。

2026-10-25 S3 Worker OOM 增量：真实容器在 Worker 领取任务/进入解析后 OOM/137，验证无发布、容量保留、失租后不重放、确认停止后 owner 释放，以及正常 CLI 完成新任务且容量归零；专项 1 passed/0 skipped。CI 新增真实零跳过作业步骤，托管尚未运行。尚非所有提交瞬间/宿主 OOM 或双机故障，整体 S3 仍未完成。见[证据](production-verification.md)。

2026-10-25 S3 物理限额本机增量：最终运行镜像完成真实 cgroup OOM/137、PID fork 拒绝、tmpfs ENOSPC 与 CPU 节流；验证器安全/清理单测 6 项通过，CI 已增加显式步骤。这里只证明本机容器限额生效，不证明 Worker OOM 后业务恢复/生产容量或双机故障。双 Linux VM 当前需管理员权限（已请求确认），未自行提权，整体 S3 不勾选。见[解析部署边界](parser-isolation.md)及[证据](production-verification.md)。

2026-10-25 S2/S3 完整制品补验：已构建含解析沙箱的最终镜像，不挂载 app 代码复验沙箱/六格式和真实 PostgreSQL 迁移→独立 Worker CLI 上传/重解析/取消及容量归零。首次新漏洞库扫描发现 pypdf 3 HIGH，升级锁定版本到 6.21.2 后重建复扫为 0 HIGH/0 CRITICAL；SBOM 14 Debian+143 Python。新镜像 ID、测试范围和局限见[验收记录](production-verification.md)。托管 CI/签名/当前制品跨宿主与重建、双机/生产角色等仍未验收，不勾选完整 S2/S3。

2026-10-25 S3 解析隔离增量：Linux x86_64 子进程默认 Landlock ABI ≥ 5 + seccomp，CLI 领取前合成自检，不支持的节点拒绝启动。当前代码在 distroless 运行环境通过越界文件/socket/IPC/进程组/继承测试和六类格式；修复 PDF 第三方输出污染 JSON。无外部服务全量 335 passed/282 skipped，真实 PostgreSQL 知识组 23 passed/1 backup deselected；最终 Linux deny-list 收紧后专项重验通过。新制品重建、目标内核安全/物理资源与双机验收、额外格式仍缺，整体 S3 未完成。见[解析隔离](parser-isolation.md)与[证据](production-verification.md)。

2026-10-25 S2/S3 代理增量：真实 Linux NGINX 语法、HTTPS 信任、转发头覆盖、SSE 无缓冲、202/413 合成协议专项 **3 passed / 0 skipped**；新增固定 digest CI 零跳过作业（托管尚未运行）。替代下方“无 Linux NGINX 运行验证”的历史限制，但未替代真实应用浏览器 HTTPS/Cookie、双代理/VIP 或跨主机故障验收，整体阶段仍未完成。

2026-10-25 S3 取消增量：durable 知识导入新增管理员取消 API/UI，持久化 cancelled 与审计，取消后禁止发布但保留已提交文档；解析 Worker 停止后才释放容量，失联占用须 owner 明确核对。HTTP/跨租户/customer 拒绝、真实子进程停止、abandoned 释放及刷新终态有证据，见[验收记录](production-verification.md)。下方“主动取消未实现”仅为历史状态；跨主机 HA、完整沙箱与生产验收仍未完成，不勾选整体 S3。

2026-10-25 S3 当前增量：head `0030_knowledge_actions`，durable 上传与重解析已统一入容量队列；重解析固定已发布原文/目标版本，旧 Worker 领取新动作被拒绝，入队审计同事务，待处理本地输入可随备份恢复。最新测试分组见[验收记录](production-verification.md)，不合计为全量。下方“HTTP 未入队”仅为历史状态。

剩余优先级不因本批通过而缩减：P0 为 S2 制品托管 CI/签名与实际 Linux 部署、S3 双机及数据库/Redis 故障和恢复演练、解析物理隔离；P1 为 S4 权威业务沙箱/所有权与幂等联调、S5 已授权真实供应商/逐节点本地模型及费用故障验收、S6 独立业务盲测与灰度/回滚。合成接口、同机进程和自动生成问题不能替代这些验收，完整 S2–S6 仍未完成。

2026-10-24 S3 共享容量增量：当前 head 为 `0029_knowledge_admission`，queued 知识导入的全局/单租户容量在数据库领取事务中约束，失联占用保留至 Worker 停止确认或 owner 人工核对释放。新迁移拒绝运行任务未排空的升级，旧客户端无预留领取被拒绝；配置/人工释放同事务审计，运行角色只读策略。Linux 联合 27 项、PostgreSQL 主集成 29 项、专用角色 1 项通过；不能相加作为全量数。HTTP 直接上传/重解析尚未统一入队，跨主机故障及物理资源限额仍缺，完整 S3 不勾选。见[容量操作](knowledge-capacity-operations.md)。

2026-10-24 后续 S2 补验：现行 Dockerfile（含四个复制库的 dpkg 元数据）缓存/无缓存 OCI 逐 blob 与 manifest 校验通过，摘要 `sha256:5ae40fb765593309a698dd29b6df7035b42c908193957d50024a3fbcba0c6aab`。下段“元数据变更后尚未重验”属于本次补验前状态；跨宿主重建和托管 CI 仍缺证据。

2026-10-24 S2 制品最新状态：现行 `Dockerfile` 已从 Python slim 运行基底切到固定摘要的官方 distroless Debian 13 `base:nonroot`，只携带实际需要的 Python 3.12 解释器、标准库、依赖与四个原生动态库。`linux/amd64` 完整镜像本机非 root/只读/无网络导入、OCR、隔离 PostgreSQL 迁移及 MinIO 版本化读写通过。最初候选的 OCI 缓存/无缓存重建 manifest 相同、严格 Trivy HIGH/CRITICAL 为 **0/0**；随后为四个复制库补齐 Debian 包元数据，现行镜像 SBOM 校验为 **14 Debian + 143 Python**，再次扫描仍为 **0/0**。元数据变更后的 OCI 重建一致性尚未重验。旧候选在相同工具和漏洞库下仍为 26 HIGH，故该差异不是扫描失效。CI 的 distroless 运行用户/无 shell 烟测已同步修改，但**托管 CI 尚未实际运行**，跨宿主重建、制品签名/发布、双机部署及正式环境验收仍缺；S2 整体不勾选。下方 44 HIGH 等均为旧制品历史批次。见[验收记录](production-verification.md)。

2026-10-22 S2 最新制品候选：生产镜像使用锁定 headless OpenCV 代替带界面 OpenCV，去除 OpenGL/Mesa/LLVM/libxml2 图形依赖，真实 OCR 空白图调用通过；同宿主缓存／无缓存 OCI 摘要一致。固定 Trivy 对完整镜像实扫由 54 HIGH＋1 CRITICAL 降为 **44 HIGH、0 CRITICAL**，严格扫描仍失败，S2 制品项未通过；见[验收记录](production-verification.md)。

状态：实施中，全部阶段尚未验收完成。初稿日期：2026-10-07；最近更新：2026-10-24。复选框只在代码、自动化测试与运行证据齐备后勾选。当前证据见 [实施与验收记录](production-verification.md)。本文不授权上线、生产数据访问或外部业务操作。

2026-10-22 S2 旧制品批次：按推荐默认方案迁移 Python 构建/运行基底至固定 digest 的 Debian Trixie；本机完整 `linux/amd64` 构建及无网络、只读、非 root 导入烟测通过。当时固定 Trivy 0.66.0 扫描有 **55 条 HIGH/CRITICAL（含 1 条 CRITICAL）**，其后 headless 候选的 44 条结果见上文。严格 CI 门槛保持失败，不勾选 S2。托管 CI、制品签名、双机部署和正式漏洞处置仍缺；详情见[验收记录](production-verification.md)。

同日独立重建核验：修正构建时 `/etc/shadow` 日期差异后，缓存/无缓存镜像的检查目录文件内容除运行时 hostname 外一致；但镜像摘要仍不一致，固定导出时间戳实验也未消除差异。S2“可重建制品”尚无字节级通过证据，不以内容抽查替代验收。

后续 S2 增量：定位并清除 apt/dpkg 日志与 `ldconfig` 缓存的四项构建期内容差异后，本机固定时间戳 OCI 导出在缓存／无缓存双构建下得到**相同 manifest 摘要**；CI 已加入同一 OCI 制品的摘要相等、SBOM 和严格漏洞扫描门槛。上段摘要不一致是修复前的历史批次。托管 CI 与跨主机重建尚未运行，严格漏洞扫描对最新 headless 候选仍失败，S2 不勾选。

2026-10-22 S3 增量：同宿主两个独立聊天 Worker 对同一 PostgreSQL 队列竞争，真实进程专项和完整耐久聊天组均零跳过通过；单 Run 只有一个 attempt、助手消息和终态。目标双 Linux 主机、Redis/PG HA 与分区/切主演练未提供，不把进程级测试当作 S3 完成。

同日 Redis 进程级故障注入：隔离固定 digest Redis 容器停机时新准入与旧许可续期失败关闭；重启后旧许可不复活，新许可正常。真实 PG/Redis 专项 5 项零跳过通过。尚非 Sentinel/Cluster 切主或跨宿主故障，S3 不勾选。

2026-10-18 当前状态：逐主机回环本地模型、OpenAI 逐租户外发授权和 0024 备用模型预算合同已有实现；默认在线跨供应商外发仍禁止。认证 PostgreSQL 聊天的请求级切换已接入，在备用 attempt 出站前复核授权、租约与预算。0025 又加入 PostgreSQL 跨实例共享熔断、代际 fenced half-open 探针和私有聚合指标；真实 PostgreSQL/Prometheus 联合 **79 passed / 0 skipped**，专用角色测试另 **1 passed / 0 skipped**；隔离 Alertmanager 告警送达也已验证。正式值班接收人、真实供应商合同/费用、两台 Linux 主机与 PG 切主/分区故障验收仍待完成，不勾选 S2/S5。下方较早的“仅设计/尚未实现”描述是对应日期的历史批次状态。

正式目标已由用户选定为**自管 Linux 双实例＋反向代理**。双实例指应用/API/Worker 的目标形态；PostgreSQL/Redis 的主备、fencing、故障切换和反向代理自身高可用仍须单独设计与验收。不得把本机双进程、单 Redis 或单代理冒充正式双机 HA。

本地模型接口已选定为**每台主机的回环 OpenAI 兼容服务**。S5 实现需区分逻辑配置与每节点实际端点、禁止任意 URL/跨主机回环误用，并分别测试两节点能力和故障注入；当前仅是设计决策，尚未实现或验收。

已提供待填充的同机 NGINX HTTPS/SSE 代理样板，覆盖 202 回执后的事件流路径；尚无 Linux 语法检查、证书/域名、双代理/VIP 或浏览器 HTTPS 现场验收，不能据此勾选 S2/S3。见[部署 Runbook](deployment-dr-runbook.md#制品与环境准备)。

已增加 `scripts/serve_api.py` 的显式 HTTPS/本地可信代理/API 角色装配入口，并通过 4 项合成安全边界测试；两个 Worker 随后增加私有数据库 URL 文件入口，聊天 Worker 增加私有 Redis URL 文件入口及远程明文 Redis 拒绝。当前无服务全量 **242 passed / 218 skipped**。真实 Linux 代理、凭据供应/ACL/TLS、私有指标采集和双机运行仍待验收，不能单独勾选 S2/S3。证据见[验收记录](production-verification.md#2026-10-15-s2s3-增量自管-linux-api-显式装配入口)。

CI 已增加隔离 PostgreSQL/Redis 专项及零跳过门控，尚未在 GitHub 托管运行器执行。本机新建回环 PostgreSQL 16.10 对相关三组测试验证 **37 passed / 0 skipped**，测试库残留 0 且实例已停止；不替代 Redis 或正式双机验收。见[验收记录](production-verification.md#2026-10-15-s2s3-增量隔离-postgresql-与-ci-集成门控)。

2026-10-13 验收更新：上一后端全量隔离测试 **429 passed / 1 skipped**，唯一跳过的专用 PostgreSQL 角色用例随后在其要求的独立实例上 **1 passed / 0 skipped**；其后 S2 OTLP/数据库追踪、真实隔离 S3/对象追踪、知识 Worker 追踪入口分别有专项证据，PostgreSQL 迁移及 API→独立知识 Worker OTLP 联合回归 **56 passed / 0 skipped**，有界 HTTP 日志/Prometheus 告警专项 **32 passed / 1 skipped**，尚未重跑全量。各批专项可能重叠，不能相加为新的全量通过数。真实 Redis、S3、Prometheus 联动已运行；此前各段“Docker 不可用”“真实 Redis/S3 未运行”的文字是保留的历史批次限制，不是当前测试缺口。测试通过不等于生产部署、HA、安全或灾备验收；详情见[实施与验收记录](production-verification.md)。

2026-10-14 增量：真实隔离 PostgreSQL + SeaweedFS + 独立知识 Worker + 回环 OTLP 的对象 `get/put/check_ready` 子 Span 跨进程专项最终 **35 passed / 0 skipped**。测试根 Span 为 API 侧合成上下文；真实认证 HTTP API 入队关联由前一批 **56/0** 单独证明。两批不相加；首次 protobuf ID 类型断言错误和修复见[验收记录](production-verification.md#2026-10-14-最新证据与历史限制勘误)。生产 Collector/IAM/TLS、告警送达、完整后端回归仍未完成。

2026-10-14 CI 增量：已新增无密钥、无部署的 push/PR 基础工作流；本机无服务配置后端 **234 passed / 218 skipped**，前端 **39 passed** 且构建成功。托管运行器尚未实际执行，Python 锁依赖、隔离服务门控、扫描与可重建镜像均未完成，不能勾选 S2 制品项。详情见[验收记录](production-verification.md#2026-10-14-s2-增量无服务-ci-基线)。

2026-10-14 锁依赖增量：已由 `uv 0.13.0` 生成带来源/哈希的 `uv.lock`，CI 改为锁一致性检查与锁定安装；新的本机 Python 3.10 环境安装和无服务回归 **234 passed / 218 skipped**。对锁定 Python 包的 `pip-audit 2.10.1` 本机扫描未发现已知漏洞，CI 已加入相同门控。上段“Python 锁依赖未完成”是 CI 初始批次限制。Linux 托管 CI、真实集成跳过门控、前端/系统扫描、SBOM/签名和可重建镜像仍缺，S2 制品项不勾选。见[验收记录](production-verification.md#2026-10-14-s2-增量python-跨平台锁依赖)。

2026-10-15 前端审计增量：已精确升级 `source-map-js` 和 `vitest` 的锁定版本，完整 npm 审计 **0 vulnerabilities**，39 项前端测试与构建通过；CI 已加入完整 npm 审计门控。上段“前端扫描仍缺”是此前批次限制。随后增加固定基础镜像 digest、非 root 的 Linux 多阶段 Dockerfile、无网络只读运行检查、镜像高危/严重漏洞扫描和 CycloneDX SBOM CI 作业；本机 Docker 引擎不可连接，镜像尚未实际构建、扫描或生成 SBOM，托管 CI、签名、独立重建比对仍缺，S2 制品项不勾选。见[验收记录](production-verification.md#2026-10-15-s2-增量linux-构建制品入口)。

## 分阶段安排

| 阶段 | 依赖 | 主要交付物 | 完成证据 |
| --- | --- | --- | --- |
| S0 边界与基线 | 无 | ADR、身份/租户方案、容量和数据政策、冻结评测集 | 审阅后的需求与可量化门槛 |
| S1 数据底座 | S0 | Repository、PostgreSQL、Alembic、导入器、对象存储、备份 | 空库/旧库升级与隔离恢复报告 |
| S2 观测与制品 | S1；日志可提前 | OTel、仪表盘、告警、费用账本、容器和 CI | 故障可追踪，制品可重建 |
| S3 运行协调 | S1、S2、身份隔离 | Redis、Worker、租约、取消、SSE 重连 | 双实例故障与重放测试 |
| S4 工具安全 | S0、S1、S2，分布式验收依赖 S3 | 可信身份、审批、幂等、审计、Outbox、业务适配器 | 越权/重复/未知结果/并发测试 |
| S5 模型路由 | S2；共享熔断依赖 S3 | 能力注册、错误分类、重试/熔断/降级 | 故障注入与费用核对 |
| S6 混合 RAG | S1、S2、身份隔离 | pgvector、Embedding、Reranker、证据门控 | 固定集和独立盲测对比 |
| S7 发布与灾备验收 | S1–S6 | 发布回滚流水线、值班流程、演练报告 | 按 Runbook 恢复并达到 SLO/RPO/RTO |

S2 的日志与构建、S4 的接口规范、S6 的离线模型选型可以提前开展；数据库切换、多实例开关、写工具开放和索引切流必须按依赖顺序执行。Kubernetes、Qdrant、Temporal 不是首批必选组件。

## 下一批可执行任务与优先级

优先级表示**推进顺序**，不是当前完成度：P0 是先决条件/先做项或最终发布门槛，P1 是依赖明确后实施的必要能力；P1 未完成同样阻断本计划的生产发布。P2 仅在容量证据驱动下评估。S7 虽为 P0 发布门槛，执行顺序仍在 S1–S6 之后。每项仅在可复现证据形成后勾选下方原清单；现有隔离测试不自动改变勾选状态。

| 阶段 | 优先级 | 下一项可执行交付 | 验收条件 / 依赖 |
| --- | --- | --- | --- |
| S0 生产边界 | P0，首先 | 产品、业务、安全、运维共同冻结容量/预算、租户归属与保留期、SLO/RPO/RTO、部署与备份区域、值班责任人；批准脱敏盲测集与业务沙箱范围 | 形成有数值、负责人和批准记录的基线；缺外部输入时只继续隔离开发，不自行宣布上线标准 |
| S1 数据与备份 | P0，S0 后 | 实现可信租户上下文、全表 RLS 与直接 SQL 攻击测试；完成旧 SQLite/对象正式迁移、密钥包装转换、停写切换及切换前回退；接入 PITR、异地加密备份和恢复验证 | 跨租户直读在数据库层拒绝；批准环境中空库/旧库迁移及恢复可重现，TLS/IAM/KMS/四角色/连接预算与实测 RPO/RTO 通过 |
| S2 观测与制品 | P0，可与 S1 并行做不依赖生产数据部分 | 将现有可选 Trace/有界日志接入正式 API 与 Worker 配置；部署私有 Collector、Prometheus/Grafana/Alertmanager，定义日志留存/访问控制并演练触发与恢复送达；在已有 Python/前端锁及包扫描基础上补真实隔离服务门控、系统/镜像扫描及非 root 可重建镜像 | 从 API→两类 Worker 的故障可关联 Trace、脱敏日志与指标；告警由真实接收人确认；CI 不静默跳过必需集成测试，同一提交可重建制品，采集故障有已验证降级 |
| S2 费用 | P0，S0 合同后 | 覆盖连接测试及 Embedding/Rerank 等计费路径，核对供应商 usage/价格合同，补未知金额对账和受控预留释放 | 每次收费尝试可关联 Run/实际模型，抽样账单差异可解释且未知费用不按零计算，并发预算不越额 |
| S3 多实例运行 | P1，依赖 S1/S2 | 实现有界重试/死信、取消通知和解析资源预算；部署双 API/Worker、PG/Redis HA，执行切主、网络分区、旧 Worker 迟到和 SSE 重连故障注入 | 无重复终态/越额/丢取消；协调服务失效时安全阻断或按已验证策略恢复，故障演练可复现 |
| S4 业务工具 | P1，依赖 S0/S1/S2；分布式验收依赖 S3 | 先接一项只读沙箱业务适配器及资源级鉴权；写工具另做审批绑定、operation_intent、幂等/unknown 对账、Outbox 和独立审计 | 外部成功本地崩溃、重复审批、并发及撤权测试不产生重复写入；无外部幂等/查询能力时不自动重试 |
| S5 模型路由 | P1，依赖 S2；共享熔断依赖 S3 | 建立跨模型 attempt/费用合同、能力和数据政策筛选；按固定总时限/预算实现有界重试、共享熔断及已由管理员配置的备用/本地端点切换 | 429/5xx/超时/鉴权/取消故障注入记录实际模型及每次费用；流式 reset 正确，未知写操作不重放，无备用时安全失败 |
| S6 混合 RAG | P1，依赖 S0 脱敏集、S1/S2 | 建版本化 Embedding/index 与 pgvector，做权限内词法+向量召回、RRF/Reranker/证据门控，再 shadow/灰度切流 | 固定集消融和至少 200 条独立脱敏盲测达到冻结的引用、拒答、泄漏、P95/成本门槛；可回切词法索引 |
| S7 发布与灾备 | P0 发布门槛，最后 | 建单次 migration job、蓝绿/滚动发布、摘流排空和回滚；联合演练 PG PITR、对象、密钥、权限撤销、模型与业务对账，移交值班 | 在目标部署环境实测并签收 S0 的 SLO/RPO/RTO；告警送达、回滚与故障切换有报告，整改复测前不发布 |

P2 暂不预设组件：只有容量/延迟/可靠性实测证明现有方案不达标时，才评估 Qdrant、Temporal、Kubernetes 等替换或扩展。

## S0：明确生产边界

- [ ] 冻结预期在线用户、峰值并发、RPM/TPM、文件/切块规模、保留期与预算。
- [ ] 定义用户/客服/管理员角色、资源归属和租户数据政策；旧会话不得自动向新注册用户公开。
- [ ] 确定部署区域、备份区域、通知渠道、业务系统测试沙箱和负责人。
- [ ] 定义 SLO、RPO、RTO 和写操作重复执行的禁止条件，记录签字确认值。
- [ ] 冻结脱敏问题集与训练/调参/盲测划分；标注缺失事实、型号、权限与跨文档问题。

## S1：数据、迁移与备份

RLS前置最小权限增量：Worker 对 `identity_users` 只读取会话校验必需的 `id/active/auth_version`，数据库拒绝 `password_hash` 直读且启动门控拒绝重新授予该列；真实隔离PG验证原有会话解析仍可用。其余跨租户业务表仍未启用可信RLS，S1/租户隔离项不勾选。

RLS 表清单门控：当前 40 表按 30 张租户表、10 张全局表显式分类，并锁定 `identity_events` 的可空租户例外；新增表必须先审阅访问模型。可信签发/上下文、API 会话铸造权限收紧、Worker 受控跨租户领取和全部真实直接 SQL 攻击验收仍待实施，见 [RLS 实施契约](postgresql-rls.md)。清单测试通过不勾选数据库隔离。

2026-10-10角色增量：增加独立迁移所有者、API、Worker 与备份只读四角色的事务化授权脚本及真实隔离PG验收，包含角色混用/预存危险权限拒绝、默认新表授权、实际API身份配置写入、Worker配置写入拒绝、数据pg_dump和撤权。生产账号供应、TLS/RLS、连接预算和现场恢复尚未验收，S1角色/备份项仍不勾选；见[角色操作](postgresql-roles.md)。

角色后续增量：Repository的API/Worker启动路径分别校验实际API/Worker角色，远程数据库默认强制且不可关闭，回环代理显式开启；真实隔离API和两个独立Worker证明双方身份不可互用，超级用户、迁移/备份角色拒绝，权限漂移后重启拒绝。Worker仍需跨租户领取，现有权限分离不是RLS。未覆盖持续权限监控、可信租户上下文或生产连接，S1仍不勾选。

2026-10-10连接保护增量：远程PostgreSQL连接现要求显式`sslmode=verify-full`，拒绝libpq默认弱校验模式；回环隔离测试保留。仅做URL前置校验，尚未验证实际生产证书、应用/迁移角色、连接预算及TLS终止路径，不勾选S1角色/连接项。

- [ ] SQLAlchemy/Psycopg Repository 替换直接 sqlite3 访问；事务不跨模型网络请求。
- [ ] Alembic 单一 head、迁移角色、生产应用角色、连接预算、UTC 与 JSONB 规则。
- [ ] SQLite 导入器支持 dry-run、批次记录、幂等导入、行数/外键/摘要校验。
- [ ] 全部现有表迁移，FTS5 重建；原文/图片复制对象存储并核对 manifest。
- [ ] provider 密文保留正确 nonce/AAD，演练密钥包装转换；不输出或提交敏感内容。
- [ ] 停写切换与切换前回退演练；切换后写入不回退至旧 SQLite 快照。
- [ ] pgBackRest 或托管 PITR、异地加密备份、定时逻辑备份和自动恢复验证。

对象存储当前增量：私有 S3 版本对象组件及知识原文/图片发布、跨实例重解析/鉴权图片 HTTP 已接通，通过真实 PG + S3 专项。引用在现有 metadata 中随文档/片段/资产同事务发布，失去确切版本失败关闭；失败/不明提交不盲删对象。组件与测试服务限制见 [对象存储操作边界](object-storage-operations.md)。仍不勾选 S1/S3：排队输入还依赖共享源文件、CLI/安全配置供应、迁移/GC/对象备份和自动调度尚未完成；当前逻辑备份显式拒绝对象引用，不能算完整备份恢复。

S1 当前增量：加密 pg_dump 与业务/文件 manifest、同一 exported snapshot、空库/新文件根恢复、全表/序列/原文/图片校验、来源路径重定位已有隔离实现。需要显式 offline maintenance，当前限 loopback；不自动调度/启用服务，不提供 WAL/PITR、对象桶/KMS/异地保护、生产角色/TLS或恢复后撤销/工具对账。未勾选 S1 备份项，不能以逻辑恢复替代完整灾备。操作入口见 [逻辑备份操作](logical-backup-operations.md)。

对象备份后续增量：函数级 `object_store` 注入已接通快照确切版本下载、format 2 加密包、不同空私有桶恢复和原文/资产引用原子映射，hold 保持阻断。未配置的 CLI 仍失败关闭；自动调度、远程备份包、配置供应、生产恢复规模与异地/PITR未完成，S1/S7不勾选。此增量更新前述“对象备份待接通”的历史状态，操作见 [逻辑备份](logical-backup-operations.md)。

0016 更新前述配置供应限制：管理端批准目标、租户绑定加密、revision 并发控制/原子审计及聊天/知识 Worker 成对 policy/wrapping-key 参数已接通。恢复配置默认 recovery_rebind，不使用源桶凭据，明确重新绑定时校验本租户恢复引用。备份 CLI 安全供应、自动 scheduler/远程包归档、GC/KMS/生产 TLS/IAM/HA 仍未完成；S1/S3/S7 保持未勾选，详见 [对象存储](object-storage-operations.md)。

远程包增量（Schema 仍为 0016）：已增加独立备份凭据 CLI 输入、全包认证、4MiB 有界分块、私有固定版本归档、加密索引/原子排他回执、固定版本取回和认证后 READY 发布；隔离取回后实际恢复 PG/业务对象保持 hold。此处更新前述“CLI/远程包归档待接通”的历史状态。自动停写/排空调度、唯一租约、catalog/receipt 灾备、自动恢复/freshness、保留/GC/KMS/生产最小权限/PITR仍未完成，不勾选 S1/S7，详见 [远程备份](remote-backup-operations.md)。

0017 门控基础：持久全库维护、30业务表SQL写保护/提交屏障、过期不自动开放、显式接管/fence、单封存连接/SHARE锁和执行租约排空、备份guard及恢复旧owner失效已有真实PG证据。CLI显式permit并不自动调度/续租/排空等待；scheduler/catalog/自动恢复与生产角色/规模/HA未完成，不勾选自动备份项，详见 [维护](maintenance-operations.md)。

## S2：日志、指标、费用与部署制品

2026-10-21 Linux 镜像复验：本机构建发现并修复 slim 镜像缺少 OpenCV 运行库，非 root、只读、无网络启动已通过；固定 Trivy 扫描仍报 81 条无列出修复版本的高危/严重项。保留 CI 失败门槛，不静默忽略；需正式漏洞处置/风险批准或更新基础制品后复扫，S2 制品项不勾选。

2026-10-21 CI 集成修复：隔离 PostgreSQL 已换成固定 digest 的 PG16/pgvector 镜像；零跳过作业明确区分不需要本地 GPU 权重的集成组与离线真实模型专项。本机同等 PG/Redis 组 86 通过、零跳过，托管 CI 仍待运行；不据此勾选可重建制品或完整 S2。

2026-10-18 连接测试费用边界增量：在线供应商管理页连接测试改为只读 `GET /models`，不再发送未记账的 `chat/completions` 生成请求或测试 Prompt；2026-10-22 又增加 256 KiB 响应上限和所选模型 ID 精确匹配，重定向/错误/超限/缺模型均失败。该检查不是生成或工具能力、供应商真实计费合同证明，也不能覆盖 Embedding/Rerank/OCR，完整 S2/S5 仍不勾选。

2026-10-17 观测增量：知识 Worker 根 Trace 现按同事务提交的 `complete` 或 `partial/failed` 任务终态标记结果，避免逐项失败仍显示成功；真实 PostgreSQL 联合专项 **23 passed / 0 skipped**。这不代表生产 Collector/Alertmanager 或双机观测链验收，见[验收记录](production-verification.md)。

0022管理与告警后续（2026-10-10）：管理员模型页新增明确价格/预算发布和租户状态，token-bound config.manage HTTP接口使用期望版本、幂等编号及十进制金额字符串；客户/客服不可见。私有回环采集从PG耐久账本汇总预算策略/预留、租户/UTC日/Run软阈值事件、预算拒绝、已保护租户未知费用和采集健康；Prometheus规则及promtool合成用例覆盖新增事件与采集故障。指标不含租户、模型、金额标签。尚无生产collector、Alertmanager receiver/通知送达演练、受控结算释放或供应商合同实测，不勾选完整S2。详见[预算操作](model-budget-operations.md)。

0022预算增量（2026-10-08）：管理员版本策略/原子audit，调用前tenant级PG锁、started/固定quote/reservation同事务提交，tenant生命周期/UTC开始日/Run金额及请求数门控；默认无策略不启用。管理员明确输入计费合同上界，全输入按最大缓存费率预留，输出max_tokens受限，文本/函数工具面及默认设置复查。历史估算/声明峰值与永久reservation取大值，旧无预留未知/混币种失败关闭，取消/崩溃/下调不释放；软阈值耐久标志，安全SSE error/reset。36表维护/导入/加密恢复联动。尚非供应商硬账单保证，缺受控结算释放/更多计价与模型/费用UI/指标/外部告警/真实合同和浏览器验收，不勾选完整预算或S2，见 [预算操作](model-budget-operations.md)。

预算前置增量（2026-10-08，仍为0021）：新增管理员租户/Run/UTC日保守费用占用快照，单语句按尝试历史峰值聚合、币种分组、金额字符串与未解决数量；未知/started/partial不因人工账单声明变成已知，修正下调不释放占用。只是只读基础，不可用检查后调用代替原子预留；尚未接入限额、预留、软告警或供应商输入上界/输出约束，不勾选预算或S2。边界见 [费用操作](model-usage-operations.md)。

0021费用增量（2026-10-08）：管理员显式tenant/model价格版本/来源摘要、调用前不可变quote、Decimal缓存分项估算和unknown保留、原子审计；追加operator_reported对账声明与期望revision/幂等/receipt去重、并发控制/币种拒绝/权限复查已接通。实际独立Worker从批准价格生成费用估算、加密备份恢复四表摘要/价格/quote/声明验证。34表维护封存及用量/费用SQL不可清表保护，不改历史迁移。此项更新下述“价格/追加对账尚未实现”的内部能力，但尚无管理HTTP/UI、供应商核验、更多计价、自动预算预留/软硬限额/通知或完整费用路径，不勾选S2。

0020用量增量（2026-10-08）：PostgreSQL聊天调用前耐久started/unknown记录，之后独立事务观测Token/终态，租户/actor/Run/租约fence复查，成功结果与账本attempt_id关联、不依赖Trace启用。缺失/默认零/非法用量为NULL unknown；取消/失败后的正数为partial，SDK正数observed不是供应商账单。终结证据SQL不可改写/删除，31表维护封存/SQLite导入空账本检查/全表备份恢复联动接通。网关SDK隐式重试关闭，内存500验证一次HTTP；未来重试需新增尝试记录。仍缺版本价格/金额、追加对账、原子预算/软告警硬限额、Embedding/Rerank/连接测试和生产权限/容量，不勾选完整费用项或S2。

模型请求后续增量（2026-10-08）：网关/聊天PydanticAI WrapperModel接通普通和流式model.request，每SDK请求内部attempt_id、固定供应商/stream标签；工具循环/校验重试独立请求、自然完成/提前关闭/取消/读取及清理失败有专项测试，实际隔离API→独立Worker链验证模型子Span。此项更新历史“模型请求尚未接通”的范围，不把SDK隐式HTTP重试计为独立账本尝试；用量仍原样传递而非持久计费。count_tokens/compact/连接测试、持久用量/版本价格/预算/未知对账、生产出口与其他链路仍缺，不勾选S2/S5。

工具/检索后续增量（2026-10-08）：六个现有工具已接入tool.execute，固定工具名、每调用UUID attempt_id、继承request_id/run_id；知识工具授权后围绕真实检索入口建立rag.search子Span，不保存参数或结果。真实PydanticAI合成模型在四并发Run执行六类工具，验证线程上下文、独立编号、父子关系和敏感标记拒绝；失权、检索异常、async取消及默认关闭兼容有专项证据。此项更新下述历史“tool/RAG尚未接通”的范围，仍缺模型请求、Embedding/Reranker/全数据库/对象/知识Worker、持久attempt审计、生产出口与费用等，不勾选完整S2。

0019聊天追踪增量：显式私有OTel provider/有界离线程processor，HTTP可信新根、日志trace关联、严格内部carrier入PG队列、Worker CONSUMER与真实Agent子Span已接通；实际认证API→独立Worker/PydanticAI合成模型证明相同trace与parent关系，SQL/幂等/NULL/并发/隐私/阻塞exporter验收见记录。不含model attempt/tool/RAG/database/object storage或知识Worker全链、生产OTLP出口/平台/采样/费用，仍不勾选完整OTel或S2，见 [观测规范](observability.md)。

有界导出增量（2026-10-08）：私有采集改为独立单线程/单未完成future，无队列扩张；超时/取消保留实际占位，逐行deadline/协作关闭/UTF-8字节上限，HTTP序列上限与丢弃/导出结果计数。30并发/20次busy、1024序列/1200样本、超限/关闭/故障恢复及真实Prometheus回归已有测试；不是生产容量/平台通知、跨API/Worker OTel或费用预算通过。更新下面历史“有界异步导出待实现”为已接通基础，不勾选完整S2，详见 [观测规范](observability.md)。

私有采集后续：显式MetricsPolicy/专用凭据、独立127.0.0.1监听及API lifespan管理已接通；拒绝错误/重复认证、浏览器Origin/Host/远端peer，默认关闭，业务端口无metrics路由。真实隔离Prometheus验证有效凭据up=1、无效凭据up=0和Counter查询，真实promtool验证格式/配置，退出释放端口。此增量不是持久平台/生产collector/通知或跨API/Worker Trace验收；规模、有界异步导出/轮换和完整OTel/费用仍缺，下方保持未勾选，见 [私有采集](observability.md)。

HTTP观测增量：最外层纯ASGI服务端请求编号/上下文、白名单JSON日志可选sink、进程内低基数Counter/分离HTTP与SSE直方图及Prometheus文本已接通；不读取正文/头/查询/路径实参或原始异常，无公开metrics入口。500/拦截/断连/取消/并发上下文与promtool测试见验收记录。此项不证明代理/服务器/其他模块日志脱敏、私有采集/部署/日志留存、OTel/费用/预算或完整S2通过，以下保持未勾选。

恢复点选择增量：独立清单和逐anchor摘要、全部固定版本认证、计划/时间/gap/minimum/恢复验证门控与选择后摘要绑定取回已有实现。仅证明批准集合内选择，不证明全目录/最后一次点，可靠分发/抗回滚/真实S3与完整DR仍缺，S1/S7不勾选，见 [目录恢复](backup-catalog-recovery.md)。

catalog/receipt增量：独立store加密检查点与固定版本引导/认证取回已接通，不连接源DB、不重放任务，新增实际取回→包fetch→PG/对象restore测试但S3缺服务时跳过。独立anchor持久化/分发、全目录/最后有效点、真实联动与完整DR仍缺，S1/S7不勾选。见 [目录恢复](backup-catalog-recovery.md)。

自动对象恢复增量：每attempt隔离私有版本桶、独立verification凭据角色/批准前缀、策略绑定、实际restore接口消费及成功/失败保留资源路径已接通，配置时不创建桶。新增双周期真实PG/S3验收测试，服务缺失时跳过，不将代码/故障注入当作实际对象恢复通过；正式IAM、GC、catalog/receipt灾备仍缺，S1/S7未完成。

备份freshness增量：只读目录检查、snapshot年龄、明确remote/restore要求、固定kind指标和Prometheus规则/测试已添加；平台部署/测试receiver/通知与实时包探测尚缺，S2/S7保持未勾选。此处更新历史“freshness/告警入口未实现”的状态，见 [自动备份操作](backup-scheduler-operations.md)。

0018后续更新：单对象源/远程归档的scheduler CLI批准目标及有界角色凭据供应已接通，目标与namespace进入计划指纹。尚无自动对象恢复或catalog/receipt灾备；真实S3联动仍待环境复验，不勾选S1/S7，不以离线供应测试代替联动通过。

0018 备份调度增量：显式计划/策略指纹、唯一时间槽与全局单 running、owner/fence/数据库期限、自动续租、排空等待和实际封存备份已实现；可选本地实际恢复验证绑定目标指纹，恢复禁用计划并标记旧运行待审。CLI 仅本地文件，不自动配置，不回放未知任务。对象/远程自动供应、catalog/receipt灾备、freshness/告警、GC/PITR和生产角色仍缺，S1/S7不勾选。此处更新前述“自动scheduler/续租/排空未实现”的历史记录，详见 [调度操作](backup-scheduler-operations.md)。

2026-10-14 S2 追踪更新：显式受控 OTLP/HTTP exporter、聊天及知识 Worker CLI 入口和本机 protobuf 接收测试已接通；无隐式 OTLP 环境凭据或公网明文出口。显式启用 Trace 的 API/聊天 Worker 已产生无 SQL/参数内容的数据库子 Span，真实 PostgreSQL 跨进程追踪通过；0023 已接入知识队列内部 carrier，真实认证 API→独立知识 Worker→本机 OTLP 接收端的父子关联及数据库子 Span 通过。旧 NULL 任务保持独立根。S3 `get/put/check_ready` 对象子 Span 已在真实隔离对象存储与独立知识 Worker 进程导出验证；该新增用例使用合成 API 侧根 Span，不替代前述真实 HTTP 验收。仍缺正式 API 出口装配、生产 Collector/Tempo、日志留存、告警送达与容量/采样验收，不勾选完整 S2；见[观测规范](observability.md)及[验收记录](production-verification.md)。

2026-10-13 S2 日志增量：可选有界 JSON 出口、固定字段/字节上限、私有丢弃/失败/队列指标及 promtool 验证的日志告警规则已接通，默认关闭。正式 API 装配、受控 writer、留存/角色、生产采集/接收人送达仍未完成，不能勾选日志或完整 S2；见[观测规范](observability.md)。

- [ ] 结构化日志字段、脱敏、保留期与访问角色。
- [ ] OTel HTTP/Agent/model/tool/RAG/database spans；贯通 request_id、run_id、attempt_id。
- [ ] Prometheus 指标低基数标签、Grafana 仪表盘、Alertmanager 路由与演练。
- [ ] 每次模型尝试、Embedding、Rerank 用量入账；版本化价格、未知值和账单对账。
- [ ] 日/租户/Run 预算软告警与硬限额，原子预留防止并发越额。
- [ ] 锁定依赖、不可变镜像、非 root、漏洞扫描；可重建 CI 制品。

## S3：多实例与 Redis

2026-10-16 单次安全重排队增量：聊天 Run 仅在首轮执行失租、且数据库证明尚未开始任何模型请求/工具/输出、未取消、会话有效和原排队期限未过时，提升 fence 并重新排队一次；旧 Worker 不能在重排队后跨过模型账本预调用边界。模型尝试一旦入账或第二次失租则保留未知/终结，不盲重放。真实 PG 耐久聊天、模型用量、只读清单联合 **28 passed / 0 skipped**，包含原先因缺 `TEST_POSTGRES_BIN` 跳过的实际备份恢复用例。仍缺知识解析安全检查点、完整死信确认、真实双机/切主/分区及生产环境；S3 不勾选。见[验收记录](production-verification.md#2026-10-16-s3-增量聊天任务首次模型调用前的单次安全重排队)。

2026-10-16 失败归因增量：知识队列过期回收现在区分从未领取的 `queue_timeout` 与执行中失租的 `lease_expired`，均终结为 failed、不自动重领；前者没有发布机会，后者保留已发布项并要求核对后重新提交。真实隔离 PostgreSQL 的知识队列专项 **14 passed / 0 skipped**。这只是人工处置的准确状态证据，不是有界安全重试、统一死信清单或双机故障切换完成；S3 仍不勾选。

2026-10-16 只读异常清单增量：新增独立只读 PostgreSQL 角色门控的 `queue_review.py`，固定字段、总量/时限限制、用户取消排除和 `terminal_no_execution` / `review_unknown` / `review_items` 分类；拒绝有写权限或目标 RLS 已启用的角色，不执行重试/确认。真实 PG 聊天、知识和清单联合 **30 passed / 0 skipped**。它让异常任务可定位，但不等于完整死信状态机、安全重试或正式跨租户 RLS 运维授权，S3 仍不勾选。见[队列核对操作](queue-review-operations.md)。

当前增量：0009 提供 PG Run 租约、fenced 写入、取消与过期清理；0010 可选模式新增 API/独立 Worker、typed queue、持久 SSE 游标、GET 重连退避及服务端一致历史/草稿快照的跨刷新续读；停止按服务端回执/终态确认，不用断开订阅代替取消。0011 增加原子终态快照、累计 data 预算、显式 1–90 天有界事件清理与过期游标恢复，默认清理关闭。真实 PostgreSQL 验证包括独立 Worker 执行、持久取消、kill 后到期终结、实际 HTTP 订阅断开与重放、清理并发/回滚/迁移门控；实际前端清理后恢复答案不重复。下方仍不勾选：重试/死信、Redis、全局准入、保留政策确认/规模性能和完整双实例 HA 故障演练未完成；不会盲目重放崩溃模型/工具。

- [ ] API 创建 Run/任务/Outbox 后返回；Worker 持租约执行，不靠内存字典决定唯一性。
- [ ] PostgreSQL SKIP LOCKED 领取、续租、fencing token、会话唯一活动 Run、最大重试和死信。
- [ ] Redis 分布式准入/限流、取消通知；数据库取消标记保障通知丢失时最终取消。
- [ ] SSE event_id、游标重连、事件保留期、草稿去重、终态快照；同步前端/API 契约。
- [ ] 文件共享；禁用各实例启动时全局 recover_interrupted_runs/无租约任务恢复。
- [ ] Redis 故障时阻断新运行或启用经过验证的数据库协调；禁止各实例自行放宽限流。
- [ ] kill Worker、丢取消通知、租约过期、旧 Worker 迟到、Redis 切主、API 摘流测试。
- [ ] PostgreSQL/Redis 高可用与 split-brain 保护；主节点故障时验证读写与对账。

0012 历史增量：Redis 原子 Lua 全局容量/Run 启动速率、短租约 permit、Worker 心跳/工具/结果边界复查与失协调中断；PG 不可变策略、全局串行领取和耐久启动/预留水位防止 Redis 状态丢失后超额，抢占需旧 Worker 确认退出才释放。早期仅有实际 PG 并发与故障注入证据，因当时 Docker 引擎不可用，4 项真实 Redis 测试未运行；**2026-10-11 已在隔离 Redis 上补跑并纳入最新全量回归**。仍不勾选完整 S3：单机 Redis Lua 联动不能代替双服务进程、网络分区、切主或生产 ACL/TLS 验收。API 队列准入、租户公平、供应商 RPM/TPM 仍待补齐；费用预算已有保守预留基础，但不等于供应商 RPM/TPM 或完整生产预算验收。

0013 当前增量：显式 PG 知识后台导入、固定文件 hash/size 清单、独立解析 Worker、60 秒 owner/fence/租约、2 秒续租与有界过期终结。文档/资产元数据/切块与 item/result/progress 原子发布；原文副本与资产路径独立，不删用户输入，失租结果不能发布，未知 COMMIT 回执保留 staging。实际独立 Worker、kill 解析进程、失权/错误 fence/回滚和管理界面导入检索已有隔离证据。仍不勾选完整 S3：共享对象存储/GC/备份、全局解析资源预算、自动重试/死信、主动取消、解析沙箱、PG/Redis HA 与生产规模测试未完成；旧 local 任务不重放。

0015 输入增量：对象模式提交实例上传固定版本，Worker 无需共享源根，独立进程/不同目录的真实 PG/S3 专项已有证据；旧 NULL 输入仍按本地路径处理，缺对象配置不回退。排队输入引用纳入备份/复合主键恢复映射，hold 保留；仅函数依赖注入和合成 harness，生产配置供应/Worker CLI 尚未接通。此处更新 0013 的“共享输入根”历史限制，不勾选完整 S1/S3/S7：GC/解析沙箱/全局资源预算/重试/主动取消和生产 HA 未完成。

## S4：业务工具

- [ ] 接入服务端身份与业务资源归属，客户会话/资产访问同步隔离。
- [ ] 模拟工具标识与真实适配器隔离，供应商凭据与用户凭据分别管理。
- [ ] 工具 policy、最小返回字段、审批摘要/有效期/参数绑定与权限复查。
- [ ] 稳定 operation_intent_id、幂等键、payload hash、唯一约束、unknown 状态与对账任务。
- [ ] 外部 API 幂等能力验证；无幂等/查询能力时不自动重试真实写操作。
- [ ] 审计和 Outbox 同事务；消费者去重；独立审计归档。
- [ ] 故障注入：外部成功本地崩溃、重复审批、并发执行、权限撤销、超时取消。

## S5：模型降级

2026-10-22 增量：本地备用路由在发送用户内容前新增固定合成函数调用协议探测，失败关闭本地候选；这比仅看 `/models` 目录更接近客服工具能力要求，但仍须两节点真实权重、上下文/流式/费用和故障注入验收，S5 不勾选。

2026-10-21 本地目录检查增量：管理端连接测试现在只读有界 `/v1/models` 响应，并要求明确包含配置的模型 ID；目录异常或模型缺失不再误报成功。仍未验证该模型生成、函数工具、上下文长度、两台 Linux 主机实际部署与故障切换，S5 不勾选。

2026-10-17 租户数据边界决策：跨在线供应商自动故障切换**默认禁止**。仅当租户管理员针对目标在线供应商显式开启数据外发授权，且该供应商已配置密钥、通过能力/数据政策/预算检查时，才允许把本轮用户消息及公司知识片段发送给它。本机回环模型不属于“另一家在线供应商”，但仍须通过本机能力和健康检查。授权必须持久化、版本化、可撤销、可审计；路由在每次实际外发前复核当前租户授权，撤销后不得继续使用旧队列快照。无授权/撤销/状态不明时安全失败，不能因 DeepSeek 故障自动扩大数据接收方。当前未实现跨供应商路由或授权入口，因此此条是实施门槛而非已交付能力。

后续授权增量：已建立认证 PostgreSQL 管理 API 与管理页 OpenAI 授权/撤销入口，租户级状态默认为关闭，写入采用期望版本、幂等发布编号及同事务审计；启用前要求本租户已保存 OpenAI 密钥。尚未把授权接到实际候选筛选与每次外发前复核，也未实现跨供应商 attempt/预算和自动切换，故完整 S5 不勾选。上段“尚未实现授权入口”是先前决策时的历史状态。

后续账本增量：PG Run 现在允许备用模型以其**实际**供应商/模型建立独立用量 attempt，但须在开始前核对本租户当前授权、目标配置和模型名，撤权后的新 attempt 被拒绝，已开始的 attempt 仍可终结记账。授权写入与尝试开始通过同一租户/供应商事务锁排序。现有预算若仅覆盖原模型，备用 attempt 仍失败关闭；尚无跨模型预算、候选路由、能力过滤或真实外发，故不能据此勾选 S5。

2026-10-17 故障证据增量：模型失败分类增加固定 `delivery` 维度。收到 HTTP 状态代表 `response_received`，HTTPX 连接/连接池阶段失败代表 `not_sent`，读取/写入超时及 SDK 包装后的网络异常保守记为 `unknown`；客服失败日志只记录固定类别，不记录异常正文。该字段不是费用已知证明或重放许可，仍未自动重试/切换供应商，S5 不勾选。专项验证见[验收记录](production-verification.md)。

后续请求级增量：SDK 包装后的类型化连接前失败现可在同一模型最多重试一次，普通与尚未进入流式响应上下文的请求每次均建独立 attempt；上段“仍未自动重试”是早期批次状态。响应已收到、送达未知、流式已开始和工具/Agent 级重放仍禁止自动重试。共享熔断、跨供应商切换及完整费用/能力政策未完成。

2026-10-17 本地端点增量：严格验证逐主机 `http://127.0.0.1:<port>/v1`，禁用环境代理和 HTTP 重定向；随后接入 Gateway、API/聊天 Worker 显式装配与管理页无密钥配置，隔离协议测试通过。尚无真实本地推理、双节点能力/健康、费用合同或故障切换证据，S5 不勾选。

后续真实回环合成协议和隔离 PostgreSQL 租户路径已专项验证；真实模型权重、A/B 两机健康与切换仍缺，不能把合成服务或单机 PostgreSQL 测试视为 S5 完成。

2026-10-10前置增量：已对PydanticAI `ModelHTTPError` 与模型传输错误做固定类型分类（429、408/5xx、401/403、402、其他4xx、网络），客服失败日志仅记录分类，不读取供应商body或异常消息；流式模型异常/超时清除未提交草稿。`retryable`只是后续路由决策材料，当前不自动重试、换供应商或把失败当安全的Agent重放。Run/费用账本目前仍固定单一provider/model；必须先设计跨模型attempt与预算合同再开放故障切换，故S5所有验收项仍未勾选。

- [ ] DeepSeek 默认；备用在线模型须由租户管理员分别配置并显式授权接收本轮消息和公司知识片段，默认禁止且撤销即时生效；本地模型仅通过每台主机各自的固定回环 OpenAI 兼容端点接入，限制 SSRF、重定向和出站访问，分别验收节点健康与能力。
- [ ] 按工具能力、上下文、数据政策和费用过滤候选；不同模型独立能力验收。
- [ ] 错误归一化、总时限/Token/费用内有界重试、共享熔断与 half-open 探测。
- [ ] 流式中断后 answer_reset、新 attempt；完整工具消息保持一致，未知写操作先对账。
- [ ] 无备用或全部失败时返回证据/人工入口；保留实际模型及每次费用。
- [ ] 429/5xx/超时/认证失效、预算耗尽、取消和工具失败分类测试。

## S6：RAG

2026-10-23 增量：已增加迁移 owner/恢复保护下的租户范围有界旧版本向量清理，真实 pgvector 验证当前版本、退役 profile 回切向量和其他租户不受影响；CLI 需明确确认。shadow/灰度、生产容量/成本及独立盲测仍缺，S6 不勾选。

- [ ] 向量与关键词后端接口、版本化 chunk/Embedding/index 元数据。
- [ ] 中文关键词方案评测，保留型号精确匹配；不把 PostgreSQL ts_rank 标为 BM25。
- [ ] pgvector 索引、批量增量嵌入、任务恢复、权限/租户过滤、候选不足回补。
- [ ] RRF、Top-K Rerank、证据门控、引用对应原文/版本/图片。
- [ ] shadow/灰度索引切换、失败回切、删除与旧向量清理、成本和延迟监控。
- [ ] 合成集回归与至少 200 条独立脱敏真实问题盲测；目标见 RAG 文档。

## S7：部署、回滚和灾难恢复

0014 当前增量：逻辑恢复与 recovery hold 同事务提交，恢复后 API/Worker 与相关执行边界失败关闭；独立进程拒绝旧 queued Run、运行中 HTTP 503、失败 hold 回滚恢复事务及 0013→0014 空保护升级通过真实 PG 专项。尚无完整前向对账/授权解除，不勾选整体验收。

- [ ] TLS/可信代理/管理权限/上传隔离，准备与生产分离。
- [ ] 单次 migration job、兼容窗口、备份 freshness gate、蓝绿或滚动发布。
- [ ] 就绪摘流、Worker 排空、版本回切与 Prompt/路由/索引分别回滚。
- [ ] PostgreSQL PITR、文件 manifest、密钥恢复与外部操作对账演练。
- [ ] Redis 不可用、模型不可用、双实例切换和依赖故障演练。
- [ ] 演练报告记录实际 RPO/RTO、缺口、整改与再次验证；值班负责人接手。

## 上线判定与交付记录

每阶段提交：变更摘要、代码/迁移版本、测试报告、部署版本、运行截图或脱敏日志、实测门槛、已知限制、回退步骤。命令成功或“质量门槛通过”不能代替恢复演练、权限测试和真实模型评测。

基础回归继续使用 `python -m pytest -q`、前端 `npm.cmd test`/`npm.cmd run build` 与 `scripts/evaluate.py`。生产专项测试另建隔离 PostgreSQL/Redis/对象存储环境；真实模型调用须用户在管理界面提供密钥，真实业务调用使用授权沙箱。

详细设计入口：[总体架构](production-architecture.md)、[迁移](postgresql-migration.md)、[观测](observability.md)、[工具](business-tool-security.md)、[RAG](rag-upgrade.md)、[Runbook](deployment-dr-runbook.md)。
