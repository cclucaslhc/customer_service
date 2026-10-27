# 灰度浏览器/SSE验收（2026-10-26）

本批为隔离合成验收，不是生产部署或真实模型质量验收。head保持0036；新镜像尚未构建。

## 已证实结果

真实浏览器操作构建后的前端，经实际HTTP、Cookie/CSRF、SSE、独立Worker、PostgreSQL及pgvector完成两轮：

1. 正常问题“退货需要什么？”：页面显示“退货需要订单号。”与合成来源。
2. “暂停测试：退货需要什么？”：Worker检索后由测试控制器暂停，页面显示停止，不交付旧答案。
3. 刷新页面：只有第一轮客服答案和第二轮用户问题，没有补出第二条答案或重放运行。

关闭前数据库断言：run终态为complete/interrupted，两个run均固定candidate索引，assistant消息恰1条，首轮权威token正文完整，暂停轮次token数0，未创建模型密钥文件。测试为 **1 passed（660.50秒）**，时长包含人工式浏览器操作及工具等待，不是服务响应性能。模型为FunctionModel、Embedding为合成单位向量，无在线模型调用、无真实公司材料。

截图保存于本机忽略目录`data/acceptance/rag-browser-pause-20261026.png`，不提交Git。正常正文与暂停场景均有可见页面证据。

## 验收发现与修复

浏览器首次发现正常轮次数据库已有答案、页面却显示“回答完成/思考中”。原因：知识库草稿在complete_run提交后才由Orchestrator释放，Worker禁止向终态run追加事件，因此正文未进入耐久SSE账本；done仅带message_id，前端没有最终正文可显示。

现由run_events.terminal在assistant消息同一事务内写入answer_reset、完整正文token分块、citations/contact、done和终态快照。旧草稿先重置，避免普通流式回答被重复拼接；每块最多8192个Unicode字符，现有单事件、总字节及事件数量预算仍强制执行，失败整体回滚。不允许Worker提交后补写、不提前泄露知识草稿。新增Unicode多块重组与Worker正常最终token断言。

最终相关真实PG队列/路由专项 **25 passed（90.69秒）**；无外部服务后端 **401 passed/343 skipped（32.60秒）**；前端 **40 passed**、build成功。组有重叠，不相加；跳过不算通过。

## 夹具与范围

`tests/test_rag_browser.py`仅在TEST_RAG_BROWSER=1且提供专用TEST_POSTGRES_URL时运行。正常单元/CI不会启动交互服务。其生命周期拥有临时cs_test数据库、独立Worker及随机本机端口；只监听127.0.0.87，用独立loopback主机隔离用户127.0.0.1开发会话cookie。生产app没有测试入口。

最终成功轮使用夹具预认证storage state：pytest内临时挂载`/__browser_fixture_entry`，只把夹具已创建的合成登录会话写入正常HttpOnly/SameSite cookie后转到预建会话；所有业务HTTP/SSE依旧通过未修改的身份/CSRF边界。这不计为登录表单、正式HTTPS Cookie或生产身份供应验收。之前正常登录表单曾在失败轮走通，但不拿该轮支持完整通过。

最早一轮因浏览器工具持续超时接近审批期限而主动终止；第二轮发现SSE正文遗漏而中止；修复后的一次服务因跨turn环境结束失去进程/监听，未计通过。最终从新隔离实例启动成功轮。没有延长已批准记录、关闭期限校验或放宽断言；较长有效期来自新建合成审批计划。

复跑时先构建frontend并启动独立PG，执行带上述测试环境变量的pytest -q -s tests/test_rag_browser.py；按BROWSER_READY给出的URL操作两轮、刷新检查，再在输出的精确pytest临时目录创建browser-stop标记。测试会检查账本、停止HTTP/Worker并由fixture删除临时数据库；只清理本轮专用容器，不触碰其他应用容器。

## 仍未完成

可信真实审批/控制入口、观察窗口和阶段推进/最终提升、自动回切、完整加密恢复、新镜像/供应链再验、真实模型与费用/质量/负载、正式双Linux主机及故障验收均未因此完成。S2–S6总目标保持进行中。
