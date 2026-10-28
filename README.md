# 云小智 AI 客服 MVP

2026-10-28 后续：用户授权的现有DeepSeek配置已完成小量真实合成生成与用量获取；三种通知出站接口（企业微信/钉钉/邮箱）已形成，尚缺正式接收服务与Gmail凭据/送达。后端最新 **571 passed / 383 skipped**；新增模块后最终应用制品须重建，旧r2证据不覆盖全部新源码。用户已允许推送现有公开仓库，实际推送/CI与S2整体验收尚未完成。见[S2真实调用](docs/s2-deepseek-live-acceptance.md)及[通知接入](docs/alert-notification-operations.md)。

2026-10-28 S2 收尾：隔离组件跨模块组 **175 passed / 0 skipped**，普通后端全量 **556 passed / 383 skipped**（跳过不计通过）。Grafana固定插件制品、Prometheus3.15.1和Collector0.164.0均扫描0 HIGH/CRITICAL，真实查询/Collector故障恢复通过；隔离VM的Collector已可回退升级并复验257 Span的API→Worker链路。已补日志/Collector部署、本地推理计量与告警、应用制品重建/测试身份签名。正式告警渠道、账单核验、发布信任身份及远端 CI 仍缺。**S2 尚未整体完成，当前没有第二笔完成提交，也不需要用户合并 main。**见 [S2 收尾清单](docs/s2-completion-checklist.md)。

2026-10-25 制品与解析安全更新：已构建包含 Linux Landlock/seccomp 沙箱的完整镜像，复验六类文档与真实 PostgreSQL Worker CLI；修复新漏洞库发现的 pypdf 高危问题，锁定 6.21.2 后重建扫描为 0 HIGH/0 CRITICAL。生产知识 Worker 要求 Linux x86_64、Landlock ABI ≥ 5，领取任务前自检，不能满足则拒绝启动；Windows 仅作开发兼容。当前制品仍缺托管 CI/签名/跨宿主及正式双机验收，**不可据此上线**。见[解析隔离](docs/parser-isolation.md)和[最新验收证据](docs/production-verification.md)；下方旧批次数字不代表当前全量结果。

2026-10-26 当前Schema head：`0037_rag_phase_clock`。默认PostgreSQL向量Worker已接run快照、受限候选检索、模型/Prompt绑定及锁内提交；已完成[合成浏览器/SSE验收](docs/rag-browser-acceptance.md)，已有[候选观察窗口](docs/rag-observation.md)与[受控阶段推进/最终提升内部事务](docs/rag-rollout-advance.md)。仍缺自动调度/异常动作、真实控制入口、自动回切及生产验收；新镜像未构建，旧0030镜像不可直接使用。升级须先暂停运行中灰度，恢复后旧审批需重审；API/Worker不可读写控制表。详见[灰度边界](docs/rag-rollouts.md)，其他限制见验收文档。

2026-10-24 最新补验：包含四个原生库包元数据的现行 Dockerfile 已完成本机缓存/无缓存 OCI 重建，逐 blob 校验通过、manifest 一致。下段“元数据变更后的 OCI 重建一致性未验收”已由本次本机证据更新；跨主机和托管 CI 仍待验收。

2026-10-24 生产镜像进展：现行 `Dockerfile` 已切到固定摘要的 distroless Debian 13 非 root 运行基底。本机 `linux/amd64` 完整镜像的只读离线导入、OCR、隔离 PostgreSQL 迁移及 MinIO 读写通过；复制的四个原生库已补齐 Debian 包元数据，固定 Trivy 对更新后的镜像扫描为 **0 HIGH / 0 CRITICAL**，SBOM 校验为 14 Debian + 143 Python 组件。此次元数据变更后的 OCI 重建一致性、托管 CI、签名、跨主机重建和正式双机部署仍未验收，**S2–S6 未完成，不可据此上线**。详见[验收记录](docs/production-verification.md)。

2026-10-20 S6 进展：当前 PostgreSQL Schema head 为 `0028_rag_vector`。已具备版本化切块快照、固定 1024 维 profile 的 pgvector 存储、可重跑的本地 Embedding 索引 Worker、完整性封存／原子激活／安全回切，以及按租户／当前文档版本过滤的精确向量候选查询。聊天 Worker 与 API 已增加默认关闭的实验性混合检索接线；真实隔离 pgvector 已验证索引流程，但本次聊天路径改动尚待真实 pgvector 复测。证据门控、独立盲测与双机验收仍缺，完整 S2–S6 未验收。此前 `0026_rag_metadata`、`0025_model_circuit` 为历史批次 head。

2026-10-18 S2/S5 共享熔断增量：当前 PostgreSQL Schema head 为 `0025_model_circuit`，每租户/供应商/模型状态在数据库共享；健康故障达阈值后阻断新出站和付费 attempt，冷却后只放行一个带代际 fencing 的 half-open 探针。新增不含租户/模型标签的私有聚合指标和告警规则；真实 promtool 与隔离 Alertmanager firing/resolved 均通过。隔离 PostgreSQL/Prometheus 相关联合 **79 passed / 0 skipped**，专用运行角色测试另 **1 passed / 0 skipped**；无服务后端全量 **278 passed / 249 skipped**。这不是生产双机、数据库切主、真实供应商/账单或正式值班接收人验收，完整 S2–S6 仍未完成。见[验收记录](docs/production-verification.md)。

2026-10-18 前一 S2/S5 批次：当时 PostgreSQL Schema head 为 `0024_model_route_budget`。备用 OpenAI/本地模型可由租户管理员发布独立 Token 合同，预留金额仍归入租户主预算；OpenAI 跨在线供应商外发授权默认关闭，须管理员显式开启。认证 PostgreSQL 聊天请求可在主模型发生可分类、尚未交付响应的故障后先尝试本机模型，再考虑经授权的 OpenAI；每次备用请求在出站前重新检查租户授权、Run 租约、目标模型与预算。流式响应已开始、不可分类故障及整个 Agent/工具均不重放。该批次真实 PostgreSQL 七组联合 **69 passed / 0 skipped**；此前无服务后端全量 **271 passed / 236 skipped**、前端 **39 passed** 且构建成功，是熔断接入前的基线。真实供应商能力与账单、双机验收仍缺。详见[验收记录](docs/production-verification.md)和[预算操作](docs/model-budget-operations.md)。

2026-10-16 S3 队列增量：知识任务排队超时与执行失租分别记为 `queue_timeout` / `lease_expired`，均不自动重领；聊天任务新增首次模型调用前、数据库证明无外部效果的**最多一次**重排队。另提供仅供受控只读 PostgreSQL 角色使用的[异常终态清单](docs/queue-review-operations.md)，不含正文/路径，也不重放未知任务。最新真实 PG 聊天/用量/清单联合 **28 passed / 0 skipped**；此前聊天/知识/清单 **30/0** 为改动前专项。这不是完整安全重试、死信处置或双机故障切换验收，见[验收记录](docs/production-verification.md)。

2026-10-15 制品增量：已按自管 Linux 双实例＋反向代理目标增加固定基础镜像摘要、锁依赖、非 root 的多阶段 `Dockerfile`，CI 增加无网络只读启动检查、镜像漏洞扫描和 CycloneDX SBOM 作业。本机 Docker 引擎不可连接，镜像和新 CI 作业尚未实际跑通；本机前端构建及入口专项 **7 passed**。这些不代表双机部署或 S2 验收，见[部署 Runbook](docs/deployment-dr-runbook.md)与[验收记录](docs/production-verification.md)。

2026-10-13 观测增量：可选有界 JSON 日志出口已实现固定字段白名单、非阻塞队列和私有丢弃/失败指标；Prometheus 告警规则经真实 promtool 校验。默认未启用，尚无正式日志平台/接收人送达，不能视为生产 S2 验收，见[观测规范](docs/observability.md)。

2026-10-13 增量：当前 PostgreSQL Schema head 为 `0023_knowledge_trace`。知识导入队列仅在显式追踪时保存受约束的内部 Trace carrier，已验证认证 API→独立知识 Worker→本机 OTLP 接收端的父子关联；旧任务保持 NULL 兼容，公开任务响应不含 carrier。隔离 PostgreSQL 相关回归 **56 passed / 0 skipped**；此前 429/1 全量仍是旧源码基线，不把专项相加为新全量。生产 Collector、告警及完整 S2–S6 尚未验收，见[验收记录](docs/production-verification.md)。

2026-10-11 隔离验收基线：上一完整后端回归 **429 passed / 1 skipped / 0 failed**；唯一跳过的专用 PostgreSQL 角色用例随后在单独的 `::1:55433` 实例上 **1 passed / 0 skipped**。此后新增的 S2 OTLP 出口和数据库追踪合并专项为 **36 passed / 0 skipped**，尚未重跑全量。真实 Redis、S3、Prometheus 联动已运行，旧段落中的“Docker 不可用”“Redis/S3 未复验”和较小测试总数均为历史批次记录。两次运行不可合并成一次全量无跳过；隔离测试也不等于生产部署/安全/HA/灾备验收。最新边界见[验收记录](docs/production-verification.md#当前验收基线2026-10-11)，剩余任务与优先级见[实施清单](docs/production-roadmap.md#下一批可执行任务与优先级)。本次未重跑前端，最近前端证据仍为 **39 passed + build 成功**。

生产数据库角色前置：独立迁移、API、Worker 与只读备份身份已有[授权脚本及隔离验收](docs/postgresql-roles.md)；[RLS 范围与可信边界](docs/postgresql-rls.md)已建立清单门控，但政策尚未安装，也未在生产供应账号或验证TLS/RLS；不能直接上线。
远程PostgreSQL的API/Worker现分别默认校验实际API/Worker角色；若经回环地址或本机代理连接，应在装配Repository时显式启用`strict_role=True`，Worker增加`--require-app-role`。启动校验不能替代RLS或生产权限演练，当前两种运行身份直接SQL仍能跨租户读取。

2026-10-10 预算管理与私有告警增量：管理员模型页现可发布明确的模型价格及租户预算版本，并查看本租户占用/剩余额度；认证HTTP与config.manage权限绑定，金额只用十进制字符串。显式开启私有指标时，从PostgreSQL耐久账本汇总低基数软阈值、拒绝及未知费用指标；提供Prometheus规则和promtool合成验收。尚未部署collector/Alertmanager接收端，也没有真实通知送达、受控结算释放或供应商账单验收。以下0022初始描述中的“缺管理UI/费用指标”为历史状态，当前边界见[预算操作](docs/model-budget-operations.md)和[验收记录](docs/production-verification.md)。

2026-10-08 的 0022_model_budget 增量：PostgreSQL模式新增管理员明确预算策略及跨实例原子预留，普通/流式PydanticAI请求在调用供应商前按tenant生命周期、UTC日、Run金额与Run请求数门控；输入使用管理员批准的计费Token合同上界，输出max_tokens受限。未知旧费用、缺价、币种/请求计价面不受控时失败关闭；取消、崩溃和人工下调不自动释放预留。默认无策略、不启用预算。这只是应用侧保守控制，不保证供应商硬账单，仍缺受控结算释放、更多计费路径、外部软告警送达及真实合同验收。管理UI与私有预算指标已在2026-10-10后续增量补齐。详见 [预算操作](docs/model-budget-operations.md)。

2026-10-10历史后端全量回归：**367 passed / 45 skipped，453.96秒**；全量启动后的UTC日与未知金额修正另由最终专项 **6 passed** 覆盖，当时未包含在该次全量内。前端 **39 passed** 及构建通过。此段以及此前361/45、下方0021“最新回归”均为历史阶段记录；当前后端基线以本页顶部和[验收记录](docs/production-verification.md#当前验收基线2026-10-11)为准。完整生产化仍未完成，不能上线。

2026-10-08 预算前置补充（Schema仍为0021）：内部管理员费用接口新增租户/Run/UTC日保守占用快照，历史金额按尝试取峰值、不混币种；未知保留NULL，不因人工向下修正释放占用。最终真实隔离PostgreSQL费用/用量专项21项通过，无跳过；本批未重跑全量，下方全量结果是此前源码证据。尚无原子预留或软硬限额，不能将读快照当作调用许可。详见 [费用边界](docs/model-usage-operations.md) 和 [验收记录](docs/production-verification.md)。

一个本地运行的中文 AI 客服原型。后端使用 FastAPI、PydanticAI 和独立 `ModelGateway`，默认支持 DeepSeek，可选支持 OpenAI；企业知识使用结构化解析、SQLite FTS5/BM25 召回和确定性二次排序。

## 已实现

- React 流式聊天界面、停止生成和来源引用。
- 管理页录入、测试和切换 DeepSeek/OpenAI。
- API Key 使用本机主密钥和 AES-256-GCM 加密，读取接口不回显明文。
- 支持 PDF、Office/WPS 文档、电子表格、演示文稿、网页、文本和常见图片；扫描内容及嵌入图片自动 OCR，旧版 Office 通过 LibreOffice 转换。
- 统一保留标题、表格、页码、工作表/行号、幻灯片和 OCR 等结构元数据，按章节/句子/表格切块并建立中文全文索引。
- 同一来源文件更新时原位重建并递增版本；管理页展示检测类型、解析警告、来源和切块类型，可手动重新解析。
- 目录扫描通过持久化后台任务执行，显示当前阶段、文件和逐文件错误，服务重启可恢复未完成任务。
- 检索混合使用中文分词、字符相似度和 BM25，并支持在管理界面维护企业术语同义词及查看排序解释。
- 管理页可直接输入本机文件或目录地址，也可点击“选择文件/选择目录”；默认托管目录为 `data/knowledge`，目录导入会递归扫描支持的文档。
- 模拟订单、物流、退款资格、营业时间等 PydanticAI 工具。
- 联系方式本地登记。
- 单会话运行取消与新消息抢占基础设施。
- 企业管理中心包含运营概览、完整会话详情、提示词版本发布、知识库、模型、联系记录和审计日志七个独立页面。
- 会话支持负责人、标签、内部备注、管理状态、归档恢复、安全删除和 Markdown/CSV 导出。
- 会话采用真正的延迟落库：只有首条消息开始处理时才建立记录，启动时会清理没有消息、运行、联系记录和备注的历史空会话。
- 系统提示词采用不可编辑安全基线与可版本化企业规则分层，每次 Agent 运行固定提示词版本快照。

首版不会从环境变量读取或自动使用模型密钥。请在管理页中手动提供密钥。

## 本地运行

要求 Python 3.10+ 和 Node.js 20+。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[test]"
Set-Location frontend
npm.cmd install --registry=https://registry.npmmirror.com
npm.cmd run build
Set-Location ..
.\.venv\Scripts\python.exe main.py
```

打开 <http://127.0.0.1:8000>。进入“系统管理”，录入 DeepSeek API Key，测试连接后保存；DeepSeek 是默认供应商。OpenAI 未配置时不会被调用。

在线模型的“测试连接”只读取有界的供应商模型目录，不发送生成请求；成功要求目录列出所选模型，但不代表其生成或工具能力已通过验证。

认证 PostgreSQL 管理页提供 OpenAI 备用外发授权开关：默认关闭，只有本租户管理员在保存 OpenAI 密钥后可显式开启或撤销。开启本身不会发出请求；仅当认证聊天的主模型发生合格故障、本机备用不可用或其门控不通过、目标价格与合同可用且备用 attempt 再次核验授权通过时，才可能向 OpenAI 发送该次模型请求中的会话与知识片段。

开发前端时可在另一个终端运行：

```powershell
Set-Location frontend
npm.cmd run dev
```

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
Set-Location frontend
npm.cmd run build
```

知识库检索与 Agent 编排的隔离评测：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate.py --repetitions 30
```

指标定义、适用范围和结果解读见 `docs/evaluation.md`。评测不读取生产数据库、上传文档或模型密钥。

## 数据安全

显式 PostgreSQL 可通过 AuthPolicy 启用 Cookie 认证模式；模型配置、Prompt、管理、知识和聊天/联系登记路由已按请求绑定。Agent 使用服务端身份与可撤销检查，认证模式不返回共享模拟订单；未绑定路由仍失败关闭。前端通过 `/api/auth/config` 确认模式，在认证模式提供登录、刷新恢复身份、退出与失效清理；只有服务端明确返回 `enabled: false` 才打开未认证界面。默认本机兼容模式仍未启用认证。账户供应、真实部署/TLS、业务适配器、耐久 Worker/多实例等门槛尚未完成。接口、约束与未完成项见 [API 契约](docs/api-contracts.md) 和 [验收记录](docs/production-verification.md)。

历史回归（0017）：后端214项通过、41项跳过，285.34秒；本次环境拒绝启动隔离S3，因此37项对象测试未复验，另4项Redis未验收。前端38项测试和构建通过。新增8项真实PG维护门控：提交屏障/SQL保护、过期不放行/旧owner fencing、排空/单封存连接、guarded实际备份恢复与CLI、失租无成功包、HTTP/独立Worker。浏览器访问被客户端拦截，页面验收仍缺，真实TCP503不冒充浏览器证据。此前243 passed/37真实S3属0016历史批次，不能视为当前0017对象联动已通过。自动调度/恢复/catalog/KMS/TLS/IAM/PITR和多数S2–S7仍未完成，默认服务未切流。

0010 在 0009 租约基础上提供可选持久聊天：`create_app(..., durable_chat=True)` 要求显式 PostgreSQL/AuthPolicy，POST 返回 202，独立 `scripts/chat_worker.py` 执行。SSE 使用私有持久事件与游标，前端 GET 退避重连、按一致草稿快照跨刷新续读，不重发命令；断开订阅不取消任务，停止保留早期意图并等待服务端确认/终态。0011 提供同事务终态快照、累计事件预算和过期游标恢复；事件清理默认关闭，需显式审阅配置 1–90 天保留期，不删除历史消息/幂等引用。默认仍为 SQLite/直接 SSE。Redis 真实验收、检查点与完整 HA 未完成，不等于完整 S3；PG 准入和知识租约见下文 0012/0013。生产 Worker 要求显式数据库与文件根；密钥仍只由管理员提供。

0012 新增显式 Redis 共享准入及 PG 策略/启动/预留水位，默认不创建策略、不启用 Redis。配置后所有 Worker 必须一致；失协调停止执行，不能降级为各进程独立限额；抢占在旧执行者确认退出前仍占槽。PG 部分、故障注入及隔离真实 Redis 联动已验证；多服务进程/切主/网络分区、生产 ACL/TLS、租户公平与供应商 RPM/TPM 仍待验收。启用方法和严格边界见 [接口契约](docs/api-contracts.md)。

0013 提供可选耐久知识导入：`create_app(..., durable_knowledge=True)` 要求 PostgreSQL/AuthPolicy，API 固定文件清单后返回 202，独立 `scripts/knowledge_worker.py` 解析。原登录会话、租约 owner/fence 和实时权限约束发布；文档/片段与逐文件结果同事务提交，源文件使用不可变托管副本，数据库提交结果不确定时保留副本而非删除可能已发布文件。刷新管理页面可只读恢复活动或最近任务，轮询串行进行，不重新提交导入。共享文件根不是对象存储；自动重试、解析沙箱、文件 GC、备份和完整原子审计尚未完成。默认 SQLite/本地执行模式未改变。

0014 增加 recovery hold：逻辑恢复在数据库恢复的同一事务写入停用保护，API 启动/请求及 Worker 领取失败关闭，恢复的旧队列不自动执行。界面明确显示恢复维护状态，不误提示修改密码。新库不默认持 hold；没有自动或租户管理端解除入口，完整授权/业务前向对账仍待完成。

0015 增加私有对象排队输入：对象模式提交时扫描/上传固定版本，Worker 可在另一文件根甚至独立进程下载解析，无需共享输入目录。旧 SQL NULL 输入保持本地行为。下载前后复查身份/租约，缺版本或缺配置不读本地/latest；输入引用也纳入备份及复合主键恢复映射，旧任务恢复后仍阻断。

0016 增加对象存储管理供应：`create_app(..., storage_policy=..., auth_policy=..., secret_store=...)` 只接受部署批准目标；管理员在知识库页面显式提供已有凭据，加密绑定租户/目标指纹/版本，配置与审计同事务。页面清空/不回显凭据，版本冲突拒绝，不自动使用环境密钥。聊天/知识 Worker 成对提供 `--storage-policy` 和 `--storage-master-key-path`，从本租户加密配置消费凭据，不传服务密钥。恢复配置默认 recovery_rebind，旧凭据拒绝，hold 与恢复对账后才可匹配新桶重新绑定；无解除 hold API。备份 CLI 独立供应见下文；KMS/TLS/IAM/自动备份/GC/解析沙箱/HA 仍待完成，详见 [存储操作](docs/object-storage-operations.md)。

- 主密钥首次加密保存供应商密钥时生成在 `data/secrets/master.key`；构造应用不读取或生成主密钥。
- 供应商 API Key 加密保存在 `data/app.db`，管理 API 不会返回明文。
- `data/secrets`、数据库和上传文件均已加入 `.gitignore`。
- 默认本机 SQLite 模式无认证；显式 PostgreSQL/AuthPolicy 模式才启用登录。服务仍默认只绑定 `127.0.0.1`，不要直接暴露到公网。
- 主密钥丢失后无法恢复已保存的供应商密钥，需要重新录入。

## 后续扩展点

最新回归（head 0021_model_cost）：后端 **344 passed / 45 skipped，402.26秒**，前端38项和构建通过。管理员内部Repository明确tenant/model价格版本/来源、pre-call固定quote、Decimal缓存分项估算及追加operator_reported声明/原子审计接通；没有默认真实报价、HTTP/费用界面或供应商自动核验。未知/partial/无价保留NULL金额，修订历史不可相加，无隐式汇兑；并发版本仅一成功、请求/receipt去重、权限/撤销/hold拒绝已验证。真实独立Worker产出固定价格费用估算，实际加密恢复四用量/费用表摘要/价格/quote/声明；34表维护封存、导入空费用表及SQL防清表联动，不改历史迁移。耐久SDK用量尝试、HTTP→Worker→Agent→模型/六工具/检索追踪、SDK隐式重试关闭、有界导出及真实Prometheus/promtool回归保留，不采集Prompt/回答/Key、不发现历史凭据，无生产OTLP出口。仍缺原子预算/软硬限额、费用HTTP/UI/指标、真实供应商usage及账单认证、更多计价/计费路径、持久平台/通知/全系统日志脱敏。41项S3和4项Redis跳过不算通过；模型路由/熔断、独立分发/抗回滚、GC/IAM/KMS/PITR/HA、生产角色/容量及整体生产化未完成，不能上线。默认SQLite仍为MVP，不自动切库/迁移/启用Worker。证据见 [验收记录](docs/production-verification.md)，操作见 [用量/费用边界](docs/model-usage-operations.md)、[可观测性](docs/observability.md)、[自动备份](docs/backup-scheduler-operations.md)和[目录恢复](docs/backup-catalog-recovery.md)。下文早期0017/0016限制为历史增量。

0018 新增显式自动逻辑备份 Worker：持久计划与唯一执行租约、续租/排空/维护封存、包认证及可选实际隔离恢复验证；恢复验证目标绑定计划指纹，恢复后禁用旧计划且保持 hold。CLI 当前只支持本地文件，默认不启用；未完成对象自动恢复、catalog/回执灾备、保留/PITR及生产权限，不代表自动灾备全部交付。见 [自动备份操作](docs/backup-scheduler-operations.md)。本节后续0017/0016描述为历史增量，当前状态以此及验收记录为准。

0017 当前head增加全库维护/写入封存基础：默认不启用，持久owner/fence/期限，30业务表SQL写保护与旧写事务提交屏障，过期不自动恢复；可信运维CLI控制，备份CLI可用明确permit封存并复查guard。恢复使旧维护permit失效、独立recovery hold保留。自动续租/排空等待/scheduler、统一错误及页面维护状态仍待完成，见 [维护门控](docs/maintenance-operations.md)。

备份增量：逻辑备份 CLI 可通过批准目标和安全 stdin 管道读取独立运维凭据（无历史/模型密钥回退）。`scripts/backup_archive.py` 增加全包认证、私有固定版本分块归档、加密索引/排他回执与确切版本取回；取回完成后可执行实际隔离数据库/对象恢复并保留 hold。自动停写调度/租约、catalog/回执灾备、自动恢复、保留/GC/PITR和生产 IAM/TLS/KMS 仍未完成，不等于自动备份已交付。操作与资源边界见 [远程备份](docs/remote-backup-operations.md)。

私有版本对象存储已有知识发布/跨实例读取和对象排队输入，0016 管理供应/Worker 入口见上文；原文/图片按确切 VersionId 与 SHA256 读取，重解析保留文档 ID，图片 HTTP 在下载前后鉴权且不返回对象 URL。默认本地模式不变。函数级对象备份/恢复已接通：快照确切版本加密归档、不同空私有桶恢复与引用原子映射，保留 recovery hold 与配置 rebind。未配置 store 的备份 CLI 仍拒绝对象引用、不读旧缓存。旧 NULL 排队输入仍需共享根；备份安全供应、迁移/GC/自动调度、远程备份包/异地/PITR未完成。边界见 [对象存储操作说明](docs/object-storage-operations.md)及[逻辑备份](docs/logical-backup-operations.md)。

- 多 Agent：在 `ChatOrchestrator` 与 PydanticAI Agent 之间增加意图路由，不改变 ModelGateway 和业务工具。
- 文档类型：已有扫描件 OCR、Excel、网页等解析；新增类型继续统一输出结构化内容和位置元数据，再进入检索索引。
- 真实工具：将模拟订单服务替换为有权限控制、审计和幂等性的业务 API。
- 复杂流程：真正退款或取消订单时使用 PydanticAI Deferred Tools；出现跨天、多节点流程后再评估 LangGraph 或耐久执行平台。

## 生产化计划（实施中，尚未完成）

已增加 [加密逻辑备份与隔离恢复](docs/logical-backup-operations.md)及[私有远程归档/取回](docs/remote-backup-operations.md)：同一 PostgreSQL 快照的数据库/文件清单、AES-GCM、真实 pg_dump/pg_restore 与恢复校验，备份包确切版本远程归档/取回。数据库入口仍只支持显式 loopback/离线维护，工具不启动服务；自动停写调度、自动恢复/WAL/PITR/KMS、生产 TLS/IAM 和恢复后前向对账仍未完成，不能据此上线。

当前仍为默认 SQLite 的本地单实例 MVP。显式 PostgreSQL 应用工厂已通过隔离集成验收，Redis 共享准入已有可选实现但真实服务验收未完成；向量检索、自动备份和监控平台尚未接入。以下文档包含目标设计，目标命令须等对应工具与环境实现后使用。

| 文档 | 内容 |
| --- | --- |
| [总体架构](docs/production-architecture.md) | 组件职责、多实例协调、模型熔断与供应商降级、生产目标 |
| [分阶段实施清单](docs/production-roadmap.md) | S0–S7 依赖、交付物、任务与验收证据 |
| [PostgreSQL 迁移与备份](docs/postgresql-migration.md) | 全表映射、Alembic、停写切换、WAL/PITR、自动恢复验证 |
| [可观测性规范](docs/observability.md) | 日志/Trace 脱敏、指标、告警、用量账本与费用预算 |
| [业务工具安全规范](docs/business-tool-security.md) | 可信身份、资源归属、审批、幂等、审计、Outbox 和未知结果对账 |
| [RAG 升级方案](docs/rag-upgrade.md) | pgvector、关键词融合、Embedding 版本、Reranker、证据门控与灰度 |
| [部署与灾难恢复 Runbook](docs/deployment-dr-runbook.md) | 发布、回滚、实例/数据库/Redis/供应商故障及恢复演练 |

推荐先完成身份/租户边界与 PostgreSQL 数据底座，随后接入观测和 Redis 协调，再开放经过沙箱验收的真实工具、模型降级和混合 RAG，最后执行发布/灾备验收。DeepSeek 保持默认；备用在线模型密钥仍由用户在管理界面提供。本地模型、Temporal、Qdrant 按能力与实测需要接入。

当前实现参考：[系统架构](docs/architecture.md)、[本地服务运行](docs/service-operations.md)、[现有 API 契约](docs/api-contracts.md)、[检索与编排评测](docs/evaluation.md)。

生产化实施已开始：已有 PostgreSQL/Alembic 基线及 0002–0017 迁移、隔离快照迁移工具、SQLAlchemy Repository、租户/所有者访问控制、显式认证 API、前端登录、PG Run 租约/取消与可选独立聊天 Worker/事件账本/终态快照/共享准入、知识 Worker/对象输入/发布 fencing、加密本地及对象逻辑备份与恢复 hold、对象管理供应/rebind、远程加密备份归档/取回和维护封存基础。旧数据进入停用 legacy 租户；新租户显式初始化未配置 DeepSeek 和默认 Prompt，不复制历史密钥。认证模式的 HTTP、Agent 与数据层身份已贯通，登录预算及撤销通过真实 PG 验证；前端角色隐藏只是展示控制，权限始终由后端判断。默认仍为 SQLite；RLS、旧数据授权认领、完整原子审计、全局协调完整验收、正式备份角色供应/自动备份灾备及多数 S2–S7 能力尚未完成，不能直接上线。本次0017对象与浏览器复验缺口见 [实施与验收记录](docs/production-verification.md)。
