# 生产日志、指标、告警与费用规范

状态：部分基础能力已实现；生产监控平台、真实值班接收人、完整费用路径和正式多机验收尚未完成。已有健康探针、运行/工具状态、管理审计及私有观测出口，不能据此通过完整 S2。最新 0025 共享模型熔断指标/告警见下节；更早章节的限制应按当时批次解读。

2026-10-18 熔断增量：认证 PostgreSQL 且显式启用私有 loopback metrics 时，`ModelCircuitMetrics` 以只读短事务输出 `customer_service_model_circuit_collection_success`、采集时间戳、固定三种状态数量及耐久累计打开次数；无租户、模型、用户、正文或密钥标签。失败显示 `success=0`，不能把其余零值当健康。多个 API 实例读取同一全库数据时只采一个聚合 target 或明确去重。[熔断操作](model-circuit-operations.md)和[告警规则](../ops/prometheus/model-circuit-alerts.yml)记录阈值与处置；真实 promtool 解析/场景评估 **2 passed**，隔离 Prometheus→Alertmanager→回环 webhook 的 firing/resolved 两场景 **2 passed**，真实 PostgreSQL 熔断/预算指标及认证回环抓取联合 **12 passed**。没有正式平台、真实值班接收人、TLS/ACL、双机切主或通知升级链，不勾选完整 S2。

## HTTP 观测接入与边界

2026-10-10预算增量：显式配置认证PostgreSQL与私有loopback metrics listener后，`ModelBudgetMetrics`从PG耐久证据以只读短事务导出固定低基数series：model_budget_collection_success、collection_timestamp_seconds、policies、reservations_total、soft_threshold_events_total{scope=tenant|day|run}、rejections_total{reason=exceeded|unavailable}、unresolved_tenants。Counter由不可变或终态证据聚合，API进程重启不清零；所有值为全库聚合，多个API实例采同一库时不能相加。采集失败返回success=0且时间戳为0，不泄漏数据库异常；无租户/用户/Run/模型/币种/金额标签或值。规则与真实promtool合成验证见[预算告警](../ops/prometheus/model-budget-alerts.yml)及[预算操作](model-budget-operations.md)。生产collector、Alertmanager接收端、送达及容量仍待验收。下文0022“尚未导出指标”仅为当时历史状态。

0022预算后续：SDK请求在数据库事务中串行检查并持久预留，按tenant/UTC日/Run金额及Run次数拒绝；reservation保存tenant_soft/day_soft/run_soft。软标志是耐久内部证据，当前尚未导出指标、配置Alertmanager或送达通知，不宣称软告警全部完成。预算错误只用固定code/message，不输出模型合同、金额、密钥、用户输入或原始异常。未知占用/保守永久预留与供应商限额边界见 [预算操作](model-budget-operations.md)。

`app/http_observability.py` 通过最外层纯 ASGI 包装观测 HTTP，包括 ServerErrorMiddleware 产生的 500、鉴权拒绝和恢复/维护门控。不缓存或读取请求/响应正文，不改变 SSE 帧，不把返回响应头误认为流已经结束。每请求生成新的服务端 UUID，写入 request.state.request_id、ContextVar 和响应 X-Request-ID；结束/异常/取消后恢复上下文。客户端编号即使合法也不被信任，不影响请求体业务幂等键。

默认只维护进程内聚合指标，日志显式启用：

```python
from app.http_observability import HttpTelemetry, json_log_sink
from app.main import create_app

telemetry = HttpTelemetry(sink=json_log_sink)
app = create_app(http_telemetry=telemetry)  # 其余生产依赖仍需显式配置
```

运维需为 customer_service.http 配置专用 JSON 消息输出和有界、非阻塞 QueueHandler；示例不是完整生产部署。sink 异常不改变客服结果，计入 http_log_sink_failures_total；队列满丢弃需队列适配器另行计数，当前计数不证明成功投递。禁止 sink 执行同步网络请求。默认 Python logger 未配置 INFO handler 时不会实际输出，不能把启用 sink 当作落盘成功。

2026-10-13 新增可选 `app/log_export.py` 的 `BoundedJsonLogSink`，可由部署方显式注入 `HttpTelemetry(sink=...)`，不自动打开或读任何历史日志配置。构造时必须提供输出函数、有界队列容量和 UTF-8 单行上限；请求线程仅校验固定 JSON 字段并 `put_nowait`，单独 daemon 线程调用输出函数。它只接受 middleware 产生的 UTC 时间、固定服务/事件/级别、UUID request_id、注册 route 模板、固定方法/状态/outcome、stream、非负时长以及可选合法 trace_id；额外字段、查询串、未认证字段和超大记录拒绝。队列满/关闭丢弃、写入异常、拒绝和已调用 writer 的记录分别计数；不记录 writer 异常或正文。`flush`/`close` 有界返回值，卡住的输出函数不能被 Python 强杀，也不等于远端或磁盘已持久化。

私有 metrics 在显式注入该 sink 时新增 `customer_service_http_log_records_total{outcome=written|failed|dropped|rejected}`、`customer_service_http_log_queue_depth` 和 `customer_service_http_log_export_worker_alive`，无用户/租户/路径实参标签。规则见 [HTTP 日志告警](../ops/prometheus/http-log-alerts.yml)：缺出口/采集失败、丢失或持续积压会告警；真实 promtool 检查与合成评估已通过。部署方仍须配置受控 writer、文件/平台保留期、访问权限与收集器，并实际验证接收端送达和恢复通知；`written` 仅表示输出函数返回。旧段落的“需另做 QueueHandler/队列丢弃计数”是该增量之前的状态，不表示现有默认服务已经启用日志出口。

日志仅含 UTC timestamp、level、service、event、request_id、注册 route 模板、固定 method、status、outcome、stream、duration_ms。不记录路径实参、查询串、任何头、正文、原始异常、Prompt 或文档。尚未补齐 instance/environment/release、Trace/Run/attempt 和授权身份关联。Uvicorn/反向代理/其他模块的日志不经过该白名单；生产入口必须禁用原始 access log，或另行实现等效脱敏，不能宣称全系统日志已安全。

`app.state.http_telemetry.snapshot()` 返回只读副本，`prometheus()` 返回 LF 结尾的 Prometheus 文本。没有新增公开 /metrics 路由；后续已提供下面的独立回环认证监听器，默认关闭。指标重启重置，Prometheus 应按实例识别 counter reset，不能将此作为耐久审计或账本。

指标前缀 customer_service_：http_requests_total 记录所有终结观测；http_request_duration_seconds 和 http_sse_connection_duration_seconds 分开直方图；http_log_sink_failures_total 计日志 sink 异常。标签固定 method/status_class/outcome，route 仅已注册模板，未知路由统一 __unmatched__；请求编号不进入标签。直方图单位秒、累计桶含 +Inf。SSE 时长含连接等待，到 ASGI 结束为止，不等于模型耗时或首 token 延迟；完整非 SSE 响应后的应用收尾也计入该时长。outcome 区分 completed/error/disconnected/cancelled/incomplete，status_class 保留已发送状态：流途中失败可能仍为 2xx/error，不据此算成功。观察到 disconnect 不证明用户主动取消。

测试覆盖合成敏感标记、未知路径/方法低基数、调用方编号注入、未处理 500、sink 故障、并发 ContextVar、SSE 等待/结束、取消/断连/发送错误，并用真实 promtool 验证文本。耗时使用高精度单调 perf_counter；Windows 短 sleep 可能提前唤醒，测试用实际经过时间而非假定 sleep 精度。这些能力尚无 OTel、运行/供应商/工具/RAG spans、部署后的采集/通知或费用预算，不勾选完整 S2；本批没有新增浏览器验收。

## 私有采集监听器

`app/private_metrics.py` 的 MetricsPolicy 需显式提供专用 bytes token（43–256位base64url安全字符），建议运维生成至少32随机字节编码后供应；语法校验不证明熵。token不进入repr，无凭据发现、不读取环境/历史文件、不复用客服登录、CSRF或模型密钥。示例中的变量由明确批准的配置供应方提供，不是自动读取已有密钥：

```python
from app.private_metrics import MetricsPolicy

app = create_app(
    http_telemetry=telemetry,
    metrics_policy=MetricsPolicy(token=operator_supplied_metrics_token, port=9107),
    # 其余已批准的 Repository/auth/storage 配置在此显式注入
)
```

仅在应用lifespan启动时绑定独立127.0.0.1端口；初始化不监听，None默认关闭。port=0是隔离测试用途，生产每实例分配稳定独立端口。占用端口失败不抢占、不SO_REUSEADDR；应用启动不宣称成功，退出关闭自己的监听并清空内部private_metrics_port。该监听器不抢占主API进程信号；独立Config不运行Uvicorn全局日志配置，私有H11禁用访问/原始协议日志，不清空主服务access handler或改变其logger级别。proxy_headers关闭、ws=none，不挂到业务router、不进OpenAPI、不依赖业务登录或数据库健康；恢复/维护hold不屏蔽观测。

只允许直接loopback peer、单一正确Host、单一专用Bearer header；恒定时间比较有效长度凭据，拒绝Origin/Referer/Sec-Fetch-Site浏览器请求、伪造远端peer、重复Authorization和错误Host，不信任X-Forwarded-*。仅GET /metrics且无query，其他路径/方法拒绝；WebSocket关闭，不构成备用通道。不读取正文，所有响应no-store/nosniff、无CORS。失败只返回固定错误文本，无凭据/异常/部分成功数据。采集请求不进入业务HTTP指标，避免反馈放大。

配置例见 [Prometheus私有HTTP采集](../ops/prometheus/private-http.example.yml)。credentials_file须运维独立挂载、最小文件权限，内容不提交到Git；按实例配置target，不能把同一固定端口分配给多个同宿主进程。collector必须同宿主或共享network namespace（普通不同容器的127.0.0.1并不互通）；没有修改绑定到公网来绕过隔离。当前只回环HTTP，无远程TLS/mTLS或跨宿主采集；不得把端口反代/转发至公网。Prometheus自身查询端点也须部署侧保护，测试中的loopback不是生产访问控制证明。

真实Prometheus3.5.0隔离进程已通过正确凭据up=1、错误凭据up=0、实际Counter抓取/查询，真实promtool解析HTTP结果和示例语法；测试后确切子进程终止、监听端口可重新绑定。并非持久监控平台/告警通知部署，未部署receiver或生产collector。下述后续实现已替换同步格式化，但生产容量/CPU预算、token受控轮换、多实例部署、链路/Worker观测和告警演练仍缺；不以单采集成功判定整个S2完成。

### 有界异步导出（2026-10-08）

`app/metrics_export.py` 为每个私有监听器懒建一个专用ThreadPoolExecutor工作线程，最多一个未结束format任务；不使用共享默认executor排队采集。有效请求才提交，其他并发采集立即503。调用者超时/取消使用shield，不释放实际future占位；原任务仍在运行时后续请求不再次提交，只有future真实完成才允许新快照。无成功快照缓存，不返回过期/部分数据冒充当前健康。

MetricsPolicy.export_timeout默认1秒，显式允许0.05–5秒；max_export_bytes默认4MiB，允许1KiB–16MiB。HttpTelemetry.max_series默认4096、允许1–16384：到上限后新组合的样本拒绝，已有序列仍更新，日志sink仍执行，拒绝数单独计数。不把拒绝样本当已统计成功；出现丢弃时HTTP指标/SLO覆盖不完整，生产需配置告警和校准容量，不能为降低错误率而忽略丢弃。

内置prometheus格式化逐行检查perf_counter deadline、关闭Event和UTF-8字节预算；超限整次失败不发布部分指标。上限控制已保留序列和格式化输出，快照及最终编码仍有有界额外副本，并非4MiB总进程内存保证。shutdown停止接受任务、发出协作取消、有限等待并关闭executor。Python不能强制杀死运行线程：只有内置有界、无I/O、协作取消的renderer受此生命周期保证；不批准任意插件/网络I/O替换renderer。不合作的注入实现可能在关闭预算后继续占用一个线程，不能把timeout当该线程已结束，也不自动重建renderer来扩大资源。

新增customer_service_http_metric_samples_dropped_total计序列上限拒绝的样本；customer_service_metrics_exports_total仅固定outcome：busy/timeout/failure/oversize/cancelled/success/closed。成功导出快照包含采样时已有计数，本次success在返回前递增，通常于下次采集可见。HTTP 503表示采集不可用，Prometheus up=0，不把上一次成功当本次成功；原始格式化异常与敏感内容不入响应。采集本身仍不进入业务HTTP Counter。

隔离测试覆盖30并发busy、20次超时/取消后不重复提交、关闭时协作取消、坏格式化安全失败/后续恢复、1024序列/1200样本的有界输出与176拒绝、UTF-8精确字节上限、deadline/cancel、真实Prometheus/promtool重新验证。测试仅证明上述合成边界，不证明生产峰值、所有租户容量或完整平台/OTel/费用预算验收。

备份观测增量：已增加只读目录新鲜度检查/固定低基数Prometheus输出及告警规则与promtool测试，用源snapshot_at而非finished_at，区分local/remote/restore，采集故障不发布历史健康。规则覆盖缺失/失败/停更、恢复点过期、needs_review/失租及整体保护失败。仍未安装Collector/Prometheus/Alertmanager、部署receiver或验证实际通知，不把此增量视为全套观测完成。操作和验证限制见 [自动备份监控](backup-scheduler-operations.md)。

## 职责与原理

日志说明单次事件，指标描述时间趋势，Trace 连接一次请求的各个阶段，费用账本记录计量事实。四者共享 request_id/run_id/attempt_id，可从仪表盘定位慢调用，再检查脱敏日志。审计承担操作责任追踪，不能用可采样的运行日志代替。

建议通过 OTel Collector 输出 Prometheus 指标、Loki 日志与 Tempo traces，Grafana 展示，Alertmanager 通知。生产也可接入等价托管平台，字段与指标语义保持一致。

## 日志规范

结构化 JSON：timestamp（UTC）、level、service、instance、environment、release、event、request_id、trace_id、run_id、attempt_id、tenant_id、actor_id、tool_call_id、tool_name、provider、model、duration_ms、status、error_code。身份字段仅内部授权访问，公开响应只提供可查询的错误/请求编号。

默认不记录正文、Prompt、文档、电话、邮箱、地址、Authorization、密钥、供应商原始异常 payload。异常堆栈经敏感字段过滤；HTTP 当前直接忽略用户传入 X-Request-ID，始终生成服务端编号，后续可信 trace 同样由服务端建立，防止日志注入。

可选调试采样需脱敏、短保留期和明确授权；所有密钥字段永不采样。内部备注不进入模型 Trace。导出和看板同样执行租户权限。建议运行日志/Trace 30 天，聚合指标 90 天，业务审计与费用保留期另按政策确认。

## 链路规范

### 0019 已接通的聊天链路边界

`app/tracing.py` 提供显式SafeTracing实例，不设置全局OTel provider，不启用PydanticAI自动内容采集、不发现collector地址/凭据。production extra锁定opentelemetry-api/sdk 1.44.0。调用方明确注入SpanExporter后，私有provider使用固定service资源、ParentBased(ALWAYS_ON)和有界SpanLimits；此阶段采样为新根全采样、继承内部parent标志，不是生产采样/尾采样策略验收。

2026-10-11 增加 `app/otlp_export.py` 作为**可选** OTLP/HTTP 出口：`PrivateOtlpTracing('http://127.0.0.1:4318/v1/traces')` 可显式注入 `create_app(tracing=...)`；独立聊天 Worker 可显式使用 `--otlp-traces-endpoint`。端点必须有确定端口和 `/v1/traces` 路径，明文仅允许 IP 回环，非回环要求 HTTPS；拒绝 URL 内凭据、查询、片段以及存在的继承 OTLP 环境配置。HTTP session 禁用环境代理，固定 1 秒出口超时和无压缩；不复用模型密钥或自动连接平台。创建者须在 API/Worker 停止后 flush/close，不可在 exporter 仍阻塞时宣称远端已接收。隔离本机 HTTP 接收端已验证真实 protobuf 传输和仅白名单 Span 属性；这**不是**生产 Collector、Tempo、权限/证书、采样、日志留存或通知送达验收。API 正式启动装配、知识 Worker 和数据库/对象 Span 仍未接通，S2 不勾选。

后续数据库追踪增量：`install_database_tracing(engine)` 在 API 与独立聊天/知识 Worker 显式启用追踪时安装一次 SQLAlchemy cursor 监听器。已有私有父 Span 下的成功/DBAPI 失败生成 `db.query`，仅继承合法 request_id/run_id/job_id/attempt_id，并添加固定 outcome；不读取或导出 SQL 文本、绑定参数、表名、连接 URL、原始异常或结果。无追踪上下文的数据库调用保持无 Span；不覆盖连接建立、连接池等待或 ORM 内部计算。真实 PostgreSQL API→队列→独立聊天 Worker 验证同 trace/parent 与无敏感属性，合并专项 36 项通过。上段“数据库 Span 未接通”及本段原先“知识 Worker 跨进程数据库/对象子 Span 待验收”均为历史限制：知识 Worker 数据库子 Span 已在 0023 的 56 项专项覆盖，对象子 Span 由 2026-10-14 的 35 项隔离联合回归覆盖。Embedding/Reranker Span、正式 Collector 和采样/容量仍缺，S2 不勾选。

2026-10-13 知识 Worker 增量：`scripts/knowledge_worker.py` 可显式提供 `--otlp-traces-endpoint`，调用方拥有 flush/close；不提供时默认不追踪。已领取的每个任务在 `worker.knowledge` CONSUMER Span 内执行，属性仅为内部 UUID `job_id`、可信服务端 `request_id`（有 carrier 时）和固定 outcome；SQLAlchemy 监听器只在活动上下文中产生内容为空的 `db.query` 子 Span，S3 客户端的固定操作可产生 `object.request` 子 Span。0023 在 `knowledge_job_queue` 添加可空、SQL 约束的内部 Trace carrier，与聊天队列使用相同白名单；API 入队事务保存活动 HTTP root，Worker 重新授权并领取后以该 carrier 为 parent。旧 NULL 任务仍独立起根，不伪造关联。真实隔离 PostgreSQL 与本机 OTLP/HTTP 接收端验证认证 API→独立知识 Worker 的 trace_id/parent、数据库子 Span、无公开 carrier/敏感正文；相关回归 **56 passed / 0 skipped**。对象操作的真实跨进程导出由下一批隔离测试补充；旧段落的“知识 Worker 未接通”应按其历史批次理解，不能据此声称完整 S2 已完成。

2026-10-14 对象链路验收增量：隔离 PostgreSQL、回环 SeaweedFS 与回环 OTLP 接收端中，另一 Python 进程运行知识 Worker，实际 `get/put/check_ready` 对象请求导出与 `worker.knowledge` 同 Trace 的 `object.request`；核对 `job_id`、父子关系和合成敏感内容不外泄。该测试以显式合成 `http.request` 根 Span 提交真实任务，不单独验收真实 HTTP API 入口；真实认证 HTTP 入队链路由上段 56 项专项证明。相关四组联合 **35 passed / 0 skipped**，不与 56 项相加。它不证明生产 S3 IAM/TLS/KMS、Collector 权限/证书、平台留存、告警送达或采样容量，完整 S2 仍未通过。

```python
from app.tracing import SafeTracing

tracing = SafeTracing(operator_approved_exporter, capacity=256)
app = create_app(tracing=tracing)  # 示例仅追踪注入，其他生产依赖仍须显式供应
# 同进程组件可共用；所有组件停止后由创建者有限flush/close。
```

当前注册 http.request、worker.chat/worker.knowledge（CONSUMER）、agent.run、tool.execute、rag.search、model.request、db.query、object.request 等固定 Span。HTTP在最外层ASGI创建新的可信根，忽略客户端traceparent/tracestate/baggage，不读正文/原始路径实参/查询/认证头。保留服务端request_id、方法/注册route模板、状态/终结类别/stream；JSON日志可带trace_id，指标标签不带编号。原始异常/堆栈/描述/事件、Prompt/回答/文档、用户身份均不采集；processor拒绝未知Span名/属性，以及非法UUID、方法、状态、outcome、route值或异常描述。这里的白名单不覆盖代理/服务器/外部全局OTel provider，不能宣称全系统隐私已验收。

2026-10-21 实验性混合检索增量：白名单增加 `rag.embed_query`、`rag.vector`、`rag.fuse`、`rag.rerank` 四个固定子 Span，`rag.stage` 只接受对应固定值；不记录查询、文档、向量、模型输出或租户标识。真实 PydanticAI 工具原有 `rag.search` 保持父 Span，纯合成管线专项验证五个 Span 均导出且无敏感正文，相关追踪/检索专项 **12 passed / 0 skipped**。这仅提供阶段耗时定位，不是 Embedding/Reranker Token 用量账本、私有 Collector 实际送达或正式延迟 SLO 验收。

模型后续增量：ModelGateway.build_model返回PydanticAI WrapperModel，ChatOrchestrator对替代/合成gateway同样包装，已包装对象幂等复用，不产生重复Span。普通request与request_stream每调用独立内部attempt_id，固定model.provider（deepseek/openai/local/synthetic/unknown）和model.stream布尔值，继承request_id/run_id。provider是配置路由标签，不证明真实供应商网络执行；不读取或导出model_name/base_url/API Key/provider_response_id/provider_details/任意usage.details。模型与返回对象/事件/用量保持原样交给SDK；没有自建缓存、重试或降级。普通响应complete及流式自然EOF记completed；公开SDK生命周期incomplete/suspended记incomplete，interrupted或任务取消记cancelled，读取/清理异常记error，Scope保留到供应商上下文退出完成。

S5前置错误分类只在安全客服失败日志中加入固定`failure_kind`：rate_limited、provider_unavailable、authentication、billing、request_rejected、transport或unknown；工具失败标为tool。分类不记录供应商body、响应头、异常文本、URL或密钥，`retryable`不表示可以重放整次Agent/写工具。尚无分类聚合指标、共享熔断或实际降级。

model.request覆盖一次PydanticAI SDK请求，不保证等于一次底层HTTP尝试：客户端既有隐式重试仍可能在一个Span内。真实工具循环、回答校验重试会建立不同SDK请求Span。Span里的attempt_id只是追踪编号，尚无耐久用量记录/价格版本/unknown费用/并发预算，不能把SDK默认零Token当作零费用。管理界面连接测试仍使用独立原有HTTP路径，暂不在该wrapper覆盖；计费前置预留时必须同时接通该路径及count_tokens/compact等潜在计费调用，统一底层SDK重试。本段描述的是当时的阶段边界：后续已有耐久用量/价格/预算基础、数据库/对象/知识 Worker 隔离 Span 证据与可选 OTLP 出口；当前仍缺正式生产出口装配、Embedding/Reranker、完整计费路径和平台告警验收，完整S2保持未验收。

工具后续增量：六个现有客服工具使用保留函数签名与同步/异步模式的装饰器，在PydanticAI真实工具执行边界建立tool.execute；固定tool.name白名单，每次调用生成内部UUID attempt_id，继承request_id/run_id。同步工具线程继承上下文；search_knowledge先复查身份，再围绕knowledge.search建立rag.search（固定rag.stage=retrieve），沿用父工具attempt_id。完成、异常、async取消分别记录固定outcome，任何异常文本/工具参数/返回值/查询/文档均不进入Span。completed仅说明函数返回，不代表业务订单找到、审批成功或答案质量合格。关闭追踪时不启动线程、不改变工具业务结果。没有新增公开接口或数据库迁移；工具attempt_id尚未持久化为审计/费用账本，也不是外部写操作幂等键。该检索Span覆盖当前知识工具调用，不覆盖管理端搜索、Embedding、Reranker或所有数据库调用。

0019给chat_run_queue增加可空trace_context JSONB，严格只有request_id/trace_id/span_id/sampled；Python与SQL形状/类型/长度/非零编号约束，无baggage或自由属性。API捕获内部活动根后同提交事务存储，幂等重提交不改首次上下文；它不参与身份、租约、幂等或业务控制。HTTP/SSE/公开snapshot不返回此私有字段。旧NULL任务仍执行，无追踪配置默认不产生上下文。

配置追踪的Worker在重新授权并claim后，以已持久化carrier为parent建立worker.chat；agent_scope围住真实Agent执行建立子Span。可关联已经结束的202请求，排队等待不伪装成HTTP执行时长。没有carrier时Worker从新根开始，不猜旧HTTP上下文；上下文按异步任务隔离并在取消/异常/关闭路径恢复。该链路当前限聊天，不含知识导入Worker或model attempt/tool/RAG/database/object storage各阶段Span，不能据此勾选完整OTel项。

独立processor单daemon线程、最多capacity条排队Span（1–4096）加一个正在export的Span；on_end只非阻塞入队，满/停用丢弃计数，exporter失败不向客服传播或打印原始错误。有限flush返回bool，close等待最多1秒；Python不能杀死卡住的exporter，超时不等于任务结束，不重建线程扩张资源。配置供应方须使用有明确网络超时的exporter并负责在真实export完成后关闭它；当前不自动调用任意exporter.shutdown，不发现历史OTLP headers/密钥。实例由调用方所有，create_app不会擅自关闭其他组件共用的追踪器。

SafeTracing.snapshot仅含固定计数与队列/worker状态；启用追踪的HTTP私有指标增加customer_service_trace_spans_total（exported/failed/dropped/rejected/instrumentation_failed）、trace_queue_depth和trace_export_worker_alive。此处exported仅表示exporter返回SUCCESS，不证明远端耐久存储或通知到达。独立Worker的生产collector供应/CLI与指标发布仍待接通。

已通过实际认证API→PG队列→独立Python Worker→真实PydanticAI合成模型的trace_id/parent/request_id/run_id关联、SQL坏carrier拒绝、首次carrier幂等、NULL兼容、8并发上下文隔离、未采样parent、500/原始异常/正文marker不泄漏、阻塞exporter/有限queue及拒绝值计数。测试Worker的--synthetic-trace-output仅允许明确合成模式、新临时JSONL文件和cs_test_/cs_browser_数据库，不是生产Trace出口；未进行真实模型调用或生产collector/OTLP/Tempo验收。

Span 树：HTTP → 准入/排队 → Agent run → model attempt / tool / retrieval → database、object storage。检索再拆 Embedding、词法召回、向量召回、融合、重排与门控。异步 Worker 使用传播的 trace context 建立 link/parent，不把跨小时等待伪装为 HTTP 延迟。

每次切换模型创建独立 attempt span；记录路由/提示词/索引/模型版本与降级原因。默认采样少量成功 traces，尾部采样保留错误与慢调用；Span 不记录 Prompt/回答原文。Collector 故障不能阻塞客服，但有丢弃计数与告警；财务账本和业务审计仍通过事务持久化。

## 指标定义

| 指标建议名 | 类型与含义 | 标签 |
| --- | --- | --- |
| http_requests_total | Counter，完成的请求 | method、route 模板、status_class |
| http_request_duration_seconds | Histogram，响应时长；SSE 连接时长单列 | route、status_class |
| agent_runs_total | Counter，终态运行 | outcome、scene |
| agent_active_runs / agent_queue_depth | Gauge，聚合活动与等待量 | pool；不要把每实例的值误当全局 |
| agent_queue_wait_seconds | Histogram，排队到准入/拒绝 | outcome |
| model_attempts_total | Counter，含所有重试/失败 | provider、model、outcome、error_class |
| model_first_token_seconds | Histogram，从模型请求到首个可见文本 | provider、model |
| answer_first_token_seconds | Histogram，从用户提交到最终答案流首 token；另记录草稿重置 | scene、degraded |
| model_duration_seconds / model_tokens_total | Histogram/Counter，单 attempt 耗时/Token | provider、model、token_type |
| tool_operations_total | Counter，实际工具操作结果 | tool、outcome、error_class |
| tool_idempotency_hits_total | Counter，命中已有操作 | tool |
| rag_stage_duration_seconds | Histogram，检索各阶段耗时 | stage、profile |
| rag_requests_total | Counter，空召回、证据不足或可回答 | outcome、profile |
| task_queue_depth / task_lease_expired_total | Gauge/Counter，积压与失租 | task_type |
| db_pool_usage_ratio / db_replication_lag_seconds | Gauge，连接压力与复制延迟 | role |
| redis_available / backup_last_restore_verified_timestamp_seconds | Gauge，依赖健康与恢复验证新鲜度 | service、backup_type |
| model_cost_total | Counter，估算费用；账本是准确查询源 | provider、model、currency |

标签不能包含 user_id、run_id、query、订单号、完整 URL、任意异常文本；这些高基数字段留在日志/账本。模型名须来自注册列表。Recall、事实错误率、引用支持率来自离线/质检结果，不从召回数量直接推断。

SLO 建议月可用性 99.5%，成功只读问答 P95 首 token ≤ 5 秒、总时长 ≤ 30 秒。分母包括排队拒绝、模型/系统失败；排除真实用户取消但单独计数，不能把全部断连当用户取消。按场景/模型区分，审批等待与导入独立计时。

## 费用账本与预算

0021新增实现：tenant/model明确价格版本及sha256来源、pre-call同事务quote、终结同事务Decimal估算或unknown、管理员追加operator_reported声明及原子审计。Quote固定版本不按事后新价重算；未知/partial/非法缓存不输出金额，不能按0计费。全历史保留，最高revision是当前声明，不能把estimate及修正相加；没有自动汇兑。后台SDK/Worker已经联动，但没有管理HTTP/UI或供应商认证账单，仍缺预算预留/软硬限额、更多计费调用、metrics与独立账单核验。本节下述费用字段/预算为完整目标，不以本批内部Repository代替完整S2；操作见 [用量与费用](model-usage-operations.md)。

0020实现更新：PostgreSQL聊天每SDK请求先提交started/unknown，再调用模型，终结observed/partial/unknown和Token；相同attempt_id写私有Trace，无Trace仍入账。SDK缺省零或非法用量保留NULL unknown；SDK正数（可能估算）不视为已核对供应商账单，没有价格或金额。写失败/失租/撤权不返回持久成功，不自动重试，未结算started保留。终结行SQL不可改写/删除，新增表进入31表维护封存、SQLite导入空账本门控和实际备份恢复。网关兼容SDK关闭隐式重试，内存500测试确认一次HTTP；替代/合成gateway不能据此宣称全部外部客户端都关闭重试。前述0019“attempt仅在Trace”和“网关隐式重试仍启用”属于历史限制，此处更新；管理端连接测试/count_tokens/compact/Embedding/Rerank、版本价格、费用对账和预算仍未实现。详见 [用量操作边界](model-usage-operations.md)。下面账本/预算字段是完整目标，不以本批表代替全部费用系统。

每次 attempt 写 usage_ledger：唯一计量键、tenant_id、run_id、attempt_id、purpose（chat/embedding/rerank）、provider/model、输入/输出/缓存 Token、请求计费单位、usage_source、pricing_version、币种、估算金额、状态、时间。重试与失败可能计费，全部保留。

价格表保存生效区间、来源和缓存计费规则；用 Decimal，避免浮点累积误差。供应商没有返回 usage 时记 unknown，不能按 0 计费。估算与实际账单分别保存，异步对账补齐；取消后用量可能不完整。Embedding、Rerank、OCR/本地 GPU 成本与在线 Token 费用分项展示，本地成本按资源估算，不能声称免费。

Run 开始前原子预留预算，执行中根据实际用量扣账/释放余额；设置 max_requests/max_tokens/最长运行和工具调用次数。按 Run/租户/日设置预算，80% 软告警，100% 拒绝新计费调用；已批准写操作收尾与对账不能因模型预算耗尽被遗弃。预算记录在 PostgreSQL，Redis 可缓存但不做唯一账本。

## 告警初始规则

| 触发条件（待实测校准） | 等级 | 处理入口 |
| --- | --- | --- |
| 5 分钟错误率 > 5%，至少 20 次请求，持续 5 分钟 | 高 | Runbook 服务故障 |
| 模型超时率 > 3%，至少 20 次请求，持续 10 分钟 | 中 | 路由/供应商状态 |
| P95 总回答 > 30 秒且有足够样本，持续 10 分钟 | 中 | 查 Trace 各阶段 |
| PostgreSQL 不可用或主角色冲突 | 高 | 数据库故障/封禁写入 |
| 池使用 > 80% 持续 10 分钟 | 中 | 连接预算/慢事务 |
| Redis 不可用持续 1 分钟、Worker 积压超 SLA | 高 | 协调故障 |
| 工具 unknown 超过对账 SLA，或重复副作用 | 高 | 写工具封禁与对账 |
| WAL 恢复时间落后 > 5 分钟，备份或恢复验证失败 | 高 | 备份/恢复检查 |
| 主模型熔断或全部候选不可用 | 中/高 | 模型降级 |
| 日费用 80% / 100%，未知费用异常增长 | 中/高 | 预算限制与账单核对 |

每条规则包含 owner、服务/环境、去重键、Runbook 链接、影响、触发/恢复通知与静默到期时间。高等级通知值班，中等级进入待处理队列；渠道由项目负责人配置，本次不发送消息。告警断路演练使用测试 receiver，避免通知真实客户。

## 验收

在隔离环境注入模型 429/超时、数据库断连、Redis 丢通知和工具未知结果，确认 Trace → 日志 → Run/操作记录可关联。向调试输入植入合成敏感标记，验证日志/metrics/Trace 无泄露；确认无高基数标签。验证告警触发与恢复、Collector 故障降级、费用重试计数、并发预算与 unknown 对账。测试报告记录样本量和实测时延，不能以 dashboard 已创建作为完成。

关联：[架构](production-architecture.md)、[实施清单](production-roadmap.md)、[工具安全](business-tool-security.md)、[Runbook](deployment-dr-runbook.md)。
