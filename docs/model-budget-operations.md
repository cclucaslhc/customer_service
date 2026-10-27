# 原子模型预算预留（0022保守模式）

状态：内部应用门控已接入，完整财务系统及真实供应商/生产验收未完成。默认没有策略、不启用预算，不自动发现价格、密钥或供应商。保留PydanticAI、ModelGateway及默认DeepSeek。当前 Schema head 为 `0025_model_circuit`，在 0024 合同之上增加共享熔断状态；下文的 0022/0023/0024 “当前 head”限制是对应批次的历史记录。

## 0024 备用模型 Token 合同

`0024_model_route_budget` 增加每租户、目标供应商和模型的版本化不可变 `model_route_budget_policies`，并使预留账本以 `route_policy_id` 固定实际使用的合同版本。仅支持 `openai` 和 `local` 作为备用目标；发布前必须有租户主预算，币种必须一致，且目标模型必须另有匹配价格。备用合同批准单请求输入 Token 上界和输出 Token 限额；金额仍计入原租户的 tenant/UTC 日/Run 主预算，不创建第二份独立额度。缺合同、缺价格、币种不符或超额均在 Provider 调用前失败关闭。管理员通过 `GET/PUT /api/admin/model-route-budget/{provider}` 查看/发布，需 `config.manage`，版本、发布编号和审计与主预算同样受控。管理页可选 OpenAI/本地模型查看和发布；缺主预算或价格时不可提交。发布合同不会开启跨供应商外发授权，也不会自动启用故障切换。

真实隔离 PostgreSQL 的预算、备用授权、用量、费用、耐久聊天和 HTTP 七组联合 **69 passed / 0 skipped**，其中包含请求级路由、SSE 实际供应商标记及加密备份恢复；专用运行角色测试在独立 `::1:55433` 集群 **1 passed / 0 skipped**。这只证明当前应用侧合同、账本和隔离路由路径；供应商账单、模型真实 Token 计量、受控释放、共享熔断及生产双机仍未验收。

## 策略与模型合同

预算功能由 `0022_model_budget` 引入；当前 Schema head 已接续至 `0023_knowledge_trace`，该迁移只增加知识队列内部 Trace 字段，不改变预算表。0022 新增不可变model_budget_policies/model_budget_reservations，维护清单36表；旧迁移不改。管理员通过token-bound `ConfigurationRepository.model_budgets()` 发布策略，仅config.manage，重新检查租户/身份/维护/恢复hold。publication_id、expected_version实现幂等/并发版本校验，与审计同事务提交，审计失败回滚。需明确provider/model、CNY或USD、tenant/day/run金额限额、input_token_ceiling/output_token_limit/max_run_requests/soft_percent及来源SHA256。金额为严格Decimal或十进制字符串，整数拒绝bool。当前策略只批准一个精确模型，不是多供应商预算路由；历史策略存在时不能隐式换币。

input_token_ceiling是管理员按模型合同批准的**单请求全部可计费输入上界**，包括历史、系统规则、工具Schema/结果和供应商输入开销；不是字符数/UTF8猜Token，不执行远程count_tokens。批准摘要不表示系统已认证合同，真实Token/隐藏计费/供应商限额遵从仍待验收。预算启用后只支持当前文本和函数工具计价面；媒体、native tools、结构化/推理输出、always-thinking Profile、未知Provider设置和extra_body拒绝。合并模型默认settings后检查，max_tokens为管理员与请求的较小者；不接受零/负/布尔/未知上限。

**本门控控制应用侧保守占用，不提供供应商硬账单保证。** 当前四项Token费率要求输入缓存分类互斥；供应商更多计价、Tokenizer、上下界、限额遵从及真实账单仍需独立验收。Embedding/Rerank/OCR/连接测试等其他计费路径尚未接入。

## 原子准入与额度

普通和流式SDK调用均先短事务复查身份、Run和owner/fence/租约，取得tenant级PG advisory transaction lock，然后写started、固定quote、选择最新策略、聚合历史占用、写不可变reservation。提交后才进入Provider，不跨网络持有数据库事务。策略发布、用量结束和人工对账共用租户锁，跨实例串行门控，不采用只读检查后调用。

预留金额：`(input_token_ceiling × max(input/cache_read/cache_write费率) + capped_output × output费率)/1000000`，9位小数向上取整。金额检查tenant生命周期、调用开始UTC日和Run三个范围；超过拒绝，恰好等于允许。每Run尝试次数独立限制，明确零价格也不能无限调用。缺匹配价格、币种不同、旧无预留未知记录或不支持的请求失败关闭；拒绝回滚attempt/quote/reservation，不进入Provider。

历史占用按attempt取全部金额声明峰值，与reservation取更大者，不将修订相加。未知旧请求不能靠operator_reported变成已知；只要有未预留未知，整租户新调用拒绝。已知估算或追加声明超过预留会抬高后续占用，阻断继续收费；不能撤回已发生/并发中的外部费用。

当前采取**永久保守预留**：成功、失败、取消、崩溃、过期Run和人工下调都不释放；SDK估算不退款，新策略不重置旧占用。管理员可以明确升额，没有自动释放、退款、按月重置或供应商核验结算。这避免未知免费与重复用额，但可能比实际账单早耗尽预算；受控结算释放与生产可用性仍待实现，不能算完整预算/财务能力已交付。

## 管理配置、软阈值与用户错误

管理员登录后的模型配置页可读取当前租户价格版本、预算版本、租户/UTC日已占用与剩余额度、未知占用及软阈值事件数，并发布明确价格/预算。`GET /api/admin/model-budget`、`GET /api/admin/model-prices/{provider}?model=...`、两个同路径PUT接口均需config.manage；客户/客服无法访问。价格和额度均为十进制字符串，UUID publication_id与expected_version由客户端提交；重复发布同参数幂等，旧版本或冲突返回409。UI不会获取或回显密钥。来源SHA256由管理员核对的合同/价格依据计算，发布只保存摘要，不验证供应商原始文件。管理员发布预算前必须自行确认供应商输入上界与计费规则，界面展示合同限制提示。

显式开启私有指标监听时，API通过独立有界导出线程从PostgreSQL读取耐久预算证据：预算策略租户数、预留总数、tenant/UTC日/Run各自首次达软阈值的不同范围数、Run终态预算拒绝数、已保护租户中的无预留未知账本数，以及采集成功/数据库时间戳。指标不含租户ID、Run ID、模型、币种、金额、密钥或用户输入；只从独立认证的loopback `/metrics` 输出。PostgreSQL只读事务设置短超时，失败公开success=0且不输出原始异常。该总数适合全局告警，具体租户需管理员授权页面/账本定位。

[Prometheus规则](../ops/prometheus/model-budget-alerts.yml)覆盖采集失败/停更、新软阈值事件、预算拒绝与已保护租户未知账本；[promtool用例](../ops/prometheus/model-budget-alerts.test.yml)验证健康/触发/故障场景。规则只在配置独立安全采集与可验证的Alertmanager receiver后才会实际送达通知；尚无生产collector、值班接收端或送达演练。Counter从数据库事实聚合，删除/DDL及重建历史会破坏单调性，应用层触发器和备份不能防超级用户篡改；同一库多个API实例会输出相同全库计数，部署时须只指定一个预算采集target或先做去重，不能把各实例相加。

## 用户错误与操作边界

reservation持久记录tenant_soft/day_soft/run_soft，达到soft_percent即true。内部管理员 `reservations(run_id=...)` 在本租户Run授权后读取。私有指标及Prometheus规则已经提供；没有生产Alertmanager接收端或送达验收，不能将规则测试当作实际通知。旧 `ModelCostRepository.exposure` 仍只是费用证据视图，**不含reservation**，不得用它当剩余额度或门控。

聊天失败发送answer_reset和固定安全error：budget_exceeded为金额/次数达限，budget_unavailable为缺价/旧未知/不支持计价。Run为error，不保存助手、不发送done，不提示改密钥或自动重试。迁移/导入/备份联动：新两表不可UPDATE/DELETE/TRUNCATE，36表维护封存，SQLite导入要求预算表为空，全表加密恢复保留策略/预留和recovery hold，不能重放收费。

新版本需受控停写/排空后迁移到当前 head（现为0025），应用精确head检查；不自动迁移/切库/启用策略，downgrade禁止。正式应用角色必须撤销DDL/禁用trigger/清表权限；当前触发器不防超级用户篡改。供应商认可的输入界限、请求级路由与共享熔断的真实双机故障注入、更多计费路径、受控结算释放、真正外部通知与生产容量仍缺。0024 管理页已完成隔离浏览器检查，不等于正式双机浏览器验收。

关联：[费用账本](model-usage-operations.md)、[路线图](production-roadmap.md)、[验收记录](production-verification.md)。
