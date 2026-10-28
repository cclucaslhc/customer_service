# S2 现有 DeepSeek 配置真实验证

2026-10-28，用户明确授权使用管理界面已有DeepSeek配置。仅通过应用ModelGateway/SecretStore获取该配置，不扫描环境历史API Key，不输出密钥，不发送公司文档或历史聊天。

新增显式opt-in `scripts/verify_configured_deepseek.py`：仅active provider为DeepSeek时获取配置，合成固定提示、无工具、max_tokens16、request_limit1、关闭Agent验证重试，45秒超时；证据只包含供应商/模型、用量计数与范围，不记录返回正文或原始异常。它不属于默认pytest真实调用，普通测试只用FunctionModel。

首次实际探针在取得模型返回后使用旧SDK的`result.usage()`写法，遇到TypeError且未保存用量。现SDK为usage属性；已修复并离线回归。首次调用必须保留为可能已计费、用量证据缺失，不伪造其费用/Token，也不当作没有发出。之后另发的新合成探针成功，不能用它覆盖首次未知记录。

成功探针：deepseek-chat；供应商返回input_tokens21、output_tokens3、cache_read/write均0、requests1，elapsed1.0秒；固定预期回复校验通过。仓库外证据：`D:/codex_project/customer_service_vm_lab_20261026/s2-r2-output-20261028/deepseek-live-generation-20261028.json`。

此项证明管理配置、真实生成和返回用量可用，**不证明PostgreSQL预算/attempt账本、合同价格或供应商账单已对账**。当前Windows本机管理服务使用本地SQLite配置，隔离VM为另一套PostgreSQL配置；不得把两个配置域混同，也未将真实密钥自动复制到测试VM或PG。后续正式受保护调用需明确目标配置与已批准价格/预算，再核对供应商账单，包含首次未知调用。
