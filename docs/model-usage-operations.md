# 模型用量、价格与对账操作边界

状态：实施中的SDK观测账本，不是完整计费、预算系统或上线授权。保留PydanticAI/ModelGateway，默认DeepSeek，密钥只由管理员在界面提供。

## 0021 价格快照与追加式费用

本文0021内容是历史增量；当前 head 为 `0023_knowledge_trace`，预算功能由0022迁移引入。以下未接通预算的历史描述由下面0022章节更新，其他外部验收限制保留。

0021阶段新增model_price_versions/model_price_quotes/model_cost_assessments；当前head已由0022预算迁移接续。默认空价格、不内置真实供应商报价、不自动抓取或启用供应商。管理员通过token-bound `ConfigurationRepository.model_costs()` 使用内部ModelCostRepository；没有新增HTTP/前端入口，正式费用配置界面和账单供应流程仍待实施。仅config.manage可发布/查询/对账，复查租户/角色/撤销/维护及恢复hold，受控短事务5秒statement/1秒lock超时。

`publish_price`要求publication_id UUID、expected_version、明确provider/model、CNY或USD、四类每百万Token费率（input/output/cache_read/cache_write）和来源sha256。值只接受Decimal或严格十进制字符串、非负且最多9位小数/每百万不超过1000000；float、bool、NaN/Infinity/科学计数字符串拒绝。无历史/未来回填：数据库published_at立即生效，tenant/provider/model串行版本递增。并发期望版本仅一个成功；同publication_id同参数/原期望版本幂等，变更拒绝。价格及审计同事务，审计失败回滚价格。

每个新SDK尝试与started记录同事务写不可变quote，按固定tenant/provider/model锁选择最新已发布version；无匹配价格固定NULL，不从其他租户/模型借价，也不在结束后补用新价。finish在用量终结同事务追加revision=0估算；旧0020无quote请求记no_quote，不伪造历史报价。完整SDK observed使用(普通输入×input_rate + cache_read×read_rate + cache_write×write_rate + output×output_rate)/1000000，普通输入=input-read-write。缓存总和超过输入、unknown/partial用量或无报价/无价格均amount=NULL；明确零费率与已知完整用量才可得到estimated零。使用Decimal高精度，金额保留9位小数向上取整；这只是保守估算，SDK计数可能为估计，不是供应商已核对金额。原SDK/HTTP/SSE结果不变；内部金额为Decimal，未来HTTP输出必须十进制字符串，不能隐式float。

`reconcile`要求request_id UUID、attempt_id、expected_revision、明确amount/currency及新receipt_sha256；固定租户尝试行锁，追加正revision和operator_reported状态，原用量/quote/价格/估算不改写。可以为started未知请求添加管理员账单声明，但不重发模型或把该SDK请求伪改completed。支持按原request/参数/期望revision幂等，冲突/并发旧revision拒绝；tenant范围receipt摘要唯一，不能重复关联两个attempt。修正需新的声明摘要/请求及最新期望revision，完整历史保留；当前金额按最高revision选取，不可把全部estimate与修正相加。引用摘要是管理员提供的声明，不代表系统已下载、认证或核实供应商原始账单；状态因此不是supplier_verified。币种必须匹配既有报价/声明，无隐式汇兑或跨币种汇总；来源与审计同事务，不保存原始发票/用户资料。

新三表UPDATE/DELETE/TRUNCATE和旧用量表TRUNCATE由0021附加trigger拒绝，0020不可变迁移保持原样；正式撤销DDL/禁用trigger/超管、RLS与独立归档仍待完成，不能当生产防篡改证明。当前维护清单34表SHARE封存/statement guard，SQLite导入/重放要求三费用表也为空，备份manifest纳入四用量/费用表；实际加密恢复比对价格/quote/金额/声明及摘要，恢复hold不重放旧收费或默认启用Worker。

## 执行与失败处理

### 预算前置：保守占用快照

内部管理员接口 `ModelCostRepository.exposure(run_id=None, utc_day=None)` 读取当前租户全部尝试，或限定租户内 Run / 调用开始时间所在 UTC 日。仅接受 Python date（非 datetime）；不存在或其他租户的 Run 拒绝，不借用他租户费用。数据库单条聚合语句保持一致快照，先按 attempt 取历史金额最大值，再按币种汇总，不把多个 revision 相加。CNY、USD 和无币种记录分别返回，金额为9位小数字符串。

每组 `attempts` 是尝试数量，`unresolved` 是没有完整 estimated 证据或存在混币种证据的数量，`retained_amount` 是已记录金额的保守历史峰值之和；只要 unresolved 非零，`amount` 就为 NULL。retained_amount 为零不表示未知请求免费；operator_reported 不能将 started/unknown/partial 自动变成已知，不论人工下调金额还是修改当前价格都不释放历史占用。该值是存储证据的保守汇总，不是供应商账单的数学上下界或经核验应付金额。

这仍是只读前置能力，**不得用 exposure → 比较额度 → 调用模型的非原子流程实施预算控制**。尚无预留表、调用前租户串行扣占、软阈值事件、输入上界/输出上限约束或未知占用释放机制；未启用任何限额，不改变现有请求执行。后续原子门控必须覆盖所有并发实例，处理旧无预留尝试及币种政策变化；不得以字符/Token 猜测或SDK估算宣称供应商硬账单上限。

PostgreSQL聊天（直接租约及耐久Worker）在每次普通/流式SDK请求前先提交model_usage_attempts.started，默认usage_status=unknown、Token列NULL；随后才执行模型网络调用。attempt_id独立UUID，与私有Trace一致，无Trace配置也必须入账。每事务5秒statement_timeout/1秒lock_timeout，不跨模型网络；Token绑定的ChatRepository复查租户、资源归属、配置provider/model、running及owner/fence/期限。调用取消后，仅同有效租约可记录当前请求结果；身份撤销、失租、维护/恢复hold失败关闭。

返回完整SDK正数用量为observed，提前关闭/取消/失败但有正数为partial，缺失/默认零/非法类型/负数/越界或缓存大于输入为unknown与NULL。原SDK结果/事件/用量不修改；不采集正文、自由usage.details、密钥或原始异常。模型配置名只存内部账本，不导出Trace。SDK可能估算Token，observed不等于供应商已报告或可计费；0020用量行没有金额，0021另存估算/声明，不能把unknown算0费用。

post-call账本写失败不授予成功助手持久化；未知外部结果保留started，不发起第二模型调用。进程崩溃留下started/unknown；本批没有自动结算/reaper，不根据运行失败推断免费。数据库故障、撤权/失租后的未结算记录需要受控追加对账能力，不能直接UPDATE修成成功。最终SDK返回状态completed也不证明答案质量。关闭SDK隐式重试后500/429/连接失败不在底层自动重发；后续网关有限重试必须每次重新登记attempt并遵守预算/总时限。

## 证据保护与恢复

0020需要受控停写/排空迁移，API/Worker精确head检查，downgrade禁止。FK绑定租户/Run/actor；数据时间/状态/Token约束，终结记录的SQL UPDATE与DELETE被trigger拒绝；不是独立审计归档，也不防具有禁用trigger/DDL权限的数据库超级用户。0020历史限制为正常运行未拦截TRUNCATE，0021已附加不可清表trigger；正式应用角色仍须撤销TRUNCATE/DDL/禁用trigger权限，最小权限/RLS/保留策略待实施，不能当生产不可篡改账本。

0017历史迁移输入不变，0020时31表、0021当前维护清单maintenance_current为34表SHARE封存与statement写guard；旧备份/恢复制品需schema匹配。SQLite快照导入和幂等重放要求目标用量及费用表为空，不把旧历史消息伪造为计费记录。全表manifest含这些表，实际加密备份恢复比对行数/摘要/Token及终结不可改写，恢复hold保持阻断，不启用Worker或自动重试旧请求。

## 仍需实施

供应商usage provenance/实际账单认证核验、更多计价规则/货币/汇兑，Run/租户/日原子预算预留与软告警硬限额、未结算预留保留与释放规则；管理端连接测试、count_tokens/compact、Embedding/Reranker/OCR计费路径；费用HTTP/管理界面、指标、真实独立账单盲核对和生产权限。账本不能依赖采样/可能丢弃的Trace，operator_reported不能升级为真实供应商验证。默认SQLite仍为MVP，无自动资金控制；不得因此宣称完整S2或生产上线。

官方资料核对使用 [OpenAI官方库说明](https://developers.openai.com/api/docs/libraries) 和 [错误指南](https://developers.openai.com/api/docs/guides/error-codes)：后者说明429/Retry-After处理，但两页未明确本项目安装版本的max_retries参数。本批具体with_options(max_retries=0)由已安装客户端公开接口及内存HTTP500一次请求测试验证，不据官方页面声称已验收DeepSeek真实供应商。没有创建OpenAI配置或读取历史Key。

关联：[观测规范](observability.md)、[迁移方案](postgresql-migration.md)、[验收记录](production-verification.md)。

## 跨模型备用尝试边界（2026-10-17 增量）

认证 PostgreSQL Run 可记录不同于入队主模型的实际供应商/模型 attempt。备用在线模型开始前须匹配本租户当前显式授权、已配置密钥和模型名；本地备用须有本租户模型配置，节点回环端点由网关另行校验。缺失、损坏或已撤销授权均拒绝新 attempt，已开始的尝试仍须完成用量终结。当前预算策略只覆盖一个模型，若备用模型不匹配已启用预算则失败关闭。**尚无自动候选路由和跨模型预算验收**；不要通过手工构造 meter 将账本能力误认为已具备故障切换。

