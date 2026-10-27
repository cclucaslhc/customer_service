# 公共接口契约

2026-10-26 耐久完成事件修正：queued成功提交时，同事务写入answer_reset→完整权威正文token分块（每块至多8192字符）→citation/contact_form→done与终态快照，done仍最后；这避免缓冲知识正文在提交后被Worker终态门控丢弃，也避免旧草稿重复拼接。单事件/总账本预算不变，失败整体回滚。direct非耐久流不变。实际浏览器两轮及刷新已验，测试预认证夹具不计正式登录/HTTPS验收，见[浏览器证据](rag-browser-acceptance.md)。

2026-10-25 灰度运行链路（schema0036）：没有新增公开审批/控制HTTP入口或客户端index选择参数。配置本地向量网关的PostgreSQL运行器在授权claim/start后固定内部路由，检索/提交均复查；暂停或路由/模型/Prompt绑定变化沿用knowledge_changed终止语义，已检索草稿在持久化成功前不发送最终token。灰度路由轮次不自动跨模型故障切换；普通非灰度路由策略不变。合成Worker/SSE事件账本测试通过，真实浏览器验收仍待补齐。

RAG 运维库接口（2026-10-25）：`RagIndexAdmin.activate` 必须提供 `expected_active_index_id`（首次 None，后续旧 UUID），省略或预期不匹配拒绝；CLI 原有 `--expect-no-active-index` / `--expected-active-index-id` 合同不变。stage/stage-existing/seal/activate/非空 prune 的状态变更与运维审计同事务。审计归控制域、actor 为数据库角色、target_tenant_id 为明确目标租户，不把 owner 冒充租户用户；不新增 HTTP/SSE 或向普通租户暴露运维记录。详见 [RAG 发布约定](rag-upgrade.md)。

本文档记录前端、后端、Agent、知识库之间的接口事实。变更按当前会话的用户授权执行，并同步所有受影响调用方和测试；不要求总控会话或模块交接。

## 通用约定

- API 前缀：`/api`。
- JSON 使用 UTF-8，字段名使用 `snake_case`。
- 时间在数据库中使用 UTC ISO 8601；前端按 `Asia/Shanghai` 展示。
- 错误响应使用 FastAPI `detail`；SSE 错误使用 `{code, message}`。
- API Key 是只写字段，任何 GET、日志或 SSE 都不得返回明文。
- SQLite MVP 管理模式仍只允许绑定 `127.0.0.1`；认证 PostgreSQL 模式已有登录与 `config.manage` 后端授权。正式公网部署、代理与双机验收尚未完成。

## 聊天 API

### `POST /api/chat/sessions`

分配一个待使用的会话 UUID，但不立即写入数据库。只有首条消息通过模型配置检查并进入处理流程时，后端才原子写入会话、用户消息和运行记录；页面加载、点击“新会话”、模型未配置或发送中断均不得留下空会话。

```json
{
  "id": "uuid",
  "title": "新会话",
  "status": "idle",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

首条用户消息写入时，后端使用该消息前 36 个字符自动生成会话标题。

### `GET /api/chat/sessions?scope=active&limit=50`

按 `updated_at` 倒序返回包含有效消息的会话，`scope` 可取 `active` 或 `archived`，`limit` 范围为 1–100。软删除会话不在用户端列表中。每项包含：

```text
id, title, status, archived_at, deleted_at, message_count, last_message, created_at, updated_at
```

### `GET /api/chat/sessions/{session_id}`

返回会话及 `status=complete` 的有效消息。消息中的知识引用使用 `citations` 数组，不暴露数据库内部的 `citations_json` 字段。

### `PUT /api/chat/sessions/{session_id}/messages/{message_id}/feedback`

仅允许评价该会话中的 AI 客服回答。请求 `{ "rating": "up" }` 或
`{ "rating": "down" }` 设置评价，传 `{ "rating": null }` 取消；用户消息、
其他会话消息和软删除会话返回 `404`。历史消息通过 `feedback` 字段恢复评价状态。

### 会话生命周期接口

| 接口 | 含义 |
| --- | --- |
| `PATCH /api/chat/sessions/{id}` | 使用 `{ "title": "新标题" }` 重命名，标题为 1–80 个字符 |
| `POST /api/chat/sessions/{id}/archive` | 归档会话；正在运行的回答会先取消 |
| `POST /api/chat/sessions/{id}/restore` | 清除归档和软删除状态，恢复为当前会话 |
| `DELETE /api/chat/sessions/{id}` | 安全删除，只设置 `deleted_at`，不清除消息、运行或联系记录 |

归档会话继续发送消息返回 `409`；软删除会话对普通详情接口表现为 `404`。

### `POST /api/chat/sessions/{session_id}/messages`

请求：

```json
{"content": "用户问题"}
```

响应类型：`text/event-stream`。

### `POST /api/chat/sessions/{session_id}/runs/{run_id}/cancel`

返回：

```json
{"cancelled": true}
```

不存在或已结束的运行返回 `cancelled=false`，不视为服务器错误。

## SSE 事件

每个 frame 使用 `event:` 和 JSON `data:`。

| 事件 | 数据字段 | 含义 |
| --- | --- | --- |
| `run_started` | `run_id` | 本次运行已建立 |
| `provider` | `provider`, `model` | 初始为主模型；认证 PostgreSQL 请求级切换成功后可再次发送实际备用模型。前端以最后一帧为准，不能将首帧当作最终回答供应商。 |
| `ping` | `timestamp` | 供应商暂时无输出时的 SSE 保活心跳；前端无需展示 |
| `token` | `text` | 增量回答文本 |
| `answer_reset` | 无 | 清除工具调用前、校验失败或终态失败时未提交的临时文本 |
| `tool_start` | `tool` | Agent 开始执行工具；不得包含工具参数或敏感数据 |
| `tool_end` | `tool` | Agent 工具执行完成；不得包含原始工具结果 |
| `citation` | `document_id`, `document`, `section`, `location`, `excerpt` | 一条本地知识引用 |
| `contact_form` | `summary` | 前端应展示联系方式表单 |
| `interrupted` | `run_id` | 运行被用户或新消息取消 |
| `done` | `message_id`, `run_id` | 通过最终答案校验的回答已完整保存 |
| `error` | `code`, `message` | 本次运行失败 |

### 预算管理 API

预算管理（仅认证PostgreSQL、config.manage；默认SQLite返回409，认证响应no-store）：GET `/api/admin/model-budget` 返回`{enabled,policy,usage,soft_events}`，未发布时policy/usage为null；policy含当前版本、provider/model/currency、tenant/day/run上限、单次输入合同Token上界、输出上限、Run请求上限、软百分比及发布时间；usage含tenant/day占用、剩余额度和unresolved标志。已知金额为固定9位十进制字符串；任何旧未知或混币种证据使占用/剩余额度均为null，不能按当前币种汇总，不返回API Key、原始合同或跨租户数据。GET `/api/admin/model-prices/{provider}?model=...` 返回`{price:null|{provider,model,version,currency,input_rate,output_rate,cache_read_rate,cache_write_rate,source_sha256,published_at}}`。

PUT `/api/admin/model-prices/{provider}`及PUT `/api/admin/model-budget`需要管理员显式publication_id UUID、expected_version和价格/策略全字段；金额只接受十进制字符串，同ID同原期望版本同参数幂等，版本/参数冲突409，校验失败422。模型管理页只允许当前provider/model/currency匹配已发布价格时提交预算。依据SHA256由管理员提供，不代表系统已认证供应商合同；费用对账和受控释放仍无HTTP入口。详见[预算操作](model-budget-operations.md)。

跨在线供应商备用授权（仅认证 PostgreSQL、`config.manage`）：GET `/api/admin/model-fallback/openai` 返回 `{provider,enabled,version,state}`，默认 `{provider:"openai",enabled:false,version:0,state:"valid"}`；坏状态返回 `enabled:false,version:null,state:"invalid"`。PUT 同一路径接受 `{publication_id:UUID,expected_version:非负整数,enabled:boolean}`；启用前本租户必须已保存 OpenAI 密钥。相同发布编号与相同参数幂等，版本或参数冲突 409，未配置/非法供应商 422，非管理员 403；每次变更与租户审计同事务。启用本身不触发外发；认证 PostgreSQL 聊天的主模型遇到合格故障、无可用本机备用且目标价格/合同齐备时，备用 attempt 会在出站前再次复核当前授权。流式响应已经进入时不切换，不重放工具或整个 Agent Run。

备用模型预算合同（0024，仅认证 PostgreSQL、`config.manage`）：GET `/api/admin/model-route-budget/{provider}?model=...` 返回 `{policy:null|{route_policy_id,provider,model,version,currency,input_token_ceiling,output_token_limit,source_sha256,...}}`；PUT 同路径接受 `{publication_id:UUID,expected_version:非负整数,model,currency:"CNY"|"USD",input_token_ceiling:正整数,output_token_limit:正整数,source_sha256:64位小写十六进制}`。当前目标 provider 仅 `openai`/`local`；发布须先有同币种租户主预算，实际预留还须有目标模型价格。相同发布编号和参数幂等，冲突 409，非法输入/缺主预算 422，非管理员 403。合同和预留版本不可变；合同发布不授予跨在线供应商外发，也不启用自动故障切换。

在线供应商 `POST /api/admin/providers/{provider}/test` 只向供应商发带鉴权头的 `GET /models`，不发送聊天正文或生成请求；响应读取上限为 256 KiB，且目录必须明确列出所选模型 ID 才显示“API 连接正常且模型目录包含所选模型（生成及工具能力未验证）”。HTTP 重定向、非 200、超限、无效目录或模型缺失均为错误。该探测不能证明模型生成、工具/流式能力或真实供应商费用为零。

0022预算增量：启用PG租户预算后，普通/流式SDK调用前原子预留。`budget_exceeded` 表示tenant/UTC日/Run金额或Run请求次数达限；`budget_unavailable` 表示缺价、旧无预留未知费用、币种或请求计价面无法受控。均先发answer_reset清空草稿，再发固定安全error；Run终态error，不保存助手、不发done、不自动重试或建议更换密钥。默认无预算策略，不改变SQLite MVP。软阈值已有私有指标和合成告警规则，但无真实通知送达；财务与真实合同边界见 [预算操作](model-budget-operations.md)。

事件顺序约束：

```text
run_started → provider → (ping | token* → answer_reset? → tool_start → tool_end)* → token* → citation* → contact_form? → done
run_started → provider → (ping | token | answer_reset | tool_start | tool_end)* → interrupted
run_started → provider → (ping | token | answer_reset | tool_start | tool_end)* → answer_reset → error
error  (启动前失败；没有可见草稿)
```

空回答或只有“让我查询”等过程性文字会触发模型重试；重试耗尽后发送
`error(code=answer_validation_failed)`，不得保存完整助手消息或发送 `done`。

错误码按失败阶段区分：Agent 工具尚未返回时发生异常使用 `tool_error`，模型请求、
响应或其他非工具阶段异常使用 `model_error`。进入运行后的终态失败先发送 `answer_reset`，避免遗留未提交草稿；两者都会结束运行且不会发送 `done`；
服务端日志只记录内部运行标识、供应商/模型、工具名和异常类型，不记录用户正文、
工具参数、工具结果、供应商响应正文或密钥。

并发舱位等待超时使用 `service_busy`，并额外返回整数秒 `retry_after`；客户端应延迟并加入随机抖动后重试。单次模型运行超过服务端上限使用 `model_timeout`。这两类错误都不会发送 `done`。

## 服务健康 API

| 接口 | 含义 |
| --- | --- |
| `GET /health/live` | 仅验证 ASGI 进程可响应 |
| `GET /health/ready` | 验证数据库可用，并返回当前模型并发舱位 |
| `GET /health` | 兼容健康接口，额外返回 SQLite journal mode、主密钥状态和模型并发舱位 |

所有由应用发出的 HTTP 响应包含服务端生成的 UUID `X-Request-ID`，包括鉴权/维护拒绝和未处理异常的 500。客户端提供的同名头不被采纳或回显；它不构成可信关联编号。该响应头仅用于诊断，不是业务幂等键；消息请求体的 `request_id` 仍按原有幂等契约处理。代理或服务器在请求到达应用之前生成的错误不在此保证范围内。

运维指标不属于公开业务API。显式MetricsPolicy仅在lifespan启用独立127.0.0.1监听，GET /metrics需专用Bearer、直接回环peer和正确Host，拒绝query/浏览器Origin/重复认证/代理头冒充。业务端口不挂载该路由、不复用登录或模型密钥，默认关闭；无公开配置/停用接口。操作与限制见 [私有采集](observability.md)。

私有GET /metrics仅返回完整当前快照；单format占位、超时、字节预算/格式化失败或关闭时返回固定503，不返回旧成功快照/部分指标。默认导出1秒/4MiB、HTTP指标4096序列，批准配置可在规范的有界范围内调整；取消/超时不代表后台format已经停止，不自动提交替代任务。固定丢弃/导出结果Counter暴露覆盖缺口，无请求编号等高基数标签。

显式启用SafeTracing时，HTTP总是建立服务端新trace根，不采纳客户端traceparent/tracestate/baggage。0019聊天queue仅内部保存严格carrier，首次同事务写入、幂等重提交不改首次关联，Worker重新授权后建立CONSUMER与Agent子Span。carrier不属于任何公开响应/请求契约，不参与身份、业务幂等或fencing。NULL旧任务仍执行，默认关闭；目前没有生产Worker OTLP配置接口。边界见 [追踪规范](observability.md)。

工具与知识检索追踪沿用该私有上下文：固定工具名及每调用内部attempt_id，不公开参数/结果/查询；attempt_id不属于公开SSE或审批/幂等契约，也不替代持久业务operation_intent_id。知识工具仍先鉴权再检索，completed追踪结果只表示函数返回。

模型网关构建的Model保持PydanticAI公共Model接口，通过WrapperModel私有model.request Span关联普通/流式SDK调用；同一包装不重复建Span，不改变HTTP/SSE及模型用量。追踪attempt_id不是客户端提交编号或持久计费键，SDK底层隐式重试尚未逐次计量，管理端连接测试路径尚未纳入。没有改变模型密钥供应规则或启用新供应商/本地端点。

0020更新上述历史限制：PostgreSQL租约聊天为每SDK请求自动绑定内部ModelUsageRepository，网络调用前先提交用量attempt；相同UUID用于model.request Trace（无Trace也入账），仅内部表/Repository可见，无新增公开SSE/HTTP账本字段。pre-call失败不调用模型；post-call持久化失败不保存成功助手消息、不自动重试，started未知记录保留待后续对账。finish复查当前身份及租约，可在同owner/fence有效租约内记录已请求取消后的用量，撤权/失租/hold不绕过；终结后重复相同值幂等，冲突拒绝。网关构建的兼容SDK关闭隐式重试；原SDK返回用量保持不变，缺省零在账本为NULL unknown，不表示免费。SQLite MVP不自动创建账本；管理端连接测试、Embedding/Rerank及价格预算仍缺，操作见 [用量边界](model-usage-operations.md)。

0021新增内部ModelCostRepository：token-bound config.manage管理员发布per-model价格/追加账单声明与审计，不暴露公开HTTP/SSE字段。SDK尝试与quote同事务、结束与revision=0估算同事务；金额Decimal精确值、unknown为NULL，不能隐式float/当0或汇兑。reconcile追加operator_reported，不改用量，不意味着实际供应商核验；价格publication与reconcile request UUID绑定原参数及期望version/revision幂等，冲突拒绝，receipt SHA256租户内去重。此前“价格/追加对账仍缺”只更新内部能力；费用管理HTTP/UI、供应商核验及原子预算等仍待实现，不改变Key提供方式。

## 企业管理 API

所有管理端分页列表统一返回 `{items,total,page,page_size}`。默认本机兼容模式没有登录；显式 PostgreSQL/AuthPolicy 模式使用下文 Cookie 认证与请求级权限。HTTP 仍仅接受本机回环地址请求，不得将服务直接暴露到局域网或公网。

| 接口 | 含义 |
| --- | --- |
| `GET /api/admin/overview` | 会话、运行、完成率、错误率、联系登记、知识库和模型健康概览 |
| `GET /api/admin/chat/sessions` | 分页筛选会话；支持正文搜索、生命周期、管理状态、负责人、标签、模型、运行结果、转人工和时间范围 |
| `GET /api/admin/chat/sessions/{id}` | 完整对话、引用、运行、提示词版本、工具名称/状态、联系人、备注与审计记录 |
| `PATCH /api/admin/chat/sessions/{id}` | 更新标题、`management_status`、负责人和标签 |
| `POST /api/admin/chat/sessions/{id}/notes` | 添加内部备注 |
| `GET /api/admin/chat/sessions/{id}/export?format=markdown\|csv` | 导出会话；CSV 防止公式注入 |
| `POST .../{id}/archive`、`POST .../{id}/restore`、`DELETE .../{id}` | 管理端归档、恢复与安全删除 |
| `GET /api/admin/prompts` | 返回只读安全基线、当前发布版本和版本历史 |
| `POST /api/admin/prompts/drafts` | 创建企业提示词草稿 |
| `POST /api/admin/prompts/{id}/publish` | 原子发布指定版本 |
| `POST /api/admin/prompts/{id}/rollback` | 复制历史内容为新版本并发布 |
| `GET /api/admin/audit` | 分页查看管理操作审计日志 |

Agent 每次运行记录不可变的 `prompt_version_id`；工具轨迹只记录工具名称、状态和时间，不保存或返回参数与原始结果。

## 模型管理 API

### `GET /api/admin/providers`

返回当前供应商以及 DeepSeek、OpenAI 配置状态。只返回 `configured`，不返回密钥或密文。

### `PUT /api/admin/providers/{provider}`

```json
{
  "model": "deepseek-chat",
  "api_key": "只写；null 表示不修改"
}
```

支持的 `provider`：`deepseek`、`openai`。

### `DELETE /api/admin/providers/{provider}/key`

删除该供应商的加密密钥。

### `POST /api/admin/providers/{provider}/test`

```json
{
  "model": "可选",
  "api_key": "可选的本次测试密钥，不自动保存"
}
```

### `PUT /api/admin/active-provider`

```json
{"provider": "deepseek"}
```

切换从下一条用户消息生效，不影响正在运行的回复；失败时不得自动切换到另一供应商。

## 知识库管理 API

| 方法与路径 | 行为 |
| --- | --- |
| `GET /api/admin/documents` | 列出文档元数据 |
| `POST /api/admin/documents` | `multipart/form-data` 上传 `file` |
| `GET /api/admin/documents/{id}` | 返回文档、解析警告和带块类型/位置元数据的切块预览 |
| `POST /api/admin/documents/{id}/reparse` | 使用当前解析器和切块器重新建库，版本递增 |
| `DELETE /api/admin/documents/{id}` | 删除原文件、片段与索引 |
| `GET /api/admin/knowledge/location` | 返回本机托管知识库目录和支持格式 |
| `POST /api/admin/knowledge/import` | 输入本机文件或目录路径，复制到托管目录并批量建立索引 |
| `POST /api/admin/knowledge/jobs` | 创建持久化后台路径导入任务，返回 HTTP 202 和任务状态 |
| `GET /api/admin/knowledge/jobs/{id}` | 查询阶段、当前文件、进度计数和逐文件结果 |
| `POST /api/admin/knowledge/jobs/{id}/cancel` | durable 模式下撤销排队/运行任务的后续发布权限，返回任务；终态重复调用不改写结果 |
| `GET /api/admin/knowledge/jobs` | 查询最近导入任务 |
| `GET /api/admin/knowledge/terms` | 查询内置和自定义企业同义词 |
| `PUT /api/admin/knowledge/terms` | 保存自定义同义词组 |
| `POST /api/admin/index/rebuild` | 重建所有分词和 FTS5 索引 |

当前支持 PDF，DOC/DOCX/WPS/RTF/ODT，XLS/XLSX/ET/ODS/CSV/TSV，PPT/PPTX/DPS/ODP，MD/TXT/HTML，以及 PNG/JPEG/TIFF/BMP/WebP。单文件最大 50 MB；旧版 Office/WPS 和 OpenDocument 依赖本机 LibreOffice，扫描 PDF、图片及 Office 嵌入图片使用 OCR。

批量路径导入始终返回逐文件结果；单个损坏文件不会中断同批其他文件。重复内容和不支持格式计入 `skipped`，解析或读取错误计入 `failed`：

```json
{
  "imported_count": 1,
  "skipped_count": 1,
  "failed_count": 1,
  "skipped": [{"path": "...", "display_name": "重复文件.txt", "reason": "该文件已经建立索引"}],
  "failed": [{"path": "...", "display_name": "损坏文件.pdf", "reason": "文档解析失败"}]
}
```

兼容同步模式单文件上传遇到重复内容返回 HTTP `409`；durable 模式返回 202 任务回执，重复内容在任务结果中计入 skipped。路径导入时同一路径、同内容计入跳过；同一路径内容变化会保留文档 ID、替换旧切块并增加 `version`。所有未定义的 `/api` 或 `/api/*` 请求返回 JSON `404`，不得回退到前端 `index.html`。

### 0030 上传与重解析统一队列

`durable_knowledge=True` 时，POST `/api/admin/documents` 和 `/api/admin/documents/{id}/reparse` 均返回 HTTP 202 与 KnowledgeJob，而不是 Document；关闭该模式时保留同步 200 合同。上传固定不可变输入并保留显示名；重解析固定已发布原文字节、同租户目标 ID 与预期版本，不读取后来变化的外部源文件。目标在发布前变化或删除则失败，不复活文档；非法目标 UUID 返回 400。

三种入队操作（upload/reparse/path）的 `knowledge.job.enqueue` 审计与任务提交同事务。公共回执不暴露私有清单、lease 或 execution_slot_held。列表按 running、pending、终态再按时间排序；前端跟踪多个新回执并轮询完成，完成后刷新文档，重解析入队后关闭旧预览。待处理本地输入的字节/哈希及固定目标纳入备份；对象模式固定私有对象版本，允许不同 Worker 文件根执行。

下方 0013 等分节为历史增量；其“审计单独事务”“无共享容量”等限制已由 0029/0030 对 queued 路径更新，不代表当前合同。主动取消、完整文件系统/网络沙箱及双机故障验收仍未完成。

管理界面使用后台任务接口。任务状态为 `pending/running/complete/partial/failed/cancelled`，阶段包括 `scanning/reading/parsing/chunking/indexing`；`result` 保留与同步导入相同的 `imported/skipped/failed` 逐文件结构。

取消要求同租户 `knowledge.manage` 及 Origin/CSRF 校验；兼容非 durable 模式不开放，回执 `cancellation_supported` 指示支持情况。取消与 `knowledge.job.cancel` 审计同事务，行锁与发布检查串行：取消之前提交的文档保留，之后不再发布。`cancelled`/finished_at 表示逻辑撤销完成，不是解析进程已停止的证明；Worker 心跳检测后终止隔离子进程，确认停止才释放容量。Worker 失联则保留占用，owner 必须确认旧解析进程停止才可人工释放，不按租约到期自动释放。对已完成任务取消不改写成功结果；重复取消不重复审计。无自动重放或删除已发布文档。

### PostgreSQL 后台导入（0013，显式模式）

- 管理页面刷新只读查询最近 20 条授权任务，优先恢复 pending/running，否则展示最近终态及逐文件错误；不重新 POST。单任务进度 GET 串行轮询（正常 500ms、失败 2 秒后重试），页面/任务切换丢弃迟到响应，原有身份 epoch 检查仍生效。这不是全量任务管理或请求幂等保证。
- `create_app(..., durable_knowledge=True)` 要求显式 PostgreSQL/AuthPolicy。POST `/api/admin/knowledge/jobs` 只校验/固定清单并创建 typed queue，返回 202；API 不调解析器、OCR 或本机线程池。关闭此开关时 PG 后台导入继续拒绝，默认 SQLite 兼容模式不变。
- 仅允许管理员本租户托管目录。最多 500 文件，支持且未超单文件限制的源文件合计最多 100 MiB；API 有界读取并记录相对路径/字节长度/SHA256。清单此后不重扫，不把后来新增文件加入任务；Worker 读取长度/哈希变化的文件标记失败，不静默索引不同内容。队列保留可撤销会话摘要引用，不保存 Cookie/API Key，公开任务不暴露私有清单或 lease 字段。
- `scripts/knowledge_worker.py --database-url <显式URL> --knowledge-root <绝对共享根> --asset-root <绝对共享根> --legacy-root <绝对共享根>` 为独立解析 Worker。API/Worker 必须使用相同受信任共享根；路径指向本租户 UUID 子目录，不发现默认数据/历史密钥，不构造模型客户端。它不包含对象存储、解析沙箱或自动重试，不能当作完整生产部署命令。
- claim 使用 PG SKIP LOCKED 和实时 actor/租户/knowledge.manage 校验；排队期限 1 小时，Run-like 60 秒 owner/fence/数据库时钟租约，每 2 秒续租；进度及最终发布复查身份、status、owner、fence、期限。解析不持数据库事务；非可中断解析可能继续占 CPU，但失租/撤销后结果不允许发布。
- publication 先取现有知识发布事务锁，再取 job 行锁。文档/切块/资产元数据与该 item imported 记录及 job 计数/result 同事务；任一失败全部回滚，任务不会记录未提交的文档。版本 CAS 和租户内内容/源路径并发检查继续保留。最终完成要求清单没有 pending item。
- Worker 原文写独立不可覆盖副本；源路径用于版本识别，已索引 stored_name 不再指向可变源文件。资产也使用独立 attempt 路径；失败只删本次文件，旧执行者不覆盖/清理别的 attempt 或用户输入。文件 staging 不是跨文件系统事务：进程被 kill 可留 orphan，GC/对象存储 manifest 仍待实现。
- SQLAlchemy/传输错误可能是提交回执丢失，不能按“未成功”删除可能已发布的文件；此时保留 staging 供后续 manifest 对账/GC。已提交 imported item 不允许再降为 failed。确知的普通回滚仍只清理本次文件；不能把保留 orphan 说成已经完成自动 GC。
- API/Worker 有界回收只处理 queued 的过期 pending/running job（每轮最多 100、SKIP LOCKED）；标 failed，保留已原子发布的文件结果，不解析/重领/盲重放。旧 execution_mode=local job 不碰。失权 owner 不走无身份路径强行更新；等数据库期限回收。再次导入是新任务，已有不变内容去重跳过；这不是自动检查点重试或 exactly-once 文件系统。
- 普通 `KnowledgeRepository.update_job` 不允许改 queued 任务，维护只允许内部未绑定 Repository。GET jobs/detail 仍限 knowledge.manage 和本租户，客户端只是展示进度。Job 创建审计目前仍单独事务；Outbox/完整原子审计、请求幂等、主动取消、全局解析资源预算和解析隔离尚未完成。

## 联系登记 API

### `POST /api/contacts`

```json
{
  "session_id": "uuid 或 null",
  "name": "称呼",
  "phone": "可选中国大陆手机号",
  "email": "可选邮箱",
  "summary": "问题摘要"
}
```

手机号或邮箱至少提供一个。此接口只登记，不创建真实工单。

### `GET /api/admin/contacts`

按创建时间倒序返回本地登记记录。

## Agent 与工具契约

| 工具 | 关键输入 | 稳定行为 |
| --- | --- | --- |
| `search_knowledge` | `query` | 返回本地片段；同时收集最多四条引用 |
| `get_business_time` | 无 | 返回上海时区时间、星期和示例营业状态 |
| `lookup_order` | `order_id`, `phone_suffix` | 只查询模拟订单 |
| `lookup_shipping` | `order_id` | 只返回模拟物流 |
| `check_refund_eligibility` | `order_id` | 只判断资格，不执行退款 |
| `show_contact_form` | `reason`, `summary` | 触发前端表单，不创建工单 |

Agent 必须通过 `AgentDeps` 获取 `KnowledgeService`，不得直接查询知识库表。

## KnowledgeService 契约

```python
ingest(filename: str, data: bytes) -> dict
list_documents() -> list[dict]
delete(document_id: str) -> None
rebuild() -> int
search(query: str, limit: int = 6) -> list[dict]
```

`search` 的每个结果至少包含：

```text
document_id, display_name, content, section, location, block_type, score, relevance, ranking
```

`score_kind` 标记原始排序来源：本地 `sqlite_bm25`、PostgreSQL 过渡关键词 `postgres_ts_rank`，或无关键词命中的 `character_fallback`。SQLite 的 `score` 是原始 BM25 分值，PostgreSQL 使用取负的 ts_rank 以维持原始分数升序；ts_rank 不是 BM25，也不是向量检索。`relevance` 是最终降序相关度；`ranking` 提供词覆盖、短语、章节、型号、字符相似度和命中同义词等解释字段。调用方按服务返回顺序消费结果，不跨后端比较原始 score。

文档、切块、图片、导入任务和知识术语通过 KnowledgeRepository 持久化；发布时同事务校验预期版本并替换切块/图片元数据。检测到解析期间的并发修改会拒绝发布，调用方应刷新后重试。托管文件名是内部标识，重新解析保持 display_name。PostgreSQL 禁止无租约的全局导入任务恢复，分布式 Worker 尚待 S3 实现。

调用方不得依赖 `knowledge_chunks` 或 `knowledge_fts` 的内部字段和触发器。

## 契约变更规则

消息列表顺序由服务端保证，调用方按数组顺序渲染，不按 `created_at` 或 UUID 自行排序。PostgreSQL 内部 `messages.sequence` 是会话内排序键；客户消息响应字段保持不变，管理详情可能包含该内部附加字段，不应把它当作全局消息编号、SSE 游标或授权依据。

- 新增可选字段：保持向后兼容，同步受影响调用方。
- 删除/重命名字段、事件或路径：破坏性变更，必须在用户授权范围内同步所有受影响模块和测试。
- 新错误码：同时更新本文档和前端映射。
- 数据库迁移：后端模块拥有迁移实现，提出需求的模块提供数据与兼容要求。

## 联系记录与管理检索补充

- `POST /api/contacts`：可不关联会话；提供 `session_id` 时，只能关联存在且未软删除的会话，否则返回 `404`。认证模式另检查租户与资源归属，默认本机兼容模式没有可信身份。
- `PATCH /api/admin/contacts/{id}`：可更新 `status`（new/following/closed）及 `note`；不修改状态时省略该字段，显式 `status=null` 返回 `422`，`note=null` 可清除备注。无字段返回 `400`，未知记录返回 `404`。
- 管理会话/联系记录/审计中的文本查询按字面子串匹配，`%`、`_` 不作为 SQL 通配符；会话标签按数组元素精确匹配，不按 JSON 字符串模糊匹配。
- 管理查询与联系人使用 SQLAlchemy Repository，HTTP 响应字段保持不变。应用默认仍为本地 SQLite；显式 PostgreSQL/AuthPolicy 装配已有请求级身份/租户边界，其他生产上线门槛仍未完成。

身份实施说明：0003 和 IdentityRepository 提供账户、成员关系、密码校验、可撤销会话及 Principal/RBAC。0004–0007 的令牌绑定 Repository 已接入显式认证模式下的 HTTP 与 Agent，覆盖会话、联系人、管理、知识、模型和 Prompt。默认本机模式不自动启用这些身份依赖；客户/管理 API 继续保持仅本机访问。账户供应、RLS、完整原子审计和生产部署仍待完成。

会话 tenant_id/owner_id 属于服务端归属，不是客户可提交的会话属性；普通 Repository 更新拒绝修改这些字段。`start_run` 内部参数 user_id 历史上表示用户消息 ID，不是登录 actor_id，不能据此判断资源所有者。无身份的 PostgreSQL 兼容会话路径仅见停用 legacy 租户，尚未完成旧会话的授权认领。

0005 补充：联系人创建、跟进修改和内部备注各自与审计同事务，失败则整体回滚；路由不再另写重复 contact.update/session.note。审计动作包含 contact.create，元数据只记字段名，不复制联系方式或备注正文。认证模式的管理查询拒绝客户，客服仅处理本租户，审计总列表仅租户管理员可读。历史备注 author_id 可能为 NULL；不能把未知作者显示为客户或管理员。配置状态只向管理员显示本租户数据，现有本机兼容响应不变。

0006 补充：认证模式的知识检索和字符回退在查询时过滤租户，图片元数据与文件访问也验证同一租户；客户/客服不开放文档来源和解析管理接口。文档/任务归属由服务端生成，调用方不得提供 tenant_id/created_by/requested_by。同 SHA256 在不同租户可以分别导入，不能将去重结果作为跨租户上传存在性查询。术语和索引版本存入租户知识设置，不回退全局。管理概览的 documents/chunks 为本租户实际数量；模型状态仅向管理员显示。HTTP 身份依赖已接入，不代表对象存储、向量检索或 Worker 完成。

## 可选认证接口与前端登录

除模式发现接口外，仅显式 PostgreSQL 装配并传入 `AuthPolicy(origin=...)` 时注册。默认本机 SQLite 模式仍未启用认证，不得公开。安全 Cookie 模式必须 HTTPS、固定可信 Origin；HTTP 非 Secure Cookie 仅允许明确配置的 loopback 测试。仍保留本机来源限制，不信任 X-Forwarded-For。

- `GET /api/auth/config`：两种模式均注册，无需 Cookie，返回 `{enabled: boolean}` 且 no-store；不包含凭据或部署配置。前端仅在 `enabled === false` 时挂载兼容界面，网络错误或非法结构不得降级为未认证模式。
- `POST /api/auth/login`：application/json，恰好含 username/password/tenant_id。需匹配 Origin，最大请求体 8192 字节；密码 12–1024 字符。走持久 LoginService 准入，失败 401、格式 400、类型 415、过大 413、限流 429/Retry-After、认证数据库故障 503。错误不回显密码/输入。成功返回 principal、expires_at、csrf_token；登录令牌只进入 HttpOnly/Secure/SameSite=Strict、Path=/、无 Domain 的 __Host-cs_session Cookie，不在 JSON 返回。
- `GET /api/auth/me`：Cookie 校验并实时读取角色/租户状态，返回 principal 与 csrf_token；未知/过期/撤销为 401。响应 no-store。
- `POST /api/auth/logout`：有效 Cookie、匹配 Origin 和 X-CSRF-Token；数据库持久撤销成功后删除 Cookie。撤销失败返回 503，不声称退出成功。
- 所有有副作用 API 在此模式均要求 Origin 与 CSRF；login 以 Origin 防登录 CSRF，无须已有 Cookie。非可信 Origin 与 cross-site 请求拒绝 403；CORS 仅允许配置的唯一 Origin，支持 Cookie 凭据。
- **请求级管理绑定**：已开放模型配置/测试/激活/删密钥、Prompt 草稿/发布/回滚（config.manage）；管理会话列表/详情/编辑/内部备注/导出及概览（chat.tenant）；管理联系人列表/修改（contact.tenant）；全租户审计（audit.read）。每个请求创建独立令牌绑定 Repository，不修改全局服务身份。管理字段参数错误统一 422，不回显输入中的密钥/联系方式；撤销后的 Cookie 返回 401，角色不符 403，外租户资源通常 404。模型网络连接池可共享，但密钥/配置快照不共享身份。
- `POST /api/admin/setup`：只在认证模式注册，需 config.manage、Origin 和 CSRF；幂等创建本租户未配置 DeepSeek 和默认 Prompt，不复制历史配置/密钥，不创建 OpenAI。返回 providers 配置摘要和 prompts。此接口不创建用户，账户供应仍为受控内部操作。
- **请求级知识绑定**：文档列表/上传/详情/重解析/删除、知识位置/目录导入、任务查询、重建、检索测试和术语读写需 knowledge.manage；图片需 knowledge.read，可供同租户客户读取。服务使用本租户目录，外租户文档/图片不可见，目录源不能逃出本租户托管根。客户管理操作在读取 multipart/解析前拒绝；上传解析和审计移至线程池。图片在认证模式 no-store，避免退出后复用浏览器缓存绕过重新鉴权。PG 任务创建仍返回未启用租约 Worker 的错误，不启动本地线程执行器；此绑定不是任务执行能力。
- **请求级聊天/Agent 绑定**：会话创建/列表/历史/改名/归档/恢复/软删除、消息发送/反馈/取消需 chat.own，Repository 对客户仅限本人，对客服/管理员限本租户；管理生命周期操作另需 chat.tenant。已存在但无权访问的会话 ID 不能以“新会话”重新使用，消息发送在返回 SSE 前拒绝 404；取消先查资源访问权，再访问共享运行令牌。客户联系登记绑定 contact.own 与父会话归属，模型不能提交 actor/owner/tenant。
- 每个 ChatOrchestrator 请求视图独立绑定配置、Prompt、知识与 Chat Repository；进程级 ChatRuntime 仅共享 Semaphore、计数和取消映射，不共享请求身份。数据库成功启动后才注册/替换旧运行，权限失败不能取消旧运行。AgentDeps 的 Principal 与身份复查回调由服务端构建；知识/表单工具以及 SSE 事件/心跳复查撤销。中途失效返回 answer_reset 和 authentication_required，不保存助手最终消息。不能收回失效前已发送的内容或已提交的外部操作。
- 订单/物流/退款资格仍是本机演示工具；认证模式返回真实适配器未配置，不提供全局模拟订单记录。真实业务鉴权/审批/审计/幂等仍待 S4。退出导致失去权限的执行者不能用匿名 Repository 强行写终态；PG leased Run 到期后由受限内部清理器终结。当前有数据库取消意图与租约保护，但不是队列 Worker、检查点恢复或完整多实例服务。
- 路由名/权限白名单仍对其他未显式绑定路由失败关闭，不按整个 admin 前缀放行。默认本机兼容模式响应不变。已有前端登录页，但尚无公开注册、默认管理员、生产账户供应入口或 MFA/SSO；真实部署/TLS、业务适配器和多实例能力未完成。
- 前端 API/SSE 使用同源 Cookie，有副作用请求添加内存中的 X-CSRF-Token；不在 localStorage/sessionStorage 保存凭据。身份 epoch 拒绝账户切换前发起的迟到响应，401 或 SSE authentication_required 清除身份并卸载聊天/管理视图、终止活动流；退出成功另清除账号/租户输入与页面路径。登录状态每 30 秒和窗口 focus 时复查；数据库/网络故障不被伪装成退出成功。
- 客户不显示管理入口，客服只显示概览/会话/联系记录，管理员显示全部；直接输入不允许的管理 URL 回到角色允许页面。界面隐藏不是权限边界，后端继续对每个操作鉴权。前端 HTML no-store，静态目标必须位于构建目录内；不能用旧 HTML 缓存或路径穿越替代身份校验。

## PostgreSQL Run 租约与取消（0009）

- PG ChatOrchestrator 启动 Run 时以内部随机 owner 领取 30 秒数据库时间租约；续租不能复活过期 owner。同会话最多一个 leased running Run；新请求在会话事务锁内将旧 Run/未完成工具终结为 interrupted/superseded，再原子保存新用户消息与新 Run。后续旧事件不能覆盖新运行或保存旧答案。
- 结果和工具记录写入均验证 run_id/owner/fence/未过期/未取消，继续使用原请求身份鉴权。Agent 上下文工具前复查租约，独立监视任务每 2 秒轮询续租与取消，慢模型没有 token/SSE 心跳时也能取消。数据库故障停止执行授权，不将故障解释为继续持有租约；不能撤回此前已发送数据或外部副作用。
- `POST /api/chat/sessions/{session_id}/runs/{run_id}/cancel` 的响应字段不变。cancelled=true 表示同进程取消通知已发出，或授权范围内的 PG 取消意图已持久提交，不表示模型/外部工具已经停止。第二实例无需拥有本地 token；执行实例轮询发现标记后中断。未知、终态、他人资源或错误 Run ID 返回 false，不碰其他活动运行。SQLite 仍为本机取消。
- 各 PG 应用实例每 5 秒执行最多 100 条的过期 leased Run 清理：会话 try-advisory lock→Run SKIP LOCKED→重新检查数据库时钟→Run/未完成工具终态与会话状态同事务。忙碌会话/锁定行跳过，下一轮重试；清理不读取用户正文/密钥，不重放模型/业务调用，旧无租约记录不受影响。
- 直接执行模式中，失租/取消清空当前草稿并发送 interrupted；身份失效仍发送 authentication_required。直接模式 HTTP 驱动模型，客户端断开会中断执行；0010 的可选持久模式见下节。全局容量/限流、Redis 通知、检查点、工具幂等/未知结果对账仍待实现，不能宣称完整 S3。

## 持久聊天与独立 Worker（0010–0011，可选模式）

- 显式 `create_app(..., durable_chat=True)`，要求 PostgreSQL 与 AuthPolicy；默认 SQLite/直接 SSE 不变。HTTP 只提交，不嵌入 Worker、不调用模型或解密模型密钥。
- POST `/api/chat/sessions/{session_id}/messages` 接受 `content` 与可选 UUID `request_id`，返回 HTTP 202 JSON `{run_id, events_url}`。相同 actor/session/content 的同一编号只创建一次；编号冲突为 409。新命令事务性终结该会话旧排队/运行任务，不自动重试模型或未知业务副作用。
- `chat_run_queue` 保存用户消息引用、不可变 Prompt 文本与可撤销会话摘要引用，不保存原始 Cookie、API Key。Worker 领取时重新检查身份、父会话归属/生命周期与 120 秒排队期限；claim 使用会话 advisory lock、SKIP LOCKED、Run fence/30 秒租约。执行时仍重新鉴权/续租；配置在排队期间更换时失败关闭。
- GET `/api/chat/sessions/{session_id}/runs/{run_id}/events` 返回带递增 `id:` 的持久 SSE；`after` 或 `Last-Event-ID` 从游标之后读取，header 优先。ahead 游标 409、无效 header 400、不可见资源 404。终态所有事件读完后关闭连接；ping 是未持久化的心跳。
- 订阅断开不写取消意图、不取消 Worker；取消必须调用原有带 CSRF 的 POST cancel。前端只重连 GET，最多额外 3 次，以 200/400/800ms 退避、按 ID 去重，不因网络错误重发 POST。订阅与退避都受 AbortSignal/身份 epoch 约束。
- durable 模式 GET `/api/chat/sessions/{session_id}` 附加 `run`（无活动 queued/running Run 时为 null），在同一会话事务锁下读取历史、运行状态、草稿、引用、取消意图及游标。刷新/重新打开会话时只订阅该 cursor 之后的事件，不重发用户消息、不将模型推理重新执行。游标在服务端快照中取得，不在浏览器存储凭据或私有草稿。
- GET `/api/chat/sessions/{session_id}/runs/{run_id}` 返回同格式 `{session,messages,run}`，可查询终态；不可见/不存在资源 404、默认直接模式 404。Run 公共字段为 run_id/status/cursor/draft/citations/cancel_requested/error_code/message_id/contact_summary，不暴露 lease owner、提交身份摘要、队列 Prompt 或凭据。
- Stop 在创建会话期间可阻止后续发送；消息已经开始提交时保留 stop intent，202 的 run_id 回执到达即发 cancel，不必等待 GET 首帧。cancelled=true 只显示“停止请求已确认，等待运行结束”，终态事件才结束活动状态；false 时查询 durable Run 状态，失败明确显示未确认，不断开订阅冒充停止。POST 回执丢失/网络不可达时仍可能需要刷新确认，不盲重发命令。
- 单事件最大 256KiB；每 Run 非终态 data 的 PostgreSQL JSONB 文本预算 3MiB/9000 事件，全部含终态最多 4MiB/10000 事件。预算检查与事件序号/插入在会话/Run 锁事务内；预留终态空间，超限拒绝写入。完成时引用超限回滚整个助手答案/终态事务，Worker 可提交受限错误终态。这里只限制 data 字节，不代表整个数据库/索引的物理空间预算；聚合校验与快照重放仍需大规模性能验收。
- final message 与 citation/contact_form/done 在同一终态事务提交。取消、超时、失租、被抢占也有持久 answer_reset/终态。撤销产生的旧任务错误是 `run_identity_revoked`，不让后来有效登录的用户错误退出；当前订阅身份失效才发送临时 `authentication_required`。
- 独立入口 `scripts/chat_worker.py` 要求显式数据库 URL、master-key-path 与三个文件根，不发现历史密钥、不迁移。测试 `--synthetic` 限定本机临时数据库/目录；生产不使用该选项。Redis、全局准入、重试/死信、检查点、知识导入 Worker 与文件 fencing、数据库 HA 仍待完成。

### 0011：终态快照与事件保留（替代上文聚合预算实现）

- Run 增加公共 `event_floor`（最低可读游标）；私有累计 `event_data_bytes` 持久保存，每帧只计算本帧 JSONB 文本大小，条件更新预算/序号并插入事件，不再逐帧聚合整个账本。清理不重置累计预算；3MiB/9000、4MiB/10000 和 256KiB 限制不变，规模性能仍待验收。
- `chat_run_snapshots` 与助手消息、done、Run 终态同事务提交：complete 使用已保存的助手内容/引用，不把 token 草稿当最终答案；error/interrupted 清空草稿、引用和联系摘要。终态快照序号必须等于 Run 最终 event_sequence；活动快照仍从事件重放。缺失/不一致返回 503，不伪装成 404。
- 清理默认关闭。显式 `create_app(..., durable_chat=True, event_retention_seconds=86400)` 才启用，保留期必须是 1–90 天的整数秒。每轮最多 100 个、按数据库时钟检查的 terminal queued Run；会话 try-lock→Run SKIP LOCKED→复查快照→删除事件与提高 event_floor 同事务。忙碌、活动或快照不一致目标跳过。
- 可信维护 CLI `scripts/prune_chat_events.py --database-url <显式URL> --retention-seconds 86400 --limit 100` 只输出数量，不自动寻找数据/密钥或迁移；审批前不得对真实库执行。请求/租约绑定 Repository 不允许执行维护。
- 授权检查后，`after < event_floor` 返回 HTTP 409 `{code:"cursor_expired",detail,snapshot_url}`；已经建立的流被清理追上时发送未持久化的 `cursor_expired` 后关闭。`after == event_floor` 合法，终态尾游标可能得到空流。ahead 游标仍 409，不是 cursor_expired。
- 前端由自己的同源 events URL 推导快照路径，不使用响应提供的任意 snapshot_url；重新鉴权并校验 Run ID/游标/终态/已保存消息，再替换历史并结束活动状态。`run_snapshot` 仅是前端内部事件，不是服务端 SSE。流已读到尾游标而关闭也查询快照，活动状态才有界重连；不重发 POST，不重复显示已保存助手消息。身份 epoch 变化丢弃迟到快照。
- 此清理只回收事件，不删除消息、终态快照、queue 的幂等/身份引用或 Run；并非用户数据删除/匿名化政策，也不包含备份清除。相关保留及隐私政策仍需另行制定。

### 0012：共享运行准入（显式启用，验收中）

- `chat_admission_policy` 为 PostgreSQL 单例不可变策略，包含非敏感 UUID namespace、全局 capacity（1–1000）、每分钟启动数（1–60000）。需在没有活动 Run 时由可信维护入口显式创建；Worker 不自动创建、扩大或重置策略。重复相同配置幂等，变更须停机审阅迁移，不能直接修改配置绕过限额。
- `scripts/configure_chat_admission.py --database-url <显式URL> --namespace <UUID> --capacity <整数> --starts-per-minute <整数>` 创建策略；只输出指纹，不发现数据/密钥。普通客户端没有该 HTTP 权限入口。真实库操作仍须审批，参数只是配置流程示例。
- Worker 显式同时配置 `--redis-url`、`--admission-namespace`、`--global-capacity`、`--starts-per-minute`，须与 PG 策略一致；缺项或不匹配失败关闭。配置策略后未配置 Redis 的 Worker 和直接 PG 执行均拒绝运行，不能通过老执行路径绕过。未配置策略的默认兼容模式不变，不是生产模式。
- Redis Lua 使用服务端 TIME、同一 Cluster hash tag 的策略/slot/starts 键；UUID permit、30 秒失效、2 秒监测及工具/结果边界复查。容量不足不领取任务，完成释放容量但不退启动速率；没有领取任务才退预留。过期 owner 不能续期，连接异常或丢 permit 终结 `coordination_unavailable`，不自动改用每进程限额。失联释放依赖 TTL，不重放模型或业务调用。
- PG 领取事务持全局 advisory lock、再取 session/Run 锁，复查固定策略、数据库时钟的容量及最近 60 秒启动记录；Redis 状态丢失也不放大领取。此机制仍要求 Redis permit，不是 Redis 故障时的降级旁路。启动记录是 Run 私有元数据，不进入 SSE/公共快照。
- PG `admission_until` 随租约续期；抢占/终态不立即释放，旧 Worker 关闭生成器/模型客户端后才按 owner 确认退出并清除。旧 owner 无回执或 teardown 失败时等数据库时钟过期；错误 owner 不能释放。这样 Redis 丢失状态时也不会把尚未退出的被抢占 Run 当成空槽。fencing 仍禁止旧执行者保存答案或发起新的工具动作。
- 限制对象是 Run 并发及 Run 启动速率，不是供应商请求 RPM/TPM、租户公平调度、API 排队数量或费用预算；多工具/多模型请求限额仍待 S2/S5。生产 Redis 要求专用 ACL/TLS/网络隔离、noeviction、容量监控及故障切换演练。Redis/Sentinel/Cluster HA 和 Pub/Sub 取消加速尚未验收，数据库取消轮询保障仍保留。

### 0016：对象凭据供应与恢复绑定

0017 当前head新增全库维护：默认空maintenance_state，HTTP503 `{code:maintenance_hold,detail}`/no-store，API启动/Worker拒绝处理，过期不自动开放。SQL层30业务表statement触发器/共享事务锁保护READ COMMITTED写，封存单连接/表锁和排空有效执行租约；无租户管理HTTP开关。scripts/maintenance.py可信运维request/renew/release和逻辑backup CLI显式owner/fence见 [维护门控](maintenance-operations.md)。恢复同引用映射事务使旧维护permit失效，recovery hold不解除；排空/续租调度和统一竞态失败提示仍待实现。

运维备份后续接口（非租户 HTTP）：`scripts/logical_backup.py create|restore` 可额外成组提供 storage-policy/storage-target/storage-credentials-stdin，在明确维护/隔离确认后读取独立有界凭据，禁止环境或应用密钥回退，SDK finally 关闭。`scripts/backup_archive.py archive|fetch` 使用相同独立供应加 namespace/receipt/package/key-path/scratch-root；固定版本有界分块、AAD 加密索引、完整校验和排他发布，不自动删除/重试/启用服务。CLI 回执与 verified 范围、单源桶和生产 IAM/TLS 限制见 [远程备份](remote-backup-operations.md)。无新 HTTP 权限或解除 hold 入口。

本节更新下方历史“无凭据入口”限制。

- GET `/api/admin/storage`：需要 config.manage，响应 enabled/configured/target/revision/targets/policy_configured/recovery_rebind，无任何凭据/密文。legacy 模式 enabled=false、PUT 409。
- PUT `/api/admin/storage`：需要 config.manage、Origin/CSRF，body 为 `{target,revision,access_key,secret_key,session_token?}`。只允许批准名称，不接受用户 endpoint/bucket/region；参数错误 422 无请求回显，版本或目标冲突 409，对象不可用脱敏 503。
- 保存先鉴权/版本检查→私有版本桶 readiness→复查身份→租户/目标 fingerprint/revision AES-GCM→配置及审计同事务。并发相同版本只允许一个成功；active 配置不允许切换目标，轮换仍需完整凭据与当前 revision。
- 请求级 KnowledgeService 和两个 Worker 按原 actor 读取本租户配置，实例未提供 policy 但有配置行时拒绝执行，不回退默认凭据/local/latest。SDK 在请求/任务结束后关闭。
- Worker 成对参数 `--storage-policy`/`--storage-master-key-path` 仅传绝对非敏感批准文件/包装密钥路径，服务凭据不进入 CLI。对象新输入不需共享源根，旧 NULL 输入仍需可信根。API 装配使用 storage_policy，不可混合直接 object_store。
- restore 将所有配置设为 recovery_rebind，与引用映射同事务；报告 storage_rebind_required。hold 内管理也拒绝，恢复对账之后重新绑定须与本租户恢复对象 bucket 一致并提供新凭据，不复用源凭据。无解除 hold API。
- UI 凭据不持久化/回显，保存前清空；GET 最多等候 10 秒、PUT 45 秒，无自动重试。PUT 超时不保证服务器回滚，必须刷新核对 revision。部署/IAM/KMS/backup CLI 等限制见 [对象存储操作](object-storage-operations.md)。

### 0015：跨主机知识输入

- 0015 引入 `knowledge_job_items.input_object` 可空 JSONB，旧任务保持 SQL NULL，不自动上传/转换/重放。非空值须为对象且有固定 sha256；入队在事务内复查租户、引用 size/hash/kind/version 和 recovery hold。当前 head 为 0017_maintenance_gate。
- `KnowledgeJobService` 在提交实例扫描托管文件、读取有界内容并上传不可变版本；上传后才原子入队清单。失败/未知 COMMIT 不删除对象，留待对账。对象引用不出现在公共任务结果中。
- 注入对象 store 的 Worker 可使用另一根目录读取确切版本，下载前后检查原登录凭据、owner/fence/lease，再解析/发布。缺 store/缺版本不得读取本地或 latest；权限撤销/失租不进入解析。不支持的后缀无需本地文件即可跳过；旧 local 输入仍校验实际路径/hash/size。
- 排队输入对象与文档/资产一起纳入加密备份 manifest。恢复按 `(table,job_id,position)` 映射每个引用，hold 保持阻断，不能把恢复后的 queued 任务直接重新执行。
- HTTP 202/任务轮询/前端结果结构不变；此批没有对象凭据供应 HTTP 或生产 Worker CLI 参数，独立进程验收使用合成 harness。

### 私有知识对象引用（部分接入）

- 通过知识服务依赖注入启用版本化对象存储；默认本地模式不变。原文和图片引用与文档/切块/任务结果原子发布，管理列表及详情不输出 `_private_object`。
- 图片路由始终返回 PNG 内容而非重定向对象 URL，`Cache-Control: no-store`；读取前后复查权限和资产版本。对象/指定版本不可用返回 503 `{code:"object_store_unavailable",detail}`，不回退本地或 latest；越权/撤销保留 404/401。
- 对象原文重解析保留 document ID 并递增版本，expected_version 冲突拒绝。排队源文件和对象备份尚未接通，详见 [对象存储操作边界](object-storage-operations.md)。

### 0014：恢复保护

- 当前 PostgreSQL Schema head 为 0017_maintenance_gate，0014/0017保护迁移不默认阻断普通新库。逻辑恢复在恢复事务内写 recovery_state blocked=true，恢复库不能先提交再设置保护。
- 有 hold 时 API 启动失败；已运行 API 的请求（包括静态页面/登录/健康）返回 503 `{code:"recovery_hold",detail}`，no-store。客户端不会获得权限、模型或业务内容，不能把此错误当成登录成功或绕过本机模式。
- 独立聊天/知识 Worker 检查启动与领取；知识管理/发布与已有 Run 的租约写入复查。恢复队列保持原记录供对账，不重放旧命令；没有租户管理员/自动解除 hold 的 HTTP 入口。完整授权前向对账与维护解除尚待实现。
- 此应用层保护不代替数据库 RLS、网络隔离或旧版本防护；只有受控应用角色可访问相关表，角色/权限正式供应仍须生产验收。
# 显式HTTPS代理客户端边界

生产serve_api入口保持127.0.0.1绑定、仅信任127.0.0.1转发及canonical Host白名单。create_app默认仍本地访问；仅显式allow_authenticated_proxy_clients=True并同时使用secure AuthPolicy和strict API-role时，允许可信同机代理传递的非loopback客户端IP。Cookie、Origin、HTTPS、CSRF和各业务资源鉴权不因此豁免。该参数不是开放任意监听地址、可信所有转发源或公网部署的授权。双guest实测入口与边界见docs/two-node-runtime-acceptance.md。

