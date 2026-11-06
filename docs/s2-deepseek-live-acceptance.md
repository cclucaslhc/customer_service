# S2 现有 DeepSeek 配置真实验证

2026-10-28，用户明确授权使用管理界面已有DeepSeek配置。仅通过应用ModelGateway/SecretStore获取该配置，不扫描环境历史API Key，不输出密钥，不发送公司文档或历史聊天。

新增显式opt-in `scripts/verify_configured_deepseek.py`：仅active provider为DeepSeek时获取配置，合成固定提示、无工具、max_tokens16、request_limit1、关闭Agent验证重试，45秒超时；证据只包含供应商/模型、用量计数与范围，不记录返回正文或原始异常。它不属于默认pytest真实调用，普通测试只用FunctionModel。

首次实际探针在取得模型返回后使用旧SDK的`result.usage()`写法，遇到TypeError且未保存用量。现SDK为usage属性；已修复并离线回归。首次调用必须保留为可能已计费、用量证据缺失，不伪造其费用/Token，也不当作没有发出。之后另发的新合成探针成功，不能用它覆盖首次未知记录。

成功探针：deepseek-chat；供应商返回input_tokens21、output_tokens3、cache_read/write均0、requests1，elapsed1.0秒；固定预期回复校验通过。仓库外证据：`D:/codex_project/customer_service_vm_lab_20261026/s2-r2-output-20261028/deepseek-live-generation-20261028.json`。

此项证明管理配置、真实生成和返回用量可用，**不证明PostgreSQL预算/attempt账本、合同价格或供应商账单已对账**。当前Windows本机管理服务使用本地SQLite配置，隔离VM为另一套PostgreSQL配置；不得把两个配置域混同，也未将真实密钥自动复制到测试VM或PG。后续正式受保护调用需明确目标配置与已批准价格/预算，再核对供应商账单，包含首次未知调用。

## 2026-10-29 隔离 PG 计量与预算真实传输补验

仓库外受控探针`customer_service_vm_lab_20261026/test_s2_authorized_live_accounting.py`，SHA256 `f7a3424558d1fcf4e87698e0dee3ca30e836931b81d6b04462b8637735599b37`。先FunctionModel干跑1 passed，再明确授权模式真实传输1 passed/0 skipped（5.69秒）；每批新建独立loopback PG容器、随机数据库、合成租户与Run，最终只清理自己的隔离资源。探针仅通过现有ModelGateway/SecretStore使用用户已授权管理配置，不读取环境Key、不复制密钥到PG。

真实一轮deepseek-chat：requests1、input21/output3、cache read/write0。生产ObservedModel与ModelUsageRepository路径执行调用前started/quote/reservation提交，独立连接在真实SDK请求前验证started为unknown且已有预留；返回后四项Token与PG终态逐项一致、恰一条completed/observed。第二次直接预算准入被max_run_requests=1拒绝，真实SDK封装入口仍只有1次，不再发送第二个请求。最大输出16 Token、无工具、无公司文档或历史消息；结果正文不写证据。

JSON为实验室`s2-r2-output-20261028/deepseek-pg-accounting-20261029.json`，JUnit为`s2-accounting-live-20261029.xml`；干跑为独立dry文件，不能冒充真实调用。本次**另有一轮真实API调用**，不覆盖前次成功或首次取证失败的可能计费未知记录。

价格/预算为隔离合成参数，仅证明报价绑定、持久入账、原子预留与真实SDK接口兼容，**不是供应商合同或真实费用对账**；不改变生产租户价格、默认SQLite、VM配置或密钥。专用运行角色边界由另外的角色集成组证明，本探针不冒充真实ChatWorker整机部署、供应商故障切换或生产容量。真实账单按用户要求跳过，仍标未验证。
