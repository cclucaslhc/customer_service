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

创建会话。

```json
{
  "id": "uuid",
  "status": "idle",
  "created_at": "ISO-8601"
}
```

### `GET /api/chat/sessions/{session_id}`

返回会话及 `status=complete` 的有效消息。

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
| `token` | `text` | 增量回答文本 |
| `citation` | `document_id`, `document`, `section`, `location`, `excerpt` | 一条本地知识引用 |
| `contact_form` | `summary` | 前端应展示联系方式表单 |
| `interrupted` | `run_id` | 运行被用户或新消息取消 |
| `done` | `message_id`, `run_id` | 回答完整保存 |
| `error` | `code`, `message` | 本次运行失败 |

事件顺序约束：

```text
run_started → provider → token* → citation* → contact_form? → done
run_started → provider → token* → interrupted
error
```

`tool_start`、`tool_end` 尚未实现，不属于当前稳定契约。

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
| `DELETE /api/admin/documents/{id}` | 删除原文件、片段与索引 |
| `POST /api/admin/index/rebuild` | 重建所有分词和 FTS5 索引 |

当前支持 `.pdf`、`.docx`、`.md`、`.txt`，单文件最大 20 MB；扫描 PDF 不支持 OCR。

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
document_id, display_name, content, section, location, score
```

调用方不得依赖 `knowledge_chunks` 或 `knowledge_fts` 的内部字段和触发器。

## 契约变更规则

- 新增可选字段：向后兼容，可由所有者模块提交并通知总控。
- 删除/重命名字段、事件或路径：破坏性变更，必须由总控协调所有受影响模块在同一集成批次更新。
- 新错误码：同时更新本文档和前端映射。
- 数据库迁移：后端模块拥有迁移实现，提出需求的模块提供数据与兼容要求。
