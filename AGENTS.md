# 项目多会话协作规则

本项目采用“一个总控会话 + 四个模块会话”的 Worktree 协作方式。开始修改前必须阅读：

1. `docs/architecture.md`
2. `docs/api-contracts.md`
3. `docs/collaboration.md`
4. `docs/module-status.md`

## 模块所有权

| 模块 | 主要所有文件 | 不得直接修改 |
| --- | --- | --- |
| 前端 | `frontend/**` | `app/**`、数据库结构、Agent 工具 |
| 后端 | `app/main.py`、`app/chat.py`、`app/database.py`、`app/schemas.py`、`app/security.py`、`app/settings.py`、`main.py` | `frontend/**`、`app/agent.py`、`app/knowledge.py` |
| Agent | `app/agent.py`、`app/providers.py` | HTTP 路由、前端、知识解析与索引实现 |
| 知识库 | `app/knowledge.py` | Agent 提示词、前端、HTTP 路由 |

以下内容属于共享契约，只由总控会话或获得总控明确授权的会话修改：

- `docs/**`
- `AGENTS.md`
- `pyproject.toml`
- 根目录 `README.md`
- 跨模块数据库表结构
- 公共 API、SSE 事件和错误码

## 工作规则

- 每个会话只处理一个模块；需要跨模块修改时，不直接越界编辑，先在交接摘要中提出契约变更。
- 开始任务前执行 `git status --short`，不得覆盖其他会话或用户的未提交修改。
- 公共接口变化必须同步更新 `docs/api-contracts.md`，并由总控通知受影响模块。
- 新功能必须带模块级测试；完成后报告测试命令与结果。
- 不读取、输出或提交 `data/secrets/`、`.env*`、API Key、真实联系方式或生产数据。
- 不把 `data/app.db`、上传文档、构建产物、虚拟环境提交到 Git。
- 禁止对共享分支执行 `git reset --hard`、强制推送或改写他人提交历史。
- 每次提交保持单一模块、单一目的；提交信息使用 `type(module): summary`。

## 完成交接格式

```text
模块：
完成内容：
修改文件：
接口或契约变化：
对其他模块的影响：
验证命令与结果：
提交 SHA：
剩余问题：
```
