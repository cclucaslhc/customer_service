# 系统架构与模块边界

## 当前架构

```text
React 用户端 / 管理端
          │ HTTP + SSE
          ▼
       FastAPI
          │
          ▼
   ChatOrchestrator
          │
          ▼
    PydanticAI Agent
          │
          ▼
      ModelGateway
      ├── DeepSeek
      └── OpenAI

独立服务：SQLite、SecretStore、KnowledgeService、客服工具
```

## 四个模块

### 前端模块

- 目录：`frontend/**`
- 职责：聊天界面、管理界面、SSE 消费、表单校验、用户交互。
- 只依赖 `docs/api-contracts.md`，不得导入后端实现。
- 后续拆分目标：`/chat` 用户路由与 `/admin` 管理路由。

### 后端模块

- 文件：`app/main.py`、`app/chat.py`、`app/database.py`、`app/schemas.py`、`app/security.py`、`app/settings.py`、`main.py`。
- 职责：HTTP/SSE、会话运行、持久化、取消机制、安全边界、静态资源服务。
- 通过接口调用 Agent 与知识库，不内嵌提示词、模型客户端或文档解析逻辑。

### Agent 模块

- 文件：`app/agent.py`、`app/providers.py`。
- 职责：PydanticAI Agent、系统指令、工具注册、模型供应商适配和错误归一化。
- 通过 `AgentDeps` 使用知识服务，不直接操作知识库表。
- ModelGateway 不得从环境变量静默读取密钥；密钥仅来自管理界面保存的加密配置。

### 知识库模块

- 文件：`app/knowledge.py`。
- 职责：文档解析、结构化切块、中文分词、FTS5/BM25 索引和检索。
- 对外只暴露 `KnowledgeService`；调用方不依赖 FTS5 表结构。
- 新文档类型通过独立解析器接入，统一输出章节和位置元数据。

## 依赖方向

允许的依赖方向：

```text
frontend → HTTP/SSE contract
main/chat → agent + providers + knowledge + database
agent → KnowledgeService protocol
knowledge → database/settings
providers → database/security
```

禁止的依赖方向：

- 前端读取 SQLite 或密钥文件。
- 知识库调用 Agent 或模型供应商。
- Agent 直接访问 HTTP Request、前端组件或上传目录细节。
- 业务工具把供应商原始响应直接传给前端。

## 扩展点

### 多 Agent

当单 Agent 的提示词、工具选择或评测结果明显恶化时，在 `ChatOrchestrator` 与 Agent 之间增加路由层。候选专业 Agent 包括知识问答、订单售后和联系登记。所有 Agent 继续共用 ModelGateway、标准消息、工具服务和 SSE 契约。

### 多类型文档

解析器统一输出：

```text
ParsedSection
- title
- location
- text
- metadata
```

PDF、DOCX、MD、TXT 为当前实现；扫描件 OCR、Excel、HTML/网页和图片作为独立解析器增加。解析器不负责聊天、模型调用或索引查询。
