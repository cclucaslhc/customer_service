# 灰度发布状态与运行时路由（0037）

最新进展：[advance一步推进及最终索引提升内部事务](rag-rollout-advance.md)已实现，必须重新采样并复查两份审批/版本/质量/索引；最终激活、审批消费与完成审计原子提交，失败一起回滚。没有自动调度/失败动作或真实控制入口，操作指标不替代业务质量。以下早期批次“尚无推进”保留其原时间范围。

最新0037增加[数据库阶段时钟与候选观察窗口](rag-observation.md)：只读PG账本、隔离route epoch/baseline、未知费用/pending不通过，不自动推进流量；新迁移要求先暂停running灰度。下方0036及更早测试保留原批次范围，不能作为0037镜像或完整恢复证明。

2026-10-26 最新：[真实浏览器/SSE两轮及刷新验收已通过](rag-browser-acceptance.md)，并修复“知识答案已入库但最终正文未进入耐久事件账本”。当前成功提交同事务重置草稿并写权威正文分块，随后citation/done，预算及回滚边界不放宽。真实PG专项25 passed，本地后端401 passed/343 skipped，前端40 passed+build；成功手动浏览器夹具1 passed（含工具等待660.50秒，非性能证据）。下方“浏览器待验”属于此前批次状态；可信审批/阶段观察推进/最终提升/自动回切、新制品及生产验收仍缺。

当前head0036：新增仅运行角色可EXECUTE的`cs_rag_route_context`布尔谓词，不放开控制表，也不返回完整证据。向量运行器在pin时核对真实ProviderSnapshot及本轮完整有效instructions，与审批model_config_sha256/prompt_sha256匹配才可进入灰度。V1模型摘要为UTF-8 canonical JSON（sort_keys、紧凑分隔符、ensure_ascii=False）`{schema_version:1,provider,model}`的SHA256；Prompt摘要为完整instructions原始UTF-8字节SHA256，评测/审批控制器须用`binding_for_runtime`同一算法。摘要不含Key；目前未支持请求级自定义endpoint/模型settings，后续引入时须先扩展版本化契约，真实模型权重/服务revision验收仍缺。两个摘要写入run快照，在检索及提交复查；不匹配失败关闭，不以baseline回退掩盖错误。灰度路由轮次禁用FallbackModel，避免主模型故障后自动换成未获本轮质量审批的模型；非灰度原路由降级策略不变。

当前默认PostgreSQL向量运行路径已接线：授权启动/claim后自动pin run快照，创建每轮KnowledgeService/Repository副本，检索时在共享发布锁与身份SHARE锁内核对当前路由，仅允许读取快照选中的active/ready索引；提交前再复查持久化快照。未配置向量网关路径仍保持原active模式。合成浏览器/SSE与刷新已验；真实审批/控制HTTP入口、观察/推进/最终提升、完整恢复和生产负载证明仍缺。

0035引入`cs_rag_route_lock(uuid,text)`，仅API/Worker额外EXECUTE，PUBLIC禁止，控制表权限不变。固定search_path的SECURITY DEFINER函数先取共享发布锁，授权调用身份与会话，再按tenant→排序users→排序memberships→排序sessions取得SHARE行锁；共享读取可并行，实际撤权更新被阻塞，外键KEY SHARE仍兼容。取锁后再校验调用身份及当前投影，锁保留至调用事务结束；无灰度时也锁当前调用身份，防止失效身份被当作baseline回退。SQLSTATE28000转为AuthenticationError。检索与已pin run提交均使用此函数，来源复核后再验时间/投影。已接默认Worker，但尚不代表完整生产灰度验收。

0034引入agent_runs可空rag_route（未增加表），在授权启动/claim之后、检索之前pin一次，记录tenant/session/index/rollout/release/路由version；JSON对象大小有SQL约束，数据库触发器禁止已固定快照被替换或清空。旧run保持NULL，不自动回填。接口核对身份、会话、运行状态及租约，重复pin仅允许仍相同路由；不是重启模型许可。

complete_run对已pin的run不依赖调用者传index_pinned标志，始终在共享发布锁内重取当前投影并逐字段比对持久化快照，失配不插入assistant消息；active切换、启动/暂停灰度或路由version变化使旧快照失效。未pin旧run仍用原active校验。检索使用run内副本，避免并发会话共享可变路由；普通非路由SQL仍只读active，不能任意传ready profile绕过授权。

0033引入`cs_rag_route(uuid,text)`受限投影函数；未增加表（仍49表）。运行时仅额外授予EXECUTE，整张审批/灰度表仍禁止读取。当前应迁移至0035、使用匹配制品并重新授权，旧0030镜像不能用于当前head。

函数使用固定`search_path=pg_catalog`、全限定业务表和SECURITY DEFINER，PUBLIC无执行权。输入是内部会话UUID与会话摘要，不是公开HTTP接口，也不接收任意tenant/index。只允许当前有效身份访问本租户本人会话（support/admin遵循租户会话权限），并要求双方指定管理员会话仍有效、release已批准且未过期、计划精确一致、旧索引active/候选ready且manifest绑定匹配。返回仅tenant/rollout/release/路由version/index；不暴露证据、计划、审阅者或会话摘要。以租户/会话/rollout/已批salt确定稳定桶，阶段扩大时cohort单调包含；MD5仅作非安全分桶，不作为认证或完整性签名。候选未被选中的会话也绑定同一路由版本。暂停/撤回/审批撤销后不返回灰度投影。

此函数只是当前查询时的受限投影，不是可持有的发布许可；运行器通过run快照、候选SQL授权和锁内提交复查消费它，不能拿旧结果直接提交。模型/Prompt/质量绑定仍须可信控制器复查，函数不重新运行模型或校验完整业务证据来源。

`rag_rollouts`是owner控制的耐久状态，不是单凭状态即可读取候选的许可。已有stage/start/pause/rollback内部服务和向量运行路由；HTTP/CLI控制入口仍缺。必须由受信控制器使用审批冻结计划启动；不可手工改running状态绕过证据与身份核验。

`RagReleaseService.create(...,rollout_plan=...)` 可把完整显式阶段策略纳入不可变正文及审批hash；未提供则仍是一次性发布。计划要求2–16个严格递增的basis-point阶段且最终10000，固定cohort_salt、观察时长/请求数/新鲜度及错误率/P95/每请求费用/币种阈值，无隐式放行默认。灰度绑定的release禁止走一次性execute绕过阶段。`RagRolloutService.stage`在同事务复查双方当前审批、版本/正文hash、证据和实际索引上下文，只从已冻结计划创建staged、流量0，审计失败回滚；不接受另传plan替换已批策略。该服务尚未接HTTP/CLI，不意味着真实独立审阅或流量生效。

`start`再核对rollout/release两份版本/摘要、计划、证据/索引及双方会话，只从staged进入第一步，不能跳阶段或恢复paused。`pause`使running流量归零并保留步骤；`rollback`使staged/running/paused/recovery_review转为rolled_back且归零。停止仅要求owner和正确回执，不要求审阅者会话仍有效或计划未过期；遵守maintenance/recovery hold，状态与审计同事务。rollback仅撤回未完成灰度，不改变全租户active索引，不能回退已完成发布；已由检索/提交路由复查拒绝旧轮次，浏览器验收仍待完成。

每行绑定同租户 release_id；同一审批计划只允许一行灰度记录。plan/摘要、创建/到期时间与身份键不可修改，正文仅有对象/大小形状约束，完整计划语义、摘要真实性和审批授权仍由后续服务核验。每次更新必须 version 增1，调用方须显式 expected-version。单租户 staged/running/paused/recovery_review 至多一条，恢复复核状态也占用槽位，不能直接另起灰度绕过复核。

状态为 staged/running/paused/completed/rolled_back/recovery_review；basis_points 为0–10000，step_index 为-1–15。数据库要求 staged 未起步且流量0、running 为非零非全量、completed 为10000、rolled_back/recovery_review 为0。SQL约束不验证合法状态转移或已获批准；只有 owner 可写，普通API/Worker连读取都不允许。后续运行时须通过受限接口获取经授权的路由字段，不得放开整表。

新表安装维护写触发器，并纳入显式 SHARE 封存清单。当前49张表，45张受保护业务表、4张协议控制表。恢复在原始快照完整性验证后，将 staged/running/paused 改为 recovery_review、basis_points=0、version+1，保留原计划和步骤；恢复解除hold不自动恢复分流。已完成/已回切历史不重放。此钩子尚未做带灰度记录的完整加密备份恢复。

升级要求受控停写、排空、迁移0036、重授最小权限并使用同head制品；不自动升级真实数据库，不支持丢弃证据的downgrade。此前0030制品与扫描不覆盖本批运行链路，新镜像尚未重建。

剩余实现/验收：可信计划来源及真实审批/控制入口；自动观察调度与不健康动作；暂停后的恢复及成功提升后的受控回退；完整备份恢复/新制品；真实业务质量、模型费用、负载与双机验收。候选观察、受控一步推进/最终提升内部服务及合成浏览器/SSE已验；一次性`RagReleaseService.execute`不能替代阶段控制。

2026-10-25 验证：迁移/RLS/封存清单静态18 passed；真实 PG 灰度存储+审批存储+维护组8 passed/7 skipped（20.69秒，7项缺主机PG16客户端）；灰度存储+PG集成+审批存储扩大组35 passed/1 skipped（54.24秒，真实本地模型条件未提供）；新增表封存锁专项1 passed/8 deselected（6.21秒）。各组重叠不累加。无外部服务完整后端381 passed/318 skipped（27.32秒）；跳过不算通过。真实PG证明计划/版本约束、同租户引用、单租户唯一进行中记录、恢复后归零且不能绕过占位、普通角色无读写权限；未证明灰度运行或完整加密恢复。临时数据库已精确清理。

计划审批绑定续验：计划校验18项单元测试，加真实PG审批服务/灰度staging26项，合并 **44 passed / 0 skipped**（61.78秒）。覆盖显式计划无默认放行、严格阶段与数值约束、已批计划持久化、灰度计划拒绝一次性execute、非灰度release拒绝staging、撤销后拒绝staging及审计失败无残留记录。不包含任何实际分流或生产指标采集证据。

控制状态续验：计划单元18项、真实PG控制服务7项及存储3项，合并 **28 passed / 0 skipped**（23.42秒）。覆盖仅启动第一步、旧版本拒绝、暂停归零、终态撤回、撤权后禁止启动但owner仍可停止、暂停审计失败完整回滚；候选保持ready，未改变active索引。这不是实际路由暂停或线上回切验收。

0033受限投影续验：真实PG投影4项 **4 passed / 0 skipped**（11.08秒），明确覆盖候选与基线两类cohort、稳定结果、未启动/暂停不投影、同租户非owner客户及跨租户管理员拒绝、审批会话撤销拒绝、PUBLIC无执行权、单独EXECUTE授权后可取投影但不能读两张控制表及identity_sessions、调用者search_path不影响固定投影。此前投影3项+静态18项合并21 passed（9.29秒），重叠不累加。无外部服务后端399 passed/328 skipped（26.50秒，新增第4项投影测试在此后单独跑）；不代表真实运行时分流/提交校验。临时数据库已清理，新制品仍未构建。

0034仓库快照续验：真实PG run快照+既有PG集成 **33 passed / 1 requires_local_rag_model deselected**（51.59秒）；新run快照4项覆盖持久化、幂等、不可替换/清空、正常提交、暂停后省略index_pinned仍拒绝提交且无assistant消息、启动灰度使旧baseline快照失效、无权会话拒绝pin。先前快照+迁移静态19 passed（11.17秒），随后统一了迁移约束名再跑上述扩大组。无外部服务后端399 passed/333 skipped（27.20秒）；不累加重叠组、不计条件跳过通过。尚无默认Worker接线或并发身份撤销锁证明；新镜像和完整加密恢复仍待验收。

0035身份锁续验：首轮并发观测因观察连接缓存pg_stat_activity快照未见新backend而失败；将测试观察事务改为AUTOCOMMIT刷新统计，保留实际锁等待断言。两种并发撤权顺序专项2 passed/4 deselected（6.26秒）；快照/投影/迁移组25 passed（31.82秒）；最终快照/投影/PG扩大组 **39 passed / 1本地模型deselected**（70.89秒），覆盖撤权先提交则等待答案拒绝、答案先持身份锁则真实撤权API等待提交，以及独立EXECUTE授权不放开表读取。各组重叠不累加，不证明所有故障交错或多主机情形。完整本地后端 **401 passed / 335 skipped**（32.64秒）；Windows解析临时目录清理问题在复测中修复后才获得此结果。默认Worker和候选检索仍未接线，新制品未构建，完整恢复/生产灰度未验收。

0036运行接线续验：初批真实PG检索/Worker3 passed（12.06秒），扩大组38 passed/1本地模型deselected（66.64秒）。复核发现模型/Prompt未绑定实际轮次，追加0036谓词与快照摘要、禁用灰度跨模型自动fallback后，当前检索/Worker+run快照9 passed（27.63秒）；再加模型/Prompt不匹配在模型构建前拒绝、连接失败不尝试其他供应商及最小函数权限，最终检索/Worker6项+投影4项+静态迁移15项合并 **25 passed/0 skipped**（31.72秒）。模型负测首轮被原有排队配置变更门控提前拒绝，修正测试使排队模型与Gateway相同而与灰度审批不同后再次通过，未修改或放宽门控。HTTP/本地SSE回归41 passed（13.14秒），完整本地后端 **401 passed/341 skipped**（26.20秒），重叠组不累加；无真实模型或公司材料。新Worker测试证明实际candidate向量有命中、普通ready查询仍空、克隆不污染共享服务、暂停后旧答案不持久化且无最终token。浏览器验收、观察/推进/最终提升、真实独立审阅、完整恢复/负载/双机仍未完成，新镜像未构建。
