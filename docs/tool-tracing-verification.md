# 2026-10-08 工具与检索追踪验收

本批继续S2，不缩小S0–S7目标，不授权真实模型/业务调用或上线。Schema仍为0019_trace_context，无新增迁移、公开HTTP/SSE字段或前端行为。

代码：app/tracing.py增加六工具固定名字、tool.execute/rag.search、内部attempt_id与run上下文；app/agent.py六工具装饰器和授权后的知识检索边界。保留签名、同步/异步执行方式和原业务结果。导出继续使用已有有界线程和显式调用方所有权，无自动供应商/Collector配置。

新增tests/test_tool_tracing.py五项专项：真实PydanticAI FunctionModel在四个并发Run实际执行六工具；36个Span验证trace/parent/request_id/run_id及每Run六个独立attempt_id，RAG继承父工具编号。知识返回、用户输入、订单/手机号/表单均使用合成SECRET标记，Span没有泄露。失权不进入知识访问；检索异常、async取消、to_thread上下文恢复、未知标签拒绝、关闭追踪/签名兼容。该模型无网络，不证明真实供应商能力或真实业务权限适配器验收。

工具/追踪/身份专项：**13 passed，2.27秒**。完整隔离PG/Prometheus/promtool回归：**316 passed / 45 skipped，405.50秒**，退出码0；41项S3和4项Redis仍跳过，不能算通过。前端**38 passed，2.00秒**及构建通过；编译、pip check、相关Markdown链接及git diff --check通过。收尾检查本批PG的cs_test_/cs_browser_/cs_backup_verify_数据库为空，精确停止本批临时cluster；证据文件与测试运行时保留，不触及实际8000服务或其他数据库服务。

限制：completed仅表示函数返回，未找到订单等业务失败仍是正常返回，不充当业务成功率。attempt_id只在Trace中，不充当持久审计、费用账本或写操作幂等编号。此处RAG只覆盖Agent知识工具内knowledge.search，不代表管理搜索、Embedding/重排/数据库/对象存储全链。仍缺model attempt、知识Worker、生产OTLP/平台、费用与其余阶段；本批未新浏览器验收，不绕过先前限制。

关联：[主验收记录](production-verification.md)、[观测规范](observability.md)、[实施清单](production-roadmap.md)。
