# Codex Worktree 多会话协作流程

依据官方 OpenAI 文档，Git Worktree 可让同一项目的多个 Codex 任务在独立检出中并行工作，避免直接干扰本地工作区：<https://learn.chatgpt.com/docs/environments/git-worktrees>。

## 会话角色

### 总控会话

- 维护架构、契约、模块状态和集成顺序。
- 创建或协调模块任务。
- 接收模块交接摘要，将接口变化转发给受影响模块。
- 合并提交、解决冲突、运行全量测试。
- 只有总控默认可以修改共享契约文件。

### 模块会话

- 前端、后端、Agent、知识库各一个长期会话。
- 每个会话使用独立 Worktree 和分支。
- 只修改 `AGENTS.md` 指定的所有文件。
- 不假设其他会话自动知道自己的聊天内容；以 Git 提交和契约文档为通信事实。

## 推荐分支

```text
main
codex/frontend-<topic>
codex/backend-<topic>
codex/agent-<topic>
codex/knowledge-<topic>
```

一个分支只服务一个模块任务。不要让两个 Worktree 检出同一个分支。

## 单次任务流程

1. 总控先确认需求、成功标准和受影响契约。
2. 如果涉及公共接口，总控先更新或批准 `docs/api-contracts.md` 的目标形态。
3. 模块会话从 `main` 的最新集成提交创建 Worktree。
4. 模块会话阅读 `AGENTS.md` 和 `docs/module-status.md`。
5. 在所有权范围内实现、测试并提交。
6. 模块会话按交接模板报告提交 SHA 和影响。
7. 总控审阅并合并；发现契约偏差时退回原模块修正，不在集成分支临时重写其实现。
8. 总控运行全量后端测试、前端构建和必要的浏览器验收。
9. 总控更新 `docs/module-status.md`。

## 跨模块通信

不同独立会话不会自动共享完整上下文，通信按以下优先级进行：

1. `docs/api-contracts.md`：稳定公共接口。
2. Git 提交：可审查的代码事实。
3. 结构化交接摘要：任务结果与影响。
4. 总控发送的显式后续消息：临时协调和阻塞解除。

禁止仅在某个聊天中约定公共字段而不更新契约。

## 冲突处理

- 模块所有权冲突：由文件所有者实现，提出需求的会话只描述期望契约。
- 共享文档冲突：由总控处理。
- API 与实现不一致：以已批准的 `docs/api-contracts.md` 为目标，修正实现。
- 两个模块需要同一原子变更：拆为按依赖顺序的两个提交，或由总控建立短期集成任务。
- 合并前基线过旧：模块分支先合入最新 `main` 并重新测试；不得强推覆盖。

## 验证门槛

所有模块提交前至少执行相关测试。总控集成时执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
Set-Location frontend
npm.cmd run build
```

涉及 UI、SSE 或浏览器行为时，还需启动本地服务进行浏览器验收。涉及真实模型时，必须由用户在管理页显式提供密钥后才能测试。

## 模块任务提示词模板

```text
你负责【模块名】模块，只修改 AGENTS.md 中该模块拥有的文件。
开始前阅读 docs/architecture.md、docs/api-contracts.md、docs/module-status.md。
目标：【目标】
验收标准：【标准】
如果需要改变公共契约，不要直接修改其他模块，先输出契约变更请求。
完成后运行模块测试、提交代码，并按 docs/collaboration.md 的交接格式汇报。
```
