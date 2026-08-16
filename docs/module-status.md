# 模块状态

本文件由总控会话维护。模块会话通过交接摘要报告，不直接修改本文件。

| 模块 | 当前基线 | 状态 | 下一优先事项 |
| --- | --- | --- | --- |
| 前端 | React 聊天与管理页面、SSE 消费 | 首版完成 | 拆分 `/chat` 与 `/admin`，增加管理鉴权后的登录态 |
| 后端 | FastAPI、SQLite、SSE、取消、密钥加密 | 首版完成 | 管理 API 鉴权、数据库迁移机制 |
| Agent | PydanticAI、DeepSeek/OpenAI ModelGateway、模拟工具 | 首版完成 | 配置真实 DeepSeek 后执行跨模型评测 |
| 知识库 | PDF/DOCX/MD/TXT、Jieba、FTS5/BM25 | 首版完成 | 评估 OCR、Excel、HTML 解析器与检索质量 |

## 当前共享约束

- 默认供应商为 DeepSeek，但没有保存任何真实 API Key。
- OpenAI 仅保留适配入口，不自动配置或调用。
- 本地知识库不生成向量。
- 管理界面与聊天界面目前在同一 SPA 中；公网使用前必须拆分路由并增加鉴权。
- 首版为单 Agent；没有证据表明当前需要多 Agent。

## 待总控决策

- 管理员认证方式和会话有效期。
- 多 Agent 的触发指标、路由策略和评测基线。
- OCR、Excel、网页等文档类型的优先级及解析质量标准。
