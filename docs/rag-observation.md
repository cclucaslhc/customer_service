# 灰度操作观察窗口（0037，尚不自动推进）

`RagRolloutService.observe`仅供owner调用，要求显式tenant/rollout/version/plan摘要与运行中状态。它从PostgreSQL生成当前阶段候选请求的操作指标，不接受请求方上传的统计，也不改变流量、版本或发布状态。返回的thresholds_satisfied不是质量审阅或发布授权；[advance内部服务](rag-rollout-advance.md)必须在自己的事务内重新采样并复查审批，不能消费旧报告切流。

## 阶段时钟与范围

0037新增rag_rollouts.step_started_at，start在UPDATE语句内使用数据库clock_timestamp设置。SQL触发器禁止同一步重置时间、回填旧时间、未来时间，以及未刷新时钟就切换步骤；新时间必须落在当次statement_timestamp与clock_timestamp之间，并处于计划有效期内。暂停/撤回及恢复复核保留历史阶段时间，不能借清空时钟重置观察期。

采样只包含同租户、同rollout、同route_version且实际选中候选index的run，不混入baseline或旧epoch；模型/Prompt摘要不匹配的候选run计入invalid_context_runs并阻止通过，不静默丢弃。相关JSON字段有局部索引，查询仍需真实规模容量验收，未承诺生产吞吐。

## 账本指标

一次MVCC查询生成计数、nearest-rank P95及费用信息，不导出问题、答案、用户行或会话摘要。requests分为complete、error/interrupted、queued/running；任何未完成请求阻止通过。P95采用run.started_at至finished_at的毫秒差，所有终态都计入，缺失/逆序/晚于观察时间的终态时间阻止通过。跨主机时钟/NTP和真实排队延迟仍需正式验收。

每个run累加所有模型attempt的最新费用assessment，费用均已知、attempt已结束、时间不晚于观察点且币种一致才视为完整。无attempt、任一未定价/缺assessment/币种错误/未结束attempt都算unknown_cost；SQL对NULL显式视为未知，不能被bool_and忽略。max_cost_per_request取“每轮累计费用”的最大值，不是平均值，也不是单attempt最大值，避免多次调用被低估。

estimated是SDK用量估算，operator_reported是带receipt摘要的受控操作员账本，不等于供应商账单已独立核验。窗口不声称财务结算精确，也不证明答案支持率、正确拒答或无泄漏；这些仍依赖独立质量证据和已冻结审批。

## 失败关闭

健康判定新增 `unknown_terminal_cost_runs`，SQL单独统计已结束且费用未知的run，并校验它与总未知费用、pending计数的集合约束。`assess_health`区分hold/healthy/pause：正常pending、样本不足或观察期不足为hold；终态费用缺失、终态时间错误、上下文错误等为pause；错误率/P95/费用阈值仅在窗口成熟且无pending时判定。

`RagRolloutService.check_health_and_pause`现为owner内部健康tick：显式核对rollout与release回执后，在排他发布锁中复查审批、绑定、manifest及账本窗口。审批撤销或上下文失效可触发暂停，不要求过期审批继续有效；旧调用方回执直接拒绝，不能拿它作为暂停证据。pause将basis_points归零、版本递增与`rollout_health_pause`聚合审计同事务提交；审计或数据库故障向上抛出并回滚，不能返回假成功。hold/healthy不改版本，不推进、更不恢复流量。

## 显式监控入口

`scripts/rag_rollout_watch.py`默认只检查一次；指定`--checks N --interval-seconds 30`才重复运行（最多1000000次，间隔1–60秒）。使用`--database-url-file`提供私有owner连接文件；`--database-url`仅允许回环测试连接。启动检查数据库恰为当前schema head，始终只检查控制文件指定的单个rollout，无扫描、自动推进、自动恢复或回执刷新。

`--control-file`为绝对路径、非符号链接、最大64KiB的私有单行JSON文件；Linux权限须600或更严。字段严格为`tenant_id,rollout_id,expected_rollout_version,expected_plan_sha256,expected_release_version,expected_body_sha256,current_binding`，对应start成功后的当前回执。每次检查重新读取；`current_binding`须由可信部署控制器根据实际运行配置更新，禁止直接复制审批表来假冒“当前配置”。重复JSON键被拒绝。控制文件不是新增的审批授权，也不含登录token或模型密钥；Windows须操作者配置文件ACL。

退出码0表示限定次数完成（包括hold，不代表发布质量通过）；2表示已提交暂停；1表示错误、没有可依赖的暂停成功结论；130表示人为中断。输出仅decision/state/version/reason_codes，不打印连接URL或binding。遇回执变更或数据库失败立即退出，不自动重试/追随新阶段；运维须告警并核查真实状态，重新核准控制文件后才能重启。此脚本尚未部署为正式守护服务，也未验证进程被杀后的监控接管、生产告警或双机HA。

### Linux监督模板（尚未部署）

`ops/systemd/customer-service-rag-watch.service`提供专用低权限Unix账户、只读文件系统、无额外capability的监督模板。owner数据库角色与API/Worker角色分离；数据库凭据只能由该控制账户读取，禁止复用Web服务账户。部署路径、Python环境、账户及文件ACL须由运维实际建立并核验。本模板面向主机venv，不是将distroless镜像当作含Python命令的主机目录。

模板`Restart=no`，不把退出2设为成功；暂停、错误、RuntimeMaxSec=7d超时均进入systemd失败处理。`OnFailure=customer-service-ops-alert@%n.service`是必须由运维提供的本地告警适配器，不是已接通的通知渠道。启用前须验证该handler存在、无凭据泄漏且能送达值班人，并测试停止/被杀/数据库不可用/回执过期等场景。正常退出也可能意味着检查次数耗尽，须独立监控unit存活与journal新鲜度，不能只依赖OnFailure。不得在未完成上述验证前启用正式灰度；模板未安装、未自动启动，当前Windows仅有静态契约测试，不算Linux监督或告警送达验收。

运维顺序：准备专用账户及600文件 → 在目标Linux运行`systemd-analyze verify`核验模板和告警handler → 安装但不启用 → 隔离环境手动启动并故障注入 → 验证告警和真实数据库归零状态 → 明确值班责任后才批准启用。灰度推进改变回执后，必须先核实新部署binding并更新控制文件，再显式启动监控，禁止靠重启循环绕过旧回执拒绝。

2026-10-26本次验证：观察单元36 passed（0.20秒）；隔离真实PostgreSQL观察与推进9 passed（33.33秒）；无外部服务完整后端437 passed/352 skipped（27.76秒）。各组重叠不累加，条件跳过不算通过；专用回环PG容器与临时数据库已清理，没有触碰其他容器。

随后接入健康tick事务：真实PG健康/推进/观察合并16 passed（53.75秒）；新增数据库采样失败用例后健康专项最终8 passed（23.23秒）。覆盖empty/pending等待、healthy不推进、审批撤销、旧回执拒绝、绑定变化、混合pending与终态缺费用、审计回滚和数据库异常透传。无外部服务全量437 passed/360 skipped（27.51秒），新增PG用例的条件跳过不是通过；CI真实PG组已包含该专项，但托管CI本身未在此运行。专用PG容器已清理。

纯函数assess_window校验报告形状、UTC时间、计数与显式计划，按完整计划摘要绑定策略。观察时长/样本不足、报告过期、计划到期、未结束请求、上下文异常、未知费用、异常时间、错误率/P95/单轮总费用超限或币种不符均不通过。错误率以精确整数比例比较，金额用十进制字符串；NaN、指数表示、布尔计数、未来时间或不一致计数不能转成零/成功。

报告只有摘要，没有签名或外部提交授权。advance已在自己的控制事务内重新收集；observe仍受控只读，不持久化报告。advance成功时将该次聚合报告和评估写入同事务审计。目前没有自动观察调度器。

## 升级

head为0037，表数仍49（45业务受保护、4协议控制）。迁移前须停写/排空并暂停所有running灰度；迁移检测到running行直接拒绝，不会替操作者改状态或伪造旧阶段起点。旧paused记录没有阶段时钟，不能自动恢复；应受控撤回并创建新审批计划。新增字段进入现有表备份/封存，但本批未进行带观察记录的完整加密恢复，新镜像未重建；旧0030镜像不能用于新head。

## 本批证据与剩余

计划/操作观察单元25项，加真实PG观察4项、控制服务7项及存储3项，合并39 passed/0 skipped（35.43秒）；静态迁移/RLS清单/封存清单18 passed（1.15秒），各组重叠不累加。本地无外部服务全量426 passed/347 skipped（30.41秒），跳过不算通过。真实PG证明候选/epoch隔离、pending/缺费用拒绝、两attempt费用累加及阶段时钟约束；合成手写用量/费用只验证聚合逻辑，不是实际模型费用证据。

最后增加局部epoch索引及显式非NULL快照谓词后，真实PG观察4项+运行检索6项+迁移静态15项重新合并 **25 passed/0 skipped（31.89秒）**；与上组重叠不累加。临时PG实例已清理；该索引的存在不等于大规模查询容量通过。

阶段推进/最终提升的内部事务已实现；仍需可信自动调度与失败动作、真实审批/控制入口、自动回切、完整恢复/新制品、真实业务独立审阅和模型/费用/负载/双机验收。S2–S6目标未完成。
