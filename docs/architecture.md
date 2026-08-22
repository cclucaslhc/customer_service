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

- 文件：`app/knowledge.py`、`app/knowledge_parsers.py`、`app/knowledge_chunking.py`、`app/knowledge_models.py`、`app/knowledge_jobs.py`、`app/knowledge_terms.py`。
- 解析器先按文件特征识别真实类型，再统一输出 `ParsedDocument -> ContentBlock[]`。块类型包含标题、段落、列表、表格、备注和 OCR，并保存页码、幻灯片、工作表、行号、标题路径和坐标等位置元数据。
- DOCX/XLSX/PPTX 直接结构化解析，覆盖页眉页脚、批注脚注、公式、合并区域、组合对象、表格和图表缓存；DOC/PPT/WPS/RTF/ODT/ODS/ODP/ET/DPS 由本机 LibreOffice 隔离转换；PDF 和图片在文本不足时调用 RapidOCR。归档大小、单元格数和幻灯片数均有限制。
- 切块尊重章节和表格边界，正文按句子聚合，长块保留重叠；表格切分时重复表头。每个切块可追溯到原内容块。
- 入库保存原文件、文档版本/解析器版本/警告和结构元数据；同一路径内容变化时原位替换切块并增加版本。
- 路径导入通过持久化 `knowledge_jobs` 串行后台执行，记录扫描、读取、解析、切块、索引阶段以及逐文件结果；重启会恢复 `pending/running` 任务。
- 检索采用 Jieba、中文二元组、SQLite FTS5/BM25 和字符相似度混合召回，再按查询覆盖率、完整短语、章节、精确型号和企业同义词进行可解释重排与阈值过滤。
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

新增格式只需实现独立解析函数并注册到统一入口，禁止在检索层针对源文件格式写特殊逻辑。解析失败应形成逐文件错误；局部 OCR/图片失败形成 warning，文档其余可用内容仍可入库。

解析器统一输出：

```text
ParsedSection
- title
- location
- text
- metadata
```

PDF、DOCX、MD、TXT 为当前实现；扫描件 OCR、Excel、HTML/网页和图片作为独立解析器增加。解析器不负责聊天、模型调用或索引查询。
