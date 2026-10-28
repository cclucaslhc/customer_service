# S2 收尾与 Git 交付清单

更新：2026-10-28。用户已将本轮目标从 S2–S6 改为完成 S2，完成后提交 Git。本清单按 production-roadmap.md 原始 S2 六项建立，不以已完成的测试反向缩小范围。S3–S6 不再作为本轮新增交付，但不得破坏已有实现。

## 完成标准

### 用户确认的正式联调边界（2026-10-28）

用户最新变更：**跳过真实告警送达验证**，保留三个通知接口；不再等待Gmail凭据或继续OAuth。通知协议/鉴权/安全仍实际测试，外部收件验证作为用户明确豁免项而非通过项。当前584 passed /384 skipped，真实认证Alertmanager和取消保护7 passed；最终源码已启动新OCI构建及独立空缓存重建。两个历史tracked data/backend日志仅取消Git跟踪、保留原文件；origin/main之后没有这两个日志的新提交，未改写公开历史。忽略所有data及.env*，公开推送不新增运行日志。

后续明确授权：允许使用管理界面已有DeepSeek配置做联调，真实生成已通过，详见[真实调用边界](s2-deepseek-live-acceptance.md)；首次探针用量取证失败仍保留未知。用户已允许推送现有公开GitHub仓库；推送前必须敏感检查，不推送密钥、账单原件、真实邮箱或运行材料。邮箱选Gmail，地址不入库中源码/文档，仍缺应用专用密码及正式收发证据。三个出站通知接口已实现并完成离线回归，尚非部署/送达完成，见[通知接入](alert-notification-operations.md)。新增通知模块后，前批r2应用镜像不覆盖最新源码，最终制品须重新构建/扫描/绑定证据。

最终验收按正式联调，不以隔离模拟替代。用户确认三种通知接入、Gmail为首个正式渠道，已提供收件地址但不写入Git；仍缺安全提供的SMTP凭据、接收服务部署及真实送达。用户已明确授权现有管理界面DeepSeek配置，并允许推送现有公开仓库；不包括读取环境历史密钥、发布私钥或擅自部署公网。组织发布信任身份仍须明确供应。

| 原始要求 | 已有基础 | 仍需直接核实或补齐 |
| --- | --- | --- |
| 结构化日志、脱敏、保留期、访问角色 | 白名单日志、有界出口/丢弃指标；node2 专属 journald、读取拒绝和重启复验；新建合成namespace实际到期归档清理/新日志保留通过 | 正式读取角色供应与生产容量/限速演练；加速机制测试不等于等待满14天 |
| HTTP/Agent/model/tool/RAG/database OTel，关联 request/run/attempt | 组件与真实 PG 测试；node2 原生 Collector、API→独立 ChatWorker 父子/ID，隐私过滤及故障恢复 | 正式平台/容量与知识 Worker 的本批 Collector 接线；活动文件严格7天清理未保证 |
| 低基数 Prometheus、Grafana、Alertmanager | 私有抓取、六面板真实查询/浏览器、规则和回环 firing/resolved；固定插件的Grafana修复镜像严格扫描通过 | 正式权限/持久部署与真实值班渠道送达 |
| 每次模型/Embedding/Rerank 入账、价格、未知及账单对账 | 聊天 attempt/quote/追加对账；离线 Embedding/Rerank started/finished、真实权重/tokenizer/PG回执 | 用户显式配置后的供应商账单与合同核验；离线基础设施成本未知而非0 |
| 日/租户/Run 软告警与硬限额、原子预留 | PG 原子预留、并发/未知/恢复测试；持久阈值及实际 Prometheus→Alertmanager 测试 | 正式预算/价格配置和真实渠道验收；未来在线 Embedding/Rerank 仍须单独付费门禁，不由离线策略放行 |
| 锁依赖、不可变非 root 镜像、漏洞扫描、可重建 CI | Authlib修复后的r2、0 HIGH/CRITICAL、SBOM、独立空缓存OCI逐blob一致、测试身份签名/篡改拒绝；Grafana完整镜像0 HIGH/CRITICAL | 真实托管CI、正式发布信任及跨物理宿主重建 |

“正式渠道未提供”“真实账单未核验”和“外部 CI 未运行”必须明确保留，不得用合成接收端、估算费用或本机重建证明替代。若这些要求需要用户资源/权限，报告具体缺口再确认，不虚构完成。

## 实施顺序

1. 核对当前代码与上述要求，先处理 S2 相关回归问题。
2. 完成日志/Collector/Prometheus/Grafana/Alertmanager 的受控部署装配和隔离验收。
3. 补齐收费路径账本与预算缺口、相关并发/未知/恢复测试。
4. 冻结最新源码，重建、扫描、验证制品与 CI/签名流程。
5. 汇总逐项证据；未满足要求不勾选整个 S2 完成。
6. 确认 Git 范围，检查敏感文件、制品和运行数据未被纳入，再提交并核对提交内容。

## Git 范围

用户已批准“既有实现基线＋S2收尾分笔”。基线提交 `7fd57f7` 保存此前405个源码/配置/测试/文档文件；路径检查排除运行data、密钥、环境文件、依赖目录、构建产物与大文件，密钥特征扫描未发现匹配。本收尾清单刻意未纳入基线。基线只是现状快照，不能据此声称所有阶段通过；完成S2后的第二笔提交才包含本轮收尾与最终证据。尚未完成前不创建宣称完成的第二笔提交。

## 本轮证据

### 当前汇总（2026-10-28，优先于以下历史批次）

- 新增三种通知出站接口及显式授权DeepSeek探针后，后端全量 **571 passed / 383 skipped，32.37秒**，新增离线专项13 passed。真实合成生成1次成功（21输入/3输出Token），首次探针用量取证失败保留未知；账单与PG受保护调用尚未完成。较早556/552为历史源码范围。新app模块尚未进入最终重建制品，旧r2不作当前全代码扫描证明。

- 显式接入隔离 PostgreSQL、Redis、S3、Prometheus、Alertmanager 及本地模型目录的 S2 跨模块组：**175 passed / 0 skipped，284.91 秒**。JUnit 报告在仓库外 `D:/codex_project/customer_service_vm_lab_20261026/s2-final-integration-20261028.xml`。这是所列 S2 测试组，不是全仓库集成全量。
- 最新普通后端全量：**556 passed / 383 skipped，27.23 秒**；最终CI步骤顺序变动后静态契约另 **5 passed**。跳过项没有被计为通过；真实固定权重计量另有 **1 passed / 18.78 秒**，不与全量相加。
- 应用 r2 制品严格扫描为 0 HIGH/CRITICAL，同宿主独立空缓存重建逐 blob 一致，测试身份签名及篡改拒绝通过；正式发布身份和远端 CI 未验证。
- 原生 node2 journald 权限/脱敏/重启、Collector 权限与真实 API→独立 ChatWorker 链路已验证；留存期限未实际等待到期，不宣称生产容量通过。
- Grafana 旧固定镜像和普通 latest 仍不准使用。后续已完成官方 distroless-slim＋固定SHA256插件的专用制品：**0 HIGH/CRITICAL**，覆盖可执行插件，六面板真实查询及浏览器通过；CI新增对应构建/扫描/查询，详见 Grafana 验收记录。旧合成12.3.1测试栈已精确删除，截图/JSON证据保留，修复版栈接替；未移除用户服务或业务数据。
- 仍缺真实值班渠道、供应商账单/合同核验、正式发布信任身份及托管 CI 运行。没有授权外部发布或访问生产凭据；整个 S2 尚不能据隔离测试自动勾选完成。
- 监控补充：Prometheus3.15.1与Collector0.164.0官方固定摘要均扫描0 HIGH/CRITICAL。新Prometheus搭配六面板真实查询/401通过；新Collector配置/隐私/暂停恢复通过，并已保留旧二进制升级node2，升级后257 Span的真实API→ChatWorker对话定向审计通过。CI最终制品上传置于应用与监控门禁之后。当前服务与回退边界见各自验收页。

### 历史过程记录

- 实际固定Embedding/Reranker权重与PG计量对账1 passed；最新应用r2镜像资源限制、复扫0 HIGH/CRITICAL、独立空缓存逐blob重建一致及测试身份签名/篡改拒绝通过。首次Authlib HIGH已修复，正式发布身份与托管CI仍未验证，见[制品验收](s2-image-acceptance.md)。

- 本地推理只读聚合已装配到私有metrics，失败不输出假零、未知Token单独计数；真实PG/私有指标专项、真实promtool触发/恢复及Prometheus→Alertmanager→回环webhook firing/resolved通过。真实值班渠道、生产容量与API/Worker现场账本采集仍不冒充完成。

- 离线Embedding/Rerank与索引构建已接入追加式回执和tokenizer观测路径，原租户/route复查、未知保留、最小权限角色通过；见[本地推理入账](s2-local-inference-accounting.md)。实际权重复验、读取汇总/未确认告警、成本报价及供应商账单仍不冒充完成。

- 真实Collector validate、三层Span落盘父子/ID、额外字段/事件/状态隐私过滤、暂停时导出失败及恢复后新链路落盘通过。后续已原生部署node2专用Collector账户，真实API→PG队列→独立ChatWorker/PydanticAI→模型/数据库链路关联落盘，状态目录0700、非提权用户读取拒绝，见[Collector验收](s2-collector-acceptance.md)。模型仍是合成，查询平台/容量/严格留存和知识Worker接线不冒充完成。

- 日志已实际接入 node2 专属 journald namespace，query/header/path合成敏感标记不入日志、非提权OS用户读取拒绝、API重启后复验通过。修复默认UID拆分权限缺口；相关测试12 passed，见[日志部署](s2-log-deployment.md)。14天到期和平台读取角色不冒充已验收。

- Grafana 六面板已完成真实 Grafana→Prometheus→当前私有指标出口查询和浏览器渲染，未登录API拒绝及预算无数据不伪装健康通过；详见 [Grafana验收](s2-grafana-acceptance.md)。尚非完整监控部署、权限/留存或新镜像批准。

- 模型追踪及供应链静态契约：17 passed，5.53秒。该组不是全部 S2 或制品实测证明。
- 较早完整后端批次最终输出未取得；后续已重新完成上述 549/383 批次，不再把早期未取得输出当作当前阻塞。

工具层现存旧自动目标不能直接覆盖：create_goal 返回已有 unfinished goal，本轮未虚假完成旧目标以绕过。执行范围以用户最新明确要求为准；若自动续行仍携带旧 S2–S6 文本，不据此恢复已取消的扩展范围。
