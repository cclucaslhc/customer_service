# 云小智 AI 客服 MVP

一个本地运行的中文 AI 客服原型。后端使用 FastAPI、PydanticAI 和独立 `ModelGateway`，默认支持 DeepSeek，可选支持 OpenAI；企业知识使用 SQLite FTS5/BM25 检索，不生成向量。

## 已实现

- React 流式聊天界面、停止生成和来源引用。
- 管理页录入、测试和切换 DeepSeek/OpenAI。
- API Key 使用本机主密钥和 AES-256-GCM 加密，读取接口不回显明文。
- PDF、DOCX、Markdown、TXT 上传、解析、中文分词和全文索引。
- 模拟订单、物流、退款资格、营业时间等 PydanticAI 工具。
- 联系方式本地登记。
- 单会话运行取消与新消息抢占基础设施。

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
