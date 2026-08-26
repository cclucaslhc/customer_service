# 云小智 AI 客服 MVP

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

- 主密钥首次启动时生成在 `data/secrets/master.key`。
- 供应商 API Key 加密保存在 `data/app.db`，管理 API 不会返回明文。
- `data/secrets`、数据库和上传文件均已加入 `.gitignore`。
- 当前版本无登录机制，服务默认只绑定 `127.0.0.1`，不要直接暴露到公网。
- 主密钥丢失后无法恢复已保存的供应商密钥，需要重新录入。

## 后续扩展点

- 多 Agent：在 `ChatOrchestrator` 与 PydanticAI Agent 之间增加意图路由，不改变 ModelGateway 和业务工具。
- 文档类型：为扫描件 OCR、Excel、网页分别增加解析器，统一输出结构化章节后进入现有 BM25 索引。
- 真实工具：将模拟订单服务替换为有权限控制、审计和幂等性的业务 API。
- 复杂流程：真正退款或取消订单时使用 PydanticAI Deferred Tools；出现跨天、多节点流程后再评估 LangGraph 或耐久执行平台。
