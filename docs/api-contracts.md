# 公共接口契约

本文档是前端、后端、Agent、知识库之间的共享事实来源。任何破坏性变更必须先由总控会话批准并同步受影响模块。

## 通用约定

- API 前缀：`/api`。
- JSON 使用 UTF-8，字段名使用 `snake_case`。
- 时间在数据库中使用 UTC ISO 8601；前端按 `Asia/Shanghai` 展示。
- 错误响应使用 FastAPI `detail`；SSE 错误使用 `{code, message}`。
- API Key 是只写字段，任何 GET、日志或 SSE 都不得返回明文。
- 当前版本无管理员认证，只允许绑定 `127.0.0.1`；公网部署前必须为 `/api/admin/*` 增加鉴权。

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
| `provider` | `provider`, `model` | 本次运行的不可变供应商快照 |
| `ping` | `timestamp` | 供应商暂时无输出时的 SSE 保活心跳；前端无需展示 |
| `token` | `text` | 增量回答文本 |
| `answer_reset` | 无 | 清除工具调用前或校验失败的临时文本 |
| `tool_start` | `tool` | Agent 开始执行工具；不得包含工具参数或敏感数据 |
| `tool_end` | `tool` | Agent 工具执行完成；不得包含原始工具结果 |
| `citation` | `document_id`, `document`, `section`, `location`, `excerpt` | 一条本地知识引用 |
| `contact_form` | `summary` | 前端应展示联系方式表单 |
| `interrupted` | `run_id` | 运行被用户或新消息取消 |
| `done` | `message_id`, `run_id` | 通过最终答案校验的回答已完整保存 |
| `error` | `code`, `message` | 本次运行失败 |

事件顺序约束：

```text
run_started → provider → (ping | token* → answer_reset? → tool_start → tool_end)* → token* → citation* → contact_form? → done
run_started → provider → (ping | token | answer_reset | tool_start | tool_end)* → interrupted
error
```

空回答或只有“让我查询”等过程性文字会触发模型重试；重试耗尽后发送
`error(code=answer_validation_failed)`，不得保存完整助手消息或发送 `done`。

错误码按失败阶段区分：Agent 工具尚未返回时发生异常使用 `tool_error`，模型请求、
响应或其他非工具阶段异常使用 `model_error`。两者都会结束运行且不会发送 `done`；
服务端日志只记录内部运行标识、供应商/模型、工具名和异常类型，不记录用户正文、
工具参数、工具结果、供应商响应正文或密钥。

并发舱位等待超时使用 `service_busy`，并额外返回整数秒 `retry_after`；客户端应延迟并加入随机抖动后重试。单次模型运行超过服务端上限使用 `model_timeout`。这两类错误都不会发送 `done`。

## 服务健康 API

| 接口 | 含义 |
| --- | --- |
| `GET /health/live` | 仅验证 ASGI 进程可响应 |
| `GET /health/ready` | 验证数据库可用，并返回当前模型并发舱位 |
| `GET /health` | 兼容健康接口，额外返回 SQLite journal mode、主密钥状态和模型并发舱位 |

所有 HTTP 响应包含 `X-Request-ID`；客户端传入该请求头时服务端原样回传，否则自动生成 UUID。

## 企业管理 API

所有管理端分页列表统一返回 `{items,total,page,page_size}`。当前没有登录机制，HTTP 中间件仅接受本机回环地址请求；不得将服务直接暴露到局域网或公网。

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

单文件上传遇到重复内容返回 HTTP `409`。路径导入时同一路径、同内容计入跳过；同一路径内容变化会保留文档 ID、替换旧切块并增加 `version`。所有未定义的 `/api` 或 `/api/*` 请求返回 JSON `404`，不得回退到前端 `index.html`。

管理界面使用后台任务接口。任务状态为 `pending/running/complete/partial/failed`，阶段包括 `scanning/reading/parsing/chunking/indexing`；`result` 保留与同步导入相同的 `imported/skipped/failed` 逐文件结构。

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

`score` 是原始 BM25 分值；`relevance` 是最终降序相关度；`ranking` 提供词覆盖、短语、章节、型号、字符相似度和命中同义词等解释字段。调用方应按服务返回顺序消费结果，不自行重新解释 BM25 正负号。

调用方不得依赖 `knowledge_chunks` 或 `knowledge_fts` 的内部字段和触发器。

## 契约变更规则

- 新增可选字段：向后兼容，可由所有者模块提交并通知总控。
- 删除/重命名字段、事件或路径：破坏性变更，必须由总控协调所有受影响模块在同一集成批次更新。
- 新错误码：同时更新本文档和前端映射。
- 数据库迁移：后端模块拥有迁移实现，提出需求的模块提供数据与兼容要求。
