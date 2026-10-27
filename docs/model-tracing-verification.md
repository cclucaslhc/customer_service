# 2026-10-08 模型请求边界追踪验收

本批继续完整S0–S7目标，Schema仍为0019_trace_context，无迁移/公开接口变更、真实模型密钥或外网调用。

app/model_observability.py基于PydanticAI公共WrapperModel.request/request_stream转发，ModelGateway真实构建和ChatOrchestrator替代gateway路径均接通。已包装对象幂等复用且标签错配拒绝；固定provider/stream及内部UUID attempt_id继承Run/request，未导出模型名、URL、请求/响应、密钥或供应商自由metadata。流式保持Scope至供应商context teardown结束，SDK公开state区分完整、提前关闭、取消，读取和清理异常失败。用量和结果不复制/改写为账单。

tests/test_model_tracing.py九项：实际PydanticAI工具往返加输出校验重试有三个独立请求Span；流式自然EOF/早退/显式stream.cancel/任务取消/读取错误；供应商context清理错误；实际DeepSeek兼容SDK通过内存MockTransport解码11/7Token和原始合成回答；固定标签/幂等包装/Model profile接口兼容。HTTP transport只在内存，不连接外网；合成Key不写密钥文件。SECRET输入/输出/异常/模型名/凭据/provider ID不在Span中。

第一轮专项 **1 failed / 8 passed，3.61秒**：新增测试误用旧SDK的result.usage()，现版本是usage属性；修复后模型/工具/追踪专项 **20 passed，3.73秒**。原聊天/韧性/工具/追踪回归 **48 passed，12.43秒**。最终全量隔离PG/Prometheus/promtool回归 **325 passed / 45 skipped，413.58秒**，退出码0；41项S3和4项Redis跳过不算通过。前端 **38 passed，1.86秒**及构建通过；编译、pip check、相关Markdown链接和git diff --check通过。初次失败保留，不能算通过。

现有真实PG/API→独立Python Worker验收增加model.request断言并在本批全量实际运行通过：与HTTP/Worker/Agent同trace，parent为agent.run，内部Run/request与模型attempt编号合法、stream=True、complete业务终态。收尾检查本批cs_test_/cs_browser_/cs_backup_verify_数据库为空，精确停止临时cluster，保留测试运行时与证据；未触及实际8000服务及其他数据库服务。

限制：当前一个Span是一SDK调用，不证明SDK内部HTTP重试逐次计量。SDK返回complete不证明答案质量；usage原样传递不证明持久计费或供应商账单已核对。未知用量不能按零费用解释。未覆盖管理界面连接测试、count_tokens/compact等其他潜在计费调用，没有耐久ledger、价格、预算、共享熔断/路由/本地模型或生产Collector出口。本批无新浏览器验收，不绕过既有导航限制。

关联：[主验收记录](production-verification.md)、[观测规范](observability.md)、[实施清单](production-roadmap.md)。
