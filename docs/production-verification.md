# 生产化实施与验收记录

## 2026-10-29 当前 S2 验收

本轮目标仅为S2及Git收尾，不等于整体生产上线。权威逐项状态见[最终验收账本](s2-final-acceptance.md)。最新本机普通后端588 passed/384 skipped（31.31秒），S2跨模块真实组件组192 passed/0 skipped（276.39秒）；组间不累加，跳过不计通过。前端40 passed/build/npm audit0。

用户明确跳过真实外部告警送达和供应商账单对账，两项仍标“未验证”，不再等待Gmail凭据或导出。现有管理配置的DeepSeek真实生成已验证，不从环境发现密钥。当前应用OCI独立重建和扫描通过；新库检出的Grafana go-jose高危已在官方固定源码重建中修复，本机完整扫描/六查询/浏览器复验通过。GitHub CI运行40788755603仍在执行，尚无最终OIDC验签或main第二笔完成提交。下文“最新/尚未推送/缺凭据”和旧镜像扫描均属对应日期的历史，不覆盖本段当前边界。

## 2026-10-28 S2 收尾复验

最新增量：显式授权管理界面DeepSeek配置后，真实合成生成成功，input21/output3、requests1；首次探针usage属性调用错误造成取证缺失，不伪造其用量/费用，保留可能计费未知。三通知出站适配器及离线回归13 passed，后端全量 **571 passed / 383 skipped（32.37秒）**。Gmail正式凭据/送达与PG受保护收费链路尚缺；用户已授权推送现有公开仓库，但未据此宣称CI已运行。新增应用模块使最终制品需重新构建，历史r2扫描不覆盖新增源码。

本轮最终普通后端全量 **556 passed / 383 skipped，27.23秒**，Collector/Grafana/供应链/签名专项24 passed，CI最终步骤顺序变动后5 passed；组重叠不累加。Prometheus3.15.1和Collector0.164.0固定镜像补扫0 HIGH/CRITICAL；新版真实六面板查询及Collector故障恢复通过，node2可回退升级后Run `68e8445b-d0af-4927-8a53-31b5f37d9dee`的257 Span全链关联再次通过。CI将最终制品上传放在应用和监控所有门禁之后，但没有远端执行证据。下文552/549均为前批历史计数。

后续修复：Grafana已形成官方13.2.5 distroless-slim＋固定SHA256 Prometheus插件制品，禁用运行时插件自动下载，严格复扫0 HIGH/CRITICAL（含真实可执行backend）；六面板查询/浏览器、401与缺数据灰色NOT HEALTHY通过。manifest `sha256:1b03b2eb88da8ff5c4994742070cfcffd9b7fdde2a6fcd27727d7f88219255d8`，详见 [Grafana验收](s2-grafana-acceptance.md)。新增CI构建/扫描/真实查询门禁及契约回归16 passed；普通后端最新 **552 passed / 383 skipped，27.90秒**，下段549为前批历史值。旧12.3.1合成测试栈已精确移除，保留仓库外历史证据，新修复栈接替；没有删除业务数据。

显式接入隔离 PostgreSQL/Redis/S3/Prometheus/Alertmanager/本地权重目录的 S2 跨模块选定组：**175 passed / 0 skipped，284.91 秒**。报告保存于仓库外 `D:/codex_project/customer_service_vm_lab_20261026/s2-final-integration-20261028.xml`。普通后端全量另为 **549 passed / 383 skipped（29.57 秒）**；固定本地权重计量另项 **1 passed（18.78 秒）**，各组不得相加为一次全量。

应用 r2 0 HIGH/CRITICAL、独立空缓存 OCI 逐 blob 重建一致及测试身份签名/篡改拒绝通过；正式发布身份/远端 CI 不在此证明范围。Grafana 旧12.3.1与本次解析的官方 latest 固定摘要均未过 HIGH/CRITICAL 安全门禁，未部署替换或豁免。后者发现 Go stdlib、x/net 及 Tempo 依赖的 HIGH，不能仅靠升级基础 OS 消除。真实渠道/账单及生产容量仍缺；S2 整体未勾选完成。详细边界见 [S2 清单](s2-completion-checklist.md)及 [Grafana验收](s2-grafana-acceptance.md)。

2026-10-26 S6阶段推进/最终提升内部事务：head仍0037。advance在发布锁内重采样PG窗口、复查冻结计划/版本/审批/质量/索引，只推进一步；最终激活、审批executed、rollout completed及审计同事务。初版5 passed（22.54秒）；加最终期限复查后扩大15 passed（52.39秒），无外部服务426 passed/352 skipped（25.66秒），组重叠不累加。没有自动调度/失败动作或真实控制HTTP入口，合成1秒/1请求及手写费用不作真实质量/费用/负载证明；新镜像、完整恢复、真实业务与双机仍未完成。见[边界及证据](rag-rollout-advance.md)。

2026-10-26 当前head0037：新增owner只读候选观察窗口和数据库阶段时钟，按tenant/rollout/路由version/候选index隔离，NULL费用不忽略、每轮累加所有attempt再取最大值；pending、未知费用、上下文或时间异常均阻止通过。计划/观察单元+真实PG观察/控制/存储39 passed（35.43秒），静态18 passed，本地426 passed/347 skipped（30.41秒），组重叠不累加。observe不接受外传统计、不改流量，阶段推进/最终提升尚未实现；迁移必须先暂停running灰度，旧镜像/完整恢复/生产质量及双机验收仍缺。见[窗口边界](rag-observation.md)。

2026-10-26 S6浏览器/SSE验收：实际构建前端、HTTP/Cookie/CSRF/SSE、独立Worker和PG/pgvector完成正常候选回答、检索后暂停、刷新不重放；账本断言两候选run为complete/interrupted、仅1条assistant、暂停最终token0，合成模型调用无在线出站。浏览器揭示并修复缓冲知识正文未进入耐久账本：终态事务写answer_reset+权威正文分块+citations/done，预算不放宽。最终真实PG25 passed（90.69秒），本地后端401 passed/343 skipped（32.60秒），前端40 passed+build；成功浏览器夹具1 passed（660.50秒，含人工式交互/工具等待）。使用仅pytest内的合成预认证storage state，不计正式登录/HTTPS；失败与中止轮未计通过。见[完整证据/范围](rag-browser-acceptance.md)。head仍0036，匹配新镜像/完整恢复/阶段推进/真实业务与双机仍缺。

2026-10-25 当前head0036：默认PostgreSQL向量运行器已接run快照→受限候选检索→身份锁内提交；实际模型/完整Prompt摘要绑定灰度审批，灰度轮次禁用未获批准的自动换模型。最终检索/Worker6+投影4+迁移15合并25 passed/0 skipped（31.72秒），HTTP/本地SSE41 passed（13.14秒），完整本地后端401 passed/341 skipped（26.20秒）；各组重叠不累加。合成Worker证明candidate有向量命中、暂停后无assistant持久化/最终token、模型/Prompt不符在模型构建前拒绝、连接失败不跨供应商。浏览器验收尚未做，阶段观察/推进/最终提升、独立业务质量/完整恢复/真实模型/双机仍缺；新镜像未重建。详见[边界与测试过程](rag-rollouts.md)。

2026-10-25 最新head0035：已pin run提交改用受限身份锁投影，身份锁按tenant/users/memberships/sessions顺序持有到事务结束，来源复核后再检查时间/路由。最终真实PG扩大组39 passed/1本地模型deselected（70.89秒），并发覆盖撤权先提交拒绝答案、答案持锁则真实撤权API等待；测试观测连接改AUTOCOMMIT修复旧统计快照导致的假超时。完整本地后端401 passed/335 skipped（32.64秒）。期间Windows解析超时清理重复失败，新增有界文件占用重试（不忽略最终错误）及2项回归；解析专项10 passed/3平台跳过。默认Worker未接pin/候选检索，新镜像、完整恢复和实际灰度仍未完成。

2026-10-25 最新head0034：agent_runs增加write-once rag_route，仓库提供授权/租约内pin与提交时共享发布锁下的快照复查，即使调用者省略index_pinned也不绕过已pin快照。真实PG扩大组33 passed/1本地模型deselected（51.59秒），无外部服务399 passed/333 skipped（27.20秒）。默认Worker尚未调用pin，候选检索授权及审批撤销/提交并发身份锁待实现，因此不能计端到端灰度完成；新镜像与完整恢复仍待验证。详见[当前边界](rag-rollouts.md)。

2026-10-25 最新head0033：新增PUBLIC禁止执行的SECURITY DEFINER会话路由投影函数，固定pg_catalog搜索路径、业务表全限定；API/Worker仅新增EXECUTE权限，控制表仍不可读取。真实PG投影4 passed/0 skipped（11.08秒），明确覆盖候选/基线cohort与跨租户/非owner客户/撤销/暂停/最小权限；静态18项通过。无外部服务后端399 passed/328 skipped（26.50秒，最后1项新增投影单独补跑）。尚未接Worker/run快照/候选SQL及提交复查，不能宣称实际灰度已开通；新镜像、完整恢复、真实业务与双机仍未完成。详见[灰度边界](rag-rollouts.md)。

2026-10-25 S6 灰度控制状态：owner服务新增start/pause/rollback，启动复查双审批与索引/计划并只进入首阶段，暂停/撤回归零且不依赖审阅会话仍有效；所有状态转换与审计同事务。专项28 passed/0 skipped（18单元+10真实PG，23.42秒）。候选仍ready，实际检索/回答提交尚未接入，所以不计线上灰度、暂停或回切完成。见[当前灰度边界](rag-rollouts.md)。

2026-10-25 S6 灰度计划审批绑定：可选rollout_plan进入不可变release正文及审批hash，严格显式阶段/观察与费用策略；灰度绑定release拒绝一次性execute，owner staging只接受仍有效的双人审批并复制已批计划，零流量、审计同事务。单元18+真实PG26合并44 passed/0 skipped（61.78秒）；无外部服务全量399 passed/321 skipped（26.12秒）。尚无真实分流、阶段控制/回切、HTTP审批入口、新镜像或完整恢复证明；详见[灰度边界](rag-rollouts.md)。

2026-10-25 S6 灰度存储基础：head0032，新增owner控制的rag_rollouts，冻结计划、同租户审批引用、单租户唯一未结束灰度、版本递增及恢复后流量归零/复核；API/Worker无整表读写权限，49表中45业务受保护、4协议控制。静态18 passed，真实PG扩大组35 passed/1 skipped，封存锁专项1 passed/8 deselected；无外部服务后端381 passed/318 skipped。各组重叠不累加，模型及主机PG16客户端等缺失条件不算通过。未实现灰度路由/阶段推进/暂停回切，未重建0032镜像，未做带灰度记录完整加密恢复。详见[灰度边界与证据](rag-rollouts.md)。

2026-10-25 S6 审批服务与原子发布续验：owner 内部服务现可冻结质量证据、收集两名指定同租户管理员当前会话审批，并在同事务内复查、激活、消费及审计；不是 HTTP/UI 功能或灰度控制器。真实 PG 合并专项58 passed/0 skipped；最终代码服务/发布审计17 passed/0 skipped（重叠不累加）。再加并发撤权专项5 passed/14 deselected，实际观察锁等待，覆盖撤权先提交拒绝发布、发布先锁定则撤权等待。无外部服务完整后端381 passed/311 skipped（25.95秒）；新增服务测试无PG时跳过，不能计通过。临时PG已删除，当前镜像未重建；完整加密恢复、真实审阅、灰度/回切、双机及模型验收仍未完成。详见 [审批边界与证据](rag-release-approvals.md)。

2026-10-25 S6 耐久审批存储基础：head `0031_rag_release_intents`，新增默认空的独立表，绑定同租户候选/旧索引、两个不同成员引用、冻结 body/hash 与期限；定义不可更新，可变状态必须 version 恰增 1，旧版本 CAS 更新影响 0 行。API/Worker 授权脚本撤销整表权限，启动门控检查无其读写权限；新表接入维护触发器/显式封存，当前 48 表中 44 业务、4 协议控制。恢复钩子把 pending/approved 改为 recovery_review 并增版本、保留正文/审批摘要，避免旧批准因解除恢复保护复活。

首轮真实迁移因两组复合外键默认名称碰撞而全部回滚，改为明确唯一名称后重跑通过，并增加所有当前约束名唯一的静态回归。真实存储/审计/维护组 **8 passed / 7 deselected**（19.11 秒）；扩大 PG 主集成+存储+审计组 **35 passed / 1 real-local-model deselected**（52.84 秒）。最后增加严格版本递增后，存储/维护组 **5 passed / 7 deselected**（13.44 秒），覆盖跨租户/重复主体拒绝、冻结定义拒改、无权限角色 CRUD 的 42501、恢复重审/保留证据和版本 CAS；恢复仅直接调用同一重置 SQL，**没有运行带审批记录的完整加密恢复**。静态迁移/RLS 分类/封存清单组 **18 passed**（1.16 秒）；最终无服务全量 **381 passed / 292 skipped**（28.46 秒）。组有重叠，不相加。新的真实存储文件已加入 CI PG 零跳过组，托管未运行；真实完整应用角色/备份角色专项尚待复验。

最终增加两会话摘要不可相同的约束后，真实存储文件再次 **3 passed / 0 skipped**（6.25 秒），包含版本 CAS/恢复重审等最新断言；上述全量与扩大 PG 组早于此项约束，未把局部复验说成新的全量。专用实例测试库与临时 role 残留均为 0，cs-release-store-pg-20261025 随后移除。

本批**只有存储和防护，不是可用的双人审批或执行系统**。成员 FK 不验证当前管理员权限，长度正确的会话摘要/state=approved 也不是真实批准；后续服务必须验证可信身份、会话撤销/过期、报告正文/哈希、质量及当前上下文并原子消费，不能只读 state 发布。没有新 HTTP/SSE、没有创建真实审批账号、没有启用自动切流；旧 0030 已扫描镜像不包含本批，须后续重建。生产数据库未迁移，仅使用本任务隔离实例。详见[审批存储边界](rag-release-approvals.md)。

2026-10-25 S2/S3 审批基础边界核对发现并修复封存清单漂移：最新五张 RAG/解析容量业务表已有 cs_maintenance_write 触发器，但显式 SHARE 表锁清单停留旧版本。补齐 embedding_profiles、knowledge_index_versions、knowledge_chunk_versions、knowledge_embeddings_1024、knowledge_admission_policy，当前 43 张业务表与实际触发器集合一致；四张协议控制表明确例外，不改不可变 0017 输入。新静态测试要求 metadata 减控制表恰为完整无重复清单，未来新增审批表遗漏时直接失败。

真实独立 PostgreSQL 的新表锁/既有排空封存组 **2 passed / 8 deselected**（8.25 秒）；静态 inventory 用例另 **1 passed**（0.19 秒）。在只针对该隔离库的测试事务禁用触发器后，对五张表执行 WHERE false 的空 DELETE，仍被 SHARE 锁阻止，且事务结束 session_replication_role 回到 origin；未操作真实业务行或服务配置。无外部服务全量 **380 passed / 289 skipped**（28.77 秒）。本机没有显式 PG16 客户端目录，故本批未重跑完整 pg_dump/restore，不能拿局部锁测试代替恢复证明；CI 有对应 PG16 客户端，已把整个维护测试文件加入真实零跳过组，托管尚未运行。

无迁移/权限扩张/自动发布，源码修复尚未进入上一批镜像。测试库残留检查为 0，本轮专用 cs-seal-pg-20261025 已移除；随后继续推进审批存储，不能把本修复说成耐久审批已完成。

2026-10-25 S6 质量门槛计算组件：新增纯函数 `rag_release_gate.assess_quality`，严格拒绝未声明/额外字段（含 approved 标志）、retrieval-only 报告替代生成事实审阅、非有限数/布尔计数/不一致样本数，以及无显式政策。报告绑定 tenant/index/manifest、题集/来源映射、模型配置/Prompt，UTC 时效/样本分层/支持率/拒答率/泄漏/P95/费用币种逐项比较，输出固定原因码和冻结哈希。比率采用精确整数交叉相乘，输入精度/数值边界有界；不因浮点/Decimal context 舍入放宽门槛。新组件与既有盲测评分/结构组 **43 passed**（0.11 秒，含新组件 38 项）。

随后无外部服务后端全量 **379 passed / 288 skipped**（32.27 秒），跳过项未计为通过；diff/编译检查通过。此组件**没有发布权，也尚未被现有激活 CLI 消费**；伪造审阅引用/自报数字仍可满足结构比较，所以结果明确不是授权，不能把合成测试当业务质量或批准证明。受信评测、独立审阅核验、耐久审批、分批发布及自动回切尚待实现；没有新迁移或现网行为变更。源码新组件尚未进入上一批 3d0880... 镜像，不沿用旧扫描结果宣称新制品完成。S6 仍未完成。

2026-10-25 S2/S6 RAG 发布控制完整镜像补验：新建 `customer-service:rag-audit-verify-20261025`，Docker image ID **`sha256:3d0880f49fb98c7948f9584a3f4c7764688d865648a50660bd969099ead65fe5`**，UID/GID 65532。未挂载 app 源码，实际制品通过只读/无网络启动、固定 pypdf 6.21.2 与前端资源检查；专用 PG 中先运行镜像内真实迁移/知识 Worker CLI 上传→重解析→取消及容量归零，再运行 RAG 发布控制检查。后者验证库级缺失 CAS 拒绝、审计故障导致激活回滚、真实独立 CLI 正向激活/回切、最终仅一 active，以及全局运维控制域有准确目标租户和 9 条成功生命周期审计。使用空文档的新合成租户，不把它当作向量质量、非空 manifest 容量或正式灰度验收；非空数据与并发场景的源码 PG 证据见前批。

同一新 image ID 经固定 Trivy 0.66.0 的正常 DB freshness 检查（未跳过更新检查）复扫 **0 HIGH/0 CRITICAL，退出 0**；CycloneDX 校验 **14 Debian+143 Python**。本机报告为忽略目录 `data/acceptance/rag-audit-image-trivy.json`、`rag-audit-image-sbom.cdx.json`，扫描与 Docker inspect ID 一致。保留解释器/第三方 SBOM/许可证与自定义安全机制未由零漏洞结论完整证明的限制。镜像内新检查已加入 CI，YAML/diff 检查通过；托管 CI 尚未运行，没有推送或发布。

此批补齐“新 RAG 代码尚未进入已扫描镜像”的历史缺口，不补齐当前制品跨宿主/字节重建、签名、双机、业务工具/真实模型/独立盲测及灰度控制面。最终镜像保留作本机验证制品，专用 cs-rag-image-pg-20261025 按本批清理结果移除。下方旧镜像 ID 和源码-only 限制按对应日期/批次保留，不当作当前最新镜像事实。

2026-10-25 S6 原子发布审计与 mandatory CAS：RagIndexAdmin stage/stage-existing/seal/activate/非空旧向量 prune 新增同事务审计，审计写入失败时整个操作回滚；空 prune 不刷无效记录。库级 activate 不再允许省略旧 active 预期值，与已有 CLI 门槛一致；所有仓库内调用方已同步。记录控制 UUID、manifest 与计数，不记录查询、文档、向量或密钥。数据库首次拒绝将非成员 owner 写成目标租户无 actor_id 的审计，修正为沿用全局控制域运维审计、明确 target_tenant_id/current_user，没有弱化 actor scope 约束或伪造用户。

真实 PG 新发布/审计/竞争及现有索引 Worker、回答提交锁/Shadow 联合 **6 passed / 27 deselected**（10.84 秒）：覆盖 stage、stage-existing、seal、切换审计失败后的状态不变、完整回切事件、无显式 CAS 拒绝、两个并发发布仅一个成功及仅一个相应成功审计；旧向量删除在审计失败后仍保留，非空删除逐批记数。扩大到整个 PostgreSQL integration 文件及新审计文件 **32 passed / 1 real-local-model deselected**（52.35 秒），无外部服务后端全量 **341 passed / 288 skipped**（32.19 秒）。跳过/未选项不计为通过，测试组不相加。CI 的真实 PG 零跳过组已加入新文件，托管尚未运行。

Schema head 仍为 0030，无 DDL 变更，既有 owner-only/恢复 hold/发布锁继续生效。此为源码及真实 PG 证据，**上一批 sha256:614e... 完整镜像尚未包含本批新 RAG 代码，不能把其旧扫描结果作为本批制品证明**；后续须重新构建/验证。批次审批、业务质量阈值、自动停止/回切、真实灰度与独立盲测仍缺，整体 S6 未完成。本轮测试库残留查询为 0，专用 cs-rag-audit-pg-20261025 已移除，未触及其他服务。

2026-10-25 S3 发布事务崩溃边界补验：将 `tests/test_knowledge_oom.py` 扩展为 OOM、before_commit、after_commit 三个真实容器场景，最终补充直接分块计数断言后 **3 passed / 0 skipped**（35.16 秒，JUnit `data/acceptance/worker-commit-faults.xml`；此前无直接分块断言版本为 35.13 秒）。仍使用已记录的最终镜像，不挂载 app 源码。before_commit 在文档/分块/任务 item/计数写入同一事务后、提交前停住；after_commit 在底层 DBAPI commit 已实际返回后、Worker 收尾前停住。由本轮专用合成目录标记同步外部协调器，再对明确的自有容器执行 Docker KILL，确认退出 137、Running=false、OOMKilled=false（区别于真正 OOM）。首轮自发 SIGKILL 对 namespace PID 1 未触发预期终止，断言失败；改用外部同步 kill 后全组重新通过，未将未发生的崩溃算作通过。

| 故障点 | 数据库发布证据 | 受控恢复结果 |
| --- | --- | --- |
| 解析入口真实 cgroup OOM | 无文档，item pending，计数 0 | 保留占用/不重放；确认停止后释放，新任务只发布一次 |
| 发布事务提交前 KILL | 文档与分块回滚，item pending，processed/imported 0 | 新任务正常发布一次，无半提交任务结果 |
| DBAPI commit 返回后 KILL | 文档 ready、item imported、processed/imported 1，结果 ID 与文档 ID 一致 | 已发布内容保留；显式重提交 skipped 1，文档 ID/版本不变、无重复 |

三场景都验证 running/held 证据、加速 DB 租约截止后 failed/lease_expired、旧租约拒绝续期、新任务不能抢占保留槽、未确认释放被拒、确认旧容器停止后人工释放、正常独立 CLI 执行新任务及最终容量归零。after_commit 的旧任务终态仍为中断失败但保留 imported 结果，不自动伪造 complete；这是保守核对语义。数据库事务原子性不代表文件系统原子回滚，提交前 KILL 可能留下未引用副本，GC/对账仍需独立处理。此项不证明 PG 断电/PITR、对象上传未知回执或双机分区。

CI 已有同文件零跳过门槛，步骤名更新以明确包含提交边界，YAML 解析与 diff 检查通过；托管尚未运行。本轮未重跑后端全量。测试库/Worker 容器残留查询为 0，专用 `cs-oom-pg-commit-20261025` 随后移除，未触及其他服务。

2026-10-25 S3 真实 Worker cgroup OOM 业务恢复专项：`tests/test_knowledge_oom.py` **1 passed / 0 skipped**（13.18 秒）。使用同一最终运行镜像 `sha256:614e82474618546f54fd335859ffafee2d150aa01bef07fc12ac2d9298f335d1`、专用 PG/独立测试数据库、768 MiB 内存及 swap 总预算、64 PID/1 CPU、只读根和私有合成文件挂载。真实 Worker 领取 typed upload 后，在解析入口注入 1 GiB 分配；Docker 确认 OOMKilled、137、Running=false，数据库仍有 running/held 占用且无文档发布。加速 DB 截止并回收后，任务 failed/lease_expired、旧租约续期被拒；显式新任务因保留占用无法领取。未确认进程停止的 owner 释放被拒，确认上述容器停止证据后释放成功；随后独立正常生产 CLI（含启动探针/真实沙箱解析）只完成新任务，旧任务仍 failed、仅一个文档且全部容量归零。

OOM 是真实 cgroup 终止，不是 mock 抛异常；仅 lease deadline 通过隔离数据库 SQL 加速。内存注入器是测试 fixture，不进入业务代码/生产开关。它证明本机单 PG 下此次 Worker 整进程 OOM 的保守恢复，不证明任意提交瞬间/对象上传中 OOM、宿主 OOM、双机 HA 或真实文档容量。测试对指定 PG 容器与 URL 回环端口做匹配，防止运行到不同实例；使用独立 cs_test 库和临时合成根，不读取环境模型密钥。

CI 在 Linux image 作业增加同样真实测试及零跳过门槛（托管尚未运行），YAML 解析通过；不配置显式服务时相关门控/资源验证器组 **6 passed / 1 expected skipped**（2.65 秒）。测试库及 Worker 容器残留检查为 0，专用 `cs-oom-pg-20261025` 随后清理。整体 S3 仍缺双机/协调服务切主和现场验收；不把本专项代替其他故障场景。

2026-10-25 S3 容器资源故障专项：新增显式入口 `scripts/verify_container_limits.py`，对本机已有非 root 镜像按 inspect 返回的不可变 ID 测试，不自动拉取、不挂载宿主文件/凭据/Docker socket、不联网。实际镜像 `sha256:614e82474618546f54fd335859ffafee2d150aa01bef07fc12ac2d9298f335d1`：128 MiB memory/memory-swap 下申请 256 MiB，cgroup `OOMKilled=true`、退出 137；16 PID 下创建 15 个子进程后 fork 被 EAGAIN 拒绝，子进程全部回收；4 MiB tmpfs 写入超额返回 ENOSPC；0.25 CPU 配额下读取 cpu.max 并实际观察 nr_throttled 增加。三类独立容器运行均完成，末尾只清理随机 cs-runtime-limits 自身容器。

验证器自身 **6 passed**（0.02 秒），覆盖 root/UID0 变体在创建容器前拒绝，以及观察超时后的精确清理和无挂载/配额参数。最终真实故障脚本再次通过；Linux image CI 已加入同一显式步骤，托管尚未运行。此为现有最终运行镜像的 host 侧验证，新验证器无需出现在被测镜像内。128 MiB 只用于故障注入，不是生产 Worker 配额。没有证明真实 Worker OOM 后业务恢复、宿主/双机故障或容量/SLO，整体 S3 未完成。

双 VM 环境检查：本机有 Hyper-V 管理命令，但当前用户不具管理权限，Get-VM/Get-VMSwitch 被拒绝；WSL 列表仅 Docker Desktop。已向用户提出由管理员准备私网测试 VM 的确认请求，未尝试提权、创建外部交换机或把同机容器冒充独立主机。无此环境时继续不受影响的本机工作，不把缺环境转换为验收通过。

2026-10-25 S2/S3 当前完整制品补验：构建 `customer-service:sandbox-verify-20261025`，最终 Docker image ID **`sha256:614e82474618546f54fd335859ffafee2d150aa01bef07fc12ac2d9298f335d1`**，UID/GID 65532。不挂载 app 源码，在此制品复验非 root/只读/无网络导入与前端资源；沙箱拒绝/继承脚本（保留 bridge 网络）及六格式脚本通过。专用 tmpfs PostgreSQL 中实际迁移到 0030，用同一镜像启动独立生产知识 Worker CLI（含领取前自检），上传 v1→重解析 v2→取消待处理任务、文档不增加、容量占用为 0 均通过。仅测试 fixture 只读挂载，镜像内业务代码不可变；不证明生产角色/TLS/双机或真实 HTTP 浏览器链。

新漏洞库首次扫描旧锁 `pypdf 6.20.1` 检出 **3 HIGH**（CVE-2026-115572/115580/115585），严格退出码为 1。未忽略漏洞，将项目下限与锁定版本升级到 **6.21.2**（官方 PyPI wheel SHA256 `1d44d1a3a200f25062adff0085b02040e4646431ac2ccac22e42679e91466084`），再重建以上最终镜像。固定 Trivy 0.66.0 + 本批下载漏洞库，对同一最终 image ID 复扫 **0 HIGH / 0 CRITICAL，退出 0**；CycloneDX 1.6 校验为 **14 Debian +143 Python**。报告位于忽略目录 `data/acceptance/sandbox-image-trivy.json`（修复前）、`sandbox-image-fixed-trivy.json`（修复后）及 `sandbox-image-sbom.cdx.json`。扫描器提示基础 SBOM/许可证检测局限，不将零高危解释为自定义沙箱/解释器或完整许可证审查通过。

升级后的本机 Python 解析/知识 HTTP/Repository 专项 **14 passed / 4 skipped**（6.15 秒，跳过 Linux/未配置外部依赖），随后无外部服务全量 **335 passed / 282 skipped**（25.43 秒），最终镜像六格式及沙箱重新通过。CI 增加实际镜像+专用 PG 的迁移/CLI 链，YAML 解析通过；托管 CI、签名、当前制品跨宿主/字节重建、正式 Linux 部署与 S2–S6 外部验收仍未完成。专用 `cs-image-pg-20261025` 容器已移除；最终镜像和本批 Trivy DB volume 保留作本机复验缓存，无发布/推送或生产改动。下方“最新源码仅挂载验证/尚无新制品”的描述是本批之前状态。

2026-10-25 S3 Linux 解析访问隔离：新增 `app/parser_sandbox.py`，生产解析子进程默认要求 x86_64/Landlock ABI ≥ 5，安装文件授权和 seccomp filter 后才导入解析依赖、处理文档。运行库/代码只读，普通临时文件写入仅限本任务工作区；拒绝 socket、x32 syscall、若干 IPC/跨进程 FD/内存/信号与 setsid/setpgid 入口。父 Worker 不被限制，仍负责失租/取消/超时终止与发布。CLI 先解析合成样本，失败不领取真实任务；架构/旧 ABI 无保护降级被拒绝。适用边界和部署要求见[解析隔离](parser-isolation.md)，不是 VM 或内核安全审计结论。

真实 Linux 运行：已有 distroless 非 root 镜像只读挂载当前 app 源码/测试脚本，内核 6.18.33.1 / Landlock ABI 7，cap-drop ALL/no-new-privileges、默认 bridge 网络。合成越界文件读写、socket/已有 listener accept、脱离进程组、父进程信号探测、x32 和 SysV IPC 拒绝，以及 fork 后策略继承均通过；进程组超时/权限失效/解析主进程崩溃清理脚本另通过。正常 TXT、DOCX、XLSX、PPTX、PDF、PNG/OCR 六类样本在最终收紧规则下全部通过（3 GiB/PID 128/tmpfs 256 MiB 容器限制）。这不是当前源码最终镜像重建证明，CI 已增加实际构建镜像的相同沙箱/格式门槛，但托管尚未运行。

兼容性修复：只给 openpyxl 必需的 `/etc/mime.types` 增加精确只读授权，没有开放整个 `/etc`；发现 PyMuPDF 会向 stdout 输出提示，现将第三方 FD 1 输出与 JSON 结果 FD 分离，新增真实 PDF 回归。Windows 解析/运行参数专项 **11 passed / 3 Linux-only skipped**（2.78 秒）；无外部服务全量 **335 passed / 282 skipped**（26.76 秒）；真实 PostgreSQL 动作/耐久知识组 **23 passed / 1 backup deselected**（68.04 秒）。全量/PG 组早于最后 Linux syscall deny-list 收紧，最终 Linux 文件/网络/继承及六格式脚本均已重新运行通过；各组不相加。第一次后台会话句柄丢失且无存活 pytest 进程，未将其计为通过，随后以上带 JUnit 的回归重新完成。专用 `cs-sandbox-pg-20261025` tmpfs 容器已移除，包含被中断初跑遗留的合成测试库；未触及其他服务。

剩余：目标宿主内核/LSM/seccomp 配置、安全评审、物理资源耗尽与双机故障验收、额外转换器/旧格式、Windows 等效策略及新镜像制品验证仍缺。不以此勾选整个 S3；下方历史“没有文件系统/网络沙箱”和“可脱组”等描述仅对应本批之前，完整生产隔离认证仍未完成。

2026-10-25 S2/S3 Linux HTTPS 代理专项：以仓库 NGINX 样板填充临时 localhost 证书和 1 MiB 上传限额，在只读 Linux 容器运行 `nginx -t` 和真实 TLS 请求，**3 passed / 0 skipped**（3.95 秒）。覆盖不受信证书拒绝/显式信任、客户端伪造 X-Forwarded-Proto/For 被覆盖、Last-Event-ID 保留、SSE 首事件及时抵达而不是等待两秒后的尾事件、202 JSON 回执保持及 Content-Length 超限在发正文前 413。样板显式限制 TLS 1.2/1.3、关闭会话票据/版本泄露；未对所有 TLS 套件或轮换作完整验收。

官方仓库直连的 Docker 拉取超时，通过可信 DaoCloud 镜像取得 NGINX 后，和官方 Docker Registry manifest 响应头核对 digest 一致：`sha256:47928bf67eb62a1863cb08d9ecb52d3e704304ba614eaee17884204677702951`。CI 使用官方固定引用并新增独立零跳过作业，YAML 本机解析通过，托管 CI 尚未运行。首次测试修正了合成配置只读临时目录及 Docker bridge peer 断言；未通过放宽证书检查或关闭只读来规避错误。测试退出后 cs-proxy 容器残留为 0；镜像保留作缓存，未改动其他服务。

范围：真实 NGINX + 合成协议后端，不是真实应用/Cookie/SSE 重连或双机 HA；正式域名/证书、代理摘流/VIP、跨宿主故障及完整 S2–S6 验收仍缺。旧文档“无 Linux NGINX 验证”的限制仅针对本批之前。

2026-10-25 S3 主动取消：durable 知识任务新增同租户管理权限/CSRF 保护的取消端点及管理页按钮。取消与审计同事务，行锁阻止后续发布，重复或已终态调用不改写结果；已发布文档保留。运行容量不随取消释放，Worker 心跳检测后停止隔离解析子进程，再按 owner/fence 确认释放；失联时 owner 确认旧进程停止后方可人工释放。Schema head 仍为 0030，无新字段迁移；升级须同时部署新前端及 API/Worker，终态新增 cancelled。测试首次发现认证路由未绑定导致 503，补上 knowledge.manage 映射后复验通过。

分组证据（有重叠，不合计）：初次动作/耐久知识组 **22 passed / 1 deselected**（63.45 秒，早于新增 HTTP 取消断言）；补 HTTP/跨租户及真实子进程取消 **6 passed / 1 deselected**（19.03 秒）；加入 abandoned cancelled 占用后，动作/共享容量联合 **11 passed / 2 deselected**（27.73 秒，排除两项真实备份）；最后新增 customer 拒绝断言的 HTTP 用例 **1 passed / 7 deselected**（6.99 秒）。无外部服务全量 **332 passed / 276 skipped**（29.82 秒，早于最后 owner 释放补丁，后者由联合组覆盖）；前端 **40 passed**，最终生产构建成功。真实进程测试观察取消时仍保留容量、10 秒内解析进程退出、无发布、停止后容量释放；测试使用合成输入与真实 PostgreSQL，不调用模型。

浏览器使用专用隔离队列环境（明确不启动 Worker，以稳定观察 pending），上传→点击取消→cancelled，刷新仍保留终态且文档 0；截图 `data/acceptance/knowledge-cancelled.png`。这只证明 pending 的 UI 交互；running 进程停止由上面的真实子进程测试证明，不将无 Worker 页面当作进程停止证据。验收服务已退出，专用实例 cs_browser/cs_test 数据库残留为 0，`cs-cancel-pg-20261025` 容器已移除；截图与根依赖目录补入 Git 忽略规则，未提交构建产物。完整网络/文件系统沙箱、物理 cgroup、双机故障及 S2/S4/S5/S6 外部验收仍未完成。下方较早“主动取消未实现”的记录已被本批取代。

2026-10-25 S3 上传/重解析统一队列：head `0030_knowledge_actions`。durable HTTP 两入口返回 202，由独立 Worker 执行；固定上传输入、重解析原文及目标版本，禁止删除后复活和旧 Worker 误领新动作，入队审计同事务。以下为不同且可能重叠的测试组，不相加：无外部服务后端 **332 passed / 272 skipped**（26.70 秒，早于最后能力守卫补丁）；Windows PostgreSQL HTTP/知识联合 **20 passed**（56.52 秒）；Linux PostgreSQL/PGDG 16.15 动作、容量、耐久知识、真实加密备份恢复和 schema 比较 **44 passed**（82.01 秒）；真实 MinIO/PostgreSQL 输入专项 **9 passed**（29.44 秒），含跨 Worker 根的上传及重解析。能力守卫及非法 UUID 最新针对性补验 **4 passed / 1 deselected**（14.60 秒；排除 actual_backup，其真实恢复证据来自前述 Linux 组）。前端 **40 passed**，生产构建成功。本批未重跑所有外部依赖全量，不以历史总数替代最新范围。

浏览器补验：隔离 PostgreSQL 认证租户、生产知识 Worker CLI、无真实模型调用；上传合成 `repository_acceptance.txt` 后 complete 1/1，重解析观察到 pending，完成后 v2，刷新仍为 v2。证据截图位于忽略目录 `data/acceptance/knowledge-queued-v2.png`。验收服务已正常退出，专用 PostgreSQL 的 `cs_browser_%`/`cs_test_%` 数据库残留查询为 0。本轮三个 `cs-actions-*-20261024` 专用容器与测试镜像已移除，可由测试配置重建；未触碰 FastGPT 等其他容器。此前被策略拒绝删除的 Temp OCI 制品继续保留，未绕过限制。文档同步后 `git diff --check` 通过。

下方 2026-10-24“HTTP 上传/重解析未受容量约束”属于 0030 前历史状态，现已被统一队列实现取代。主动取消、完整解析沙箱、部署层物理限额、真实双机故障验收仍缺；此批不证明 S3 整体完成或可上线。

2026-10-24 S3 PostgreSQL 共享知识容量：新增 `0029_knowledge_admission`，默认全局 2、每租户 1 个 queued 任务。领取在共享事务锁下原子计数并保留占用，租约过期不释放，Worker 停止解析后以原 owner/fence 确认释放；不明回执继续保留。owner CLI 支持无内容检查、排空后配置及确认旧进程停止后的终态占用释放，配置/人工释放追加同事务审计；API/Worker 无策略写权限。迁移锁表并拒绝运行任务未排空的升级，触发器拒绝旧客户端无占用标记的领取。

本批证据：Linux 隔离 PostgreSQL 16 + 官方 PGDG 16.15 客户端的容量/耐久知识/解析器联合 **27 passed / 0 skipped**（61.81 秒），覆盖并发上限、租户回补、过期保留、错误 fence、人工确认、CLI 检查、owner 权限、真实加密备份恢复保留策略与未知占用，以及恢复 hold 阻断领取。修复降级错误文案与既有测试约定不一致后，Linux PostgreSQL 主集成 **29 passed / 1 local-model deselected**（43.46 秒）；容器内专用 IPv6 回环 55433 实例的运行角色测试 **1 passed / 0 skipped**（18.55 秒）。曾出现测试角色漏授 maintenance_state SELECT，补齐仅测试角色的前置读权限后通过 owner 拒绝验证。相关实例测试库残留均为 0，两个本批容器及测试镜像已清理。HTTP 同步上传/重解析尚未受此容量约束；S3 整体仍未完成，未执行生产迁移或部署。

2026-10-24 S3 Linux 解析进程组清理：每次解析创建独立 session；等待使用 `waitid(WNOWAIT)`，清理同组进程后再回收主进程。实际 distroless 非 root/只读/无网络镜像中运行合成 `fork` 测试，分别覆盖总超时、执行权限失效及解析主进程异常退出，确认派生子进程不再运行；正常文本解析另通过。Windows 解析专项 **5 passed / 1 Linux-only skipped**，上述 Linux 专项在实际容器中单独运行成功；该测试也会由 Ubuntu 后端 CI 执行，托管 CI 尚未运行。本批未重跑全量，未触碰现有 FastGPT 容器。此为同进程组生命周期控制，文件系统/网络沙箱、脱离进程组程序与 Windows Job Object 仍不在覆盖范围。

2026-10-24 S3 失租停止解析：为隔离解析器绑定 Worker 的心跳失租检查；启动前、等待期间和接收结果前复核，失效异常触发子进程终止并原样传回租约处理。专项真实进程验证执行权限失效后终止；真实 PostgreSQL 测试在解析子进程启动后使租约过期，确认心跳检测后在 10 秒内停止（解析总限时为 20 秒）、子进程退出、无文档发布、源文件保留，过期维护记为 `lease_expired`。耐久知识组 **16 passed / 0 skipped**（45.47 秒）；解析子进程专项 **5 passed**。本轮未重跑全量；临时数据库容器已清理。用户主动取消、解析进程树与部署层沙箱仍未完成。

2026-10-24 解析子进程与候选回补合并回归：无外部服务全量 **331 passed / 261 skipped**（25.73 秒），另见下方真实 PostgreSQL 专项与 Linux 镜像运行证据。跳过项未计入通过。

2026-10-24 S3 解析进程资源限制：生产知识 Worker CLI 默认使用逐文档子进程，父进程负责心跳/发布；输入与输出为有界文件/JSON，环境变量显式供应，不传数据库/对象凭据。父进程强制总时限及 128 MiB 输出上限；Linux 使用地址空间/CPU/文件大小/描述符/core 限制。真实子进程的文本解析、超时后终止、环境凭据排除和超大输出拒绝有专项覆盖。独立真实 PostgreSQL 耐久知识组 **15 passed / 0 skipped**；固定 distroless 镜像在非 root/只读/无网络/临时 `/tmp` 下实际子进程文本解析通过。最初默认 ONNX 线程池在 2 GiB 限制下 OCR 超时；固定单线程后，默认 2 GiB 的实际 OCR/PNG 资产 JSON 返回通过。测试服务和镜像已清理。本项不提供文件系统/网络沙箱，也未完成跨实例全局解析预算、主动取消或部署层 cgroup 验收。

2026-10-24 S6 候选回补：修复 Top-K 截断先于最终文档校验造成的召回空洞。检索在既有候选预算内保留备用池，最终复核后再截取返回数量。相关检索契约、RRF、证据门控与 API 组 **56 passed**；独立 pgvector/PostgreSQL 专项 **2 passed / 0 skipped**，实际在检索后、返回前递增首条文档版本，确认首条排除且次条补位，并复验盲测身份绑定/撤销。仅用合成文档和身份，临时容器已清理。本次没有重跑全量，不能把此前全量数字当成本次完整结果；业务质量盲测和生产容量验收仍待完成。

2026-10-24 S2 元数据变更后重建补验：以当前 `Dockerfile`（四个复制库及其 dpkg 元数据）执行 `linux/amd64` 缓存与 `--no-cache` 两次 OCI 导出，统一 `SOURCE_DATE_EPOCH=0`、`rewrite-timestamp=true`、`--provenance=false`。两次构建均成功，`scripts/verify_image_rebuild.py` 逐 blob 校验通过，manifest 同为 `sha256:5ae40fb765593309a698dd29b6df7035b42c908193957d50024a3fbcba0c6aab`。本批补齐“元数据变更后本机未重验”的历史缺口；跨宿主重建、托管 CI、签名和正式部署仍未验收。导出文件在此前同一受限 Temp 目录内，未尝试绕过已拒绝的删除策略。

2026-10-23 S6 旧向量清理：新增 owner-only、恢复 hold 门控的按租户有界 GC；只删当前不再为 ready 文档版本的向量，保留可用于回切的当前版本退役 profile 向量和切块快照。CLI 缺 `--confirm-stale-prune` 拒绝执行。隔离 PostgreSQL/pgvector 合成用例先因 Docker 引擎停止未进入 setup，恢复引擎后发现原 compose `55432` 位于 Windows 当前排除端口段，遂使用仅绑定 `127.0.0.1:55742`、无持久卷的本轮专用 PG 容器；专项 **1 passed / 0 skipped**，完整 PG 文件排除唯一真实本地权重专项后 **25 passed / 0 skipped / 1 deselected**。随后无外部服务后端全量 **318 passed / 257 skipped**，跳过项不计通过，`git diff --check` 通过。测试库残留 0；本轮专用容器以及失败启动的 compose 容器/网络均已停止并移除。用例覆盖跨租户过期向量不被误删、当前版本不删、分批清理及确认门槛。此为旧向量 GC 的隔离证据，不证明生产备份 freshness、灰度切流或 200 条业务盲测。

2026-10-22 S2 headless OCR 运行镜像候选：只读依赖分析显示原运行镜像 `libgl1 → Mesa → libllvm19 → libxml2`，其中 `libxml2` 带唯一 CRITICAL；镜像专用锁定 extra 增加 `opencv-python-headless==5.0.0.93`，Docker 构建显式不安装 RapidOCR 的带界面 `opencv-python` 传递依赖，移除三项图形系统包，常规 `production` extra 不同时安装两个 `cv2` 包。最终 `linux/amd64` 镜像 `customer-service:verify-headless-final-20261022` 在非 root、只读、无网络条件下只安装 headless OpenCV，并对合成空白图实际执行 `RapidOCR()` 成功。固定 Trivy 0.66.0 对该完整镜像扫描为 **44 HIGH、0 CRITICAL、0 条 libxml2**，较先前同基底完整制品的 54 HIGH＋1 CRITICAL 减少 11 条，严格 `--exit-code 1` 仍实际退出 **1**。最终 `uv lock --check` 通过，无外部服务后端全量 **318 passed / 257 skipped**，跳过项不计通过，`git diff --check` 通过。最终 extra 分离后，以 `SOURCE_DATE_EPOCH=0`、`rewrite-timestamp=true` 再做缓存和 `--no-cache` OCI 导出，校验器确认 manifest 均为 **`sha256:1a1a3b8c53b2798b73d3b3a38748483e568d2ce9e6068f41e814223a98955e2a`**；同字节 OCI SBOM 校验 **87 Debian／143 Python 组件**，严格 OCI 漏洞扫描仍退出 **1**。该方案减少但未清除高危项，托管 CI、跨宿主重建及签名未验证，S2 仍不勾选。此前 55 条记录保留为旧镜像历史结果。

2026-10-23 S2 供应链复核：固定 Trivy 0.66.0/当前库重扫上述完整 OCI 制品仍为 **44 HIGH、0 CRITICAL、0 条列出修复版本**。官方 `python:3.12-slim-trixie` 新 manifest 摘要 `sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704` 的只读基底扫描同为 **44 HIGH**，因此未无证据地替换现有基础摘要或放宽 CI 严格退出门槛。工作流 8 个 GitHub Action 已从可移动主版本标签固定到官方仓库当前标签对应的完整提交 SHA，并添加动作/镜像源摘要回归检查；相关 2 测试通过，无外部服务全量 **325 passed / 259 skipped**。托管 CI、签名、跨宿主重建及高危项正式处置仍缺，S2 制品门槛未完成。

2026-10-23 S2 独立 distroless 候选：44 条 HIGH 中 `util-linux`/登录相关 9 个包合计 36 条；`apt-get -s purge` 显示对应依赖牵涉 essential 包，不在现有 Debian 镜像强拆。单独的 `Dockerfile.distroless-candidate` 固定官方 Debian 13 Python distroless amd64 manifest 摘要，镜像 **272,444,426 bytes**、非 root `65532:65532`；只读、无网络、临时 `/tmp` 下导入 API/双 Worker、实际合成 OCR 和 API CLI 帮助均成功。固定 Trivy 0.66.0 扫描**完整候选**为 **26 HIGH、0 CRITICAL**（其中 20 条来自其自带但本项目未调用的 Python 3.13 包），严格门槛退出 **1**；相比现行完整镜像 44 HIGH 有下降，但仍不合格。没有完成候选的 OCI 双重可重建、SBOM、数据库/对象依赖端到端、生产双机或签名验收，现行生产 Dockerfile/CI 不切换。不能为降低扫描数字而复制未登记来源的动态库或隐藏包清单。

同日候选追加验证：固定 `SOURCE_DATE_EPOCH=0`、BuildKit `rewrite-timestamp=true` 和 `--provenance=false`，缓存／`--no-cache` 独立构建 OCI manifest 同为 **`sha256:f200aeec7f70afefeb925d159a08159f69c573b5b093e0e16c5c394365e47d75`**，`verify_image_rebuild.py` 验证 manifest/config/layer blob 哈希通过。对该同字节 OCI 生成 CycloneDX 1.6 SBOM：**38 Debian／143 Python／7 其他组件**；固定 Trivy 0.66.0 对同一 OCI 复扫仍 **26 HIGH、0 CRITICAL**。普通 `--load` 构建未重写层时间戳时摘要不同，故只声称上述明确构建参数下的同机复现，不声称跨宿主。专用临时 OCI 导出位于系统 Temp 的 `cs-distroless-repro-20261023-1`；验证后按精确路径清理被执行策略拒绝，未绕过删除限制。候选仍不通过严格漏洞门槛，正式 PostgreSQL/对象存储联动、签名、目标双机和托管 CI 未验收。

2026-10-22 S5 本地备用模型工具协议门槛：请求级故障切换现在在把真实用户消息交给本机回环备用模型前，向固定 `/v1/chat/completions` 发送不含用户或知识库内容的合成函数调用，要求返回唯一指定函数和 `{"ok":true}` 参数。探测有 8 秒总时限、256 KiB 响应上限；非 200、超限、超时、纯文本或错误工具调用均跳过本地候选。若有在线备用，仍须经过现有租户外发授权与计费 attempt 门槛。合成 HTTP、真实回环测试服务及路由专项通过；随后无外部服务后端全量 **318 passed / 257 skipped**，跳过项不计通过，`git diff --check` 通过。此测试不是生成质量、上下文长度、两台 Linux 节点能力或真实模型权重验收，也不表示在线备用一定可用。

2026-10-22 S5 在线所选模型目录门槛：管理页 DeepSeek/OpenAI 只读连接检查现在要求 `/models` 明确列出当前所选模型 ID，不能因供应商返回任意模型列表而误报成功；不发送生成请求。配置专项 **13 passed / 0 skipped**；随后无外部服务后端全量 **313 passed / 257 skipped**，跳过项不计通过，`git diff --check` 通过。仅证明目录可见，仍不能证明生成、函数工具、流式或上下文能力。

2026-10-22 S5 在线连接检查边界：DeepSeek/OpenAI 管理页的只读 `/models` 连接检查改为流式、最大 256 KiB 读取；超限、无效 JSON、重定向均不得报告成功，不发送生成请求。配置、追踪和路由专项 **33 passed / 0 skipped**；随后无外部服务后端全量 **312 passed / 257 skipped**，跳过项不计通过，`git diff --check` 通过。这仅证明连接测试有界，不证明模型生成、工具能力、真实供应商费用或正式故障切换。

2026-10-22 外部验收条件更新：用户确认目前**没有两台隔离 Linux 测试主机**。因此 S3/S5 双机、PostgreSQL/Redis HA 与跨主机故障注入尚无环境，不能以本机 Docker 双进程代替。S4 首个业务工具按最小风险默认选**只读本人订单查询**；尚无真实业务沙箱 API 契约、资源归属规则和受控凭据，认证模式继续拒绝读取模拟订单。S6 可以准备 200 条以上合成回归输入和盲测格式校验，但没有业务方批准的独立脱敏真实问题与来源映射，不能将合成题目计作正式盲测。以上是外部验收缺口，不影响继续完成本地可验证的实现。

2026-10-22 S3 真实 Redis 进程停机／恢复：单独创建无持久卷、仅绑定 `127.0.0.1:56380` 的固定 digest Redis 7.4.6 测试容器，用合成 UUID namespace 取得并续期许可。停机后真实客户端 `acquire` 与旧许可 `require` 均抛 `CoordinationUnavailable`，未静默放行；重启后旧许可仍被 fenced，新许可可取得/续期/释放。仅删除本测试 namespace 的 Redis key；临时容器已停止并移除，可通过重新创建该隔离容器复现。集成 compose 的 Redis 镜像同步固定 digest `sha256:a9cc41d6d01da2aa26c219e4f99ecbeead955a7b656c1c499cce8922311b2514`；实际启动该 compose 的 PG/Redis，`tests/test_redis_admission.py` **5 passed / 0 skipped，6.35 秒**，随后只移除本次 compose 服务和网络。此证据覆盖客户端失败关闭和进程重启，不等于 Redis Sentinel/Cluster 主备切换、跨宿主网络分区或正式双机故障验收。

2026-10-22 S3 双进程竞争增量：在本任务专用隔离 PostgreSQL 上，对同一合成 Run 同时启动两个真实独立 `chat_worker.py --once --synthetic` 进程；两者正常退出，数据库只产生 **1 次模型 attempt、1 条助手消息、1 个 done 终态**，之后无待领取任务。新用例 **1 passed / 0 skipped**，完整 `tests/test_durable_chat.py` **18 passed / 0 skipped，73.23 秒**，容器已停止。随后无外部服务后端全量 **311 passed / 257 skipped，22.55 秒**，跳过项不计通过。证明的是同宿主双进程对单 PostgreSQL 的领取排他和持久化，不证明两台 Linux 主机、Redis/PG 切主、网络分区或代理故障切换；S3 仍未完成。

2026-10-22 S2 可重建 OCI 制品增量：继续拆查上轮不一致的第 4 层，固定时间戳后的层内 695 条 tar 元数据相同，内容差异仅为 `var/cache/ldconfig/aux-cache`、`var/log/apt/history.log`、`var/log/apt/term.log`、`var/log/dpkg.log`。Dockerfile 在同一构建层删除这 4 个构建期文件；以固定 digest 基底、`SOURCE_DATE_EPOCH=0`、BuildKit `rewrite-timestamp=true`、`--provenance=false` 分别做缓存和独立 `--no-cache` 的 OCI 导出，校验器逐个验证 manifest/config/layer blob 的 SHA-256 后，两个 OCI archive 的 manifest 均为 **`sha256:21d4603559e3c188a588eff6920e37426ca9b0647672f1a56cfa2cfb4dc28bd6`**。此前未清理时的两份 archive 被校验器明确拒绝；校验器单测 **2 passed / 0 skipped**。CI 已增加双构建摘要相等门槛，并对第一份同一 OCI 制品生成 SBOM 和扫描，而非扫描另一个不确定性导出。实际本机 OCI SBOM 为 **127 Debian／143 Python 组件**；固定 Trivy 0.66.0 对同一 OCI 实扫仍 **54 HIGH、1 CRITICAL、0 条列出修复版本**，严格 `--exit-code 1` 实际退出 1。普通本地 Docker 镜像的非 root `10001:10001`、只读、无网络 API/双 Worker 导入及账号 nologin 检查通过。托管 GitHub CI 尚未运行，跨主机独立重建、签名和漏洞处置仍缺；本机同宿主双构建不能冒充完整 S2 验收。

2026-10-22 S2 独立重建核验：对当前相同构建上下文和固定基底先做缓存构建、再做 `--no-cache` 构建；首次两镜像内容差异定位到构建时 `useradd` 写入 `/etc/shadow` 的日期。Dockerfile 已将服务账号 shell 设为 `nologin` 并固定 shadow lastday，CI 增加对应断言且本机镜像检查通过；重建后以 root 只读枚举 `/usr`、`/etc`、`/var/lib/dpkg`、`/opt/customer-service` 共 **21,189 个文件**，两镜像文件内容仅 `/etc/hostname` 不同（容器运行时生成）。新镜像非 root `10001:10001`、无网络、只读启动与 API/双 Worker 导入烟测通过。**镜像层及完整摘要仍不相同**；尝试 `SOURCE_DATE_EPOCH=0` 与 BuildKit `rewrite-timestamp=true` 的不解包导出，缓存/无缓存 manifest 摘要仍分别为 `sha256:d0974075617684dc81db6ddb1bba74ce7f2333a156ccf8286cd2133505d23d3f` 与 `sha256:7bc09f22b1d94823d53142005e3a24fc72e4418ed4ccf40a6e0e81b3d82d93c1`。`type=docker` 导出器与该选项冲突，本机改用 `type=image,store=false` 才完成比较。故只能证明抽样目录内容一致，**不能宣称字节级可重建制品**；CI 仍未增加虚假的“摘要相等”门槛，需继续定位层元数据及构建依赖的非确定性。

2026-10-22 S6 跨资料拼接防护：对答案与资料先按句边界分组，同一答案句中的数字／型号锚点必须共同出现在同一来源句，阻断不同产品／不同资料的数字拼接。`tests/test_knowledge_evidence_gate.py` 含真实 PydanticAI 合成循环 **7 passed / 0 skipped**；扩大到 API、追踪、身份组 **50 passed / 0 skipped**；随后无外部服务后端全量 **309 passed / 256 skipped，23.79 秒**，跳过项不计通过。这是精确锚点同句检查，不是语义事实支持证明或 200 条独立盲测。

2026-10-22 S6 事实锚点增量：检索后答案中若出现本轮资料没有的数字、日期或含数字型号，最终校验器会清除引用并安全拒答；SSE 仍在提交成功后才发送。合成直接校验、PydanticAI 循环和 SSE 专项 **47 passed / 0 skipped**（其后补充 SSE 编造数字回归，`tests/test_knowledge_evidence_gate.py tests/test_api.py` **41 passed / 0 skipped**）；最新无外部服务后端全量 **307 passed / 256 skipped，22.42 秒**，跳过项不计通过。这只阻断精确锚点不在资料中的一类编造，不能证明语义蕴含或解决证据冲突，S6 仍未完成。

2026-10-22 S6 空证据与流式边界：知识检索无结果时，Agent 输出校验器将模型声称的公司事实替换为确定性拒答；检索后的草稿延迟至最终校验、来源复核和消息提交成功后才发送 SSE token。合成 PydanticAI 工具循环、撤销和 API/SSE 相关专项 **45 passed / 0 skipped**；撤销及零候选用例均断言未经验证的模型文本从未出现在任何 SSE 帧。随后无外部服务后端全量 **304 passed / 256 skipped，24.97 秒**；隔离真实 PostgreSQL 的认证聊天与集成组 **27 passed / 0 skipped / 1 deselected，50.93 秒**，专用容器已停止。跳过和按 marker 排除项不计通过。仍未实现有候选时的逐事实支持判定，也没有业务批准的 200 条脱敏盲测，不可据此勾选完整 S6。

2026-10-22 S2 SBOM 实测与漏洞归因：固定 Trivy 0.66.0 对上述完整 Trixie 运行镜像实际生成 CycloneDX 1.6 SBOM，含 **287 个组件**，可核对 `libxml2`、`pydantic-ai-slim`、`rapidocr-onnxruntime` 等运行依赖。CI 增加 SBOM 格式及 Debian/Python 包清单检查，本机对实际生成文件验证为 **127 个 Debian、143 个 Python 组件**；校验器专项 **2 passed / 0 skipped**。这验证本机生成与检查能力，不等于托管 CI 上传已运行或签名完成。相同镜像复扫的 55 条发现按包归因：`libxml2` 8 条（含唯一 CRITICAL `CVE-2026-6653`）、`util-linux` 相关包合计占多数；状态为 **54 affected、1 fix_deferred**，均未列出修复版本。因此不能简单通过锁定 Python 依赖修复，也不能豁免扫描；后续须跟踪 Debian 安全修复或评估缩减运行时依赖，并用完整镜像重新扫描。

2026-10-22 S2 完整 Trixie 制品候选：按用户“采用推荐默认方案继续”的授权，将 Python build/runtime 基底固定到官方 `python:3.12-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2`；Node 前端构建阶段仍为原固定 Bookworm digest，不进入运行镜像。`linux/amd64` 完整镜像 `customer-service:verify-trixie-20261022` 构建成功；以 UID/GID `10001:10001`、无网络、只读根文件系统及临时 `/tmp` 导入 API/双 Worker 并检查前端制品成功。固定 Trivy 0.66.0 对完整镜像、当前漏洞库实扫为 **55 条 HIGH/CRITICAL（54 HIGH、1 CRITICAL）**，比上一 Bookworm 完整制品的 81 条少 26 条；CRITICAL 为 `CVE-2026-6653` / `libxml2`，本次 55 条均未列出修复版本。按现行 CI `--exit-code 1` 复扫实际退出码为 **1**；门槛未放宽，镜像仍不合格、未推送或部署。`uv lock --check` 和 `git diff --check` 通过。还缺托管 CI、SBOM 上传、签名、独立重建比对及目标 Linux 双机验收。

2026-10-22 S2 基础镜像只读候选评估（上述完整构建之前的历史批次）：拉取官方 `python:3.12-slim-trixie`（digest `sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2`）并用相同固定 Trivy 0.66.0/当时漏洞库扫描，基础镜像为 **44 条 HIGH、0 条 CRITICAL**；原 `python:3.12-slim-bookworm` 基础镜像为 **63 条 HIGH/CRITICAL**（含 2 条 CRITICAL）。这只是基础镜像比较，不能据 44<63 宣称完整 S2 漏洞通过；随后完整制品构建及扫描结果见上段。

2026-10-22 S6 向量写入 API 哈希复核：管理端 `store_embedding` 现锁定来源快照并重新计算正文 hash，拒绝“声明 hash 匹配但正文已损坏”的合成用例；真实 pgvector 专项 **1 passed / 0 skipped**，PostgreSQL 集成组 **25 passed / 0 skipped / 1 deselected，44.61 秒**。未使用真实公司文档或在线模型。

2026-10-22 S6 索引 Worker 并发完整性：向量推理前拒绝正文/hash 不一致的快照；推理后持锁复核当前快照的正文与 hash，变化则不提交向量。真实 PostgreSQL 专项 **1 passed / 0 skipped**，集成组 **25 passed / 0 skipped / 1 deselected，46.83 秒**。无外部服务后端全量 **298 passed / 256 skipped，24.39 秒**，`git diff --check` 通过；临时测试库残留 0，专用容器已停止。排除项为需离线模型权重/CUDA 的端到端测试；不能把无外部服务全量的跳过项算作通过。

2026-10-22 S6 封存哈希复验：`RagIndexAdmin` 的 seal/activate 现在逐条核算当前版本快照正文 SHA-256，不再只信任声明 hash；真实 pgvector/PostgreSQL 专项 **1 passed / 0 skipped**，集成组 **25 passed / 0 skipped / 1 deselected，47.20 秒**。排除项仍是需离线权重和 CUDA 的真实模型端到端测试；历史的 450/100 全量结果早于此改动，不能冒充最新全量。

2026-10-21 S2 Linux 制品本机复验：Docker Linux Engine 恢复后，按固定基础镜像 digest 实际构建 `linux/amd64` 制品。初次只读、无网络、非 root 启动在导入 OpenCV 时因缺 `libxcb.so.1` 失败；Dockerfile 补 `libxcb1`、`libgl1`、`libglib2.0-0` 后重建，用户 `10001:10001`、无网络/只读/临时 `/tmp` 下导入 API 与双 Worker 和前端文件检查均通过。镜像当前本地 ID 为 `sha256:7d319b31a51353ee335564b21b7788993d16c98e840907d6e078968a5b6144a0`，未推送或部署。固定 Trivy 0.66.0 实扫初次发现高危/严重项，锁定升级 `certifi` 至 2026.10.19、生产 Python 3.12 的 `pillow` 至 13.0.0 后，明确有修复版本的 15 条镜像发现消除。复扫仍有 **81 条高危/严重发现、0 条列出修复版本**，包括 Debian `affected`/`fix_deferred`/`will_not_fix` 状态；按现行 CI `--exit-code 1` 规则仍应失败，不能宣称制品安全验收。Python 3.10 锁分支仍为 Pillow 12.3.0，须单独风险评估；尚无托管 CI、SBOM 上传、签名或双机部署证据。

2026-10-21 S2 CI 集成门槛修复：`compose.integration.yml` 的 PostgreSQL 改为经本机验证的固定 digest PG16/pgvector 镜像，以满足当前 0028 迁移；CI 的零跳过集成组显式排除唯一依赖独立离线权重及 CUDA 的专项，并保留该专项在开发环境单独验收。本机按 CI 的无查询参数 URL 和显式 PG/Redis/备份客户端运行，**86 passed / 0 skipped / 1 deselected，172.98 秒**，真实迁移及集成断言通过；`uv lock --check` 与 `git diff --check` 通过。测试库残留 0，已停止并移除仅本轮创建的 compose 服务及网络。GitHub 托管运行器仍未实际执行，不能称 CI 已通过。

2026-10-21 S5 本地端点连接检查增量：管理页的本地模型 `/v1/models` 检查现在有 256 KiB 响应上限，并要求目录明确列出当前配置的模型 ID；非 JSON、超限和未列出均记录失败，不发送生成请求。配置与端点专项 **26 passed / 0 skipped**；随后无外部服务后端全量 **298 passed / 256 skipped，21.92 秒**，`git diff --check` 通过。跳过项不计入通过。这仅验证当前 Windows 节点的协议目录，不证明本地模型生成/工具能力、两台 Linux 主机健康或切换。

2026-10-21 S6 盲测输入结构门控增量：新增仓库外 JSONL 的只读校验入口，检查至少 200 条、唯一 case ID、必需标签和版本化来源 hash，只输出摘要与计数，不输出题目或文档内容；合成专项 **3 passed / 0 skipped**。前端当前 **39 tests passed**、生产构建通过，`git diff --check` 通过。结构校验不证明脱敏、业务批准、独立性或检索/生成质量；真实盲测集仍未提供。

2026-10-21 最新真实环境复验：Docker Linux Engine 恢复后，仅启动本任务专用、绑定回环端口 55442 的隔离 pgvector 容器；显式设置合成 `TEST_POSTGRES_URL` 与已校验离线 `TEST_RAG_MODEL_ROOT`，最新 PostgreSQL 集成组 **26 passed / 0 skipped，77.40 秒**，随后后端全量 **450 passed / 100 skipped，463.48 秒**，零失败。新增索引封存正文一致性、聊天混合检索和引用提交事务断言均纳入此次真实 PG 结果。100 项跳过仍不计入通过。本机复验不能代替目标 Linux 双机、正式 Collector、业务工具沙箱、供应商账单或业务方批准的至少 200 条真实脱敏问题盲测。

2026-10-21 S2/S6 阶段追踪增量：实验性混合检索新增固定 Embedding、向量、融合与 Rerank 子 Span，白名单拒绝任意阶段名／内容字段；合成检索和现有 PydanticAI 追踪联合 **12 passed / 0 skipped**，导出属性无查询与文档正文。此前无外部服务全量 **294 passed / 256 skipped，27.33 秒**，Python 编译及 `git diff --check` 通过；跳过项不计入通过。索引封存又增加当前切块／版本快照正文一致性核对；当时 Docker 无法连接，真实 PG 验证已由上方最新复验补齐。尚无模型用量账本、生产 Collector、双机延迟或业务方批准的 200 条盲测集。

2026-10-20 S6 实验性聊天检索接线：Chat Worker/API 可显式加载已校验离线 Embedding 与 Reranker，`KnowledgeService` 用 active profile 查询权限内当前版本向量候选，RRF 合并词法，局部重排；默认关闭。新增单测覆盖 vector-only 命中、跨租户候选拒绝、本地模型故障仅降级为有权限词法、数据库错误不吞掉、返回前可见性复核和 API 配置拒绝。最终回答持久化前再次核对引用来源，若检索后文档已撤销则清除流式草稿、拒绝完成。此批无外部服务全量 **293 passed / 255 skipped，24.95 秒**；最新结果见下段。Docker Linux Engine 管道本轮不存在，新增真实 pgvector 聊天路径测试尚未执行；不得把上一轮 440/100 写成当前代码的全量结果。语义事实支持门控与独立盲测仍缺，实验开关不代表生产验收。

同日提交事务增强：PostgreSQL 聊天 `complete_run` 现在可接收内部引用源身份，在消息/Run 终态同一事务中对本租户当前文档与切块加共享锁并核对版本及内容 hash；不匹配则整个回答提交回滚。最新无外部服务全量 **293 passed / 256 skipped，21.46 秒**，Python 编译与 `git diff --check` 通过。新增真实 PostgreSQL 用例属于 256 项跳过之一；Docker 引擎仍不可连接，**尚无该事务检查的真实 PG 通过证据**。

2026-10-20 S6 本轮全量：显式设置本任务隔离 `TEST_POSTGRES_URL` 与已校验本地 `TEST_RAG_MODEL_ROOT` 后，后端 **440 passed / 100 skipped，452.69 秒**，零失败；包含真实 pgvector、索引 Worker／切流／回切和本地模型 CLI 端到端。100 项跳过仍需各自外部条件，不能计入通过。全量启动后追加的 Worker 持续轮询开关，连同索引／本地模型 CLI 专项以最新代码复测 **2 passed / 0 skipped，13.11 秒**；新的运行角色 ACL SQL 真实 PG 解析／owner 拒绝专项 **1 passed / 0 skipped**，但专用非 owner 正向角色集群未在本轮复测。Python 编译与 `git diff --check` 通过。没有双 Linux 主机、真实脱敏质量盲测或聊天运行时向量切流，S2–S6 仍未完成。

2026-10-20 S6 本地模型至真实 pgvector 端到端：在仅回环暴露的本任务隔离容器、合成租户和合成文档上，使用本地已核验 Qwen3 Embedding 权重由 `rag_index_worker.py` 实际生成 1024 维向量，迁移拥有者 seal／activate 后，以真实查询 Embedding 检索命中；专项 **1 passed / 0 skipped，24.39 秒**。模型只读取本机文件，没有公司文档或在线模型请求。此用例不覆盖真实业务语料质量、并发、双 Linux 主机、生产 Worker 角色或聊天路径接入。

2026-10-20 S6 索引 Worker／受控激活增量：新增限租户／固定 profile 的本地 Embedding 批量构建入口，推理与数据库事务分离，提交前复核当前文档版本及切块 hash。迁移拥有者可 stage、完整性 seal、原子 activate，并可为旧 profile 建新索引身份，经重新封存后安全回切；真实 PG 专项在扩充文档改版／profile 修改／双索引切换／回切断言后 **1 passed / 0 skipped**。此前完整 PG 集成组 **23 passed / 0 skipped，41.60 秒**，该完整组在最新断言前运行，不可冒充最新全组结果。测试使用合成向量，证明持久化/竞态控制，不证明真实模型批量吞吐、CPU/GPU 内存、双机运行或聊天路径切流。全量 437/101 为此增量之前的结果，不能冒充本次全量。

2026-10-20 S6 新增证据：用户启动 Docker 后，仅使用本任务专用的回环端口隔离 `pgvector/pgvector` 容器（pgvector 0.8.6），不访问其他容器。当前 Schema head 为 `0028_rag_vector`、46 表；真实 PostgreSQL 集成组在新增向量用例前 **21 passed / 0 skipped**，新增用例单独 **1 passed / 0 skipped**，随后含真实 PG 的后端全量 **437 passed / 101 skipped，427.88 秒**。101 项跳过为未提供其他外部测试条件的用例，不计通过。验证了固定模型 profile、版本化切块内容 hash、幂等 1024 维向量写入、租户隔离、索引激活门控及旧版本不可见。HNSW 未用于查询，双机、索引 Worker、线上切流、证据门控和 200 条独立脱敏盲测仍未验收。此前“Docker 不可用／无 pgvector”仅代表当时历史状态。

2026-10-19 本地 RAG 模型增量：已在 RTX 5070 Ti 测试机下载并本地 SHA-256 校验固定 revision 的 Qwen3-Embedding-0.6B 和 Qwen3-Reranker-0.6B；离线 CUDA 合成前向推理与项目组件测试 **2 passed / 0 skipped**。Embedding 输出 1024 维，Reranker 可区分两条合成片段，CUDA 峰值分配约 1.2 GiB。权重存隔离临时目录，未提交；尚无 pgvector、生产索引切换、质量盲测或目标 Linux 双机部署验收。

同日开发集消融：15 合成文档、55 正例、15 困难负例，向量穷举 Recall@3 98.2%、口语题 100%；RRF+Reranker 整体 98.2%、口语题 100%。0.95 Reranker 门槛虽拒绝 80% 困难负例，却误拒 18.2% 正例，明确不作为生产门控。详见 [RAG 升级](rag-upgrade.md)。

2026-10-19 S6 增量：当前 Schema head 为 `0026_rag_metadata`，新增空的 Embedding profile/索引 manifest 表；真实隔离 PostgreSQL 升级 **1 passed / 0 skipped**、SQLite 约束 **1 passed**、迁移/清单/接口专项 **17 passed**。未运行实际 Embedding、向量查询、重排、证据门控或盲测，不构成 S6 验收。此前 0025 测试数字为历史批次。

## 2026-10-18 S2/S5 增量：共享熔断私有指标与告警送达

0025 `model_circuit_state.open_count` 记录耐久累计打开/重新打开次数，模型阈值与 half-open 失败均更新它。认证 PostgreSQL API 仅在显式私有指标监听开启时，以短时只读查询输出全库 `collection_success`、数据库采集时间、固定状态数和累计打开次数；不输出租户/模型/用户/密钥/正文标签。采集失败为 `success=0` 而非伪装健康零值。多 API 实例读取相同数据库时必须指定一个聚合采集 target 或去重。

真实隔离 PostgreSQL 上 `test_model_circuit_metrics.py test_model_circuit.py test_model_budget_metrics.py` **12 passed / 0 skipped，30.54 秒**，包括专用 Bearer 凭据的真实回环 `/metrics` 抓取与未授权 401；真实 Prometheus 3.5.0 `promtool` 规则检查与情景评估 **2 passed / 0 skipped**；Prometheus→Alertmanager 0.28.1→本机合成 webhook 的 HTTP 日志与熔断 half-open 告警均验证 firing/resolved，**2 passed / 0 skipped，132.06 秒**。测试规则副本仅缩短 `for` 时间，正式规则未改。运行角色 DELETE 权限收紧后独立专用角色测试 **1 passed / 0 skipped，25.15 秒**。随后熔断/路由/预算/用量/费用/耐久聊天/HTTP 与 promtool 联合 **79 passed / 0 skipped，212.94 秒**；无外部服务后端全量 **278 passed / 249 skipped，22.54 秒**，249 项仅因缺显式外部条件跳过，不能算通过。前一次同组 **78 passed / 1 failed**：旧 killed-Worker 测试只等待 provider SSE，但那发生在新增熔断许可与付费 attempt 之前，可能在模型未出站时杀掉 Worker 并合法重排；改为只读等待耐久 attempt 后单项两次通过，再整组复测零失败。无真实值班接收端、跨宿主 Collector、通知升级、PG 切主和容量压测，S2/S5 均不勾选。

## 2026-10-18 S5 增量：0025 PostgreSQL 共享模型熔断

当前 Schema head 为 `0025_model_circuit`，新增每租户/供应商/模型共享状态表。模型请求在预算预留和出站前先获取熔断许可；60 秒内 3 次可分类健康故障会打开 30 秒。打开期间不创建付费 attempt，路由可尝试符合授权/预算的数据边界的备用模型。冷却后所有实例共用一个带 owner、generation 和长于配置的模型 Run 总时限的 half-open 探针；成功关闭，失败重新打开，过期探针可接管，旧探针回报不能关闭新状态。取消或准入失败不当作供应商恢复；Redis 不可用不会使此门控因进程本地状态而放宽。状态表受维护写封存约束，API/Worker 运行角色均需限定 DML，不保存 Prompt、密钥或用户文本。正式双节点必须采用相同 Run 时限配置。

隔离 PostgreSQL 熔断专项 **5 passed / 0 skipped**，覆盖双实例对象共享状态、租户隔离、阈值、half-open 排他、旧 owner fencing、模型边界打开时无新增出站和付费 attempt、恢复关闭。模型路由/预算/用量/费用/耐久聊天/HTTP 八组联合 **74 passed / 0 skipped，233.01 秒**；专用 `::1:55433` 运行角色测试另 **1 passed / 0 skipped，30.13 秒**，验证新表 Worker 写权限和价格/配置不可写。无外部服务后端全量 **278 passed / 243 skipped，25.00 秒**；跳过项不计通过，相关真实 PG 专项另行运行。将探针租约延长至超过配置 Run 时限后，熔断专项复跑 **5 passed / 0 skipped**，Python 编译与 `git diff --check` 通过。均为隔离本机合成模型与单 PostgreSQL 集群，不是双主机、数据库主备/分区、真实供应商或故障切换验收；阈值、容量、告警和生产恢复策略尚需批准与演练。S5 与整个 S2–S6 保持未完成。

## 2026-10-18 S5 增量：请求级备用模型切换

仅在认证 PostgreSQL 聊天 Run 中，模型调用层对主模型 429、408、5xx 或类型化传输故障，在**尚未向 Agent 交付本次模型响应**时尝试备用模型；优先本机回环端点，DeepSeek 为主模型时才考虑 OpenAI。流式响应上下文一旦进入，即使随后报错也不切换；不可分类错误、401/403、预算错误、取消、工具调用及整个 Agent Run 不重放。网关读取租户备用配置不改变 active provider；每次备用 attempt 先由持有租约的 PostgreSQL 事务核对当前租户目标配置、在线外发授权和预算合同，提交账本与预留后才触发 HTTP。缺本机端点、缺预算或撤权时跳过该候选；无合格备用则安全失败。在线外发仍默认禁止。SQLite MVP 不启用此自动切换。

合成请求/流式路由与客服回归 **68 passed / 0 skipped，13.68 秒**。隔离 PostgreSQL 端到端用例证明备用 OpenAI 出站前已有 started attempt，撤权后不再触发在线模型；完整认证聊天 SSE 在切换后再次报告 OpenAI，最终助手消息也保存实际供应商和模型。预算、授权、用量、费用、耐久聊天和聊天 HTTP 七组最终联合 **69 passed / 0 skipped，188.45 秒**，包含备份恢复。此前五组 **49 passed / 0 skipped** 是新增聊天持久化用例前的批次；首次未设置 `TEST_POSTGRES_BIN` 的 **45 passed / 4 skipped** 不作为最终证据。全部使用合成模型与测试密钥，无真实在线模型调用。尚未验证真实两节点端点能力、共享熔断、429/5xx 生产故障注入、供应商账单和正式 HTTPS/SSE；S5 与整体 S2–S6 不勾选。

## 2026-10-18 S2/S5 增量：0024 备用模型预算合同与角色权限

用户再次确认：DeepSeek 故障时默认禁止跨在线供应商外发，只有租户管理员对目标供应商显式开启才可考虑切换；未授权时只允许合格的本机模型或安全失败。OpenAI 授权配置已实现且默认关闭。`0024_model_route_budget` 新增逐租户/目标模型版本化 Token 合同；备用 attempt 的保守预留使用目标合同和目标模型价格，金额计入原租户主预算，`route_policy_id` 固定合同版本。缺合同、缺价格或币种不符在 Provider 前拒绝。管理员 API 和管理页可以查看/发布 OpenAI 或本地模型合同；发布合同本身不会自动授予外发权限。此段是请求级路由接入前的批次说明，最新边界见上节。

独立回环 PostgreSQL 上 `test_model_route_budget.py test_model_fallback_consent.py test_model_budget.py test_model_usage.py test_model_cost.py` 联合 **48 passed / 0 skipped，114.10 秒**，包含真实加密备份恢复。专用 `::1:55433` 集群另跑 `test_pg_runtime_roles.py` **1 passed / 0 skipped，24.78 秒**，验证迁移、角色授权和撤权；该批角色测试数据库初始残留为 0。无外部服务后端全量 **271 passed / 236 skipped**；前端 **39 passed** 且构建成功。隔离 8019 浏览器的合成管理员管理页显示 OpenAI 外发授权默认关闭、备用合同字段和缺主预算/价格时禁用发布；未使用真实密钥或外网。不同批次不相加为全量。供应商真实账单、请求级路由/每次外发前授权复核、共享熔断、双机部署及完整 S2–S6 仍未验收。

## 2026-10-18 S2 增量：在线供应商连接测试不再触发生成请求

管理页 DeepSeek/OpenAI 的“测试连接”由 `POST /chat/completions` 改为 `GET /models`；不发送测试 Prompt，也不产生未接入用量账本的生成尝试。隔离 `httpx.MockTransport` 验证 DeepSeek `/models`、OpenAI `/v1/models`、空请求正文、合成鉴权头，以及 302/坏 JSON 不能冒充成功；`tests/test_config_repository.py` **9 passed / 0 skipped**。结果只表示模型目录 API 可访问，不保证所选模型生成能力或供应商不对目录请求收费。没有使用真实密钥或外网请求；真实 DeepSeek/OpenAI 合同、账单与能力仍待验收。

上述 S5 账本与本批 S2 连接测试改动后，无外部服务后端全量 **271 passed / 231 skipped**；231 项需要隔离 PostgreSQL/Redis/S3 等外部条件，其中本批授权/用量/费用/预算联合 43 项已单独零跳过运行。Python 编译与差异空白检查通过；专用 PostgreSQL 集群当前 `pg_ctl` 确认未运行。不能把无服务跳过项视为全项目验收通过。

## 2026-10-17 S5 决策：跨在线供应商外发默认禁止

用户确认：DeepSeek 不可用时，**不得默认**把本轮用户消息和检索到的公司知识片段发给另一家在线模型；须由租户管理员逐目标供应商显式开启。该决策已写入架构和实施门槛，尚未实现授权持久化、管理入口或跨供应商路由。当前无自动切换路径，所以不会因故障自动扩大在线接收方；不能把“尚无路由”视为未来授权检查已经验收。后续至少验证默认拒绝、租户隔离、非管理员拒绝、授权/撤销审计、入队后撤销、并发撤权、供应商故障、无合格本机备用和敏感内容不出站。

后续授权增量：使用现有 PostgreSQL `app_settings` 租户行保存 OpenAI 备用外发授权的最新版本，缺行/坏状态均按拒绝处理；仅认证管理员可通过 `GET/PUT /api/admin/model-fallback/openai` 查看、启用或撤销。启用要求本租户已保存 OpenAI 密钥，期望版本与发布编号控制重复/并发请求，状态与 `audit_logs` 同事务写入；前端管理页明确显示默认禁止及当前未自动切换。真实隔离 PostgreSQL 的授权专项 **4 passed / 0 skipped**，覆盖默认拒绝、配置前拒绝、幂等/冲突、撤销、租户隔离、并发版本和非管理员 HTTP 拒绝；此前相关回归 **33 passed / 0 skipped**，前端 **39 passed** 且构建通过。没有调用任何在线模型。当前只是授权管理基础，路由尚未在每次实际外发前复核，也没有跨供应商 attempt/费用/能力筛选；不得把授权开启解释为已经允许或启用自动切换。上段“尚未实现授权入口”为当时状态。

收尾回归：无外部服务后端全量 **270 passed / 229 skipped**（新增 4 项隔离 PostgreSQL 用例在此运行中跳过，已单独真实跑通），前端 **39 passed**、构建通过。隔离 SQLite 浏览器在新模型管理页显示 OpenAI 未配置、跨在线供应商默认禁止、当前模式不可授权且按钮禁用；未录入或调用任何真实密钥。浏览器仅使用独立 8019 临时服务，不影响实际 8000；验收后关闭标签和服务。隔离 PostgreSQL 测试数据库残留查询为空，确切专用集群已停止并确认 `pg_ctl: no server running`。这不是认证 PostgreSQL 管理页的浏览器授权/撤销验收，HTTP 层已用 TestClient 验证，正式浏览器和双节点仍待测。

后续 S5 账本增量：`ModelUsageRepository` 对与 Run 原模型不同的备用 attempt，在开始前同事务核对当前租户授权、目标配置及模型名；授权写入/撤销与尝试开始共用事务锁。OpenAI 缺授权、撤权或模型不符时不得创建收费 attempt；授权后的 attempt 记录实际供应商/模型，撤权不阻止已开始的 attempt 终结用量。只覆盖原模型的现有预算会对备用 attempt 失败关闭，不把它作为零价或无预算处理。真实隔离 PostgreSQL 专项 **6 passed / 0 skipped**；联合授权/用量/费用/预算四组整套在显式配置匹配版 `pg_dump`/`pg_restore`/`psql` 后 **43 passed / 0 skipped，112.67 秒**。更早的 **29 passed / 2 skipped** 是未配置备份客户端目录的历史批次，不作为最终联合证据。此项没有启用自动故障切换；仍缺跨模型预算与请求级路由、双节点能力/健康、每次外发前复核以及费用结算核验。

## 2026-10-17 S2/S5 增量：模型账本备份恢复零跳过回归与 CI 门控

上一批模型用量、费用、预算联合测试的 3 项跳过，均因未显式提供 PostgreSQL 备份客户端目录。本轮在同一专用回环 PostgreSQL 16.10 上，以已有匹配版本 `pg_dump`/`pg_restore`/`psql` 补跑这 3 项：**3 passed / 0 skipped**；随后三组整套联合复跑 **37 passed / 0 skipped，98.14 秒**。验证包含连接前失败的两条独立 attempt、价格/预算预留以及加密备份恢复后证据不丢失。收尾查询 `cs_test_`/`cs_browser_`/`cs_backup_verify_` 数据库残留 **0**，仅停止本批确切集群并确认 `pg_ctl` 报 no server running，运行时和日志保留。

`.github/workflows/verify.yml` 的 PostgreSQL/Redis 集成作业现安装 PostgreSQL 16 客户端，将这三组完整测试纳入现有 JUnit 零跳过门控；这是本机静态配置和上述 Windows 隔离运行证据，**GitHub 托管 Linux CI 尚未实际执行**。不代表跨供应商路由、生产账单对账或备份灾备目标已完成。

## 2026-10-17 S5 增量：仅连接前失败的请求级单次重试

`ObservedModel` 对普通请求和流式**进入响应上下文之前**的故障，仅在类型化证据证明 HTTP 请求未发送（ConnectError/ConnectTimeout/PoolTimeout，包括 SDK 包装后的 cause 链）时，最多再试同一模型一次。每次重试生成独立 `attempt_id`、Trace 和耐久用量记录；Run 外层总时限仍生效。429/5xx、读取/写入超时、流式上下文已进入、取消及未知送达状态均不自动重试，也不重放工具或整个 Agent。失败 attempt 即使证明未发出仍按未知用量保留，费用/预算保守处理，不虚报零费用。

无服务故障分类/追踪/客服回归 **64 passed / 0 skipped**；追加 SDK 实际 DeepSeek 兼容客户端普通与流式连接故障专项各 **1 passed**，均确认 SDK 内部重试为零、网关层恰有两次尝试且无真实网络或密钥调用。随后无服务后端全量 **270 passed / 225 skipped**。隔离 PostgreSQL 16.10 上的新增用例 **1 passed / 0 skipped**，确认首次 error/unknown 与第二次 completed/observed 是两条独立记录；费用、预算、用量三组当时联合 **34 passed / 3 skipped**，其后已补跑并整组复测为 **37 passed / 0 skipped**，见本页最新条目。此项不是跨供应商切换、共享熔断或 429/5xx 重试完成证据，S5 仍未验收。

## 2026-10-17 S5 增量：真实回环协议与租户配置路径

在无在线模型密钥的隔离环境中，`tests/test_config_repository.py` 以真实 `127.0.0.1` HTTP 监听提供合成 OpenAI 兼容 `/v1/models` 和 `/v1/chat/completions`，验证管理连接测试、无密钥本地模型调用、响应和退出清理；该服务返回预置文本，**不是真实模型推理能力测试**。该文件 **8 passed / 0 skipped**。既有 PostgreSQL 租户只在完成管理 setup 后显示无密钥本地模型占位项，保存时才创建该租户的 `local` 配置行，不会跨租户可见；没有节点端点时切换本地模型失败关闭。新建仅监听 `127.0.0.1:55439` 的隔离 PostgreSQL 16.10 集群运行 `tests/test_auth_http.py tests/test_postgresql_integration.py`，修正初次发现的占位项展示断言后 **23 passed / 0 skipped**；追加租户隔离断言单项复测 **1 passed / 0 skipped**。批次范围重叠，不相加为 24 项。收尾查询 `cs_test_`/`cs_browser_` 库残留 **0**，仅停止本批自建集群并确认 `pg_ctl` 报 no server running，运行时日志保留。此证据不涵盖真实本地权重、双节点健康、费用或供应商自动故障切换。

## 2026-10-17 S5 增量：本地模型网关与装配入口

在逐主机端点校验基础上，`ModelGateway` 新增无密钥 `local` 配置及 OpenAI 兼容协议路径；API 与独立聊天 Worker 可分别通过显式 `--local-model-base-url http://127.0.0.1:<port>/v1` 装配，未配置端点的节点对本地模型失败关闭。管理页可保存模型名、查看本节点端点配置状态并测试 `/models` 连通性，不录入本地密钥或任意 URL；既有租户即使尚无 `local` 配置行也会看到可保存的只读占位项。连接成功**不代表模型能力已通过验收**。现有 DeepSeek 仍为默认，在线模型密钥规则不变。隔离 MockTransport 实际 `chat/completions` 请求验证了回环目标、无存储密钥与不跟随外部重定向；本机后端无服务全量 **261 passed / 224 skipped**，追加占位项后定向回归 **39 passed / 0 skipped**，前端 **39 passed** 且构建通过。跳过项包括需独立 PostgreSQL/Redis/S3 等服务的测试，不能算通过。尚无真实本地推理、双节点一致性/健康、模型能力与费用、自动切换或正式 Linux 浏览器验收，S5 不勾选。

## 2026-10-17 S5 增量：逐主机本地端点安全边界

新增 `LocalModelEndpoint`，只接受规范的 `http://127.0.0.1:<port>/v1`，拒绝主机名、其他回环地址、用户信息、查询、片段及路径变体；所建 HTTP 客户端禁用环境代理和重定向。`tests/test_local_model_endpoint.py` **15 passed / 0 skipped**。这是先行安全基元；后续网关装配见本页最新条目，仍未验证真实本地推理服务或故障切换。

## 2026-10-17 S2 增量：隔离告警触发与恢复送达

新增可选真实集成测试 `tests/test_alert_delivery.py`。以 Prometheus 3.5.0、Alertmanager 0.28.1 和仅监听回环的合成指标/webhook 接收端，复制正式 `CustomerServiceHttpLogPipelineUnavailable` 规则，仅在隔离副本把等待时间从 2 分钟缩到 0 秒；验证真实 firing 和 resolved 通知到达接收端。首次测试中 resolved 恰在 30 秒超时边界到达而误判；延长测试等待并增加边界检查后复测 **1 passed / 0 skipped，49.30 秒**。正式规则未修改，测试结束由 fixture 停止子进程和回环接收端。Alertmanager 下载包 SHA256 与官方 `sha256sums.txt` 核对通过。此证据仅证明隔离链路送达合成接收端，**不证明正式 Collector、生产值班接收人、通知升级链或跨主机故障下送达**，S2 保持未完成。

S5 本地模型接口已由用户选择为每台应用主机各自的回环 OpenAI 兼容服务。此处记录当时仅有设计输入；后续接入增量见本页顶部，逐节点健康和供应商故障切换仍未验收。

## 2026-10-17 S2 增量：知识 Worker 根 Trace 反映已提交任务终态

知识 Worker 不再在解析开始前将根 Span 标为 `completed`。持久 `finish()` 返回同事务确定的任务终态；`complete` 对应 Trace `completed`，逐项失败导致的 `partial/failed` 对应 Trace `error`，失租/撤权/异常分别保留固定失败分类和错误状态，不暴露文档正文。此前部分导入可能在根 Trace 中被误看作成功；这次修正的是观测语义，不改变导入结果或自动重试策略。

本机隔离 PostgreSQL 16.10 上，`tests/test_durable_knowledge.py tests/test_tracing.py` 联合 **23 passed / 0 skipped，46.03 秒**，包含真实 `partial` 任务提交与 Span 错误状态；另无服务追踪/故障分类 **20 passed / 0 skipped**。收尾发现上一批遗留、无活动连接的确切合成库 `cs_browser_927dcbcc90c44348928c45d61fa09be3`，已从本任务的隔离集群删除；复查 `cs_test_`/`cs_browser_` 残留 **0**，随后正常停止该集群。未访问实际客服数据库、生产数据或模型密钥。生产 Collector、正式双机链路与告警送达仍未验收，S2 保持未完成。

## 2026-10-17 S5 增量：供应商请求送达证据分类

`classify_model_failure()` 在既有故障类别之外新增固定 `delivery`：HTTP 状态响应为 `response_received`，直接 HTTPX 连接/连接池阶段失败为 `not_sent`，读取超时、SDK 包装异常及无法证明的路径为 `unknown`。客服失败日志只记录该固定值；不读取异常正文、供应商响应体或凭据。连接前失败也**不自动授权重放整个 Agent Run**：之前可能已有工具副作用，且账本、预算和备用模型能力尚未形成跨模型合同。当前仍不自动重试、熔断或切换供应商。

`tests/test_model_failures.py tests/test_api.py tests/test_service_resilience.py` 在追加日志断言后于本机无外部服务下联合 **50 passed / 0 skipped**；`git diff --check` 通过。本批未运行在线模型、双机或真实费用对账。S5 保持未完成。

## 2026-10-17 S3 浏览器验收边界与部署目标复核

正式目标已由用户再次确认为**自管 Linux 双实例＋反向代理**；双实例须位于独立应用主机，入口代理及 PostgreSQL/Redis 的高可用分别验收。当前尚无已批准的两台预发布主机、HTTPS 证书/域名、主备与仲裁、Collector/告警接收端，因此本机隔离测试不能替代正式双机验收。

本轮复核时，上一批合成验收服务 `127.0.0.1:8019` 与隔离 PostgreSQL `127.0.0.1:55432` 均已停止监听。此前专用 `/__acceptance/auth` 页把耐久聊天的 **202 JSON 回执**误作 SSE 处理，页面未更新；数据库当时显示对应 Run `complete`、模型尝试数 1，但这既不能证明实际前端失败，也不能作为实际前端成功证据。实际 `/` 前端的本批合成发送/刷新及首次失租重排队的浏览器行为**未完成验收**；后者仍只有下述真实 PostgreSQL 自动化测试证据。因隔离库当前未运行，本轮未重新核对残留测试库数量，不能把先前批次的清理记录挪作本轮证明。未使用真实模型密钥或业务数据。

## 2026-10-17 S2 增量：双机同机代理样板

新增待填充的 NGINX 同机 HTTPS 代理配置样板，外部入口经代理转发至固定 `127.0.0.1:8000` API，覆盖耐久聊天 202 JSON 回执与后续 SSE GET；代理覆盖 `Host`/转发协议/来源地址，关闭响应缓冲和缓存。域名、证书与上传限额故意保留占位符，模板未部署，也未通过 Linux `nginx -t`、浏览器 HTTPS、双代理/VIP 切换或证书轮换测试。当前本机 Docker Linux 引擎仍不可连接；这项仅交付可审阅的配置入口，不勾选 S2/S3 部署或 HA 验收。操作边界见[部署 Runbook](deployment-dr-runbook.md#制品与环境准备)。

## 2026-10-16 S3 增量：聊天任务首次模型调用前的单次安全重排队

`ChatRepository.reap_expired_leases()` 现在仅对首轮领取的 queued Run，在首次模型请求的 `model_usage_attempts` 记录不存在、没有工具记录或非初始化/供应商事件、未取消、会话未归档/删除且原排队期限未到时，持会话锁和 Run 行锁递增 fence 并重新置为 queued，追加 `run_queued/pre_dispatch_lease_expired` 事件。第二次领取的 fence 已越过单次重试门槛；即使又在模型调用前失租，也终结为 `interrupted/lease_expired`。模型/预算账本的 `begin()` 在供应商 I/O 前写入同一 PostgreSQL，并持有同一会话锁；若账本提交先于回收，回收器看到记录而不重试；若回收器先提升 fence，旧 Worker 的账本写入被拒，无法进入供应商请求。任何模型尝试、工具或输出证据存在时仍不重放。

隔离 PostgreSQL 专项先验证首轮重排队、旧租约写入拒绝、第二次失租终结，以及运行中取消不重排队：与只读清单/租约用例共 **14 passed / 0 skipped**。随后 `tests/test_durable_chat.py tests/test_model_usage.py tests/test_queue_review.py` 联合初跑 **27 passed / 1 skipped**，唯一跳过是未设置 `TEST_POSTGRES_BIN` 的真实备份恢复用例；提供本机隔离原生命令路径后重跑 **28 passed / 0 skipped，90.29 秒**。这不证明真实供应商 I/O 网络分区、双机进程竞态或 Redis 切主；知识解析仍无自动重试，S3 不视为完成。

## 2026-10-16 S3 增量：只读终态队列清单

新增 `app/queue_review.py` 与 `scripts/queue_review.py`：用独立私有 URL 文件连接，只在 PostgreSQL `READ ONLY` 事务中查询异常终态；启动时拒绝超级用户、数据库所有者、API/Worker 写角色、拥有 public 表/序列写权限的角色，以及目标表已启用 RLS 但未建立可信运维策略的状态。最多返回 200 条固定字段，不读取或输出正文、文件路径、原始错误或凭据；排除取消/被取代的聊天任务，分别标记未执行终态、可能未知执行及知识逐项失败。报告不领取、重试或确认任务。

本轮重新启动上一批专用临时 PostgreSQL 16.10 集群，测试动态创建并删除一个合成只读角色；验证角色权限漂移和 RLS 开启时拒绝、CLI 私有文件调用、分类/隐私/不重领。初次测试因合成聊天队列 deadline 早于 created_at 违反 SQL 约束而失败，已同时提前合成 created_at 后修正。`tests/test_queue_review.py tests/test_durable_chat.py tests/test_durable_knowledge.py` 联合 **30 passed / 0 skipped，106.94 秒**；其后收紧角色成员关系检查，新增 `pg_monitor` 成员拒绝断言并单独复测 `tests/test_queue_review.py` **1 passed / 0 skipped**，不把两批相加或将前一批视作最新完整联合回归。该证据未覆盖真实生产只读账号/TLS、完整 RLS 策略、审计确认或安全重试；S3 保持未完成。

## 2026-10-16 S3 增量：知识队列过期原因拆分

`DurableKnowledgeRepository.reap_expired()` 对从未领取、超过排队期限的任务写入 `failed/queue_timeout`，对运行后失租的任务写入 `failed/lease_expired`；两者均不重新领取。执行失租的提示保留“已发布内容可能存在、需核对后重新提交”的边界。新增真实 PostgreSQL 回归验证从未领取的任务保持 0 处理项/0 文档、不能再次领取；既有失租 fencing 用例增加明确阶段断言。

在本轮新建的仅监听 `127.0.0.1:55432` 的临时 PostgreSQL 16.10 集群上运行 `tests/test_durable_knowledge.py`：**14 passed / 0 skipped，44.14 秒**。测试结束 `cs_test_`/`cs_browser_` 残留数据库计数为 **0**，仅停止本轮临时集群。临时集群目录仍留在系统 Temp（递归清理命令被本机策略拒绝），不含生产数据。该证据不涵盖自动重试/死信操作入口、Redis 切主、正式角色/TLS 或双 Linux 主机故障演练；S3 不视为完成。

## 2026-10-15 S2 增量：Linux 构建制品入口

新增多阶段 `Dockerfile`：Node 22、Python 3.12、uv 0.13.0 基础镜像使用实际查询到的官方 registry digest；前端锁安装/构建和 Python `uv.lock` 生产依赖安装在构建阶段完成，最终镜像仅包含运行代码、迁移文件、静态资源和非 root UID/GID 10001。`.dockerignore` 排除工作区数据、`.env*`、虚拟环境和依赖目录。CI `linux-image` 作业将执行实际构建、检查非 root 用户，并在无网络/只读根/tmpfs/无密钥条件下导入 API/Worker 模块和核对前端资源，不发布镜像或连接生产服务。

当前本机 `docker buildx build --check` 因 Docker Desktop Linux engine 命名管道不存在而未执行检查；仅成功解析 CI YAML 作业结构，且基础镜像摘要通过 registry `imagetools inspect` 查询。后续 CI 作业已加固定摘要 Trivy 高危/严重漏洞门控与 CycloneDX SBOM 生成/上传，但**没有本机镜像构建、扫描或 SBOM 生成通过的证据，托管 CI 尚未实际运行**。仍需实际构建/扫描、签名和跨独立构建的摘要比对；正式主机的私有文件挂载、代理连接和只读运行也未实测。因此 S2 制品门槛保持未完成。

## 2026-10-15 自管 Linux 双机目标与故障处置契约校正

用户选定自管 Linux 双实例＋反向代理。已在 [部署与灾难恢复 Runbook](deployment-dr-runbook.md#双机拓扑与放行前提) 增加应用主机 A/B、入口、PostgreSQL/Redis/对象状态依赖和观测链的故障域与放行证据；明确两个应用实例不等于数据库/Redis/代理的 HA。该项是部署契约文档更新，**未部署、未运行双机故障演练**。

审阅当前 `DurableChatRepository.reap_expired_leases()` 和 `DurableKnowledgeRepository.reap_expired()` 后，修正 Runbook 早先“Worker 失租自动重新领取”“模型自动重试/切换”的前瞻性描述：当前实现将失租任务终结并提升聊天 fence，不重领、不盲重放；模型网关只完成错误分类，未启用跨供应商自动重试。未来重试须先有独立 attempt/费用预算与 unknown 对账边界。此次只改文档，无业务代码或新的测试成绩；预发布双主机、HTTPS 代理、PG/Redis 主备、对象存储及告警接收端尚待提供并实测。

## 2026-10-15 S2/S3 增量：隔离 PostgreSQL 与 CI 集成门控

CI 新增 `postgres-redis-integration` 作业：在 GitHub 托管 Linux 运行器上用已有 `compose.integration.yml` 启动仅监听回环的合成 PostgreSQL 16.10/Redis 7.4.6，执行 PostgreSQL、耐久聊天、数据库追踪和 Redis 准入专项，并解析 JUnit XML 要求 **0 skipped**；作业结束仅停止本作业 Compose 服务。该工作流尚未推送/在托管运行器运行，Redis CI 成绩不能预报为已通过。

为验证受本批 Worker/数据库装配改动影响的 PostgreSQL 路径，本机另新建仅监听 `127.0.0.1:55432` 的临时 PostgreSQL 16.10 集群，合成 `integration` 身份，运行 `tests/test_postgresql_integration.py tests/test_durable_chat.py tests/test_tracing_pg.py`：**37 passed / 0 skipped，101.71 秒**。收尾查询 `cs_test_`/`cs_browser_` 数据库残留 **0**；仅停止本批临时集群，55432 监听关闭。该隔离集群使用回环 trust 认证，**绝非**生产角色/TLS/HA 验收；本机 Docker 引擎未能用于 Redis CI 运行，真实 Redis 联动仍以此前隔离全量的旧源码证据为限。

## 2026-10-15 S2/S3 增量：自管 Linux API 显式装配入口

新增 `scripts/serve_api.py`：固定绑定 `127.0.0.1`，仅信任同机 `127.0.0.1` 代理的转发头；外部 Origin 必须为 HTTPS，Cookie 保持 Secure，并按 Origin 主机限制 Host。启动前要求已有且权限私有的 API 角色数据库 URL 文件、32 字节包装密钥文件、批准对象存储 policy 文件和绝对数据根；显式启用 PostgreSQL 认证、耐久聊天/知识任务，可选接入 OTLP、私有指标和有界 HTTP JSON 日志。关闭时有限 flush/close，不隐式迁移、创建账户、发现环境旧密钥或开放公网接口。`tests/test_api_entrypoint.py` 合成配置 **4 passed**，覆盖 HTTPS/文件/端口拒绝、角色与耐久装配、固定回环与代理信任；这不是 Linux 双机实际运行或反向代理 TLS/SSE 验收。

随后聊天/知识 Worker CLI 增加互斥的 `--database-url-file`，远程 PostgreSQL URL 不再允许以命令行参数传入；聊天 Worker 另支持 `--redis-url-file`，远程或含凭据 Redis URL 不允许以命令行参数传入。私有文件限制绝对路径、单行、有界大小、拒绝符号链接，Linux 检查所属者之外无读取权限，读取时对已打开文件复查以避免末端符号链接替换。`RedisAdmission` 拒绝非回环 `redis://` 明文，远程须 `rediss://`；redis-py 6.4.0 的 SSLConnection 默认要求证书并校验主机名。入口/凭据/Redis 协调专项 **8 passed / 4 skipped**（跳过真实 Redis 服务联动）；补齐新用例后当前源码在无外部服务下全量 **242 passed / 218 skipped**，同一 `uv.lock` 临时环境亦为 **242/218**。不把 218 项跳过算通过，之前 **429/1** 是旧源码隔离全量。

生产 systemd/代理/防火墙、私有 Collector、跨节点共享对象权限、真实 Redis ACL/TLS 和双实例故障切换仍待完成。上述入口只减少装配缺口，不代表 S2/S3 完成或授权公网发布。

## 2026-10-15 S2 增量：前端锁依赖审计与部署目标确认

用户确认正式目标为**自管 Linux 双实例＋反向代理**。这确定后续 API/Worker、HTTPS 代理、Collector 和双实例故障注入的验收环境形态，不代表该环境已配置或可用；PostgreSQL/Redis HA 仍须独立规划与验证。正式地址、证书、主备/仲裁、私有网络、接收人和运维负责人尚未提供，不在本批自行假设。

前端锁定依赖审计发现 `source-map-js@1.2.1` 的高危公告；把它精确更新至 `1.2.2` 后，完整审计还发现旧 `vitest@4.1.10`/`@vitest/mocker` 的中危公告，故将测试依赖明确锁为 `vitest@5.0.4`。未使用 `npm audit fix --force`。`npm ci`、`npm audit --audit-level=moderate --registry=https://registry.npmjs.org`（**0 vulnerabilities**）、前端 **39 passed** 和构建成功。CI 已加入同等级完整 npm 审计。npmmirror 当前不实现 npm audit API，因此审计请求明确使用 npm 官方仓库；这不改变一般安装镜像优先级。未运行远端 CI，不把单次已知漏洞库扫描等同于前端供应链安全验收。

## 2026-10-14 S2 增量：Python 跨平台锁依赖

使用 `uv 0.13.0` 从当前 `pyproject.toml` 生成 `uv.lock`（174 个解析包，包含平台/Python 版本分支、PyPI 来源和分发文件 SHA256）；锁文件 SHA256 为 `45B7B20C58F06EE9602AC667157AEDACD7EE31EA1545B457082EAC76E1D199B6`。CI 后端现先执行 `uv lock --check`，再 `uv sync --locked --extra test --extra production`，避免依赖范围改变后继续沿用旧锁。前端继续使用既有 `package-lock.json` 和 `npm ci`。

本机将锁安装到**新的临时 Python 3.10 环境**，没有替换项目原 `.venv`：153 个适用包成功安装，`uv pip check` 全部兼容，隔离环境未配置时 `pytest -q` 为 **234 passed / 218 skipped，22.14 秒**。从锁导出的全哈希 requirements 使用 `pip-audit 2.10.1 --disable-pip` 查询，结果 **No known vulnerabilities found**；CI 已加入同一版本扫描，但尚未在托管运行器执行。该结果只表示扫描时数据库未报告已知 Python 包漏洞，不覆盖操作系统/镜像、未收录漏洞或供应链签名；前端 npm 的后续独立审计见上节。

这验证本机 Windows/Python 3.10 的锁定安装和基础回归，不证明 Linux/Python 3.12 CI 已运行，也不证明 218 项真实依赖联动通过。SBOM/签名、镜像可重建与正式制品发布仍未做；锁文件会固定版本和哈希，但本身不证明包安全。

## 2026-10-14 S2 增量：无服务 CI 基线

新增 `.github/workflows/verify.yml`，在 push/PR 上分开执行后端 Python 3.12 安装、依赖一致性检查、编译和 `pytest -q -rs`，以及前端 Node 22 的 `npm ci`、测试和构建；仅授予仓库只读权限，不供应密钥、不连接生产服务、不部署或发布制品。本机按同等无隔离服务配置运行后端 **234 passed / 218 skipped，22.98 秒**，跳过主要为需要显式 PostgreSQL/Redis/S3/Prometheus 的真实联动；前端 **39 passed**、构建成功。GitHub Actions 尚未在远端运行，不能声称该工作流已通过托管运行器。

此增量当时只建立基础自动验证入口；随后已补跨平台 Python 锁文件、锁一致性门控和 Python 已知漏洞扫描，见上节。CI 仍未启动隔离依赖、未阻止大量集成用例跳过，也未做镜像构建、前端/系统漏洞扫描、SBOM/签名或可重建制品比较。绿色无服务 CI **不是**生产化 S2/S3–S6 验收，后续须补真实依赖门控和锁定制品。

## 2026-10-14 最新证据与历史限制勘误

在隔离 PostgreSQL 16.10、回环 SeaweedFS 4.48 和回环 OTLP/HTTP 接收端上，新增 `tests/test_knowledge_inputs.py::test_real_independent_worker_exports_private_object_child_spans`：API 侧合成可信 `http.request` 根 Span 提交真实知识任务，另一 Python 进程领取任务、读取私有对象版本并导出 OTLP protobuf。断言核对 `worker.knowledge` 的 trace_id/parent、内部 `job_id`，以及同一 Trace 中 `object.request` 的 `get`、`put`、`check_ready` 和敏感哨兵不外泄。这个用例的根 Span 是测试显式创建，**不单独证明真实 HTTP API 装配**；真实认证 API→队列→独立知识 Worker 的父子链已由此前 **56 passed / 0 skipped** 专项验证。两批合起来覆盖隔离环境中的队列关联及对象子 Span 跨进程出口，但不是生产 Collector 链路。

新增用例首次运行因测试断言把 OTLP protobuf 字节 ID 直接与整数比较而失败；修正测试解码为大端整数后，新增用例 **1 passed**。相关四组联合回归最终 **35 passed / 0 skipped，38.89 秒**。这些数字与此前 HTTP JSON 日志/告警 **32 passed / 1 skipped**、知识追踪 **56 passed / 0 skipped**、后端隔离全量 **429 passed / 1 skipped** 各有范围且可能重叠，**不能相加**；当前代码尚未在全部隔离服务配置下重新运行后端全量，上文无服务 234/218 也不能替代它。测试收尾确认 `cs_test_`/`cs_browser_` 数据库和合成 S3 桶残留均为 0，仅停止本批隔离 PostgreSQL/SeaweedFS，回环 55432/59000 监听关闭。

历史限制应按批次解读：早期 Docker 不可连接、真实 Redis/S3/Prometheus 未联动，已由后续隔离服务验收更新；“知识队列无 Trace carrier”“数据库或对象 Span 未接通”“对象 Span 未跨进程验证”分别由 0023、数据库专项和本批对象专项更新。最近一次全量 **429/1** 的唯一专用 PostgreSQL 角色跳过用例，已在另一隔离实例单独 **1 passed**，不能改写为一次 **430 passed**。现仍缺正式 API/日志出口装配、生产 Collector 与日志留存/权限、Alertmanager 实际送达、生产对象 IAM/TLS/KMS、真实供应商和业务沙箱、双实例 HA、正式迁移及 PITR/灾备演练；S0–S7 均未整体验收，不具备直接上线依据。可执行剩余任务与优先级见[生产化分阶段实施清单](production-roadmap.md#下一批可执行任务与优先级)。

## 2026-10-13 S2 增量：有界 HTTP JSON 日志出口及私有告警规则

新增可选 `BoundedJsonLogSink`：固定字段/类型白名单、UTF-8 单行上限、有界非阻塞入队和一个独立写线程；默认不启用，不自动读取现有日志或密钥。队列满、拒绝、输出函数失败和成功返回分开计数，私有 HTTP 指标只导出固定 outcome 与队列/线程状态。测试覆盖阻塞 writer 下的丢弃、有限关闭、合成敏感内容拒绝、HTTP 真实路由模板脱敏及私有指标；`ops/prometheus/http-log-alerts.yml` 的缺出口、丢失和持续积压规则使用真实 promtool 3.5.0 检查/合成评估。

`tests/test_log_alert_rules.py tests/test_log_export.py tests/test_http_observability.py tests/test_private_metrics.py tests/test_metrics_export.py` 合并专项 **32 passed / 1 skipped，5.43 秒**；唯一跳过的是需另起真实 Prometheus 服务的旧私有抓取用例，不能算此次生产采集验证。当前无正式 API 装配该 sink、日志平台留存/权限、Collector、实际 Alertmanager 接收人与触发/恢复送达，不勾选完整 S2。此前 **56/0** 是不同的 PostgreSQL 追踪专项，不与本批相加为全量。

## 2026-10-13 S2 增量：API→知识队列→独立 Worker 追踪

Schema head 更新为 `0023_knowledge_trace`：`knowledge_job_queue.trace_context` 为可空内部 JSONB，沿用固定四字段 carrier 的 Python/SQL 校验，旧任务保持 NULL。认证 API 在任务入队事务中保存当前服务端 Trace，Worker 领取并复查权限后以它为 parent；公开任务响应不包含 carrier，客户端提供的 traceparent/baggage 不被信任。追踪只承担关联，不参与租户权限、租约或任务结果判定；未配置追踪/无 carrier 时照常执行。

本机隔离 PostgreSQL 16.10 和仅监听回环的测试 OTLP/HTTP 接收端上，真实认证 API 提交、独立 Python 知识 Worker 解析、OTLP protobuf 接收验证了相同 trace_id、正确 parent、内部 job_id/数据库子 Span 及合成敏感标记不外泄。SQL 约束拒绝多余字段、错误类型和全零编号；空库升级与旧 SQLite 导入回归通过。相关 `test_production_migration.py test_postgresql_integration.py test_durable_knowledge.py test_tracing_pg.py test_tracing.py` 联合 **56 passed / 0 skipped，69.26 秒**；编译、`pip check` 和 `git diff --check` 通过。测试后查询 `cs_test_`/`cs_browser_` 库残留 **0**，仅移除本轮创建的隔离 PostgreSQL 容器与网络，55432 监听关闭，未操作其他容器或实际服务。此批不含真实 S3 对象子 Span 跨进程、生产 Collector/认证平台、通知送达或后端全量；S2 仍未整体验收。

## 2026-10-13 S2 前一增量：知识 Worker 显式追踪入口

知识 Worker 新增可选 `--otlp-traces-endpoint`，沿用受控 OTLP/HTTP 出口；每个已领取导入任务建立 `worker.knowledge` 根 Span，只有内部 UUID `job_id` 和固定 outcome。显式启用时安装一次数据库 cursor 监听器，已接入的 S3 对象操作在活动上下文内可形成子 Span；默认关闭，不读取模型密钥或隐式 OTLP 环境凭据。

`tests/test_tracing.py tests/test_database_tracing.py tests/test_object_tracing.py tests/test_otlp_export.py` 当时合并专项 **20 passed、0 skipped，1.88 秒**；CLI help、Python 编译和 `git diff --check` 通过。当时 `tests/test_durable_knowledge.py tests/test_knowledge_inputs.py` 未配置 `TEST_POSTGRES_URL` 而 **18 skipped、0 passed**。那时知识队列尚无 API Trace carrier，也未做真实跨进程 OTLP；这两项限制已由本页顶部的 0023 增量与 56/0 专项更新，但不能据此声称生产 Collector/告警或完整 S2 验收。

## 2026-10-13 S2 增量：对象存储子 Span 与证据边界

`S3ObjectStore` 的 `check_ready`、`put`、`get`、`list_versions` 现在只在已有私有 Trace 上下文中建立固定名称 `object.request` 子 Span，`object.operation` 只能取上述固定值。单测核对读写、内部校验、失败结果、父子关系及无上下文不采集；属性仅含 `request_id`、固定 operation 和 outcome，不导出桶、对象键、版本、内容、URL 或 SDK 原始异常。

本次在仅监听 `127.0.0.1` 的临时 SeaweedFS 4.48、合成凭据及 `TEST_S3_ENDPOINT=http://127.0.0.1:59000` 下，运行 `tests/test_object_tracing.py tests/test_object_store.py`：**21 passed、0 skipped、0 failed，1.35 秒**。测试后查询临时实例剩余桶为 **0**，停止本批确切 S3 进程并确认 59000 监听关闭。该批当时验证真实版本化 S3 读写和访问拒绝；当时尚未验收对象 Span 的跨进程导出及知识 Worker 全链，后续隔离证据见本页顶部。生产 IAM/TLS/KMS/Collector 与容量仍未验收。本批没有重跑后端全量，也没有修改业务代码；这是对当前未提交实现的验收证据，不代表这些实现已发布。

### 历史限制如何解读

- 早期批次记载的“Docker 引擎不可连接”“真实 Redis/S3 或 Prometheus 联动跳过”是当时环境限制。随后隔离服务联动已完成，并纳入下述 **429 passed / 1 skipped** 全量；唯一专用 PostgreSQL 角色用例另在其要求的实例中 **1 passed**。两次运行不能合写成一次 `430 passed`。
- 旧章节中“对象存储 Span 未接通”已由 21 项专项更新，“对象 Span 真实跨进程未验证”已由 2026-10-14 的 35 项联合专项更新；“数据库 Span 未接通”已由下述 36 项专项更新。其他旧段落描述其所属批次，不应直接当作当前缺口清单。
- 最新**全量**后端基线仍是 429/1；其后 S2 OTLP、数据库、对象和知识 Worker 追踪改动只有各自专项验证，PostgreSQL 知识链联合专项为 56/0，后续真实对象跨进程联合专项为 35/0。真实隔离测试证明的是代码路径，不证明生产 Collector、通知送达、双实例 HA、供应商/业务沙箱、正式迁移或灾难恢复。前端最新记录仍是此前的 **39 passed + build 成功**，本次未复跑。

## 2026-10-11 S2 增量：数据库子 Span

`app/tracing.py` 的显式 SQLAlchemy 监听器在已启用私有 Trace 的 API/聊天 Worker 内，围绕真实 cursor 执行建立 `db.query` 子 Span；同一 Engine 重复安装不会重复采集，未处于 Trace 上下文的 SQL 不产生 Span。只继承 request/run/attempt 编号与固定 outcome，既不导出 SQL 文本、绑定参数、表名或原始异常，也不调用全局 OTel provider。PostgreSQL API→队列→独立 Worker 的真实隔离测试核对 trace_id/parent 及敏感字段边界；SQLite 单测覆盖成功/数据库错误、无活动 Trace 和重复安装。

显式隔离 PostgreSQL 和 promtool 的合并专项 `tests/test_database_tracing.py tests/test_tracing.py tests/test_tracing_pg.py tests/test_otlp_export.py tests/test_http_observability.py tests/test_model_tracing.py` 为 **36 passed / 0 skipped，11.28 秒**；测试数据库残留 0，确切临时 PostgreSQL/Redis 容器已移除，`git diff --check` 通过。此增量未重跑整个后端全量；未覆盖知识 Worker、对象存储、Embedding/Reranker、数据库连接建立/池等待、生产 Collector/采样/容量或告警，S2 不勾选。

## 2026-10-11 S2 增量：显式 OTLP/HTTP 出口

新增 `app/otlp_export.py` 的可选 `PrivateOtlpTracing`，保持现有 SafeTracing 的 Span 名称/属性白名单、有界队列和显式注入。只接受带确定端口的 `/v1/traces` 端点，明文仅允许 IP 回环，非回环必须 HTTPS；拒绝 URL 内凭据和继承的 OTLP 环境配置，HTTP 会话不使用系统代理。独立聊天 Worker 新增显式 `--otlp-traces-endpoint`；未配置时原行为不变。生产依赖新增固定版本 `opentelemetry-exporter-otlp-proto-http==1.44.0`。

本机隔离 HTTP 接收端实际收到 OTLP protobuf Span，确认固定 `service.name` 和白名单属性；与原追踪、HTTP 和模型请求专项联合运行 **33 passed / 0 skipped，4.38 秒**，包含显式 `promtool` 路径。Worker CLI help、`pip check`、`git diff --check` 通过。该批当时未接入真实外部 Collector、认证/证书供应、Tempo、生产 API 装配、知识 Worker、数据库/对象 Span、告警送达或日志留存；数据库 Span 的后续增量见本页顶部，本批不勾选完整 S2。

## 当前验收基线（2026-10-11）

本节是近期 S2 OTLP、数据库、对象和知识 Worker Span 增量之前的最近一次**后端全量隔离测试证据**；新增链路分别由上方 36 项、21 项、20 项及最新 56 项专项覆盖，尚未在全量测试中复跑。专项之间存在重叠，不得相加。下方按日期排列的批次记录保留当时的结果和限制。旧记录中的“Docker 引擎不可用”“真实 Redis/S3 测试跳过”“未运行 Prometheus 联动”以及较小的全量通过数，均不得再当作当前测试状态；它们也没有因本节而变成生产环境验收。

- 显式提供隔离 `TEST_POSTGRES_URL`、`TEST_POSTGRES_BIN`、`TEST_REDIS_URL`、`TEST_S3_ENDPOINT`、`TEST_PROMTOOL_PATH`、`TEST_PROMETHEUS_PATH` 后，完整后端 `pytest -q -rs` 为 **429 passed、1 skipped、0 failed，422.69 秒**。唯一跳过的是 `tests/test_pg_runtime_roles.py`：该用例要求另一台仅监听 `::1:55433` 的专用 PostgreSQL 实例，不接受普通集成库。
- 随后在独立、临时的 PostgreSQL 16.10 实例上，显式提供 `TEST_PSQL_BIN` 和 `TEST_PG_DUMP_BIN`，单独运行 `tests/test_pg_runtime_roles.py`，结果为 **1 passed、0 skipped，21.46 秒**。因此当时的测试源码全部后端用例已在**两次不同隔离配置的运行中**覆盖；不能写成一次全量 `430 passed`。本轮新增 OTLP/数据库追踪用例仅有上方专项证据。
- Redis/PostgreSQL 使用本机 Docker Desktop 上固定版本的临时容器；S3 使用官方 SeaweedFS 4.48 Windows 测试运行时和合成凭据；Prometheus 3.5.0 的 `promtool` 及服务进程验证了规则、指标格式、正确/错误凭据抓取。Redis、S3、Prometheus 的真实联动在这次全量运行中不再因缺服务而跳过。此前单独 Prometheus 专项 **24 passed**；S3/Redis/Prometheus 联合专项在补齐 PostgreSQL 客户端路径前为 **63 passed、19 skipped**，19 项均因未设置 `TEST_POSTGRES_BIN`，已由随后全量运行覆盖。
- 首轮联合专项 **62 passed、2 failed、18 skipped**：两项 S3 失败来自隔离 SeaweedFS 的 `volume.max=2` 导致测试桶耗尽可写卷。新建容量充足的隔离实例后重跑，失败消失；这不是业务代码修复，也不隐藏首轮失败。
- 收尾确认合成测试桶、`cs_test_`/`cs_browser_`/`cs_roles_` 数据库及临时角色均无残留；移除本轮创建的 PostgreSQL/Redis 容器与 S3 测试进程。未修改业务代码、访问真实密钥/生产数据或调用在线模型。

以上只证明本地隔离环境中的功能和集成回归。仍**未完成**生产 Redis ACL/TLS/切主与网络分区、正式 S3 IAM/TLS/KMS/PublicAccessBlock、Prometheus/Alertmanager 部署和通知送达、双实例 HA、供应商及业务系统实测、正式迁移/PITR/异地灾备。SeaweedFS 的 loopback 测试豁免不能替代生产对象存储安全验收；S0–S7 均未整体验收，不能据此上线。前端本次没有重新运行，最近记录的 **39 passed + build 成功**仍是先前批次的证据。

## 2026-10-10 S1：RLS 范围与可信边界审计

当前 metadata 清点为 40 表：30 张有租户列、10 张全局表，只有 `identity_events.tenant_id` 可空；新增 `tests/test_rls_inventory.py` 将分类和例外固定为回归门控，**2 passed，0.13秒**，编译与 `git diff --check` 通过。新增 [RLS 实施契约](postgresql-rls.md) 明确 API 当前可直接铸造会话、Worker 跨租户领取和可伪造 GUC 这三个不能靠表面 RLS 掩盖的边界，并列出真实直接 SQL 攻击验收矩阵。此轮没有安装 RLS 政策，未做数据库层租户隔离通过声明；清单测试只防新表未经分类，不防现有跨租户直读。完整 S0–S7 保持 active。

## 2026-10-10 S1/RLS前置：Worker 密码摘要最小权限

四角色授权现收紧 `identity_users`：Worker 不再有表级 SELECT，只对会话校验需要的 `id`、`active`、`auth_version` 三列有读取权限；启动门控检查密码摘要列不可读，避免后续误授权静默扩大。真实隔离 PostgreSQL 联合测试 `tests/test_pg_runtime_roles.py tests/test_production_migration.py` **15 passed，23.79秒**：Worker 成功解析合成会话、直接读取 `password_hash` 被数据库拒绝；临时授予该列后 `check_schema` 拒绝，撤销后恢复。此前一次同组 **15 passed，23.46秒**，但不含权限漂移断言。编译、依赖和 diff 检查通过；查询无测试库/角色残留，只停止本批确切隔离 PostgreSQL 集群，未操作实际 8000 服务。此项不改变 API 账号管理权限，也不解决 Worker 对其他租户业务行的直接 SQL 访问，绝非 RLS 完成。

## 2026-10-10 S1后续：API/Worker 四角色拆分

`grant-runtime.sql` 现要求迁移、API、Worker、备份四个互不继承的身份；Worker 可跨租户领取任务所需的队列/业务 DML，但不得写供应商配置、身份会话、价格版本或预算策略。Repository 启动门控增加 `runtime_role='api'|'worker'`；HTTP API 与两个独立 Worker 分别选用对应身份，互用失败。隔离真实 PostgreSQL `tests/test_pg_runtime_roles.py tests/test_production_migration.py` 最终 **15 passed，23.37秒**，覆盖实际 Alembic/授权、明确预算策略 INSERT ACL、角色权限、API/Worker 启动互斥、备份只读和连接前置校验；前一次未增加 ACL 断言的角色单测 **1 passed，23.15秒**。随后当前环境完整后端回归 **337 passed / 91 skipped，382.52秒**，退出码 0；跳过项不计通过，特别不代表真实 S3/Redis/外部集成验收。`compileall`、`pip check` 与 `git diff --check` 通过。收尾查询无 `cs_roles_` 等测试库及 `cs_migrator_`、`cs_app_`、`cs_worker_`、`cs_backup_` 临时角色残留；只停止确切自有集群并复查 `no server running`，未触及实际 8000 服务。此处更新下方三角色与“Worker 接受应用角色”的历史记录，不将旧批次成绩冒充当前全量。

这只是 RLS 前置边界：API 与 Worker 凭据直接 SQL 仍可跨租户读取；单纯可由应用角色设置的 `app.tenant_id` 会话变量不是可信租户隔离。生产身份供应、TLS 握手、RLS/可信上下文、Worker 跨租户最小权限与完整恢复/HA 验收未完成，S0–S7 目标仍 active。

## 2026-10-10 S1后续：应用角色启动门控

`PostgreSQLRepositories`新增启动时有效权限检查：远程目标及URL目的地覆盖默认强制且不可关闭，回环/代理可显式`strict_role=True`；在`initialize/check_schema`业务查询前拒绝超级用户、所有者、可建库/角色/Schema、BYPASSRLS、INHERIT或可切换对象所有者、TRUNCATE/TRIGGER，以及缺少关键应用DML或具有审计/用量账本危险改写权限的连接身份。API lifespan、聊天和知识Worker均使用`check_schema`；两个Worker新增`--require-app-role`以在回环连接也强制门控。固定安全错误不回显数据库凭据。

最终联合回归`tests/test_pg_runtime_roles.py tests/test_production_migration.py` **15 passed，19.52秒**：合规应用角色通过真实Repository、FastAPI TestClient启动和两个独立Python Worker `--once --require-app-role`；迁移、备份和超级用户角色均在API/两个Worker启动时失败。授予应用角色Schema CREATE后，下一次`check_schema`失败，撤销后恢复。非PG单测覆盖远程默认强制、URL目的地覆盖、禁止显式关闭及回环默认形状；此组仅构造Engine、不连接远程。收尾查询测试库/角色均无残留，精确停止自有PG集群，未触及实际8000服务。现有历史全量结果不包含这次新增门控/测试，未重跑完整后端。生产角色、TLS、回环代理配置、持续权限变化、RLS及容量仍未验收，完整目标保持active。

## 2026-10-10 S1：迁移/应用/备份数据库角色

新增`ops/postgresql/grant-runtime.sql`：要求当前连接与数据库所有者为独立非超级迁移角色，应用与备份角色无管理属性或互相继承；同事务撤销PUBLIC连接/表/序列/函数默认授权，授予应用DML、备份只读及迁移角色未来对象默认权限，并对审计/费用不可变账本收紧改写/删除。提交前检查实际有效的CREATE、所有权、TRUNCATE、备份DML和关键账本权限；发现预存危险授权则回滚，不把GRANT脚本误称为能修复任意角色层级。

真实`::1:55433`隔离PG验收`tests/test_pg_runtime_roles.py` **1 passed，2.05秒**：创建三种临时非超级LOGIN角色及专用数据库，迁移角色实际Alembic升级；错误执行者拒绝且未授权、备份角色预存INSERT使脚本失败并回滚、修复后连续两次授权成功。随后验证迁移角色新建表的默认授权、应用真实身份/登录/租户配置写入和普通DML、应用DDL/TRUNCATE/审计改写拒绝、备份SELECT/数据`pg_dump`成功且INSERT/CREATE拒绝，以及迁移角色撤销应用SELECT后立即失败、再授予恢复。测试使用合成角色口令/密文，不调用真实模型或业务接口；测试结束删除确切临时数据库与角色。收尾查询确认`cs_roles_`、`cs_test_`、`cs_browser_`、`cs_backup_verify_`测试库和`cs_migrator_`、`cs_app_`、`cs_backup_`临时角色均无残留；精确停止自有PG集群并复查no server running，未触及实际8000服务。

授权脚本尚未在生产执行；生产TLS握手、真实账号/密钥供应、RLS、独立审计归档、连接预算、混合版本迁移和恢复演练未完成。完整S0–S7保持active，不能上线。

## 2026-10-10 S1前置：远程PostgreSQL身份校验门控

`build_engine`对非回环TCP目标拒绝未显式使用`sslmode=verify-full`的连接URL，避免libpq默认`prefer`静默接受未验证服务端；回环测试连接明确钉住实际loopback地址，并拒绝通过URL的host/hostaddr/service覆盖借豁免。`tests/test_production_migration.py`最终**13 passed，0.82秒**，覆盖远程缺TLS/弱模式、伪回环域名、目的地覆盖、缺主机、显式verify-full形状与回环hostaddr注入。另在专用`::1:55433`隔离PG执行真实`SELECT 1`及带合成密文的迁移/租户配置专项**1 passed，2.01秒**；自有测试数据库清单为空后精确停止集群。真实远程证书链/主机名握手、生产应用角色与迁移角色权限、连接预算及服务端TLS仍未验收，S1不勾选，完整目标保持active。

## 2026-10-10 S5前置：流式失败草稿清理

普通流式模型异常或超时在已发送部分token后，现先发`answer_reset`再发安全`error`；不得将未提交草稿留在页面，也不保存助手消息或发送`done`。原有工具、预算、权限和取消失败路径继续保留清理语义。SQLite隔离合成模型回归`tests/test_api.py`和`tests/test_service_resilience.py`共**38 passed，10.88秒**，其中新增流式部分输出后异常测试，并将超时测试改为先输出部分内容再停滞。未进行真实供应商故障注入、浏览器SSE验收或备用模型切换；这只是S5失败界面一致性的前置改进，完整S0–S7目标保持active。

随后加入纯固定类型供应商故障分类，并在客服失败日志输出`failure_kind`，不记录异常文本或供应商body。合成429/408/5xx/401/403/402/其他4xx/传输/未知共12项分类用例，含敏感哨兵不外泄断言；增强日志哨兵断言后，API、服务韧性与分类最终联合回归**50 passed，11.33秒**，编译与diff检查通过。该分类尚未驱动重试/熔断/切换；没有真实供应商或浏览器测试，不能勾选S5。

## 2026-10-10 0022后续：管理员预算与私有告警

新增token-bound config.manage价格/预算管理HTTP、前端模型配置页价格/预算发布和租户状态；金额只接受/返回十进制字符串，发布仍用UUID幂等与expected_version并发检查。客户/客服角色没有读取或发布权限，页面不回显Key。管理员状态只读当前租户的策略、租户/UTC日占用及软阈值事件，未知费用的剩余额度保持NULL。

显式注入认证PostgreSQL和私有回环MetricsPolicy后，`ModelBudgetMetrics`从PG耐久证据只读聚合预算策略租户数、不可变预留数、租户/UTC日/Run软阈值事件数、Run终态预算拒绝数、已发布预算策略但有旧无预留未知请求的租户数及采集健康。输出无租户/用户/Run/模型/币种/金额标签或值。UTC日事件按调用起始时间归属；采集故障返回success=0/时间0，不回显原始异常。Prometheus规则及合成用例验证健康、新软阈值、拒绝、未知账本、停更和下线实例。当前没有实际部署collector/Alertmanager receiver或送达通知，不能宣称完整软告警。

首轮价格/预算/私有指标专项 **1 failed / 39 passed / 1 skipped，46.63秒**：新路由未加入显式认证白名单而返回503，补入config.manage后复验。新预算告警promtool check/test **2 passed，0.10秒**；恢复白名单后专项 **40 passed / 1 skipped，49.07秒**，其中跳过项不计通过。随后后端全量（真实隔离PG、PG binary、Prometheus/promtool）**367 passed / 45 skipped，453.96秒**，退出码0；全量启动后修正UTC日事件归属及未知金额状态并增加断言，最终预算指标/告警专项 **6 passed，16.09秒**，不把追加改动冒充已纳入当次全量。前端 **39 passed**、构建成功；真实浏览器交互、生产权限/容量及指标送达验收未完成。

整体S0–S7目标保持active，0022应用侧预算仍不能替代供应商账单上限。旧0022章节中的“管理UI/指标未实现”为该时间点历史状态；受控结算释放、更多计价路径、真实合同/账单验收及S2其他缺口仍在。

## 2026-10-08 0022：原子模型预算预留

新增model_budget_policies/model_budget_reservations、ModelBudgetRepository及0022单head。默认无策略不启用；config.manage管理员明确发布精确tenant/provider/model/币种、tenant生命周期/UTC日/Run限额、Run请求数、软阈值、输入合同Token上界及输出上限，与audit同事务、版本/幂等/并发控制。每个普通/流式SDK调用先复查身份/Run/lease/fence，tenant级PG事务锁内写started、固定quote与保守reservation，提交后才进入Provider；策略发布、终结和对账使用同锁。文本/函数工具计价面、模型默认settings与max_tokens限制接通，不远程count_tokens、不用字符数猜Token。

占用按不可变reservation及估算/声明历史峰值取大，修订不相加；tenant/UTC日/Run金额和Run次数原子拒绝，零费率仍限次数。缺价/币种不匹配/旧无预留unknown/不支持的媒体、native/结构化/推理输出或Provider扩展失败关闭；拒绝事务无attempt/quote/reservation，不进入模型。取消、失败、崩溃、成功低用量及人工下调均不自动释放永久保守预留；追加超预留费用会阻止后续调用。soft标志耐久保存但尚未导出/通知。聊天返回固定budget_exceeded或budget_unavailable、answer_reset，Run为error、无助手/done、不提示更换密钥、不自动重试。

迁移新增两表UPDATE/DELETE/TRUNCATE证据保护，当前36表维护封存与statement guard、SQLite导入空预算表、全表manifest和实际加密备份恢复策略/未解决预留/recovery hold联动。初次预算/费用/迁移专项 **1 failed / 31 passed，47.15秒**，失败是测试对空reservation表执行DELETE时行级trigger没有可触发行，并非已有证据可删除；先写真实预留后复验。后续模型预算、用量、Trace、维护及PG集成 **1 failed / 60 passed，110.20秒**，失败仅为新迁移禁降级文案缺少测试约定的“is”，统一安全文案后收尾。

当前源码后端全量（显式真实隔离PG、PG binary、Prometheus/promtool）**361 passed / 45 skipped，459.10秒**，退出码0；跳过不算通过。该全量启动后新增的认证HTTP预算拒绝用例先单独 **1 passed，5.33秒**，最终完整预算专项（包含该用例）**15 passed，35.69秒**、无跳过；不把追加专项伪装为当次全量收集。前端 **38 passed，1.79秒**及构建成功；compileall、pip check、git diff检查和相关Markdown链接通过。没有真实模型/业务调用、历史Key读取、公网发布或实际8000服务操作。

证据覆盖并发不同Run同tenant只能一个预留、日/Run/tenant/请求数边界、软标志、默认模型settings和unsupported覆盖拒绝、普通/流式调用提交后Provider才看到、真实PydanticAI工具循环逐请求预留、unknown人工声明不解锁、已观测超额阻断、价格为零仍限次数、策略幂等/并发/审计回滚/权限撤销/SQL不可变、认证HTTP安全SSE及实际加密恢复。应用侧预算不等于供应商硬账单保证；真实合同/Tokenizer/隐藏计费/max_tokens遵从、受控结算释放、更多计费路径、管理UI、费用指标/外部通知、生产权限/容量尚缺，不勾选完整S2，完整S0–S7保持active。边界见 [预算操作](model-budget-operations.md)。

## 2026-10-08 0021后续：预算保守占用读模型

新增内部 `ModelCostRepository.exposure`，管理员实时权限/租户复查，可按Run或调用起始UTC日限定；单条SQL先按attempt取历史金额峰值再分币种聚合。未知/partial/started保留unresolved与NULL总金额，operator_reported不解决未知，向下修正不释放占用；返回9位十进制字符串，不混加币种、不把修订相加。retained_amount只是已存证据汇总，不代表供应商实际账单上下界。无Schema变化、HTTP/UI/SSE变化或自动启用预算，不可用读快照做非原子检查后调用。

初次真实隔离PG费用专项 **11 passed，24.11秒**；最终代码加租户隔离/撤权断言后，费用及SDK用量联合回归 **21 passed，44.77秒**，无跳过。覆盖started未知、完整SDK估算、人工上调/下调、unknown加人工声明仍未解决、日/Run筛选、无价币种、CNY/USD分组、客户拒绝、他租户空视图/Run拒绝、撤权与原有调用/估算/加密恢复。编译与git diff检查通过。没有重新运行全量后端/前端，上一节全量结果仅针对之前源码，不冒充本批全量证据；没有真实模型/业务调用或浏览器操作。

原子预留/租户串行准入、旧尝试未预留的迁移政策、软告警/硬限额、输入可计费上界/输出限制、未知占用处理和管理界面仍待实现。读模型只是下一步门控基础，不勾选S2预算，不宣称生产就绪；完整S0–S7目标保持active。

## 2026-10-08 0021：价格快照与追加式费用声明

新增model_price_versions/model_price_quotes/model_cost_assessments及ModelCostRepository。token-bound config.manage发布版本与audit同事务；调用前quote固定tenant/model已发布价格，finish用量与revision=0估算同事务。Decimal缓存分项/每百万单位/9位向上取整，no_price/no_quote/unknown/partial/非法缓存保持NULL金额。后续管理员以receipt摘要/请求/期望revision追加operator_reported，幂等/冲突/币种/receipt去重，不修改原价格/用量/估算。三表UPDATE/DELETE/TRUNCATE及旧用量TRUNCATE保护、34表维护封存/导入空费用表、备份全表摘要联动。无默认真实报价、HTTP/管理UI或供应商自动核验；边界见 [费用操作](model-usage-operations.md)。

初步用量/迁移/维护/Worker专项 **32 passed，52.07秒**。新增价格/费用专项 **8 passed，19.92秒**；增强端到端后 **1 failed / 20 passed / 1 skipped，23.14秒**，失败因测试发布synthetic模型价格而HTTP实际配置deepseek-chat，正确未借用不匹配价格。修正为读取隔离配置model，复验费用/Worker/迁移/实际加密恢复 **22 passed，27.79秒**；增加并发发布/审计回滚与期望版本绑定后最新费用专项 **9 passed，20.39秒**。初次失败保留，跳过不算通过。

最终当前源全量真实隔离PG/PG binary/Prometheus/promtool回归 **344 passed / 45 skipped，402.26秒**，退出码0；41项S3和4项Redis跳过不算通过。前端 **38 passed，2.18秒**及构建通过；编译、pip check及diff通过，相关文档链接检查收尾验证。实际独立API→Python Worker quote固定明确价格并产出estimate，与用量/Trace attempt一致；实际加密备份恢复四费用/用量表行摘要/价/quote/声明，保持recovery hold，无重收费/Worker启用。收尾检查本批cs_test_/cs_browser_/cs_backup_verify_数据库为空，精确停止临时PG cluster；实际8000及其他数据库未改，测试运行时/证据保留。

九项新测试覆盖Decimal/float/非有限值/精度/缓存、完整已知显式零费率与未知不能归零、价格pin与之后发布不重价、audit原子回滚、price/reconcile并发版本只一成功、重复/冲突/receipt/币种、他租户/客户/撤权/hold、直接SQL未知NULL不能绕CHECK及全部四表TRUNCATE拒绝、实际价格/费用加密恢复。operator_reported仅人工声明，不充当已核验供应商账单；最高revision是当前声明，历史行不可相加。本批无真实模型/业务调用、无新公开页面/SSE行为及浏览器验收，不绕过先前限制。

完整S0–S7未完成：预算/软硬限额、费用HTTP/UI/指标、供应商usage/账单核验与更多计价、其余计费路径、平台/权限/HA/模型路由/RAG等仍缺。0021无自动迁移/配置/切库，上线与混合版本兼容未证明；目标保持active。

## 2026-10-08 0020：持久SDK用量尝试

新增model_usage_attempts、ModelUsageRepository和0020单head；PostgreSQL聊天/独立Worker每普通或流式请求前提交started/unknown，网络后短事务终结，私有Trace共享attempt_id，无Trace也入账。租户/actor/Run/配置/owner/fence/期限复查，取消允许同有效租约记录，撤权/失租/hold不绕过；缺省零/非法值为NULL unknown，正数SDK观测为observed/partial，不视作供应商费用。SQL终结不可改写/删除，31表维护inventory/statement guard、SQLite导入空账本与全表备份manifest接通，实际加密恢复验证Token/摘要和trigger。网关SDK隐式重试关闭，内存HTTP500确认一次调用，失败unknown；未创建OpenAI配置/真实模型调用。边界见 [用量操作](model-usage-operations.md)。

初次迁移/追踪/维护专项 **1 failed / 23 passed / 7 skipped，17.08秒**，失败为0020FK目标反射不能离线DDL；修复为不可变目标stubs，不用当前runtime metadata回写历史迁移。后续用量/迁移/维护专项 **1 failed / 29 passed，48.08秒**，失败为FunctionModel自动估算缺失用量与零值测试预期不同；改用明确零值SDK响应测试，保留估算正数observed边界。修复复验用量/追踪/迁移/实际加密备份 **23 passed，31.67秒**；增加关闭隐式重试后模型专项 **18 passed / 1 skipped，22.65秒**（该次未设置PG binary而跳过备份不算通过）。最终当前源全量显式PG binary/Prometheus/promtool回归 **335 passed / 45 skipped，442.62秒**，退出码0；41项S3和4项Redis跳过不算通过，初次失败保留不计作通过。

前端 **38 passed，1.84秒**及构建通过；编译、pip check、相关Markdown链接与diff检查通过。专项证据包括：另一连接验证pre-call已提交、未启用Trace仍入账、finish重复同值幂等/冲突拒绝、异常/取消未知、他人/错误lease拒绝、撤权后不调用模型、post-call失fence只留下未结算不重试、直接SQL终结改写/删除和维护hold拒绝、实际独立Worker同trace/账本编号、加密恢复摘要一致及不可改写。收尾检查本批cs_test_/cs_browser_/cs_backup_verify_数据库为空，精确停止临时PG cluster，保留测试运行时/证据；实际8000服务及其他数据库服务未改。没有新公开HTTP/SSE/前端字段或浏览器验收，不绕过先前限制。

仍缺价格/金额、供应商usage来源与追加对账、原子预算/软硬限额、连接测试/count_tokens/compact/Embedding/Rerank覆盖、费用界面与真实账单验收及完整S0–S7，目标active，不能上线。旧head/旧包需匹配恢复制品；默认SQLite无用量账本，不自动切库或启动生产Worker。

## 2026-10-08 0019后续：模型SDK请求追踪

ModelGateway/ChatOrchestrator接通私有ObservedModel，普通与流式SDK每请求独立model.request/attempt_id；工具往返与校验重试分别关联，提前关闭/取消/读取及上下文清理失败不冒充完成。固定供应商/stream标签，不采集模型名/URL/Key/输入输出/供应商自由metadata；用量原样转发，不生成账单。真实隔离API→独立Worker/PydanticAI模型Span父子链通过；九项新增模型专项覆盖生命周期、内存DeepSeek兼容协议和11/7Token转发。详情见 [模型追踪验收](model-tracing-verification.md)。

最终全量 **325 passed / 45 skipped，413.58秒**，含真实隔离PG/Prometheus/promtool，退出码0；41项S3和4项Redis跳过不算通过。模型/工具/追踪专项 **20 passed，3.73秒**；原聊天等专项 **48 passed，12.43秒**；前端 **38 passed，1.86秒**及构建通过，编译/依赖/文档链接/diff通过。初次专项1失败为测试使用旧usage方法，已修复并保留失败证据。本批临时PG检查无残留测试库并停止；实际8000服务未改，无外网/真实模型调用，无新浏览器验收。

模型Span是SDK请求，不保证SDK底层HTTP隐式重试逐次计量；连接测试/count_tokens/compact、持久ledger/价格/unknown对账/并发预算、模型路由/熔断及完整S0–S7仍未完成，目标active，不能上线。下方“五种Span”是上一批范围，已扩展为六种。

## 2026-10-08 0019后续：工具与知识检索追踪

六个现有工具接入真实PydanticAI执行边界，tool.execute固定名称/每调用UUID attempt_id，知识工具鉴权后进入rag.search，继承内部request_id/run_id及父工具attempt_id。没有采集参数、返回内容、查询或异常文本，未新增迁移或公开字段。实际四并发Run/六工具、36个Span父子关系、同步线程上下文、失权不检索、异常/async取消/上下文恢复和关闭追踪兼容均通过，详情见 [专项验收](tool-tracing-verification.md)。

本批最终全量 **316 passed / 45 skipped，405.50秒**，含真实隔离PG/Prometheus/promtool，退出码0；41项S3和4项Redis跳过不算通过。专项 **13 passed，2.27秒**；前端 **38 passed，2.00秒**及构建通过，编译/依赖/文档链接/diff检查通过。自有临时PG中测试数据库检查为空，精确停止cluster；没有修改实际8000服务，无真实模型/业务调用，本批未新增浏览器验收，不绕过既有限制。

completed仅表示函数返回，不替代业务成功/答案质量；工具attempt_id未持久入审计/账本，不是幂等键。模型请求、Embedding/Rerank、全数据库/对象/知识Worker、生产出口/平台/费用及完整S0–S7仍未完成，目标active，不能上线。下方“三个Span”是前一批历史范围，已由本批扩展至五种。

## 2026-10-08 0019：可信聊天链路与内部队列 carrier

新增app/tracing.py、app/persistence/trace_context_v1.py、0019_trace_context迁移及tests/test_tracing.py/test_tracing_pg.py。显式私有OTel provider不改全局provider、无collector/凭据自动发现；新HTTP可信根、日志trace_id关联、同事务内部四字段carrier入队、Worker CONSUMER及真实Agent子Span已接通。队列字段可空，精确SQL形状/类型/编号约束；幂等重提交保留首次上下文，不变更授权/租约/业务指纹，不从公开HTTP/SSE/snapshot输出carrier。8并发任务独立，继承未采样parent，旧NULL提交仍执行。

processor单daemon线程/有界queue+一个活动export，API结束只非阻塞入队；未知名字/属性/非法值/描述拒绝，未存events/links，失败与丢弃固定计数，不打印异常或正文。HTTP私有指标增加固定trace outcome Counter和队列/worker Gauge。可有限flush/close，但不能杀死卡住的exporter；调用方负责有明确timeout的批准exporter及资源关闭，不把SDK返回SUCCESS当远端耐久存储证明。Worker CLI只新增synthetic-trace-output临时新JSONL证据模式，无生产OTLP自动供应。

初步HTTP/API/迁移专项 **44 passed / 1 skipped，16.01秒**；最初PG专项暴露新增测试fixture未显式导入及capture_carrier局部导入落在不同route的问题，已修复并重跑实际API/独立Worker/SSE专项 **4 passed，21.65秒**。进一步隐私字段/SQL/私有采集/耐久Worker专项 **32 passed，78.46秒**；增加未采样parent测试后HTTP/追踪专项 **13 passed / 1 skipped，0.71秒**（promtool未设置的该次跳过不算通过）。第一次完整回归 **1 failed / 310 passed / 45 skipped，413.35秒**，失败为0019禁止降级文案缺少旧契约的“is”，行为仍拒绝降级；已修复为downgrade is disabled，迁移/追踪复验 **9 passed，11.95秒**。最终当前源完整真实PG/Prometheus/promtool回归 **311 passed / 45 skipped，401.31秒**，退出码0；跳过仍为41项S3和4项Redis，不计作通过。首次失败保留，不计作通过。

实际认证API提交合成正文/baggage/伪造traceparent，队列首carrier与服务端响应编号一致；独立Python Worker/PydanticAI合成模型生成worker.chat/agent.run，同trace_id、正确parent链/request_id/run_id、业务Run complete，JSONL无敏感marker。该验收不是仅内存重建carrier或假调用，不使用模型密钥/真实供应商/生产数据。SQL直接写坏shape/类型/零/大写编号拒绝，重复请求不改首次关联。前端38项与构建通过；编译、依赖、相关文档链接和diff检查通过。本批未新增浏览器验收，不绕过先前导航限制。收尾只检查本批隔离PG中的cs_test_/cs_browser_/cs_backup_verify_数据库，结果为空；随后精确停止本批临时PG cluster，未触及实际8000服务及其他数据库服务。临时测试运行时和证据文件保留，未做广泛删除。

本批仅聊天三个Span类型；model attempt/tool/RAG/database/object storage、知识Worker、生产Worker CLI/OTLP/Collector/Tempo/receiver、采样/留存/访问角色、费用账本/预算和完整S0–S7仍缺。0019无自动迁移/启动Worker，旧head不匹配运行时会拒绝；downgrade禁用，需受控发布和schema匹配恢复制品，不能当零停机混合版本或生产上线证明。目标保持active。

## 2026-10-08 S2 后续：有界异步指标导出

新增app/metrics_export.py、tests/test_metrics_export.py；私有MetricsApplication使用懒建单线程/单未结束future，不在客服事件循环直接format，不排队无界采集。忙请求立即失败，超时或调用者取消保留真实future占位，只在完成后接受新任务，不重用旧快照。MetricsPolicy显式有限timeout/UTF-8输出大小；内置formatter逐行deadline/Event/字节预算，HttpTelemetry有界系列和拒绝样本Counter，导出结果固定七种outcome。关闭停止新工作、协作取消/有限等待及executor shutdown，不把Python线程超时当作可强制杀死。不合作插件不属于受支持renderer。

初步export/private/HTTP专项 **26 passed，12.33秒**；补充格式化故障恢复/1024序列与1200样本上限及API回归 **60 passed，19.68秒**。之后增强大快照实际线程ID验证，最终当前源完整回归 **303 passed / 45 skipped，359.19秒**，仍跳过 **41 S3 + 4 Redis**，不视为验收通过。真实PG、Prometheus/auth up/Counter查询及promtool HTTP/配置格式回归通过；不把模拟故障代替外部服务验收。前端 **38 passed，1.83秒 + build成功**，pip check/编译/相关文档链接/diff检查通过。

新增10项测试覆盖30并发拒绝、20次timeout/cancel后不重复提交、真实future完成后新快照、协作关闭、不完整/超限无成功body、UTF-8准确限制、deadline、series cap保留旧序列/176样本拒绝、安全故障恢复与高基数合成输出线程ID。没有修改页面/业务SSE帧，无新浏览器验收、模型/真实业务调用、生产数据或历史密钥访问。本批仅确切自建PG（显式::1监听）和隔离Prometheus，既有8000服务未触及。

仍未完成完整OTel/cross-Worker/Run/attempt、持久平台/receiver/通知、生产容量/预算、token轮换、费用账本和完整S0–S7；有界导出是实现基础，不是生产上线或整体目标完成证明，目标保持active。

收尾证据：自建PG中cs_test_/cs_browser_/cs_backup_verify_数据库清单为空；确切cluster正常pg_ctl停止并复查no server running。实际Prometheus子进程测试finally确认terminal，私有监听关闭/端口释放与formatter协作关闭回归通过；pytest进程完整退出。只保留隔离测试运行时/临时配置/TSDB，没有广泛删除目录、触及外部IPv4占用进程或公网发布。

## 2026-10-07—08 S2 后续：独立认证监听与真实 Prometheus 采集

新增app/private_metrics.py、tests/test_private_metrics.py与ops/prometheus/private-http.example.yml。MetricsPolicy显式专用bytes凭据/固定或测试动态端口，repr不含凭据；create_app默认关闭，lifespan管理独立127.0.0.1监听和内部端口状态，无公开业务metrics路由。直接loopback peer/单一正确Host/单一专用Bearer恒定时间比较；拒绝错误/重复认证、query、Origin/Referer/Sec-Fetch-Site、远端peer冒充和WebSocket。代理头不改变peer/Host，access log关闭；失败只固定文本、不返回原始异常/部分指标；scrape不计入业务Counter。

真实回环HTTP、认证/失败、应用lifespan/public隔离、bind冲突/退出后重新绑定、promtool实际HTTP解析和示例语法已测试。独立真实Prometheus3.5.0进程实际抓取并查询：批准凭据up=1，错误凭据up=0，业务Counter=1，指标无合成request_id/凭据；确切子进程在finally终止并确认terminal。测试仅合成凭据/临时TSDB/回环绑定，未启用管理或远程接口，不读取历史凭据、不修改实际客服8000服务。

初次复验揭示Windows短sleep/低精度计时导致旧SSE测试抖动；改HTTP耗时为perf_counter，测试按实际经过时间推进。HTTP/private/API专项 **49 passed，18.53秒**（当时新增9项私有采集），前端 **38 passed，2.78秒 + build成功**；pip check/编译/diff检查通过。后续代码复核发现Uvicorn普通Config会清空全局access handler/修改logger级别，改独立Config禁用全局日志配置、私有H11禁用访问/原始协议日志、ws=none；追加父服务logger不变/敏感路径不输出测试后 **18 passed，12.28秒**（当前新增10项私有采集）。修复期间先前已启动的全量 **292 passed / 45 skipped，337.15秒**，不能据此覆盖最后日志隔离修复；因此再次运行最终当前源全量：**293 passed / 45 skipped，333.20秒**，真实PG/Prometheus/promtool通过，仍跳过 **41 S3 + 4 Redis**，不得视为外部验收通过。

首次完整回归使用原IPv4测试地址时出现fixture连接错误；确切cluster启动日志显示127.0.0.1绑定Permission denied、只有::1成功。确认IPv4端口被其他进程占用，未停止/修改该外部进程；自建cluster的::1连接SELECT 1通过。仅停止已核对路径/命令/启动时间的本轮pytest PID29392，不重启正在正常工作的cluster；改用fixture与backup明确允许的IPv6回环同一端口重新执行完整回归。不把此首次被中止回归计作通过，不将观察超时当作终结或据此重放业务。

尚无持久平台/receiver/实际通知、OTel/Worker/Run/attempt链路、费用账本/原子预算、远程TLS/mTLS、受控token轮换、多实例部署或导出规模/有界异步CPU预算验收。当前快照同步格式化需后续改善，单次真实采集成功不等于完整S2，目标保持active、不能上线。操作边界见 [私有采集](observability.md)。

清理证据：通过自建cluster的::1查询cs_test_/cs_browser_/cs_backup_verify_库清单为空；随后正常pg_ctl停止并复查no server running。每个实际Prometheus子进程在测试finally终止并验证poll为terminal，监听退出测试包括同一端口可重新绑定；合成TSDB/配置/运行时留在测试临时目录，不做广泛删除。没有S3创建、模型/业务调用、生产数据访问或历史密钥读取，未改动外部IPv4端口占用进程。最终相关文档链接、compile/pip check与diff检查通过，README和接口契约已同步。

## 2026-10-07 S2 增量：安全 HTTP 日志与低基数指标

新增 app/http_observability.py、tests/test_http_observability.py；create_app 注入 HttpTelemetry，最外层 ASGI 包装在错误处理中间件之外，覆盖应用 500 和鉴权/维护/本机限制提前响应。服务器 UUID 取代调用方 X-Request-ID，写入请求状态/ContextVar/响应，结束与异常恢复上下文。不读取正文、头、查询或原始异常；日志只白名单字段，日志 sink 显式启用，故障计数而不递归泄漏。固定方法/状态类/outcome、已注册 route 模板和统一未知 route，无 request_id 指标标签。HTTP/SSE 分开累计直方图、终结观测 Counter 与 sink-failure Counter；提供 LF Prometheus 文本，不增加公开 metrics 端点。

初始 HTTP/API 专项 **39 passed，11.26秒**；增加真实 promtool 文本验证后的 HTTP/API/鉴权/聊天专项 **41 passed / 4 skipped，11.36秒**，此时 PG 未启动，4项不得视为验收通过。随后启动确切自建PG并执行当前源全量，最终 **283 passed / 45 skipped，367.76秒**：仍跳过 **41 S3 + 4 Redis**，新增8项均通过，真实PG与promtool回归通过，不证明缺服务的S3/Redis。前端 **38 passed，1.85秒 + build成功**；pip check、编译和 diff检查通过。

新增8项覆盖注入/敏感标记、未知路径/方法与低基数、服务端编号/500响应、SSE等待时长和完整帧、sink故障、断连/取消/不完整响应/发送失败、并发上下文及真实promtool。均用隔离合成输入，不做模型或真实业务调用。本批无新浏览器验收；先前隔离浏览器导航受限，不绕过。没有触及实际8000服务。

仍未接通私有抓取/平台/通知、OTel/Agent/model/tool/RAG链路、Run/attempt关联、日志落盘/保留/访问权限、代理/服务器/其他模块脱敏、费用账本及原子预算。sink必须由部署配置为有界非阻塞输出；异常计数不包含未配置logger或适配器静默丢弃，不能证明投递成功。指标进程内重启重置，不代替审计/账本。S2与整个生产化目标保持未完成，见 [观测边界](observability.md)。

收尾查询确切自建PG内cs_test_/cs_browser_/cs_backup_verify_库清单为空；随后pg_ctl正常停止并复查no server running。没有S3资源创建或生产数据/历史密钥读取，未做公网发布。隔离PG/promtool运行时保留，无广泛目录删除。新增HTTP文档/API契约/README已同步，未覆盖其他会话的既有修改。

## 2026-10-07 0018 后续：批准清单内恢复点选择

新增app/backup_recovery_points.py及scripts/backup_recovery_points.py。独立批准manifest原始字节SHA256、最多100项/64KiB、逐anchor绝对路径和字节摘要；全部固定版本目录记录认证/计划/策略匹配后，按带时区cutoff、60秒至30天max-gap、可选minimum及默认实际restore证据筛选最新eligible。显式allow-unrestored只豁免先前恢复验证，不冒充restore已通过；cutoff不可在当前UTC未来，生产时钟同步仍需平台验收。

任一列出记录不可读/坏摘要/错密钥/错策略/未来时间均整次失败，不隐瞒缺项后静默fallback。重复路径/record/attempt（分叉）及最新点时间并列拒绝，不按UUID猜。返回complete_catalog_proven=false，仅证明批准集合内选择，不证明集合外目录完整性/全局最新。清单和摘要的独立可靠来源、不可回滚批准记录、WORM/全目录维护仍未实现。

目录读取提取read_checkpoint、4097字节有界anchor读取和可选expected_anchor_sha256；fetch在选择后再核对摘要，拒绝文件替换，认证后才新目录排他发布/READY最后，不连接源DB、不启用API/Worker或重放队列。目录report进一步检查count/有限duration/完整restore字段，坏字段不被宽松当恢复成功。CLI需独立凭据与明确隔离选择确认，默认仅选择，destination明确提供才取回checkpoint。

初始恢复点/目录/角色专项 **25 passed，1.00秒**；清单纳入逐anchor摘要后真实PG本地调度/新鲜度与选择/目录专项 **36 passed，41.72秒**，选择/目录纯专项 **14 passed，0.86秒**；增加实际CLI选择→摘要绑定取回→关闭后 **15 passed，0.79秒**。最终当前源全量 **275 passed / 45 skipped，366.22秒**，仍跳过 **41 S3 + 4 Redis**。本批8项新增通过均为合成AES/内存存储/CLI边界，不证明真实S3；现有真实PG/S3目录取回→包fetch→PG/对象restore测试扩展选择默认拒绝无restore记录/显式豁免/摘要绑定路径，但本批仍跳过，不宣称已运行。真实PG本地恢复/维护及真实promtool回归继续通过，范围不等于整个DR。

新增测试覆盖不让更新未restore记录掩盖较旧已restore点、cutoff/gap/minimum、不可读最新点整次失败、重复/分叉/并列、独立manifest与逐anchor摘要/选择后替换、默认不豁免restore、CLI读凭据前授权及实际内存CLI选择取回。无清单自动分发、没有源DB丢失完整灾难或物理RPO/RTO演练。

前端 **38 passed + build成功**，pip check/编译/CLI help/文档链接/diff检查通过，无页面改动或新浏览器验收。没有S3实际操作、模型/业务调用、生产数据/历史密钥访问或绕过此前工具拒绝；实际8000服务未触及，只运行自建loopback PG及已有promtool。操作见 [批准清单与恢复点选择](backup-catalog-recovery.md)。

目标保持active，不能上线。独立清单/anchor可靠分发、全目录及抗回滚、实际S3/Redis、GC/IAM/KMS/PITR/HA/监控平台/receiver与完整S0–S7仍未验收；集合内筛选不缩小这些要求。

清理证据：cs_test_/cs_browser_/cs_backup_verify_数据库剩余0，确切自建cluster正常pg_ctl停止并复查no server running。无本批真实S3资源，PG/promtool运行时与日志保留，未广泛删除目录；最终diff检查通过。

## 2026-10-07 0018 后续：加密目录检查点与灾难回执引导

新增app/backup_catalog_archive.py：64KiB有界AES-GCM目录记录、namespace/record UUID AAD、计划/策略/attempt/owner/fence/制品报告/receipt绑定、报告白名单拒绝任意敏感字段。发布前按receipt确切版本解密archive index，必须同archive_id/backup_id/snapshot，目录上传后固定版本读回认证比对，再hard-link排他发布anchor。不覆盖、不盲删、不自动重试未知写入。检查点先于源catalog终态，不能据此声称源任务已complete或外部操作已完成。

BackupPlan增加catalog_policy，必须有archive且独立目标；Runner/Worker显式catalog角色与指纹绑定，成功记录catalog_verified/record_id/reference。字段扩展使旧0018指纹不匹配，需要明确审阅重新批准，不修改旧数据库hash；没有新schema，head仍0018_backup_schedule。目录阶段在可选恢复验证后、源catalog.finish前检查任务guard。

新增scripts/backup_catalog.py，明确隔离fetch/独立凭据/anchor/namespace/计划UUID/策略/密钥，读取catalog固定VersionId并认证，私有新目录排他发布record.json/receipt.json，READY最后。没有源DB URL、不查询源库、不启用服务或重放队列；认证失败/旧目标拒绝，部分发布保留待审。可用取回receipt调用既有archive fetch，再新PG/业务bucket restore保持hold。**必须独立保存anchor/key/policy**，只在package_root保存anchor不构成灾备；合法旧anchor不证明最新恢复点，尚无全目录自动发现/抗回滚或独立anchor分发。

初始新加密/旧角色边界 **25 passed，0.49秒**；新增目标不可同archive/遗漏及CLI授权边界后 **18 passed，0.58秒**（目录7项+角色11项专项）。真实PG本地调度与目录/角色专项 **29 passed / 4 S3 skipped，35.22秒**。最终当前源全量 **267 passed / 45 skipped，359.48秒**：**41 S3 + 4 Redis**仍未完成本批实际服务验收。本批8项新增通过均为密码学/参数/内存故障注入边界；新增真实PG/S3目录发布→丢失本地receipt/包→固定版本取回→包fetch→PG/对象restore/hold测试已添加但跳过，不以模拟S3证明实际版本/权限或完整DR。内存专项验证远程无明文ID、丢receipt后取回、错key/namespace/record/policy拒绝、未知写ack/失租保留无anchor、receipt恢复点不符拒绝、未知敏感report字段和未授权CLI在client前拒绝。

前端 **38 passed + build成功**，pip check/编译/CLI help/文档链接/diff检查通过；无页面变化或新浏览器验收。没有重启被拒绝过的S3、绕过限制、真实对象/模型/业务调用或生产数据/历史密钥访问；实际localhost:8000未触及。仅自建loopback PG和已有promtool进行隔离回归。操作见 [目录与回执灾难引导](backup-catalog-recovery.md)。

仍缺实际S3/Redis联合验收、独立anchor/清单可靠分发、全目录/最后有效点、WORM/KMS/IAM/区域故障保护、GC/保留、实时包探测、监控平台/receiver、PITR/HA/完整RPO/RTO和整体S0–S7。该增量是目录灾备入口，不代表目录灾备及整个目标全部完成；目标active，不能上线。

清理证据：cs_test_/cs_browser_/cs_backup_verify_数据库剩余0；确切自建cluster正常pg_ctl停止并复查no server running。无本批真实S3资源，PG/promtool运行时与日志保留，未广泛删除目录；最终diff检查通过。

## 2026-10-07 0018 后续：每attempt自动对象恢复目标

新增ObjectRestoreTargets：显式隔离确认、批准endpoint/region/4–30字符安全创建前缀、每attempt独立UUID bucket，借用调用方client。HEAD明确不存在才创建，已存在或403/5xx/未知状态失败关闭；新建后开启版本、生产目标设置四项PublicAccessBlock、私有/空桶验证，网络阶段前后复查guard。非默认region明确LocationConstraint。失租、创建ack未知或私有设置失败不删除/接管/复用资源；没有源桶写/删除操作。UUID目录与HEAD预检查不是AWS us-east-1 bucket创建的原子排他证明，正式IAM/账户/跨进程对抗验收仍缺。

LocalRestoreVerifier策略指纹绑定factory目标，换目标/省略factory不匹配批准plan。领取后的实际验证将新空bucket传入既有restore_backup，消费加密对象版本/摘要并原子重映射/rebind、保持hold，不启动服务。成功报告包含restore_bucket，成功只删除自己的确切验证DB，桶/版本/文件保留等待授权GC；失败由批准前缀+attempt精确定位，保留待审。调用方最终统一关闭client，factory副本不重复关闭。没有schema迁移，head仍0018_backup_schedule。

Worker增加verification-object-target与confirm-isolated-object-verification成对显式参数，完整PG/root/隔离确认和非空restore_policy在读凭据前要求。operator_stores有界envelope支持verification独立角色，不发现环境/历史凭据。verification target的bucket是保留创建前缀，配置/构造阶段不把它当已有bucket check_ready、不创建桶；实际创建仍在领取及guard后。源/归档原有角色和目标绑定不变。

新factory/旧CLI离线边界 **18 passed，0.29秒**；补齐verification CLI供应/关闭及“不在setup创建桶”后 **19 passed，0.43秒**。真实PG本地调度/新鲜度回归与边界专项 **40 passed / 3 S3 skipped，46.72秒**。最终当前源全量 **259 passed / 44 skipped，362.78秒**：仍跳过 **40 S3 + 4 Redis**。本批10项新增通过均为隔离配置/故障注入边界；新增真实PG/S3双周期实际恢复测试已添加但**跳过**，覆盖计划源对象→独立两桶实际恢复/引用与内容校验→hold/计划禁用→成功保留桶→源对象不变，不能宣称其联动已通过。既有真实PG本地恢复/策略/维护与实际promtool回归仍通过，不代替对象服务语义。

前端 **38 passed + build成功**，pip check/编译/Worker help/文档链接/diff检查通过，无页面变更或新浏览器验收。没有启动被拒绝过的S3服务或绕过策略，没有真实对象操作/模型/业务调用、生产数据或历史密钥访问；localhost:8000未触及。仅启动自建loopback PG合成集群。操作见 [自动对象恢复](backup-scheduler-operations.md)。

目标仍active。尚缺实际S3/Redis联动、catalog/receipt独立灾备、GC/保留、正式隔离账户/IAM/安全供应、监控平台/receiver、PITR/HA/RPO/RTO及整体S0–S7，不据此上线。此前“无对象恢复入口”为历史状态；当前是入口已接通、真实联动与生产灾备未验收。

清理证据：cs_test_/cs_browser_/cs_backup_verify_数据库剩余0，确切自建cluster正常pg_ctl停止并复查no server running。没有本批真实S3资源，保留PG/promtool运行时与日志，不广泛删除目录；最终diff检查通过。

## 2026-10-07 0018 后续：备份新鲜度、指标与告警求值

新增app/backup_health.py：固定批准schedule/策略指纹、数据库时钟、单REPEATABLE READ只读快照、5秒SQL超时、有界最近100条complete目录证据。分别按源snapshot_at计算local/remote/restore年龄，不把finished_at当恢复点。默认要求local+实际restore，明确require_remote添加远程、显式local-only才豁免restore。缺计划/禁用/错策略、待审/过期任务、坏或未来/无时区报告、恢复hold不能宣称健康；维护hold单列，监控不修复、续租、重试或解除任何hold。没有包/密钥/网络实时可用性证明，目录历史记录不等于可恢复性或完整RPO/RTO。

新增scripts/backup_health.py，显式loopback数据库与全局读授权、JSON/固定低基数Prometheus输出；0健康、2采集成功但保护不健康、1采集失败。失败只输出collection_success=0，不复制旧成功指标。UTF-8/LF字节输出修复Windows文本管道CRLF。可选专用customer-service-backup.prom同卷临时文件/fsync/原子replace，成功/不健康/授权后采集失败都更新，发布失败保留旧文件并清理自己的临时文件，旧成功文件冻结由采集时间告警覆盖。未安装textfile exporter/定时平台任务，不自动启用。

新增ops/prometheus/backup-alerts.yml及真实promtool fixtures，覆盖采集缺失/失败/超过300秒停更、scrape down（不会被其他健康目标掩盖）、所需恢复点陈旧、失败/失租待审及保护不健康。Prometheus job明确customer-service-backup，owner/环境/通知接收器需平台配置，本批没有发送通知。

初始真实PG新鲜度 **8 passed，13.05秒**；官方promtool规则解析/求值 **2 passed，0.14秒**。首次指标文本专项 **10 passed / 1 failed，12.65秒**，真实解析报告gauge含CRLF，随后修复CLI字节输出和测试输入后 **4 passed，0.43秒**；增加实际CLI管道后 **12 passed，15.69秒**；增加原子发布/失败保留后 **14 passed，17.75秒**。早先已启动的旧源全量保持到终态：**245 passed / 1 failed / 43 skipped，361.99秒**，唯一失败为同一CRLF问题，不将其隐藏或宣称通过。最终冻结修复源重新全量 **249 passed / 43 skipped，354.18秒**，包含本批14项（6项真实PG、4项纯边界/发布、4项真实promtool），仍跳过 **39 S3 + 4 Redis**。前端 **38 passed + build成功**，pip check/编译/CLI help/文档链接/diff检查通过，无页面改动或新浏览器验收。

promtool为官方GitHub Prometheus v3.5.0 windows-amd64 zip，按同发布sha256sums核对 **B3C2607D1E80A277735FD3CDE86432EB1B6843897CB82FB04FBF4C7FFB1D1C36**；版本输出确认3.5.0/revision 8be3a9560fbdd18a94dedec4b747c35178177202。只运行promtool，不启动Prometheus公网或本地服务。发布方同源摘要不代替独立签名或生产供应链认证。工具保留在临时运行时；TEST_PROMTOOL_PATH未明确供应时测试跳过，不能以无工具测试算PromQL通过。

真实PG证据包括实际local备份不冒充remote/restore、实际隔离恢复后健康、禁用与recovery hold、错策略、源恢复点陈旧/报告损坏故障、失租仍原running且只能显式reaper待审、直接CLI查询真实目录。实际promtool解析成功/失败指标与真实CLI失败管道，6个时序场景含告警触发/恢复；这不证明Alertmanager路由/真实receiver/通知送达。尚缺生产监控平台、实时包探测、自动对象恢复、catalog/receipt灾备、GC/PITR/HA/权限/KMS和完整S0–S7，目标active，不能上线。操作见 [备份监控](backup-scheduler-operations.md)。

清理证据：cs_test_/cs_browser_/cs_backup_verify_数据库均剩余0；确切自建cluster经pg_ctl正常停止，复查no server running。保留PG及promtool运行时/日志，不删除宽泛目录，不影响实际8000服务；最终diff检查通过。

## 2026-10-07 0018 后续：scheduler对象源与远程归档凭据供应

新增S3ObjectStore非秘密目标指纹，绑定实现、endpoint/bucket/region，不绑定凭据正文，凭据轮换不改目标。BackupPlan增加object_policy/archive_policy及成对规范UUID archive_namespace，Runner在领取前拒绝遗漏、替换或擅自增加store/namespace。策略字段扩展使旧0018 JSON指纹不匹配，默认失败关闭；需明确审阅重新批准/迁移，不自动改数据库hash。没有新增schema迁移，head仍0018_backup_schedule。

新增operator_stores有界32KiB角色envelope，source/archive角色必须与明确批准targets完全一致，先校验全部凭据再构造client，部分构造失败关闭已经拥有的client。CLI增加storage-policy/source-target/archive-target/storage-credentials-stdin，目标比对后check_ready、再连接schema/装配Runner，退出关闭全部client。没有历史AWS/模型/租户密文回退，密钥不在计划/命令参数/日志。单source bucket，不跳过其他bucket，不提供生产HTTP豁免。当前本地恢复验证器仍不支持对象恢复目标。

初始边界专项出现2项参数化fixture setup错误，改短参数ID后 **28 passed / 10 skipped，0.45秒**（包含9项新离线边界及旧S3/归档测试，真实服务未运行）；真实PG调度/维护/备份与新边界专项 **46 passed，64.22秒**。最终当前源全量 **235 passed / 43 skipped，339.07秒**：**39项S3（37旧+2新）+4项Redis**均未完成本批真实服务验收。新两个真实PG/S3测试覆盖调度对象→远程receipt→fetch→新PG/对象恢复及CLI成对供应/归档，但本批只添加、跳过，**不得宣称联动已通过**。本批9项实际通过的新增测试证明角色/大小/凭据轮换指纹、目标/namespace不可降级、部分构造关闭、CLI失败清理/脱敏及缺角色在凭据读取前拒绝；故障注入client不证明真实S3语义。

前端 **38 passed + build成功**，pip check、Python编译、Worker help、文档链接及diff检查通过。无页面变更或新浏览器证据；未重试之前被策略拒绝的S3启动，未绕过客户端限制；未读取真实数据/环境历史密钥、调用在线模型/业务或触及localhost:8000。实际PG专项仅使用自建loopback合成集群。

此增量更新前述“scheduler CLI无对象/远程供应”的历史状态。仍未完成：真实S3/Redis复验、自动对象恢复、catalog/receipt独立灾备、多source供应、freshness/告警、保留/隐私GC、生产IAM/TLS/最小权限/KMS/PITR/HA及整体S0–S7。目标保持active，不能上线。见 [自动备份操作](backup-scheduler-operations.md)。

清理证据：自建PG中cs_test_/cs_browser_/cs_backup_verify_均剩余0；随后确切cluster经pg_ctl正常停止，复查no server running。没有广泛删除目录或对象资源，运行时/日志保留；最终diff检查通过。

## 2026-10-07 0018 自动备份调度与本地实际恢复验证批次

新增默认空 backup_schedules/backup_jobs、不可变策略指纹、唯一时间槽/全局单running、owner/fence/数据库期限、有界过期待审清理。自动续租、维护请求/有界排空等待、真实封存pg_dump/文件备份、全包认证、源维护解除、可选函数级归档及本地实际恢复验证已接通。需要用户显式配置，不在迁移/API启动时启用。未知/失败任务不回放，needs_review阻止新任务/配置；失败不自动解除仍持有的维护或删除包/对象。

恢复验证目标（实现版本、去密码URL、规范化私有根）指纹纳入批准计划。配置和执行都检查验证器，遗漏/改根/改目标/擅自增加被拒绝；删计划中的验证要求被持久计划指纹拒绝。完成报告要求有界且具备本地认证/备份ID/时区时间/计数/耗时，远程和恢复字段成套，不能只传 local_verified 宣告成功。complete仅表示批准策略要求的步骤完成；没有restore_policy的计划不代表实际恢复验收。

LocalRestoreVerifier实际创建确切隔离数据库、pg_restore/文件和业务校验、确认recovery hold/Workers禁用；成功仅删除自己的确切数据库，失败或未知结果保留待审。明文文件仍保留等待批准GC。恢复先验证原始catalog/业务摘要，再同事务禁用恢复计划、标记源running备份任务needs_review、清除旧维护owner并保留独立recovery hold，不启动服务。

初始8项真实PG专项 **8 passed，20.68秒**；目标绑定后 **9 passed，21.30秒**；重复周期及报告校验后 **11 passed，23.93秒**。最终当前源全量 **226 passed / 41 skipped，327.04秒**，包含本批 **12项真实隔离PG调度测试**：并发唯一领取、实际备份/恢复禁用计划、真实双租约续期、注入排空超时/hold保留、过期待审、策略不符、独立Worker进程、实际自动恢复及确切成功清理、验证目标不可降级、两个周期/两个包、拒绝不完整成功报告、实际恢复后注入校验失败保留数据库/文件并阻止重放。最后失败案例确认源服务已解除维护且隔离目标仍hold；只删除本测试的确切UUID数据库。超时测试注入busy，不冒充真实长运行排空验收。

仍跳过 **37项S3 + 4项Redis**，未重试此前被工具策略拒绝的S3启动，不以0016历史S3证据代替0018联动。前端 **38 passed + build成功**，pip check、Worker help、git diff --check和本批文档链接核对通过。本批无页面变更/无新浏览器验收，前次客户端拦截仍是验收缺口；真实TCP不冒充浏览器证据。未调用真实模型/业务，未访问真实数据/旧密钥，实际localhost:8000未触及。

CLI目前只自动本地文件/包，无对象/远程凭据供应或对象恢复验证目标。生产最小权限（控制表必须与普通API角色隔离）、catalog/receipt独立灾备、freshness/告警、保留/隐私GC、Object Lock/KMS、在线PITR/HA/规模/RPO/RTO、观测费用/真实工具/模型降级/向量RAG及整体S0–S7仍未完成。目标保持active，不标S1/S7完成，不能上线。操作见 [自动备份](backup-scheduler-operations.md)。

清理证据：查询当前自建PG确认cs_test_/cs_browser_/cs_backup_verify_数据库均剩余0；随后对确切临时cluster执行pg_ctl正常停止并复查no server running。保留运行时/日志，不删除宽泛目录，不影响实际客服服务。最终Python编译和diff检查通过。

## 2026-10-07 0017 全局维护与备份封存批次

新增0017_maintenance_gate不可变迁移/helper，默认空maintenance_state、owner/fence/DB时钟期限；30业务表statement写保护、共享事务锁与request排他提交屏障，READ COMMITTED以外写拒绝。require_operational/HTTP503 maintenance_hold/no-store与Worker启动/领取已接通。期限过期不自动开放，接管须显式且旧期限已过；旧owner不可renew/release。sealed单排他try事务锁/SHARE业务表锁、拒绝有效running或无租约执行，guard复查owner/期限。控制表可续租，DDL/nextval/superuser/未迁移新表及正式应用权限尚未纳入验收，不是完整数据库防御或在线备份。

可信scripts/maintenance.py request/renew/release需明确数据库与全局确认；逻辑备份CLI明确owner/fence进入封存，无隐式持久hold解除。pg_dump后、文件处理前后和READY前复查guard；失租没有完成包。恢复原始业务摘要（包括maintenance_state）先完整验证，再在引用映射/rebind同事务清除旧维护ownership/期限，独立recovery hold保持，失败回滚此清除。没有生产/租户恢复启用HTTP入口。

迁移/逻辑备份/恢复初始专项 **33 passed，32.93秒**；首批维护 **6 passed，16.75秒**；恢复变换调整后维护/逻辑备份 **23 passed，32.09秒**；追加显式permit CLI与途中失租、PG集成合计 **28 passed，56.64秒**（8项新维护均真实隔离PG）。最终当前源全量 **214 passed / 41 skipped，285.34秒**：**37项S3**因本次工具策略拒绝启动隔离服务而未运行，**4项Redis**仍缺可用环境。不得用前一批243/37真实S3证据宣称新0017对象联动已验收。前端 **38 passed + build成功**、pip check/CLI help/git diff --check通过，无新前端页面代码。

本次浏览器访问隔离8019/api/auth/config被客户端拦截，未绕过、未重选无关浏览器，**没有浏览器通过证据**。保留实际TCP检查：独立合成PG harness正常认证config→测试专用维护注入→同一路径503 maintenance_hold→独立专用shutdown正常退出并删除自己的DB。该harness控制只存在隔离脚本，生产无HTTP维护入口；真实TCP/TestClient检查不替代页面验收。新Worker独立进程在hold下拒绝启动、不读取/生成主密钥。

本批清理：隔离harness正常退出，查询cs_test_/cs_browser_数据库剩余0，确切自建PG集群正常停止并复查no server running。S3启动请求被策略拒绝，未绕过/改用其他方式启动，37项对象联动留待复验；没有新对象测试资源。未触及现有8000服务、真实数据/历史密钥、在线模型或业务系统，临时运行时/日志保留。

仍未完成：自动续租/排空等待/残留进程回收、唯一调度键/scheduler/catalog、自动恢复/freshness/告警、对象联动复验/浏览器、统一SQL竞态错误/前端维护展示、正式角色/TLS/IAM/KMS/规模/HA/PITR以及完整S0–S7。维护门控基础不等于自动备份交付，目标active、不能上线。详见 [维护门控](maintenance-operations.md)。

## 2026-10-07 远程加密备份包与 CLI 批次（head 仍为 0016）

新增 app/backup_storage.py 的批准 policy/目标及显式有界 stdin 独立运维凭据供应；逻辑备份/恢复 CLI 可消费对象 store，先确认维护/隔离再读凭据，finally 关闭，不发现 AWS/模型/应用历史密钥。参数部分供应失败关闭，输出脱敏；当前一个源桶，不跳过其他桶。此为受信全库运维入口，不赋予租户 HTTP 管理员全库备份权，生产 IAM/安全管道平台供应未完成。

新增 app/backup_archive.py、scripts/backup_archive.py。上传前 manifest/dump/全部文件 GCM/尺寸/摘要及排他 inventory 校验，保持逻辑密文；4MiB 分块、最多8192块/32GiB密文字节、索引16MiB，确切版本读回。源包哈希从认证绑定到上传/索引发布，源变化没有回执，孤儿版本留待对账。索引 AES-GCM AAD 绑定 archive_id，receipt 绑定namespace与确切索引引用，完整同卷 hard-link 排他发布，不覆盖旧文件。取回先检查索引/文件名/唯一块/版本/桶/前缀/总预算，在私有临时根重建/全包认证，核对 backup_id/snapshot_at 后才排他发布新包，READY最后，不启用服务或解除hold。

首轮新专项 **1 passed / 5 failed，11.37 秒**：统一原因是 Windows READY 标记为 CRLF，校验只接受 LF；按既有文本协议兼容换行后 **6 passed，14.46 秒**。追加CLI供应/关闭和部分配置拒绝后，新/旧备份专项 **32 passed，53.45 秒**；再追加源包变化故障。最终全量 **243 passed / 4 Redis skipped，337.77 秒**；新增 **10项**（**7项同时真实PG/S3**，3项凭据/CLI离线边界），累计真实S3 **37项**，PG/S3重叠不相加。前端 **38 passed + build成功**，pip check无冲突，CLI help/编译、git diff --check通过。本批未修改页面，浏览器证据沿用上一批，不虚构新交互验收。

真实链路包括归档→覆盖remote latest→移走原包→按旧确切版本取回→新空PG/不同新空业务桶恢复，业务摘要、原文/图片字节与hold验证通过。故障覆盖未引用/腐败本地blob在上传前拒绝、删确切块后不读latest/不发布包、丢索引上传回执保留6个版本无receipt、AAD receipt移植、拒绝覆盖已有包/receipt、预算拒绝在上传前、上传期间源包变化无成功receipt。逻辑CLI真实对象供应使用专用可信loopback policy DI，不提供生产豁免；不是TLS/IAM验收。

本批清理：各fixture删除其确切版本/marker/桶/数据库，查询自建服务确认剩余测试桶和cs_test_/cs_browser_数据库均为0。核对自建SeaweedFS PID3156的可执行文件/命令行后停止；确切PG集群正常停止并复查no server running。仅合成内容，未读取真实数据/环境历史密钥或调用在线模型/业务，不触及现有localhost:8000。自建运行时和日志保留。

仍未完成：自动停写/排空门控、scheduler/唯一租约、catalog/receipt灾备、自动隔离恢复验证/freshness、保留/GC/Object Lock、KMS/生产角色/TLS/IAM/规模/异地/WAL/PITR，以及观测费用/业务工具/模型降级/向量RAG和完整S0–S7。默认服务未切流，整体目标active，不能上线。操作与状态范围见 [远程备份](remote-backup-operations.md)。

## 2026-10-07 0016 对象存储管理供应批次

新增不可变 0016_storage_configs 迁移、tenant 主键/FK、正 revision、active/recovery_rebind 状态及加密配置；旧导入/幂等重放要求表为空。部署批准目标白名单由绝对非敏感 JSON 提供，管理员只能选名称，不能提交任意 endpoint。生产 policy 强制 TLS，测试 loopback 豁免不是 UI/CLI 参数。GET/PUT config.manage、实时撤销/Origin/CSRF 与脱敏错误；保存前/网络后鉴权，AES-GCM 绑定租户/目标指纹/revision，配置与审计同事务，并发同 revision 仅一胜出，active 不许直接切桶。

请求级 KnowledgeService、聊天/知识 Worker 读取原 actor 租户配置，缺 policy/变更绑定/主密钥/rebind 失败关闭；SDK 在消费结束关闭。Worker CLI 成对 storage-policy/storage-master-key-path 只传批准文件和包装密钥路径，不传服务凭据。无配置行不触碰主密钥或发现环境历史凭据。真实独立解析进程在原输入删除/另一根目录，仅凭持久加密配置与临时 wrapping key 完成任务，不靠嵌入服务密钥。

恢复在原业务摘要验证后，引用映射同一事务标记所有配置 recovery_rebind，报告计数，hold 保留。resolve 在解密前拒绝旧配置；授权恢复对账/解除保护后，显式新凭据与本租户恢复引用桶一致才能重绑。专项错误绑定回源桶被拒绝、正确恢复桶可读。测试维护 SQL 仅解除自建合成库 hold，不提供生产解除方案；备份 CLI 供应和受控启用工具未完成。

浏览器：独立 localhost:8019、临时 cs_browser 数据库/版本桶、合成管理员和既有合成 S3 凭据，经实际知识管理页面保存→成功提示→revision=1→刷新仍已配置。目标禁切换，三个凭据字段空、保存按钮禁用；最终构建再次刷新验证，无凭据回显。GET 等候 10 秒、PUT 45 秒；保存前清空，不自动重试，超时只表示结果未确认，必须刷新核对。SSR/角色前端单测只证明首次渲染不请求/不输出凭据和客服不显示配置，不冒充交互证据。

初始专项迁移/边界 **13 passed**；存储/输入/HTTP **18 passed，47.35 秒**；独立配置消费 **1 passed，6.85 秒**。首轮完整回归 **230 passed / 4 Redis skipped，281.09 秒**；追加目标切换/并发轮换/policy 输入及错误 rebind/HTTP 不回显断言后，最终完整回归 **233 passed / 4 Redis skipped，284.72 秒**。新增存储配置 **10 项**（**9 项真实 PG+S3**、1 项离线 policy 校验）；累计真实 S3 **30 项**，包含 PG/S3 重叠，不与 PG 项数相加。包含实际迁移/Schema drift/离线 SQL、备份恢复及 SSE 依赖生命周期回归。前端 **38 passed + build 成功**，pip check 无冲突，git diff --check 通过。所有测试合成、隔离，无真实模型/业务或历史密钥。

本批清理：临时浏览器页关闭，8019 harness 正常退出并删除自己的 bucket/version/database。查询自建服务确认 cs_test_/cs_browser_ 数据库和 cs-test-s3-/cs-browser-s3- 桶剩余均为 **0**；核对确切 SeaweedFS PID/可执行文件/命令行后停止，确切 PG 集群 pg_ctl stop 成功。运行时与日志保留，不删全局临时目录、不访问或停止实际 localhost:8000 服务。

仍未完成：backup CLI 独立配置、scheduler/远程包归档/WAL/PITR、保留/GC/隐私删除、KMS/生产角色/TLS/IAM/规模恢复、受控对账/解除 hold、真实 Redis/HA，以及观测费用/业务工具安全/模型路由/向量 RAG 和完整 S0–S7。默认本地模式未切流，整体目标 active，不能上线。

## 2026-10-07 0015 对象耐久输入批次

新增不可变 0015_knowledge_inputs 迁移和独立 schema helper。knowledge_job_items.input_object 可空 JSONB，JSON null 不充当 SQL NULL；非空形状/固定 SHA 校验。旧 0014 item 保持本地 manifest，不自动转换或重放。入队前有界读取、显式上传/读回后发布引用，入队事务复查身份/租户/引用尺寸摘要/kind/version/hold。Worker 对象输入不依赖原主机文件，下载前后 guard 原会话、租约 owner/fence/期限；原文/资产发布前也 guard。内部稳定 object-import 来源标识用于版本替换。缺 store/版本失败关闭，无本地/latest回退；未授权下载结果不解析。不支持后缀可无本地文件跳过。

备份 _references 扩展到所有 queued input 对象（包括终态仍保留引用），记录 table/job_id/position；恢复与文档/资产一起按复合主键事务映射和校验，恢复 hold 保留，不重放队列。输入元数据不在公开任务结果暴露；HTTP 202/轮询结构不变。生产 CLI/凭据供应未完成，独立进程证明来自专用 tests/fixtures 合成 loopback harness，不冒充正式配置入口。

初始专项知识输入/旧 durable Worker/对象备份 **23 passed，72.43 秒**；补充入队事务 hold 复查 **1 passed，3.19 秒**。新增 **7 项真实 PG 专项**（其中 **6 项同时使用真实 S3**），覆盖独立进程/另一根目录/原输入删除后的解析、缺版本/缺配置拒绝缓存、下载后撤销不解析、包含同一 job 两个 position 的备份恢复、0014→0015 NULL 兼容和恢复保护。基础迁移 **12 passed**；前端 **36 passed + build 成功**。首次全量 **222 passed / 1 failed / 4 Redis skipped，259.25 秒**，失败仅为新迁移拒绝 downgrade 的错误措辞与既有契约不同；统一为“downgrade is disabled”后原失败用例 **1 passed，1.16 秒**，未改变禁止降级行为。修复后完整后端 **223 passed / 4 Redis skipped，260.88 秒**，含 **111 项真实 PG / 21 项真实 S3**（18 项重叠，不相加为独立总数）；包含实际迁移、Schema 无漂移和离线 SQL 验证。pip check 无依赖冲突、git diff --check 通过。

仍未完成：生产对象安全配置/Worker CLI、自动备份/远程归档/PITR、输入及终态引用 GC/保留与隐私删除、重试/死信/主动取消、解析沙箱和全局资源预算、真实 Redis/PG HA、观测/费用/业务工具/模型路由/向量 RAG 等完整 S0–S7。默认本地模式未切流，不能上线。

本批清理：所有随机测试桶/版本和 cs_test_/cs_browser_ 数据库由各 fixture 精确删除，复查剩余数量均为 0；停止确切自建 SeaweedFS 进程与 PG 集群，再确认进程消失、pg_ctl 返回 no server running。自建服务监听均为 127.0.0.1、遥测关闭；运行时/日志保留。未停止/修改实际 localhost:8000 服务，未读取真实数据/历史密钥或调用在线模型/业务系统。

## 2026-10-07 对象逻辑备份与恢复映射批次

`create_backup(..., object_store=...)` 已消费 exported snapshot 的确切对象版本，SHA256/size/kind 与 DB 一致才加密归档；对象包 manifest format 2，纯本地 format 1 兼容。`restore_backup(..., object_store=...)` 要求不同于源桶的空私有版本桶，拒绝 delete marker 和不完整空判定。全部 GCM 文件验证后才上传新 attempt 对象，原 dump/hold 单事务恢复并验证原始业务摘要/序列/引用；后续短事务锁定引用与 hold，将全部原文/资产引用映射到新版本并核对完整引用集。本地文件才重定位 provenance 路径，报告分开计数。未知上传回执、SQL或映射失败不盲删对象、不放行执行、不自动重试旧命令。

专项第一轮逻辑备份/对象备份/恢复保护 **26 passed**；另新增真实映射验证故障测试 **1 passed**，证明整个引用事务回滚、恢复库保留 hold、对象保留待对账。全部 **6 项新对象备份用例** 使用真实隔离 PG/S3；模拟丢上传回执/映射校验失败明确为故障注入，底层存储仍真实。覆盖源 latest 被覆盖仍备份确切版本、备份后源版本删除仍恢复、腐败的后一个文件在上传/SQL前拒绝、失去源版本不读旧缓存、拒绝源桶/非空目标、上传未知回执和映射回滚。完整后端 **216 passed / 4 Redis skipped，271.95 秒**，含 **104 项真实 PG / 15 项真实 S3**（12 项重叠，不相加为独立用例）；前端 **36 passed + build 通过**，pip check / git diff --check 通过。强化“备份前覆盖 latest”的断言后对象专项 **6 passed，21.29 秒**；再追加真实仅有 delete marker 的目标桶拒绝断言 **1 passed，4.77 秒**。

限制：仅函数依赖注入可供应对象 store，CLI/管理端安全配置供应未接入；对象引用备份不等于备份包的远程私有桶归档。自动 scheduler、保留/GC、资源预算、KMS/异地/WAL/PITR/生产角色和前向对账/解除保护仍未完成。耐久导入输入仍依赖共享文件根。仍保留全部 S0–S7，不宣称可上线。详见 [备份操作](logical-backup-operations.md)。

本批清理：fixtures 删除各自生成的确切版本、delete marker、测试桶和数据库，确认 synthetic S3 桶 / cs_test_/cs_browser_ 数据库均为 0。停止确切自建 SeaweedFS PID 和 PG 集群，所有监听此前核对为 127.0.0.1；关闭遥测。未访问真实数据/密钥/模型/业务，也未停止实际 localhost:8000 服务。临时运行时和日志保留供后续隔离验收。

## 2026-10-07 私有对象存储接入批次

新增 `app/object_store.py`，固定 production boto3/botocore 依赖，显式凭据、HTTPS/证书验证、无默认凭据链/代理、有界网络超时和禁隐式重试。私有 versioning/PublicAccessBlock readiness、租户前缀/大小/摘要/确切 VersionId、上传读回验证、流关闭与错误脱敏。原文和图片引用通过现有 metadata 与文档、切块、资产及 durable item/result 事务一起发布；读取时核对引用与 publication size/hash/kind。对象模式要求已认证租户；默认 local 模式不变。

知识服务/应用工厂/Worker 依赖注入已接通解析后对象发布，跨实例重解析按文档 ID/version 替换、无本地原文/图片依赖。图片 HTTP 鉴权下载后复查权限和资产版本，返回 no-store PNG 字节；列表/详情隐去引用，缺版本/未配置对象模式不降为本地/latest。对象异常统一脱敏 503；原文备份未集成前显式拒绝对象引用，不用旧缓存。

专项：组件 **19 passed**（含 **3 项真实 S3**，其余为结构/故障注入）；应用 **6 passed**（均真实隔离 PostgreSQL + S3），覆盖跨实例无文件重解析/图片、丢版本和未配置拒绝缓存、发布失败留待对账、外租户不触发下载、逻辑备份拒绝缓存、HTTP 图片与读取途中撤销。追加对象不可用 HTTP 503 断言通过。首轮全量为 **209 passed / 1 failed / 4 Redis skipped**：失败是 killed-Worker 测试观察者用公共 read_events 持会话锁，令 --once 正确跳过任务后退出；改为非加锁 SQL 观察序号，生产领取逻辑未改。kill/忙碌跳过/全部对象专项合计 **8 passed**。修复后完整后端回归 **210 passed / 4 Redis skipped，245.77 秒**，含 **98 项真实 PG、9 项真实 S3**（其中 6 项同时使用 PG/S3，不能相加为独立用例总数）；前端 **36 passed + build 成功**；pip check 无冲突，git diff --check 通过。

MinIO 官方下载 410，未采用不明镜像；实际 S3 为官方 SeaweedFS 4.48，官方 GitHub release hash 校验为 `FE90C04C0620AD1A1C756F86CD5E1443773F56A446688077A4BB5EC04C3CC874`。该隔离 loopback 服务不实现 PublicAccessBlock，显式测试豁免才核对 canonical-user 私有 ACL；远程豁免禁止，不能替代生产 IAM/TLS/KMS/公开访问阻断验收。合成 bucket/文件/身份，不读取真实数据或密钥、无在线模型/外部业务调用。详细边界见 [对象存储操作](object-storage-operations.md)。

未完成：排队输入仍为本地固定 manifest，共享输入根不能省略；生产配置/凭据供应和 Worker CLI、迁移/GC/资源预算、对象备份及恢复引用映射/自动 scheduler/WAL/PITR/异地/KMS待接通。真实 Redis仍缺服务。S0–S7 目标保持 active，不能上线。

本批清理：测试 fixture 删除自己的确切版本与随机桶/数据库；另核对早期能力探测桶为空后删除。确认 synthetic S3 桶和 cs_test_/cs_browser_ 数据库剩余均为 0，停止确切自建 SeaweedFS 进程和 PG 集群，复查进程已消失/pg_ctl 报 no server running。临时 runtime/日志保留供后续使用；未访问或停止实际 localhost:8000 客服服务。

日期：2026-10-07。总体目标保持 [S0–S7 实施清单](production-roadmap.md) 全部范围。本文件只记录已有证据，不代表生产化完成。

## 累计实施记录（部分段落保留各批当时状态）

- 固定 SQLAlchemy 核心依赖、Alembic/Psycopg production 可选依赖版本。
- 新增不可变 schema_v1 与正式 Alembic 0001 基线，涵盖全部 14 张旧业务表，另建 migration_batches。
- PostgreSQL 专用连接池限制、UTC/语句/锁等待超时、参数隐藏；迁移 runner 事务 advisory lock，基线破坏性 downgrade 禁用。
- 隔离 SQLite 快照迁移器：只读源、Schema/完整性/FK检查、UUID/时区/JSON转换、分批插入、整批事务、逐表 count/hash、幂等批次校验、整数序列修复。
- CLI 不使用当前应用数据库、SecretStore 或模型；拒绝本项目 data 及 secrets 路径。错误输出无行内容/凭据；CLI 只输出统计与摘要。
- 新增 disposable PostgreSQL Compose 和真实数据库集成测试。测试只创建/删除自己生成的 cs_test_<uuid> 数据库；不挂载生产目录。
- 会话、消息、Run 和工具记录接入 SQLAlchemy ChatRepository；异步运行中的数据库/模型配置/Prompt读取移到线程池。最终回答与 Run 终态同事务保存，失败整体回滚，终态不可被迟到结果覆盖。
- 工具结果按具体调用记录 ID 更新，不再更新同名工具的“最后一条”。归档/删除后到达的回答不再持久化，Run 以 session_inactive 中断。
- 会话创建/状态修改/Run 完成采用统一事务锁顺序；PostgreSQL 使用稳定会话 advisory lock，SQLite 在读取前 BEGIN IMMEDIATE。防止同时创建不存在的会话、并发结束后状态残留；这不是 Worker 租约或完整多实例运行协调。
- 模型配置接入 ConfigurationRepository；密文与 nonce 成对更新，激活检查与设置同事务。配置读取不解密，ProviderSnapshot repr 不显示密钥。没有读取环境历史密钥或调用真实模型。
- Prompt 草稿版本分配、发布、回滚接入 ConfigurationRepository；跨进程事务锁保护版本号与单一生效版本，回滚的新版本创建及发布整体原子化。
- 增加 scripts/serve_isolated.py：localhost:8019 临时数据库、临时 SecretStore 和确定性 FunctionModel，浏览器验收不访问真实数据/模型密钥。
- 运营概览、管理会话筛选/详情/备注、审计、联系人创建/查询/修改接入 AdminRepository，移除 app/admin.py 和联系人路由的直接 SQLite SQL。标签按 JSON 数组元素精确匹配，文本查询按字面子串匹配；分页含稳定 ID 次序。
- 联系记录关联不存在/软删除会话时返回 404；显式空状态返回 422；Repository 限制可更新字段与状态。这里没有实现可信身份/资源归属授权，也没有把业务修改与审计写入合并事务；这些仍属于待完成的 S4。
- 知识文档、切块、资产元数据、术语、设置与导入任务接入 KnowledgeRepository。发布同事务校验版本与切块清单；并发冲突拒绝发布。每次解析使用独立文件/资产路径，失败只清理本次文件，提交后清理失败不删除已发布资产；重新解析保留原显示名称。
- 新增实例级 create_app 装配和共享连接池 PostgreSQLRepositories；所有服务接受显式 Repository、密钥存储及知识路径。真实 PostgreSQL HTTP 验收禁止本地 Repository 和 SQLite 初始化/恢复/健康检查，确认没有静默回退。PostgreSQL 无租约导入任务暂不开放，等待 S3 Worker。
- SecretStore 构造不读取或创建主密钥；加密时才按显式路径初始化，解密缺少主密钥时不自动生成。隔离测试验证重载和供应商 AAD 绑定；生产 KMS/文件权限仍待实现。
- 新增 0002_message_order，独立运行 Schema 不修改 0001 基线。PostgreSQL 消息使用非空正序号和会话内唯一约束，持会话事务锁分配；历史上下文、管理详情和最新消息不再依赖 UUID/时钟排序。SQLite 导入到当前 head，按原时间戳/rowid 顺序生成序号，并核对独立顺序摘要；内容未变但顺序被改也会拒绝重放。
- 新增 0003_identity 的五张身份表和 IdentityRepository：显式供应账户、随机盐 scrypt 密码、仅保存摘要的随机会话令牌、绝对过期、退出/改密/用户/成员/租户撤销。身份变更与事件同事务；KDF 不持数据库事务，提交登录前复查加锁的用户版本。Principal/RBAC 默认拒绝未知权限及跨租户/他人资源访问。没有默认账户，也没有将旧数据自动授予新用户；此底座尚未接入 HTTP 与业务资源过滤，不等于权限隔离完成。
- 新增 0004_chat_tenants，为会话/消息/Run/工具记录建立 tenant_id、会话 owner_id、本租户成员所有者约束与跨表复合外键。旧会话归入停用 legacy 租户，无所有者、不允许登录/启用。令牌绑定 ChatRepository 每次读写重新校验身份，客户只见本人，客服只见本租户；禁止普通更新修改归属。无令牌 PG 会话路径仅见 legacy。该访问过滤尚未接到 HTTP 和 Agent，不是 RLS，也未覆盖其他业务 Repository。

## 可运行方式

0006 批次历史记录：知识租户字段、同租户文档子记录复合外键、租户内去重与 knowledge_settings 已实现。检索/字符回退/图片/管理元数据/任务/重建/术语均有令牌绑定权限和租户过滤；KnowledgeService 使用租户文件目录并在文件操作前鉴权。当时尚无 HTTP/Agent 贯通；当前贯通状态见下文。RLS、对象存储、租约 Worker 或向量检索仍未完成。

0005 批次历史记录：管理/联系人归属与权限过滤已实现。联系人、内部备注、审计的复合关系及真实 actor 校验通过；联系人创建/跟进修改、备注与审计同事务，SQLite 与 PG 均验证审计故障回滚。旧 0004 升级保留明确父会话归属，历史备注作者保留未知。当时尚无 HTTP 登录依赖；当前已接入。Outbox/RLS/审计归档仍未完成。

安装：`python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e ".[test,production]"`。

应用默认仍访问 SQLite。会话、模型/Prompt 配置、管理、联系人、知识库、导入任务和术语已通过 Repository 访问，显式 PostgreSQL/AuthPolicy 应用工厂已完成身份贯通与隔离集成验收。账户供应、RLS、Worker、对象存储等生产门槛尚未完成，勿切换真实数据。

```powershell
# 仅校验操作员提供的隔离、已完成 SQLite backup 的快照，不自动寻找 app.db。
.\.venv\Scripts\python.exe scripts\migrate_sqlite.py --source D:\isolated\snapshot.sqlite --dry-run
# 以下命令在本地 Docker 引擎可用后使用。固定测试账号仅存在 disposable 本机容器。
docker compose -f compose.integration.yml up -d --wait
$env:TEST_POSTGRES_URL = 'postgresql+psycopg://integration:local-integration-only@127.0.0.1:55432/postgres'
.\.venv\Scripts\python.exe -m pytest tests\test_postgresql_integration.py -q
```

正式 Alembic 与导入 CLI 使用显式 `MIGRATION_DATABASE_URL`，由受控配置注入；导入当前只接受 0014_recovery_hold 且旧业务表为空的 PostgreSQL 目标，并验证业务记录仍处于停用 legacy 隔离区、供应商 AAD 版本为 0、Run 无租约/取消标记且 execution_mode=direct/event_sequence/event_data_bytes/event_floor=0、admission_started_at/admission_until 均为 null、queue/events/snapshots 为空。知识任务必须 local/未租约/fence=0，知识 queue/items 及 recovery_state 为空。新增运行字段篡改也拒绝重放。已有表结构变化必须先升级隔离源快照，不能静默忽略字段。已存在的 0001 PostgreSQL 升级沿用此前的时间戳/UUID 顺序，不声称恢复未保存的 SQLite rowid；详见迁移文档。

## 验收状态

### 0014：恢复执行保护（当前）

最新后端全量 **185 passed / 4 skipped**（231.84 秒），包含 **92 项真实 PostgreSQL**；前端 **36 passed** 与构建通过。4 项跳过仍是真实 Redis，不能计入已验收。前一完整 0013 回归为 181/4（198.96 秒）；本批中临时 PG 停止导致 6 项 fixture 连接超时，确认进程不存在后重启相同拥有的临时集群并复测，未将环境错误计入通过。

不可变 0014 迁移新增空 recovery_state，新库无 hold，正常启动不变。逻辑恢复先由 pg_restore 生成 SQL，再用 psql 无启动配置/ON_ERROR_STOP/单事务同时恢复数据与写入 public.recovery_state hold；Schema/data 不会先提交成为可执行旧库。验证业务摘要/序列/文件与明确的新 marker 后才重定位源路径。修复 pg_dump 清空 search_path 后辅助 hold SQL 的 public 限定，不透传子进程原始错误。

4 项真实 PG 专项 4 passed（15.27 秒）：恢复 queued Run 后库装配/两种 Repository 领取及独立两类 CLI Worker 均拒绝，队列/fence 未变、无密钥文件；已运行 HTTP 在 hold 出现后 API/页面/登录/健康均 503/no-store，重新启动也拒绝；故障 hold SQL 触发整个恢复事务回滚至空库；0013→0014 添加空保护不阻断新库。备份 17 项专项在 0014 更新后 17 passed（17.37 秒）。

前端保留 recovery_hold 错误码，在认证配置/登录/退出及身份复查时停用工作界面、清除本地 CSRF/身份而不声称服务端已退出。新增分类/错误传播/401 epoch 回归；浏览器发现并修复新增 error body epoch 检查把预期 401 误判为 409 的问题。最新构建下先验证未登录页正常，再在隔离 harness 写 hold、提交合成登录，界面显示“服务暂停中”“无需修改账号密码”，未进入工作界面。IAB 对直接导航 503 响应曾报客户端阻断，未把保留旧页当成刷新成功；采用已加载页面的真实 API 响应验证。截图 `C:/Users/56226/AppData/Local/Temp/cs-recovery-hold-20261007.png`。

恢复 hold 无自动/租户管理端解除入口；前向补齐恢复点后撤销/删除/外部操作、受控解除、对象桶/KMS/WAL/PITR/自动备份与生产 TLS/角色/ACL/HA仍未完成。应用保护不是 RLS，不能阻止数据库维护账户直接改表或旧程序绕过。完整 S0–S7 目标仍 active。

本批收尾：两次隔离 8019 harness 正常退出；剩余 cs_test_/cs_browser_ 数据库数量核对为 0，精确停止拥有的 localhost:55433 临时集群，未操作实际 8000 服务。git diff --check 通过。下一批继续共享对象存储、自动备份/恢复验证调度与资源隔离，不将外部 Redis 环境缺项当作整体停工理由。

### 加密逻辑备份与隔离恢复（0013 历史批次，见 0014 当前状态）

交付 `app/backup.py`、独立 `scripts/logical_backup.py` 和 [操作规范](logical-backup-operations.md)。相同 exported snapshot 的 pg_dump/逐表摘要，原文/图片按引用和大小/SHA256备份；AES-256-GCM 对 dump/blob/manifest 分别加密，身份 AAD 防止替换，READY 最后发布。只支持显式 loopback 与离线维护声明，不读取应用 SecretStore 或隐式 libpq 配置。

新增备份专项 17 项，其中 6 项真实 PostgreSQL：加密恢复与权限/图片/重解析、源文件变化拒绝及包清理、密文篡改在 SQL 前拒绝且新文件根清理、非空库/已有根拒绝、独立 CLI 成功且输出无凭据、导出 snapshot 后并发提交未混入备份。其余覆盖 GCM 错误 key/label/篡改清理、路径逃逸及连接配置拒绝。恢复 SQL 单事务，之后验证所有业务表/摘要/序列/文件引用，再重定位文档来源；不启动 API/Worker。开始 SQL 后验证失败保留隔离现场，不自动删库或重试。

独立 CLI 小样本报告：backup_id `c0779f9b-c545-4c9c-a9ba-fb73f99a6a00`，Schema 0013，2 文件，备份 0.297 秒，恢复及验证 0.437 秒，verified=true，workers_enabled=false；对应测试 1 passed（4.62 秒）。仅合成文件/账号、官方 PostgreSQL 16.10，不是生产容量或整体 RPO/RTO。

聊天取消间歇失败的可复现机制：`read_events` 观察查询与领取同用会话 advisory lock，`--once` 在候选忙碌时正确跳过并退出 0。新增真实 PG 测试持锁运行独立 CLI 验证退出且仍 queued，解锁后再次领取到 complete；取消测试改为无会话锁的内部只读 sequence 观察，生产领取/SSE逻辑未修改。两项专项 2 passed（15.47 秒）。此前原失败当时无足够进程诊断，新增机制复现和观察修复不冒充生产 Worker 重放/HA 完成。

本批第一轮全量（新增锁竞争/快照用例前）179 passed / 4 skipped（188.80 秒）。最终全量结果另记。前端无修改，沿用上批 30 项与构建证据。

未完成：对象存储/自动 scheduler/备份执行租约/保留策略、WAL/PITR/异地/KMS、生产角色/TLS/ACL、Windows scratch 私有 ACL 验收、恢复后授权与业务前向对账、数据库 recovery hold、大规模/跨区域演练。恢复工具报告 activation_requires_reconciliation=true，但它不是数据库级启动锁；隔离库不得直接启动服务。真实 Redis 4 项仍未通过，整个 S0–S7 不缩小。

上一批 0009 全量：**119 passed，无跳过**（67.06 秒），包含 38 项真实 PostgreSQL 测试，前端 14 项测试与构建通过。以下 0009 段落为当批证据；当前 0010 实现与最新结果见下一小节。测试只使用隔离数据库、合成账号与模型，不读取实际客服数据或模型密钥。

### 0013：耐久知识导入与文件发布（当前，生产化仍未完成）

独立知识 Worker 使用固定 manifest、DB-clock 租约及 owner/fence 校验，沿用原登录会话摘要；另一次登录不能替代原授权。文档/片段/资产元数据与逐文件结果原子发布，输入 SHA256/尺寸变化拒绝解析。输入不被版本清理删除；数据库 COMMIT 回执丢失时保留可能已提交的不可变文件，不把已导入 item 降为失败。任务过期有界关闭，不自动重放可能有副作用的解析。

新增 11 项真实 PostgreSQL 用例覆盖独立 CLI 真实解析、固定清单及部分结果、并发领取、错误 owner/fence/过期/替换凭据、发布事务回滚、解析中撤销、杀死进程后过期收尾、HTTP 202 与租户/客户拒绝、旧源升级和提交确认丢失。浏览器在隔离 PostgreSQL/独立解析 Worker 上成功导入 synthetic.txt、检索命中；最新前端刷新后恢复 complete/1/1 与一条文档，不重新 POST。前端 30 项测试及构建通过，新增 3 项只验证任务选择逻辑，不声称覆盖全部轮询故障场景。

第一轮最新全量为 162 passed / 1 failed / 4 skipped（176.97 秒），失败是独立静默聊天 Worker 在草稿出现前退出；单项复跑通过（6.48 秒），根因未确认。后续全量结果另记，不以复跑成功抹去此记录。真实 Redis 4 项仍未验收。

随后全量：**163 passed / 4 skipped**（177.03 秒），包含 **81 项真实 PostgreSQL**；前端 **30 passed** 与构建通过，git diff --check 无问题。聊天取消用例增加早退时合成 Run 状态/子进程输出诊断，不改变断言或生产行为；不把当前通过视为根因修复。临时浏览器验收服务及其解析 Worker 已正常退出，数据库清理由验收 harness 执行。

收尾核对 cs_test_/cs_browser_ 数据库剩余数量为 0，精确停止本次拥有的 localhost:55433 临时 PostgreSQL 集群；未停止实际 localhost:8000 服务。任务刷新证据：`C:/Users/56226/AppData/Local/Temp/cs-knowledge-restored-20261007.png`。

限制：需要显式开启 durable_knowledge 与共享文件根，默认模式不变；对象存储、文件 GC/备份、解析沙箱/全局预算、请求幂等、主动取消、重试/死信及完整原子审计/Outbox 尚未完成。刷新从最近 20 条授权任务选择活动/最近结果，并不是完整任务管理入口。

### 0012：共享准入与耐久容量/启动水位（历史批次，Redis 验收未完成）

2026-10-07 最终全量：**152 passed，4 skipped**（146.92 秒），含 **70 项真实 PostgreSQL**；前端 **27 项测试及构建通过**。4 项跳过均为新增真实 Redis 集成，未设置 TEST_REDIS_URL；绝不计入通过或用模拟替代。上一轮 149/4 是补充抢占退出预留前的结果，当前以此处为准。git diff --check 通过。

新增不可变 0012 迁移：单例全局策略、admission_started_at/admission_until 和部分索引。历史字段 null、默认空策略、不自动启用 Redis。可信 CLI 显式排空供应策略，相同配置幂等，改变策略需受控维护迁移。Worker 初始化/领取实时校验策略；配置后未配置 Worker/直接 PG 执行不能绕过。PG 全局事务锁串行检查容量与最近 60 秒启动记录，再取会话/Run 锁；抢占后的预留持续到旧 owner 退出确认或 DB-clock 到期，错误 owner 无法释放。独立 Lua Redis 适配器采用服务端 TIME、UUID permit、30 秒 TTL、同一 Cluster hash tag，连接超时与 SDK 重试禁用、URL query 覆盖禁用，原始错误不回显凭据。Redis 操作实际执行正确性尚待真实测试证明。

7 项新增专项已通过，6 项使用真实 PG：多连接并发只领取容量上限，直接/缺策略/错策略拒绝，完成后启动限额仍生效及 60 秒窗口，策略供应必须排空/不可静默改变，抢占仍占槽且错误 owner 拒绝释放，协调传输故障不创建模型或领取命令，注入协调丢失令无新 token 的慢 FunctionModel 中断/清草稿/无助手保存/释放耐久预留。另一项纯单测验证策略输入与错误脱敏。注入适配器明确不是 Redis/Lua 证明。

真实 Redis 专项已编写：两个 client 并发原子容量与启动限额/退款/失效 owner，策略不一致与命名空间状态丢失，Redis 状态丢失但 PG 容量和启动限额保留，慢模型协调丢失中断。fixture 只使用显式 loopback 端点和本次 UUID keys，不执行 FLUSHDB；配置但不可达时失败，不自动跳过。本机 Docker daemon 初始不可用，启动现有官方 Desktop 后 info/status 请求仍未完成、WSL docker-desktop 仍为 Stopped、没有可用 Redis 端点；未绕过协议/管理员设置或安装不明 Windows 移植版。结束自建诊断和本轮启动的 Desktop 进程。故障切主、分区、双服务进程、Redis ACL/TLS、Cluster/Sentinel 和 Lua 行为均未声称通过。

本批初次 PG 重启遗漏专用 port，测试连接失败；检查自建集群状态/日志发现监听默认 5432，停止该确切集群后使用 55433/UTC/30 connections 重启。修正环境后全量重跑；未访问其他数据库服务或真实数据。新增 Schema drift、离线迁移、旧数据导入/终态快照及原有回归通过。

实际前端浏览器回归：新 cs_browser 数据库、当前 head、独立 synthetic Worker、管理 Cookie 登录/初始化（供应商数 1）、普通队列回答完成、刷新保留一会话两消息、不重复。此回归未配置共享准入，不能用作 Redis 模式验收；截图仅含合成内容。默认真实服务未切换，未使用历史模型密钥。

仍需真实 Redis 专项与双实例/切主验收；API 排队准入、租户公平、取消 Pub/Sub 加速、供应商 RPM/TPM、费用预算、知识 Worker/文件 fencing 及多数 S1/S2–S7 能力仍待完成。S0–S7 完整目标保持 active，不能上线。

本批清理已完成：仅关闭隔离 harness/owned synthetic Worker，确认 cs_test_/cs_browser_ 数据库剩余 0，再停止确切自建 PG 集群；未停止实际 localhost:8000 客服。临时合成数据库已删除，截图保留于本机临时目录。

### 0011：独立终态快照、有界事件保留与过期恢复（历史）

2026-10-07 全量：**145 passed，无跳过**（133.19 秒），含 **64 项真实 PostgreSQL**；前端 **27 项测试与构建通过**。以下 0010 及更早段落保留各批当时状态，遇到“最新/未实现保留”等表述以本节为准。测试仅使用 disposable 数据库、合成账号、FunctionModel，不读取实际客服数据/密钥、不调用真实业务或在线模型。

新增不可变 0011 迁移与终态快照 Schema、event_data_bytes 累计计数和 event_floor；单帧预算计算不再聚合整个账本。助手消息/Run/done/独立终态快照原子提交，completed 快照使用权威已保存消息，失败/取消清空草稿。快照读取实时鉴权、序号不一致失败关闭；维护删除事件/提高游标同事务，累计预算和幂等引用保留。

12 项真实 PG 保留测试覆盖：清理后完整快照及幂等、失败/取消查询、并发有界清理且活动任务保留、忙碌会话/锁定 Run 跳过、故障回滚、错误快照跳过/越权维护/政策校验、CLI 显式参数与仅数量输出、0010→0011 精确计数/权威内容回填、缺失最终消息令迁移整体回滚、HTTP 过期游标鉴权/他人 404，以及显式 lifespan 开关。全量还验证离线 SQL 和 Schema 无漂移。首次全量发现离线迁移 mock 不支持 Python scalar 查询，改为 SQL DO 完整性门控后全量重跑通过；离线脚本不绕过门控。活动运行超预算同样拒绝升级。

清理默认关闭，只接受显式 1–90 天整数秒策略，最多 100 个旧 terminal queued Run，数据库时钟截止、session try-lock/Run SKIP LOCKED 与快照复查。旧游标 HTTP 409 cursor_expired，清理追上已打开流时发临时 cursor_expired；前端由自己的同源 events URL 推导快照，鉴权/身份 epoch/Run/游标/终态/权威消息检查通过后替换历史，不重发命令。精确尾游标的空流也查询终态，活动状态才重试；run_snapshot 仅是前端内部事件。

浏览器证据：独立 cs_browser 数据库、真实最终前端构建、独立 synthetic Worker；仅 harness 故障注入将合成完成时间老化 2 天并以 1 天策略清理，让订阅确实遇到过期。第一轮数据库证据为 events=0/snapshots=1/event_floor=event_sequence=6/status=complete；界面恢复完整最终回答。刷新后再发送第二轮，完成并刷新，一会话/四消息，无重复回答。验收截图保存于本机临时目录，不含真实用户数据。此证据不等于生产保留策略批准、规模性能或供应商能力验收。

本批清理：隔离 harness 与独立 Worker 正常退出，确认 cs_test_/cs_browser_ 数据库剩余 0 后停止自建 PostgreSQL 集群；未停止或修改实际 localhost:8000 客服服务。临时合成数据库已删除，验收截图仍保留。

仍未完成：Redis/全局准入、重试/死信/检查点、知识导入 Worker/文件 fencing、对象存储与 HA；事件清理不删除消息/快照/幂等身份引用，不代表隐私删除或备份清除。默认服务未切换认证/队列模式，不能上线。全部 S0–S7 目标保持 active。

### 0010：typed queue、独立进程 Worker 与事件账本（历史）

最新全量：**133 passed，无跳过**（112.77 秒），含 **52 项真实 PostgreSQL**；前端 **23 项测试与构建通过**。durable 专项共 14 个用例，均纳入全量回归。补充跨租户/撤销断言另外针对性重跑。128/47/17 为上一批证据。git diff --check 通过；实际数据/密钥/在线模型未访问。

实现：可选认证 PG durable_chat，API 只提交 202；同编号/actor/session/content 幂等、新命令抢占同会话旧命令；typed queue 保存可撤销摘要引用与不可变 Prompt，不保存原始凭据。Worker 通过 session lock/SKIP LOCKED 领取并重新校验身份/父资源/排队期限；按 lease/fence 执行与写事件，最终助手消息和 done/citation/contact 同事务。终态清理不盲重放崩溃任务。append 失败显式关闭 async generator，清理槽与监视任务。

真实 PG 证据：并发 claim 只领取一次、重复提交不重复消息、外用户拒绝、原始摘要不是登录 token、错误 fence 拒绝、final message/done 对齐及游标重放；排队取消/超时/抢占/身份撤销产生持久终态。独立 Python Worker subprocess 执行并重复启动不重放；kill 慢 Worker 后强制隔离租约到期，reaper 中断，无助手消息/自动重领。HTTP TestClient 验证 202、独立进程执行、Last-Event-ID/非法游标及他人 404；真实 TCP HTTP 订阅读到 id:1 后断开，随后独立 Worker 仍完成。事件写故障释放 runtime，旧快照新运行字段被篡改拒绝重放。0010 实际迁移与 autogenerate drift 检查通过。

新增一致快照：同一 session 事务锁内读历史、活动 Run 状态/取消意图、重放草稿/引用及 cursor，GET session 附加 run，GET 特定 Run 可读终态；scope/身份实时校验，私有队列/lease/凭据字段不输出。新 snapshot 与历史/事件/终态写入采用同一锁顺序。当前从账本重放快照，不是已经压缩的独立终态快照表。

事件预算：JSONB data 聚合大小按 Run 计，非终态最多 3MiB/9000 事件、全部最多 4MiB/10000；单帧保持 256KiB。预留错误/重置空间，超限 final citations 会整事务回滚助手消息和 done，随后可提交受限 error。聚合检查随事件数增长，仍须压力评测/计数字段优化，不将该数据预算当作整个数据库的空间限制。新 PostgreSQL 用例覆盖草稿 reset/尾事件游标、final message 关联、锁等待不撕裂快照、他人/外租户/撤销不可见、预算拒绝和 final 原子回滚；独立慢 Worker 进程在无新 token 时通过数据库取消意图中断，草稿 reset、无助手消息。

前端：202 回执立即暴露 Run ID，停止在回执之前只登记意图，之后提交 cancel；创建会话期间的 stop 可阻止发送。cancelled=true 显示等待运行结束，而非已经停止；false 查询 Run 终态，网络失败明确未确认，不 abort 冒充取消；取消/查询共用 10 秒时限，提交响应 15 秒时限，POST 不自动重试。GET 最多额外 3 次、200/400/800ms 退避，身份切换/AbortSignal 阻止旧流，回到会话按快照 cursor 续读。新前端单测覆盖早期 stop、取消失败/完成竞态/迟到响应、回执先于 GET 与快照续读。

浏览器：临时 cs_browser 数据库/目录、真实前端、独立 synthetic Worker（两段回答间隔 20 秒），提交后刷新时恢复第一段草稿并显示“正在恢复运行事件”；随后收到第二段和 done，文本不重复，一会话/两消息。另提交问题后点击停止，先显示“停止请求已确认，等待运行结束”，之后 interrupted/reset 清除草稿，最终历史仅新增用户消息（共三条），没有助手保存。较早的普通刷新历史证据与本批活动 Run 续读分开记录。FunctionModel 不证明真实供应商能力；loopback HTTP 不证明生产 TLS。

未完成：全局准入/Redis、事件保留/压缩终态快照、重试/死信/检查点、知识导入 Worker/文件 fencing、共享文件/对象存储与 HA。POST 回执丢失或网络一直不可达时仍不能立即确认取消，需要网络恢复后刷新/查询状态；界面不宣称已经停止，不盲重发命令。账本预算/快照查询仍须规模性能验收。默认模式未切换，不能据此上线。

0009 租约本批实现：Run 的 owner/expiry/fence/cancel 意图及部分唯一索引；直接执行的 PG ChatOrchestrator 默认持 30 秒数据库时钟租约，后台每 2 秒检查/续租，Agent 上下文工具前另复查。新启动在同一事务内终结旧 leased Run/工具，旧执行者不能保存迟到结果。取消在授权会话范围先提交数据库意图，不依赖当前实例有本地 token；身份撤销/数据库故障/失租停止执行授权。答案与终态同事务，最终租约检查拒绝时不提交助手消息。每个 PG 应用实例安装每 5 秒、最多 100 条的过期清理器，try-session-lock→SKIP LOCKED Run→重新验过期，Run/工具/会话同事务终结；忙碌或锁定目标跳过，不扫描/重放旧无租约记录，不读取文本/模型密钥。身份失效的运行可在到期后清理，而不是绕过用户身份直接结束。

真实 PG 新增 10 项：无 permit/错误 owner/失租不能续租、写答案或工具结果；跨连接持久取消且阻止答案提交；6 路并发抢占只剩一个 leased running；两清理器不会重复终结，批次上限有效，live/unleased 不受影响；锁定 Run/忙碌会话跳过；最终两次租约检查之间失效不提交答案；插入故障回滚终态；他人取消失败、撤销后禁止原执行者写入但过期内部清理可完成；旧快照 Run 保持 unleased 且新字段篡改拒绝重放；两个独立运行时/连接池（非两个服务进程）的慢 FunctionModel，在无 SSE 心跳/共享 token 时经数据库意图被取消，无助手答案且运行槽/监视任务清理。没有真实业务调用或在线模型。

0009 浏览器：新建临时 cs_browser_<uuid> 与合成账户，当前实际前端恢复 Cookie，发送问题得到确定性 SSE 回答和完成状态；刷新恢复一会话/两消息。此路径已使用 PG Run 租约，但不能以快速 FunctionModel 回答代替慢模型取消、完整双实例进程崩溃或持久 SSE 重放验收。

最终复核统一 HTTP UUID 字符串（含大写）与清理器 UUID 对象的会话 advisory lock 键，增加规范化单测，防止相同数据库主键使用不同锁。隔离浏览器服务正常退出；确认 cs_test_/cs_browser_ 数据库剩余 0 后停止自建 PG 集群，不影响实际客服服务。

前端认证本批实现：AuthBoundary 先 GET `/api/auth/config`，只有结构有效且 enabled 明确为 false 才挂载本机兼容界面；失败不降级。认证模式恢复 Cookie 身份，提供账号/密码/租户登录及退出；CSRF 仅存内存，API/SSE 同源 Cookie、有副作用请求带 CSRF。HTTP 401 与流中 authentication_required 清除身份，卸载 App 清空私有视图/终止流，退出成功清空账号/租户和路径；身份 epoch 丢弃旧账户迟到响应。每 30 秒和 focus 检查 me。客户隐藏管理入口，客服隐藏模型/知识/Prompt/审计；这只是展示控制，权限由后端保证。HTML no-store，静态路径逃出构建目录返回 404。

真实前端浏览器证据：在独立 cs_browser_<uuid> 数据库、临时目录和禁止真实模型 HTTP 的 FunctionModel harness 上，实际 `/` 登录页完成登录→合成 SSE 最终回答→一会话/两消息→刷新恢复身份和历史→退出返回登录并清空私有界面。本批最终构建再次验证登录/退出，账号与租户输入均已清空。专用 `/__acceptance/auth` 页面较早的 setup/知识验收与实际前端验收分开记录。loopback HTTP 开发 Cookie 不证明生产 HTTPS/TLS，确定性模型不证明真实工具选择与回答质量；账户供应、生产部署及多实例仍待验收。

### 前几批历史证据

以下记录描述各批当时的实现，出现“尚未接入/无前端登录”不代表当前状态；当前状态以上述最新结果和末尾表格为准。

聊天/Agent 本批实现：HTTP 全部会话操作、消息/反馈/取消、管理生命周期与客户联系登记已按请求绑定；AgentDeps 带服务端 Principal/实时校验回调，知识/表单/订单类工具和流事件复查身份。共享 ChatRuntime 只保留进程运行协调，成功持久启动后才替换旧令牌；取消在令牌读取前检查会话范围。流中权限失效清除草稿/返回 authentication_required，不保存最终回答；失权终态写入不绕过鉴权，遗留运行待 S3 受控 Worker 清理，不能据此宣称耐久恢复。认证模式停用共享模拟订单结果，真实适配器未接通。

专项真实 PG 验证：两租户实际 FunctionModel→search_knowledge 只引用本租户文档；同租户其他客户历史/发送 404、取消 false、伪造父会话联系人 404；独立请求共享一个容量槽、占满超时后恢复计数、本人新请求可取消、撤销后无助手保存且进程令牌/槽/回调清理。第一次联系越权测试遗漏必填联系方式导致 422，修正为合成 example.com 联系方式后断言 404 通过。Agent 纯单测验证身份更换/撤销在知识工具前拒绝、认证工具不返回模拟业务记录。

聊天认证浏览器验收：临时 cs_browser_<uuid>、无主密钥、FunctionModel 且禁止真实模型 HTTP client 的专用测试页面，完成 Cookie 登录、租户 setup、新会话、SSE 两段合成回答至 done、历史查询 2 条消息。默认前端未改，不代表生产登录页面、真实模型工具选择或多实例验收。scripts/evaluate.py --repetitions 3 的 7 个确定性案例仍全部通过；口语 Recall@3=0.50、困难负例拒绝率=0.00 的缺口仍存在，不能视为 S6 完成。

当前知识 HTTP 追加全量：**103 passed，无跳过**（60.56 秒），包含 26 项真实 PostgreSQL 测试。知识服务/任务查询服务按请求独立绑定，所有管理路由前置 knowledge.manage，资产读走 knowledge.read；不得把此权限启用误写为完整聊天/Agent 贯通。上传解析及上传审计移到线程池，鉴权/撤销异常保留 401/403，不被解析错误兜底改成 400。真实 PG HTTP 专项验证两租户同内容独立上传、外租户文档/资产/检索/术语不可见、托管根之外导入拒绝、客户解析前拒绝、同租户图片可读且 no-store、退出图片 401。知识业务与审计多数仍分事务，向量检索/对象存储/Worker 尚未实现。

知识认证浏览器验收：自建 cs_browser_<uuid> 与临时文件目录中的合成账号，经专用测试按钮完成登录 200、上传文本 v1、词法检索命中 1 条、重解析 v2、刷新后文档列表仍为 1 条。测试不是生产登录/知识前端，HTTP loopback Cookie 不证明 TLS 验收。未访问真实文件或在线模型；服务正常退出并清理自己的数据库。原应用仍未切换认证模式。

当前最新全量：**102 passed，无跳过**（55.28 秒），包含 25 项真实 PostgreSQL 测试。前述 101/99 项作为历史批次记录保留。管理 HTTP 已按请求绑定配置/Prompt/Admin Repository；显式路由名/权限白名单前置鉴权，未绑定聊天/知识等路由继续失败关闭。新增真实 PG HTTP 测试覆盖两个租户切换不共享配置/Prompt/会话、外租户发布和详情 404、客户管理 403、令牌撤销 401、参数错误不回显密钥及 setup 仅 DeepSeek。HTTP 管理变更的大部分审计仍与业务分开事务，不能据此宣称完整 S4。

浏览器追加：`scripts/serve_isolated.py --auth-management --postgres-admin-url ...` 在自建 cs_browser_<uuid> 数据库和临时路径运行，专用 `/__acceptance/auth` 页面完成真实浏览器合成账号登录 200、setup 200（供应商数 1）、刷新后 me 200、读取 providers 200、logout 200 后 me 401。初次 harness GET 被 SPA catch-all 遮挡，已调整测试路由顺序并正常重启；不修改实际客服服务。验收仅使用明确 loopback HTTP 开发 Cookie，不代表生产 HTTPS/TLS 验收；未提供生产前端登录页。服务正常退出并清理自己的数据库，未使用模型密钥或真实模型。

最新追加全量结果：认证接口变更后 **101 passed，无跳过**（53.41 秒），包含 24 项真实 PostgreSQL 测试；下方 99 项记录保留为上一批准入实现证据。前端未修改，公开认证浏览器验收仍待完成。

认证 HTTP 追加：create_app 接受显式 AuthPolicy，限定 PostgreSQL、可信 Origin 与安全 Cookie，注册 login/me/logout；登录使用持久 LoginService，校验请求体并避免错误回显密码；退出先持久撤销。认证模式启动只读校验 Schema，不初始化 legacy 配置或重建 legacy 知识索引。业务服务尚未按请求绑定，所有非认证 API 在此模式均失败关闭（未登录 401、登录后 503），不把登录误当成全局资源权限。默认兼容模式未切换，尚无前端登录和认证浏览器验收。Auth HTTP 专项验证包含真实 scrypt、Cookie 属性、Origin、异常字节 CSRF、退出令牌不可重用和 legacy 路由屏障。

最新全量运行：显式设置隔离 TEST_POSTGRES_URL 后，`python -m pytest -q --tb=short` 为 **99 passed，无跳过**（47.62 秒），真实 PostgreSQL 集成测试 23 项通过（20 项原有集成与 3 项登录准入专项）。保留前述迁移、身份、会话、管理及知识隔离验证；0007 测试覆盖模型配置权限/撤销、租户 AAD/外租户快照拒绝、并发 Prompt 版本/发布/回滚及跨租户 Run-Prompt FK、0006→当前 head 旧配置密文原样保留且无主密钥读取、新租户无历史配置回退、AAD 格式版本篡改拒绝导入重放。0008 准入验证见下方追加记录。测试使用显式临时主密钥与合成供应商密钥，阻断实际模型 HTTP client；未读取环境历史密钥。首次新增升级测试将 legacy 账户拒绝的异常错误写为 AuthorizationError，按现有 ValueError 契约修正后全量通过。Schema 无漂移、离线 SQL、完整 HTTP/SQLite 回归通过；前端未修改，尚无公开认证模式浏览器验收。

0007 追加实现：配置/设置复合租户主键、租户内 Prompt 版本唯一、每租户单一 published 部分索引、Run-Prompt 复合外键；ConfigurationRepository 令牌绑定 config.manage/model.use/prompt.use 并逐次校验。新租户显式幂等初始化只创建未配置 DeepSeek 与默认业务 Prompt，无 OpenAI 行、无密钥复制。SecretStore AAD v0 保留旧密文，v1 绑定租户/供应商；Gateway 加密与 HTTP client 前校验权限/租户，防止未授权主密钥创建或跨租户快照使用。管理概览的配置状态仅向管理员显示本租户数据。尚无 KMS、HTTP 登录、Agent 身份贯通或数据库 RLS。

0008 追加实现：持久化全局/来源/账号预算、数据库时钟过期、最多两个并行 KDF 槽与 owner/expiry 校验、过期桶有界清理、LoginService 内部装配。真实 PG 专项测试涵盖独立连接池并发领取仅两次成功、占满拒绝仍扣预算、过期接管与旧 owner 释放拒绝、失租密码校验不能签发会话、失败凭据到达限額后不再执行 KDF、切换租户 ID/用户名大小写不能绕过账号预算、IPv6 /64 和全局预算、过期清理、非法来源及 DB 故障不进入 KDF。恢复原始密码验证器后合成账号真实 scrypt 登录成功。不表示公开 HTTP 认证/CSRF/边缘防护、Redis 准入或 Agent 租约已经实现。

Docker 引擎不可用，因此改用官方 EDB HTTPS 分发的 PostgreSQL 16.10 Windows 二进制，在独立临时目录建立仅监听 localhost:55433 的合成集群，启用 UTF8、UTC、SCRAM 和数据校验。归档 SHA256：`EBB3B6AF4FA69DEA9951B66855BC4D42DC04E56CCB9AA7024CE3C58BD89D6B0C`；版本命令确认 16.10。可执行文件未通过 Authenticode 签名认证，该下载不是生产供应链验收。测试不安装系统服务、不挂载生产目录；用完后确认剩余 `cs_test_`/`cs_browser_` 数据库数量为 0。

浏览器验收：最新前端构建配合隔离 localhost:8019 服务，完成消息发送、确定性流式回答、会话列表更新、刷新恢复历史、键盘提交反馈及刷新保留选中反馈。验收服务已停止，未改动实际 localhost:8000 服务。

管理追加验收：新隔离 localhost:8019 服务中，以 example.com 保留域名合成联系人验证运营概览、联系列表、修改“跟进中”、保存备注、刷新保留状态/备注、两条 contact.update 审计事件展示。没有真实联系方式或业务系统调用。追加验收服务同样已停止。

知识库浏览器验收完成文档上传、预览、检索及重新解析 v2，保留原名称。另以真实 PostgreSQL 隔离应用验证流式回答、会话列表、刷新恢复历史；健康检查确认 `database_backend=postgresql`，未配置主密钥。验收服务正常退出并删除自己的数据库，不影响原 localhost:8000 服务。

`scripts/evaluate.py --repetitions 3` 的 7 个确定性编排案例全部通过，现有检索回归下限通过。该结果不证明真实模型智能：挑战集口语类别 Recall@3 仅 0.50，困难负例拒绝率为 0.00；证据门控/语义检索仍须在 S6 实现并重新验收，不能用现有低门槛代替生产检索质量目标。

| 要求 | 当前证据/状态 |
| --- | --- |
| Schema覆盖14表 | 自动比较现有 SQLite 列集合，PostgreSQL DDL 编译与单 head 检查 |
| 类型/数据导入/整批回滚 | 合成快照测试，不访问真实数据；包含 FK、UUID、时区、JSON、二进制、布尔、重复导入与目标篡改 |
| PostgreSQL 实际升级、序列、导入 | 独立原生 PostgreSQL 16.10 集群通过实际升级、导入、重放、序列测试 |
| 会话/配置 Repository | SQLite 与真实 PostgreSQL 的隔离事务、并发版本、并发会话、原子回答、回滚测试通过；PG 会话内消息序号与并发/时钟故障测试通过 |
| 管理/联系人 Repository | 两种数据库查询及联系/备注与审计原子回滚通过；PG 令牌绑定管理/联系人/审计隔离与真实 actor 通过；其他业务审计、Outbox 未完成 |
| 知识 Repository/应用装配 | 两种数据库发布/检索/任务持久化与解析竞态通过；0006 令牌绑定知识/资产/设置/任务隔离及文件前鉴权通过；PG 仅过渡 ts_rank，无向量/词法索引 |
| 身份数据库与策略 | 0003/0008、可撤销会话/实时成员策略及 LoginService 的持久预算/失租签发拒绝通过真实 PG 验证；显式 Cookie 认证、请求级 API/Agent 身份与实际前端登录已接通；尚无生产账户供应入口、MFA/SSO、TLS 部署验收 |
| 会话租户/所有者隔离 | 0004 与令牌绑定 ChatRepository 的越权、撤销、并发及复合外键测试通过；显式认证 HTTP/Agent 已接通，默认 SQLite 兼容模式不启用，不是 RLS |
| 模型/Prompt 隔离 | 0007、租户主键/版本/发布唯一/FK、密文 AAD、角色/撤销、并发和旧库升级通过；显式 HTTP/Agent 已接入；KMS 未实现 |
| 业务身份/运行租约 | HTTP/Agent/Repository 身份贯通，0009–0012 聊天租约/队列/事件/准入已有证据，0013 独立知识解析/发布 fencing 已增加真实 PG 验证；Redis 真实 4 项仍跳过，完整全局容量/HA、检查点/RLS/完整原子审计未完成，聊天取消间歇失败见本批记录 |
| 对象存储、备份/PITR | 加密本地/对象/排队输入备份恢复、hold/rebind、管理/Worker供应、独立运维CLI、远程固定版本包归档/取回及实际恢复已有真实PG/S3证据；多源桶/生产IAM供应、自动停写调度/恢复、receipt/catalog灾备、WAL/PITR/异地/KMS与完整灾备尚未完成 |
| S2–S7 全部能力 | 待实施，不以本批迁移工具代替完整目标 |

外部验收待提供：业务 API 沙箱与协议、管理员配置的模型测试密钥、独立脱敏盲测问题、目标部署/告警/备份/KMS环境及负责人。暂无这些信息时继续实现可隔离验证的内部能力。

2026-10-23 S6 增量：Agent Run 开始固定 active RAG `index_id`，检索前后复核；PostgreSQL 回答提交事务通过共享发布锁及版本断言与索引激活互斥，检测切流时返回 `knowledge_changed` 并中断。隔离 pgvector PostgreSQL 全专项（不含需要本地 RAG 模型的项目）`26 passed / 1 deselected`，包括并发锁等待、旧索引拒绝提交和无助手消息残留；无外部服务全量 `319 passed / 258 skipped`。尚无 shadow/灰度、正式双主机验收；绕过发布锁直接改索引状态不受此原子保证保护。

后续 S6 增量：新增受控本地 Embedding＋真实 pgvector 的离线 active/ready shadow Top-K 聚合比较，ready manifest/模型配置与归一化向量校验；不改变用户路由、不记录题目或文档。隔离 PostgreSQL 专项加影子输入测试 `27 passed / 1 deselected`，无外部服务全量 `320 passed / 258 skipped`。尚未对业务批准的问题集执行比较，亦无租户灰度/线上延迟验收；该合成测试不能算质量门槛达标。

后续 S6 安全切流增量：索引激活 CLI 要求每租户显式旧 active UUID 或首次无索引预期，迁移 owner 事务内持排他发布锁核对；状态已变化的激活被拒绝，隔离 PostgreSQL 覆盖首次、过期预期及回切预期。此为灰度批次的并发防护，不是批次审批或自动回切。

后续 S6 证据门控增量：数字/型号锚点按逗号细分证据子句，显式 `[资料 n]` 限制到对应来源，越界编号拒答；Agent/API 相关 46 项通过，无外部服务全量 `321 passed / 258 skipped`。它仍是窄的词面必要条件，不证明独立语义事实支持或业务盲测门槛。

后续 S6 盲测评分准备：新增只收受控认证 Top-3 适配器的检索层评分函数，冻结集前后摘要校验、跨租户结果单独计数且不计命中、只出聚合指标；200 条合成题专项通过。没有真实认证适配器与业务批准盲测集，不报告实际业务 Recall、生成事实支持或权限泄漏为 0。

后续 S6 认证适配增量：`AuthenticatedBlindSearch` 以受控内存绑定的真实可撤销 PostgreSQL 登录身份逐题检索，租户/角色/完整来源映射及索引固定失败关闭；隔离真实 PostgreSQL 双租户、错绑及撤销专项通过。PostgreSQL 联合组 `32 passed / 1 deselected`，无外部服务全量 `323 passed / 259 skipped`。仍无业务批准题集、生产身份供应和双人生成事实审阅，不报告实际业务质量达标。

2026-10-23 S2 distroless 候选运行联动：仅新建 `cs-s2-candidate-net-20261023` 专用 Docker 网络及临时 PostgreSQL/MinIO 容器，使用合成凭据、无持久卷，未接触已有 FastGPT 容器。`customer-service:distroless-candidate-20261023` 在只读根文件系统、临时 `/tmp`、非 root 用户下，连接隔离 PostgreSQL，经预装 `vector` 扩展后执行 `alembic upgrade head` 成功；随后对隔离 MinIO 的版本化私有桶执行 `S3ObjectStore.check_ready/put/get` 成功。首次以容器 DNS 明文连接 PostgreSQL 被预期的 `sslmode=verify-full` 安全策略拒绝；改用共享隔离网络命名空间内的 `127.0.0.1` 测试例外。第一次对象写入采用非允许的 `document` 类别被预期拒绝，改为 `knowledge` 后通过。本测试不等于跨主机 TLS、正式 IAM/KMS、双实例切换或生产镜像放行；候选严格 Trivy 仍为 26 HIGH、0 CRITICAL，S2 漏洞门槛未通过。

同日当前工作区回归：`python -m pytest -q` 为 **325 passed / 259 skipped**（24.11 秒），`frontend/npm run build` 成功。大量跳过由外部 PostgreSQL、Redis、S3 或本地 RAG 模型前置条件导致；这次全量结果只证明无外部服务回归，不代替隔离服务专项、业务盲测或正式部署验收。

同日隔离 PostgreSQL/Redis 专项：Docker Compose 固定 `127.0.0.1:55432` 端口在本机 Windows 上被拒绝绑定；未触碰其他容器，改用独立无持久卷容器及 Docker 自动分配的回环端口。`pg_isready` 与 Redis `PING` 成功，CI 对应七个测试文件、排除本地 RAG 模型标记后结果为 **86 passed / 3 skipped / 1 deselected**（172.85 秒）。3 项跳过均明确因为 Windows 主机未提供 `TEST_POSTGRES_BIN` 所需 `pg_dump`/`pg_restore`/`psql`，因此本批不能证明真实备份恢复。临时容器已精确删除；正式 CI 的固定端口环境仍需在托管 Linux 运行，不以本机端口限制判定 CI 失败。

同日补跑上述 3 项：在独立 Linux 测试容器中、使用 PostgreSQL 官方 PGDG 的 **16.15** 客户端对隔离 pgvector/PostgreSQL 16 服务端执行原样加密备份及恢复，模型用量、预算和费用证据专项 **3 passed / 0 skipped**（8.13 秒）。首次用 Debian 默认 PostgreSQL 17.11 客户端时恢复失败，诊断为其脚本包含 PostgreSQL 16 不认识的 `transaction_timeout`；改为同主版本客户端后通过，未改业务代码或压制错误。测试运行器只挂载项目所需源码目录，不挂载 `data/secrets`；临时服务、运行器和测试镜像已删除。此补跑补齐该三项证据，不代表完整灾备、PITR 或跨主机恢复演练。

随后针对版本错配新增备份/恢复前置主版本校验：同时核对 `pg_dump`、`pg_restore`、`psql` 与 `SHOW server_version_num`，不匹配或无法判定均在创建备份目录/恢复文件根前拒绝。单元回归覆盖混入 17 客户端的拒绝及全 16 通过；当前无外部服务全量 **326 passed / 259 skipped**（24.24 秒）。同一独立 Linux PostgreSQL 16 + PGDG 16.15 环境再次执行上述三项真实备份恢复，**3 passed / 0 skipped**（10.47 秒）；测试容器和镜像已删除。这是防错门控，不代替完整恢复演练。

2026-10-23 S6 shadow 并发门控：离线 `shadow_compare` 从全局排他发布锁改为共享发布锁。真实隔离 pgvector/PostgreSQL 验证共享 shadow 与回答提交可并行取得锁，而索引激活的排他锁仍等待 shadow 完成；既有索引 worker/切流和回答提交固定版本两项一并通过，专项 **3 passed / 0 skipped**。无外部服务全量 **326 passed / 260 skipped**（新增真实 PG 测试在无服务时按条件跳过）。临时数据库容器已清理。此改动降低 shadow 对正常回答的锁阻塞，不是生产负载容量或双主机延迟验收。

2026-10-24 S2 distroless 精简运行基底与现行镜像切换：固定官方 `gcr.io/distroless/base-debian13:nonroot@sha256:6c591b4ac789b683ab3665327181e7cfb7a39b5834f42dcb73d7becb8d18a5d9`，从锁定 Python 3.12 构建阶段复制解释器、标准库、虚拟环境及实际缺失的 `libgcc_s`、`libffi`、`libsqlite3`、`libstdc++`，不再附带基底 Python 3.13。候选完整镜像 261,170,726 bytes、用户 `65532:65532`、无入口点、显式 Python 命令；无网络/只读/临时 `/tmp` 下导入 API/双 Worker、原生包及实际空白图 OCR 成功，CA 文件存在。专用无持久卷 PostgreSQL 16/pgvector 完成 Alembic head 迁移，版本化 MinIO 私有桶的 `check_ready/put/get` 成功，合成测试容器已清理。现行 `Dockerfile` 和 CI 烟测已切换到同一运行基底，生产镜像本机构建及 UID/导入验证成功。尚无真实双主机、跨主机 TLS/IAM、实际在线模型、生产部署或托管 CI 证据。

同日 S2 供应链实测：固定 Trivy 0.66.0 同一漏洞库重扫旧 `distroless/python3` 候选仍为 **26 HIGH**，新 `distroless/base` 完整镜像及从 OCI 导出的同一制品均为 **0 HIGH、0 CRITICAL**，严格 `--exit-code 1` 实际退出 0；结果含 Debian 和 Python 两类扫描，不是只扫基底。CycloneDX 1.6 经 `scripts/verify_sbom.py` 核验为 **10 Debian + 143 Python** 组件。缓存和 `--no-cache` 两次 `linux/amd64` OCI 导出以固定时间戳、无 provenance 构建，`scripts/verify_image_rebuild.py` 核验逐 blob 一致，manifest 均为 `sha256:2acc1c1c3601ce679b7e99c224141baae7028aaabae99b171f4cbbb4d02acba3`。供应链专项 **6 passed**；最新无外部服务全量 **326 passed / 260 skipped**。托管 CI、跨宿主可重建性、制品签名、漏洞库长期复扫及最终许可核验仍未完成，S2 不宣称整体通过。

2026-10-24 S2 扫描覆盖修正：上段 **10 Debian** 的初版精简镜像没有把四个手工复制的系统库映射回 Debian 包，不能把当时的 0/0 直接当成完整覆盖。现从同一固定 Python 构建基底提取 `libgcc-s1 14.2.0-19`、`libffi8 3.4.8-2`、`libsqlite3-0 3.46.1-7+deb13u2`、`libstdc++6 14.2.0-19` 的 dpkg 元数据，连同实际库一起放入运行镜像的 distroless `status.d`；更新 `verify_sbom.py` 强制要求四包存在。现行镜像 CycloneDX 为 **14 Debian + 143 Python**，四包名称/版本均实际识别，固定 Trivy 对完整镜像复扫仍为 **0 HIGH / 0 CRITICAL**。这是扫描覆盖改进，不证明 Python 3.12 自编译解释器所有潜在漏洞均被 Trivy OS 包扫描覆盖；需另做解释器来源/漏洞审查。元数据变更后的 OCI 缓存/无缓存重建尚未重验，上段 manifest 仅属于变更前候选。临时 OCI 导出位于系统 Temp 的 `cs-base-repro-20261023`；当前执行策略拒绝精确删除，未以替代 shell 绕过，后续由用户或环境策略允许时清理。

下一实施批次：补齐实际停写/排空门控、自动备份scheduler/唯一执行租约、备份catalog/receipt灾备和自动隔离恢复/freshness；现有独立凭据CLI与远程加密归档/取回及真实恢复证据不代替这些能力。并继续真实Redis/双实例故障切换、GC/解析隔离和资源预算。聊天取消及killed-Worker测试观察锁竞争已修复并保留回归。默认应用未切换认证/队列模式；旧数据授权认领、RLS、完整审计/Outbox、备份、观测、费用、工具、路由与混合RAG仍待完成，不能缩小S0–S7范围。大规模迁移/账本和32GiB归档上限需容量/锁时间/吞吐验收，不能宣称完整生产化已完成。
# 2026-10-26 灰度健康监控增量证据

监督运维增量：新增`ops/systemd/customer-service-rag-watch.service`及静态契约，监控/监督专项9 passed（0.21秒）；无外部服务完整后端446 passed/361 skipped（26.69秒）。仅验证模板配置与Python行为，没有目标Linux systemd语法/运行验收，没有真实OnFailure告警适配器或值班送达证据；未部署、未启用。限制和部署前置项见[观察运维](rag-observation.md)。

新增显式owner监控入口`scripts/rag_rollout_watch.py`：默认单次、可有界循环，只检查指定灰度；不自动推进或恢复，不刷新旧回执。私有控制文件须包含可信当前运行binding，不能以审批表中的冻结binding替代实际配置。配置与退出语义见[灰度观察](rag-observation.md)。

本批无外部服务完整后端445 passed/361 skipped（27.52秒）；各组重叠不累加，条件跳过不算通过。

监控/私有运行文件单元11 passed（0.28秒）；真实PostgreSQL健康专项9 passed（27.03秒），包括独立Python进程运行CLI、空窗口hold，以及审批撤销后暂停归零并以退出码2结束。测试仅回环隔离PG和合成数据，无在线模型调用；专用容器已清理。该证据不代表正式守护部署、故障接管、生产告警、真实费用或双机验收完成。

# 2026-10-26 当前head0037镜像补验

本机由现行Dockerfile构建`customer-service:head0037-verify-20261026`，镜像ID `sha256:4b543714cd17938c885e5cdc05ca3290daea07322114b59d3a0e781e6043a725`，linux/amd64。非root UID65532、只读、无网络导入确认head `0037_rag_phase_clock`和49表，包含最新健康监控/回退代码。不是旧0030镜像的证据复用。

真实隔离PG在镜像内完成空库迁移、知识CLI上传/重解析/取消、容量slot释放；RAG夹具验证CAS、审计故障回滚及CLI前进/新UUID切回。夹具通过只读单文件挂载，未挂载源码或密钥目录；首轮因/tmp脚本缺PYTHONPATH失败，显式指定镜像内路径后通过。该旧RAG夹具不覆盖新双人灰度服务，后者当前由真实PG宿主专项单独证明，不能据此宣称全部镜像内新链路通过。

固定Trivy0.66.0先用旧缓存扫描，后从官方镜像源更新漏洞库并重新扫描，最终HIGH/CRITICAL共0，严格退出码0。CycloneDX核验14 Debian＋143 Python组件。生成证据位于Git忽略的`data/acceptance/head0037-20261026.cdx.json`与`head0037-vuln-20261026.json`，扫描摘要关联上述镜像ID。资源验证脚本实际证明OOM退出137/oom_killed、15子进程达到PIDs限额、tmpfs限额及CPU节流；没有修改其他容器。专用PG容器已清理，验证镜像保留供后续复验。

本批是带缓存构建和单宿主验证，尚未对新镜像进行无缓存OCI逐blob重建比对、签名/发布或托管CI，也不是双Linux部署/HA、完整加密恢复或真实模型验收。S2–S6目标仍未完成。

# 2026-10-26 head0037独立OCI重建补验

固定基础镜像摘要、锁依赖、`SOURCE_DATE_EPOCH=0`与`rewrite-timestamp=true`，禁用provenance分别导出缓存允许和显式`--no-cache` OCI。`verify_image_rebuild.py`重新计算manifest及全部引用config/layer blob的SHA-256，两个manifest均为 `sha256:20d167f6472bd52568eba86af1d8c092b3de6cad3588dc49d0ea8d03bff175e9`。这与上一非规范时间戳、带attestation镜像ID不同，不能混用摘要。

两份原始OCI位于Git忽略的`data/acceptance/head0037-cached.oci.tar`、`head0037-nocache.oci.tar`。固定Trivy0.66.0对第一份OCI解包目录独立扫描，使用2026-10-26 07:15 UTC更新的漏洞库，HIGH/CRITICAL=0，严格退出码0；对应JSON为`head0037-oci-vuln.json`。CycloneDX `head0037-oci.cdx.json`库存校验14 Debian＋143 Python组件。最初直接传OCI tar被该版Trivy拒绝，改为已校验archive的解包目录后通过；扫描曾报告第三方SBOM提示，最终仍检测Debian20包及Python库存，不把提示隐藏为无告警。

本批只使用同一Docker宿主，第一侧因新增时间戳参数实际也重新安装了依赖；第二侧明确禁用构建缓存。通过的是两次独立构建的OCI manifest/blob一致，不是所有外层tar字节或跨宿主重建。未签名、未发布、未运行托管CI或正式部署；S2整体仍不勾选。保留OCI和扫描文件供后续复核，没有清理用户镜像或构建缓存。

# 2026-10-26 head0037加密灰度备份恢复

本机重新定位已有隔离测试PostgreSQL客户端，执行`pg_dump --version`确认16.10，与独立回环PG16实例同主版本。`tests/test_rag_backup_restore.py`连同完整`test_logical_backup.py`真实运行 **19 passed/0 skipped（18.48秒）**，不是SQL直接更新状态的替代验收。

新增场景：当前head0037上以双人已批准、running灰度及真实持久化candidate run路由创建加密逻辑备份，在全新随机隔离库中解密、pg_restore及语义manifest核验。恢复后独立RecoveryHold仍阻止运行；灰度转recovery_review、流量0、版本递增；release审批转recovery_review且保留原审批摘要和定义证据；plan、阶段时钟及run.rag_route原样保留。恢复不因保留证据而允许旧审批重放。原有18项覆盖加密认证/篡改、文件及身份、空库要求、CLI和导出快照一致性。

测试只用合成账号/知识/临时随机加密key，未读取历史模型密钥或生产数据；备份与恢复目标均位于pytest隔离临时目录。首轮新用例将key与备份父目录放在一起，被既有安全门禁拒绝，调整为独立keys目录后通过。CI真实PG零跳过组已加入新专项，托管CI未在这里执行。专用PG及测试库已清理。该场景不含真实外部对象存储、PITR、跨主机密钥恢复或正式RPO/RTO，不将其扩大为全部灾备完成。

# 2026-10-26 Owner灰度控制入口

新增`scripts/rag_rollout_control.py`，提供observe/start/advance/pause/withdraw/prepare-reversion，复用私有control文件、精确schema门禁和既有事务；变更必须显式confirm，旧回执不追随，不自动审批或恢复流量。单元12 passed（0.22秒），真实PG阶段推进组6 passed（28.55秒），含独立进程CLI未确认拒绝、实际advance至500bps、旧receipt暂停失败、更新receipt后暂停归零。完整无外部服务回归458 passed/368 skipped（28.70秒），不将跳过当通过。专用PG已清理。

操作边界见[控制CLI](rag-control-cli.md)。前批0037验证镜像及OCI不包含此后新增CLI，不能据镜像旧摘要宣称新入口已镜像内验收。正式审批交互/可信证据来源、生产管理UI和运行部署仍缺；S2–S6不勾选。

# 2026-10-26 跨模块真实PG/Redis回归

按当前CI真实集成组，在本机新建固定digest、只绑定随机回环端口的PG16/pgvector与Redis7.4.6，提供已核验16.10客户端，运行数据库迁移、耐久聊天/知识、容量/动作、追踪、Redis准入、维护、审批/灰度/路由/观察/推进/健康/回退/加密恢复及用量预算费用等24个测试文件。结果 **210 passed/0 skipped/1 deselected（508.10秒）**，deselected为显式排除的真实本地模型标记，不算通过。JUnit保存于Git忽略的`data/acceptance/wide-pg-redis-20261026.xml`，核对failures/errors/skipped均0。

同批前端40项通过、生产构建成功。所有测试使用合成模型/账号/报价，不读取历史模型密钥；不证明真实供应商账单、独立业务盲测或双机HA。PG残留cs_test数据库查询为0后，精确清理本批两个容器，未碰其他应用服务。未运行生产环境或远程托管CI。

新增[当前状态摘要](production-current-status.md)区分已实施与外部依赖，修正总体架构中2026-10-01基线的过时“未实现”描述。现有历史日志保留，不能把老批次限制误当当前状态，也不能把本批210项与其他重叠组相加。

# 2026-10-26 显式管理员审批入口与锁观察修复

最终无外部服务全量466 passed/369 skipped（26.49秒）；条件跳过不算通过。前批镜像不包含本入口，新制品仍须重新冻结验收。

新增`rag_release_control.py`的approve/reject/check/execute受控入口。receipt严格字段、不允许token嵌入；approve/reject仅隐藏交互终端输入，非TTY和getpass回显降级直接失败，不用历史凭据、环境或管道。操作仍由owner控制数据库连接，未提供生产审核API/角色委派，不把此CLI当作生产审批UI或独立证据来源。

单元8 passed（0.21秒）。真实PG审批服务＋入口单元最终32 passed（56.70秒），包含第一人批准仍不能execute、第二名有效指定管理员批准后执行；该测试注入合成prompt，不等于人工终端视觉验收。最初服务组22 passed/2 failed，两项失败是pg_stat_activity同事务快照导致锁等待观察超时；观察连接改AUTOCOMMIT后完整重跑通过，业务授权/锁实现未放宽。该修复确保等待证据实时，不是增加超时绕过断言。专用PG已清理。

# 2026-10-26 双Linux VM环境已建立

在用户明确授权后，用Hyper-V创建cs-s2s6-node1/node2两台Generation2 Ubuntu VM，各2 vCPU、4GiB固定内存、32GiB逻辑差分磁盘。官方镜像hash、专用受限SSH密钥、禁用密码/root登录、NoCloud初始化及双向网络验证详见[环境记录](hyperv-test-lab.md)。两台SSH真实登录及cloud-init done/errors空、两向ping 0%丢包、sshd有效配置均通过。两台已安装Python venv、Git和PG16.15客户端；未安装或调用真实在线模型。

初试Azure专用盘未开放SSH，改官方通用盘后通过。node1曾有不可取消的Hyper-V关机任务；只在权威状态确认任务已结束后才重新断电换盘，没有重启vmms、没有结束System worker或影响其他VM。临时诊断/强制worker恢复helper未使用，已移除，保留实际使用的ISO生成与新VM创建脚本；PowerShell语法检查及实际执行通过。

原始镜像、差分盘和测试访问材料均在仓库外D:\codex_project\customer_service_vm_lab_20261026；私钥内容不入文档或Git。未创建公网转发、未改现有交换机或服务。两台仍运行以供后续部署，各占4GiB，宿主不自动启动它们。当前只证明测试环境就绪，不能宣称应用双实例/PG Redis HA、网络分区或物理宿主故障验收通过；S2–S6目标未完成。

# 双VM实际应用运行与代理边界修复

两guest已实际运行API/HTTPS代理/合成ChatWorker，直接连接verify-full TLS的共享PG16.15，严格区分owner/API/Worker/backup角色；head0037迁移通过。跨guest登录Cookie/CSRF、enqueue→SSE→另一节点Last-Event-ID重放及恰一个assistant通过；停止API1期间API2完整链路仍通过，恢复后API1 HTTPS200及持久单元active已复核。不是模拟端口或仅创建VM。详细版本hash、测试runID及限制见[运行验收](two-node-runtime-acceptance.md)。

实测发现旧local-only中间件误拒绝HTTPS代理传递的客户端IP，新增默认关闭的显式代理客户端策略，仅secure AuthPolicy＋strict API-role允许；serve_api仍绑定127.0.0.1且只信任127.0.0.1转发。Windows专项48 passed；Linux专项16 passed；后续部署guard合并17 passed；最终无外部服务全量 **477 passed/369 skipped（26.32秒）**。各组重叠不累加，条件跳过不算通过。前批制品不含本次补丁，不能沿用旧镜像结论。

尚未Worker失联、自动LB/代理HA、PG Redis切主/分区、真实模型/业务/独立盲测或浏览器现场验收。服务当前在两VM内继续运行，未公开到公网；新系统单元未enable，不随guest开机启动。总目标保持未完成。

# 双guest真实Worker失联/接管与禁止盲重放

在已部署的两台Linux guest上注入两个真实SIGKILL场景：领取后、模型调用前失联由node2等待真实30秒PG租约过期后接管，fence=3、恰1次重排队、1个模型attempt/assistant/done/snapshot；调用进入且started账本已提交后失联则interrupted/lease_expired、fence=2，原1个started attempt保留、0重排队/assistant/done，恰1个interrupted/snapshot。两例旧租约append_event均以RunLeaseLost拒绝。

不是回填时间或把调用中失联当成可安全重试；真实模型未调用，不能把合成FunctionModel的进程故障当作供应商账单或公网I/O验收。runID、oracle权限与精确边界见[运行验收](two-node-runtime-acceptance.md)。两侧正常API/edge/Worker恢复active；撤销本批两个提交会话，保留账本证据；故障状态清理后跨guest HTTPS登录/CSRF/SSE/重放再次通过且只有一个assistant。

故障fixture/guard单元13 passed（0.05秒）；完整无外部服务后端 **485 passed/369 skipped（27.34秒）**。跳过不算通过，各组重叠不累加。本批未改变业务重试/租约/fencing实现，新增内容为隔离故障fixture、断言与证据。尚缺跨节点取消/实际连接故障、自动LB、PG Redis HA/分区和生产验收，S2–S6不勾选完成。

# 2026-10-27 当前代码真实组件增量

当前完整后端隔离批次在运行中发现事件保留测试使用旧6事件假设；现行完成协议已在原始token之后原子写入answer_reset和权威正文token，合计8事件。只更新测试，fixture新增完整事件类型序列和连续sequence断言，清理数量/水位/迟到cursor/并发/回滚/HTTP与lifespan仍严格验证。`tests/test_event_retention.py` 真实PG重跑 **12 passed，28.17秒**。尚在运行的全量进程在修改前已加载旧模块，它的失败不可忽略，也不能用这12项宣称全量绿色；需收齐本轮输出后再次验证当前快照。

后续独立观测组已完成：显式提供已核对的 Prometheus3.5.0/promtool 和 Alertmanager0.28.1 路径及本批隔离PG，`test_alert_delivery`、备份/日志/预算/熔断告警规则、HTTP观测与私有指标七组 **30 passed / 0 skipped，128.94秒**。这是本机真实进程/隔离接收端，不代表正式值班渠道或双guest Collector 已部署。

专用角色实例现通过 compose 独立 `postgres-roles` 服务只绑定 `[::1]:55433`，不与普通55432集群共用角色。角色测试初跑揭示准备阶段漏装 pgvector；已在生成的确切测试数据库以 DBA 身份安装扩展，再由原非超级迁移角色迁移，没有放宽业务权限或让迁移创建扩展。重跑 `test_pg_runtime_roles` **1 passed / 0 skipped，29.05秒**，覆盖迁移/应用/Worker/备份边界与撤权。并行全后端批次尚在运行，其结果不能用上述专项替代。

后续补跑唯一跳过项：从此前隔离运行时定位并验证 PostgreSQL16.10 原生工具，显式设置本批 TEST_POSTGRES_BIN/TEST_POSTGRES_URL/TEST_S3_ENDPOINT 后，`tests/test_storage_config.py::test_real_restore_fences_old_config_and_requires_matching_rebind` 为 **1 passed / 9 deselected，7.16秒**。这补齐下述联合组的存储恢复缺口：恢复后旧配置隔离、目标桶匹配及授权重绑定断言通过。没有把分批结果冒称为整组重新执行，远端对象安全兼容性仍未通过。

本批独立 `customer-service-integration` compose 环境：digest 固定 PG/pgvector、Redis 和 MinIO，端口仅 Windows 回环 55432/56379/59000、数据 tmpfs、显式合成测试凭据；未读取/复用其他项目服务。选择测试 `test_run_leases`、`test_durable_chat`、`test_durable_knowledge`、`test_knowledge_objects`、`test_object_store`、`test_storage_config`、`test_redis_admission`，真实运行 **85 passed / 1 skipped，166.41秒**。存储恢复单项带相同 PG/S3 配置及 `-rs` 复核，跳过原因 `TEST_POSTGRES_BIN unset; config restore unverified`，不计为成功。

新增 `test_real_public_access_block_capability_does_not_silently_downgrade` 在上述组启动后才加入，单独实跑 **1 passed / 19 deselected**，不冒称已包含在85项中。标准 SDK PutPublicAccessBlock 返回 MalformedXML；真实客户端下生产 readiness 拒绝该桶。其通过代表负向拒绝边界成立，不代表 MinIO 正式接入通过，不证明远端TLS、跨guest导入或公共访问阻断能力已完成。初轮测试对返回码集合假设过窄而失败，核对实际返回后将该码明确分类为“未验证/不兼容”，没有修改业务安全检查。详见对象存储操作文档。

此前快照/镜像/完整回归计数均保留为各自批次历史证据，不能当作本批新代码已进入制品或跨宿主验收。

