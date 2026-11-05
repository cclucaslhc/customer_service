# S2 最终验收账本

## 2026-11-05 当前状态

监控归档补充重扫：固定Grype0.121.0/11月5日有效库扫描Collector及Grafana归档均无HIGH/CRITICAL；Alertmanager旧base归档检出四项系统库HIGH。该程序的两个二进制均CGO_ENABLED=0，不需要libc/zlib，故运行基底改为官方固定摘要distroless/static-debian13 nonroot，真实移除未使用动态库而非隐藏包或忽略告警；新构建扫描及实际接收器测试仍待CI，不用9 passed/1 skipped的本机专项冒充真实接收端通过。

本次修复后独立Python3.12全量616 passed/384 skipped/6 warnings（36.93秒）；当日重新运行完整锁导出的pip-audit为0已知漏洞。跳过不算通过，Grype报告中低/中危不伪装为不存在；正式阻断门槛仍为所有未解决HIGH/CRITICAL。

S2仍在验收，不宣布完成。CI 41461618079四测试作业全部通过；实际OCI独立重建、库存和Trivy 0 HIGH/CRITICAL通过，原生扫描通过后被校验器的来源类型假设误拒绝。真实SBOM下载与摘要已核查，正在修复容器SBOM来源绑定。新库另检出仅影响未安装SQLite CLI的CVE-2026-119586：增加严格限定的代码不存在判定、实际CLI/恢复入口缺失探针及原始发现/VEX/发布清单绑定，不将它伪装成升级修复或原始0发现。详细证据及历史边界见[SQLite安全记录](s2-sqlite-security.md)。真实外部送达与账单继续按用户决定未验证。最终CI、OIDC及main交付仍待完成。

## 2026-11-02 最新复核（优先于下文历史批次）

后续验收分支`e4b7252d443c62c511b656232cb8f1319aec1223`的CI [41458328755](https://github.com/cclucaslhc/customer_service/actions/runs/41458328755)：后端（含审计）、前端、NGINX通过；镜像独立重建/SBOM通过后，扫描发现Debian SQLite `CVE-2026-119373` HIGH而失败。Debian尚无修复包；SQLite官方3.54.0已修复。现改为固定上游源码/官方sqlite3.c SHA3与固定GCC镜像构建实际库，保留FTS5，并增加实际加载/源ID/负rowid官方回归；清单改为真实上游组件而非伪造Debian版本，另加Grype原生CPE扫描并绑定原始报告。尚待真实镜像验收，不将源码修复当扫描成功。本次修改后Python3.12全量603 passed/384 skipped/6 warnings（31.49秒），相关29 passed；旧592组不覆盖此新增变更。

用户再次明确本轮跳过真实账单对账；与真实外部告警送达一样保留“未验证”，不计为通过。

- 支持Python最低版本调整为3.11，生产继续Python3.12；原3.10虚拟环境未覆盖。独立Python3.12.15环境按锁安装，145个已安装包依赖一致；全量592 passed /384 skipped /9 warnings，49.41秒。跳过不算通过，警告为jieba转义及PydanticAI旧httpx接口弃用。
- CI 41452849991发现joserfc、pyasn1、pypdf新漏洞，已分别升级1.7.6、0.6.5、6.21.4。Pillow13.0.0、dnspython2.9.1的安全版本要求Python3.11+，移除3.10旧条件依赖；新锁导出pip-audit为0已知漏洞。旧失败报告保留，不能当作新镜像已通过扫描。
- 完整Collector Go1.26.10补丁版已在本机实际通过255组件比对、配置/隐私过滤、3项导出失败及恢复和完整扫描0 HIGH/CRITICAL。manifest `sha256:e2746ad8752458a758eacdbfdb33280b9710640137d55d63b2b7a442d4680d31`，二进制SHA256 `7e706ea60c0979b76243520337ac45be6b3392a6758f89b4cff9803a0b6510ba`；不是失败的Go1.27候选。
- 两台原隔离VM已恢复，node2运行上述二进制。新合成Run `bdae6566-6f1b-40fa-9ee7-1226d62fa6bc`、Trace `d0dfa09c9fce11c3974ca709b195f24a`：257 Span、API/Worker/Agent/model/db关联、唯一attempt、目录0700；HTTPS/CSRF/SSE及重放通过。为固定消费节点临时停止node1 Worker后已恢复；这是单侧复验，不是新HA证明，未调用在线模型。
- CI旧45分钟上限取消后已改为120分钟有界预算，官方Go代理优先并保留校验。最新修复仍须新CI全绿、真实OIDC验签/字节绑定及最终main提交；S2尚未完成。

仓库外证据位于实验室目录：`pip-audit-311plus-20261102.json`、`s2-r2-output-20261028/collector-security-go126-20261029.json`；Collector功能证据目录为`C:/Users/56226/AppData/Local/Temp/cs-s2-collector-763db35eee2a-k_9y4n2t/`。

更新：2026-10-29。当前状态：**验收进行中，未宣布完成**。本页仅对应用户收窄后的S2；不把S3–S7或整体生产上线混为本轮完成标准。

2026-11-02恢复核查：Go1.26.10补丁镜像此前已通过全部255项组件比对、真实Collector过滤/暂停恢复及完整0 HIGH/CRITICAL扫描；隔离node2可回退升级也已成功。运行40879749202的四测试作业通过，但制品作业在完整Collector依赖下载中被45分钟作业上限取消，签名/最终制品未生成，不算全绿。已为完整应用＋监控流水线配置120分钟有界预算，GitHub runner优先官方Go代理、保留国内回退和签名校验；没有降低安全/功能门槛。测试VM在间隔期间关闭，已恢复原两台VM，当前正在复核网络/服务与新一轮CI。

## 用户明确调整

- 保留企业微信、钉钉、邮箱三个通知接口；跳过真实外部告警送达。不再要求Gmail密码/OAuth，8011安全配置页已停止。此项为豁免，不是送达通过。
- 跳过真实DeepSeek账单对账。实际生成和Token回执仍验证；首次探针取证失败保留未知，费用不能伪造为0。此项为豁免，不是供应商账单通过。
- 授权使用管理UI已有DeepSeek配置；未扫描环境历史Key，也未自动复制密钥到测试VM/PG。
- 授权推送现有公开GitHub仓库。先用验收分支跑CI；main保留基线7fd57f7，待收尾后第二笔提交。不要求用户合并。

## 六项门槛与证据

| 要求 | 当前证明 | 当前判定 |
| --- | --- | --- |
| 结构化日志、脱敏、留存、读取角色 | 白名单出口；node2 journald命名空间、非提权拒绝、重启与加速到期归档清理；14day/512MiB配置受限且无全局转发 | 机制/隔离实机已验证；不声称已等待14天或完成生产负载认证 |
| HTTP/Agent/model/tool/RAG/database OTel及关联ID | 私有Collector白名单/过滤/故障恢复；实际API→独立ChatWorker257 Span同链及唯一attempt；知识/对象/SQL真实组件专项 | 已有实机和组件证据；不声称已有生产Tempo查询平台 |
| 低基数Prometheus/Grafana/Alertmanager | 私有出口、真实查询/六面板/401/NO DATA；真实认证接收端firing/resolved与取消并发保护；Collector完整255组件补丁版功能/扫描及升级后257 Span通过 | 隔离协议/机制已验证；最终CI仍须通过，外部送达按用户豁免 |
| 模型/Embedding/Rerank用量、版本价格、未知与对账 | PG追加账本；真实固定离线权重/tokenizer计量；另一次真实DeepSeek input21/output3与PG终态精确匹配、预调用持久预留及第二次预算拒绝通过；首次取证错误未知保留 | 核心机制/真实SDK已验证；测试价格非合同，真实账单按用户豁免，离线基础设施金额仍NULL |
| 日/租户/Run预算、软硬门禁与原子预留 | 真实隔离PG并发/未知/恢复和角色测试；实际Prometheus→Alertmanager规则联动；离线推理不伪造在线收费 | 已有真实PG/规则证据，不将未知费用当0、不承诺未来在线Embedding可旁路预算 |
| 锁依赖、非root/不可变、扫描、可重建CI制品 | OTel1.45.1、Vite8.3.5；Python安全锁及独立3.12回归；CI实际独立OCI一致，但最新SQLite HIGH修复后须完整复验，OIDC证明已接线 | **待当前CI全绿、OIDC验证及最终Git交付** |

## 历史批次与本机证据（当前以页首为准）

- OTel升级后的跨模块集成：192 passed /0 skipped，276.39秒，仓库外JUnit `s2-after-ci-fixes-20261029.xml`。
- 最新普通后端592 passed /384 skipped（31.26秒），覆盖挂载/UID、完整Collector清单及重建契约；供应链/发布证据/Grafana前批专项21 passed（0.72秒）。前端40 passed、build成功、npm audit 0。未配置外部环境的跳过不计通过。
- 当前应用OCI manifest `sha256:5d9fae63aec890b1dc5ce4d46d08cac8db58601815d17580ee660f99e1f7d8ae`，config `sha256:17238a5c0c91c77066a53e610b5fbe49b8667debdda4ba3a9127077a2091a4ee`。默认/独立no-cache builder逐blob相同；0 HIGH/CRITICAL；SBOM14 Debian/145 Python。文件为实验室`s2-ci-fixed{-rebuild}-20261029.oci.tar`，报告在`s2-r2-output-20261028/s2-ci-fixed-*-20261029.*`。
- GitHub CI首轮暴露并已修复OTel新CVE、Vite高危、无关MinIO拉取、代理就绪竞态，以及Linux路径/导入/临时挂载所有权。没有关闭审计、改root或降低沙箱要求来通过。各失败批次不作最终通过证据。
- 当前验收分支源码`14f3e77359617fd5cb855149950c4f241798e3ed`，运行[40725358853](https://github.com/cclucaslhc/customer_service/actions/runs/40725358853)。前端、后端、NGINX通过，PG/Redis和制品作业仍在执行；这不是最终全绿声明。
- 后续该轮真实Worker OOM/before_commit/after_commit三项通过（54.28秒）；停在Docker默认driver不支持OCI exporter。已修复为两个独立、固定摘要的BuildKit docker-container builder并推送`92068b2e32a9c012f3d83f3b5ac25513cd86602b`。旧轮失败不作成功证明，也没有通过禁用沙箱、改root或跳过测试来绕过。
- 该修复轮[40726335704](https://github.com/cclucaslhc/customer_service/actions/runs/40726335704)已取得后端、前端、NGINX、PG/Redis四作业成功。真实PG/Redis前轮232 passed/1显式本地权重deselected/0 skipped（859.57秒），不与本机192组相加。镜像作业的实际沙箱/CLI/OOM、独立OCI重建、SBOM、应用扫描与清单绑定均通过，随后Grafana完整镜像在新漏洞库中检出`CVE-2026-116587`（go-jose/v4 4.1.4，修复4.1.6），因此该作业失败，未签发OIDC或发布最终制品。
- 本机以新独立漏洞库缓存复现该Grafana单项HIGH，旧0 HIGH结论只属于旧库时间点。已基于官方固定v13.2.5源码commit重建服务端并升级go-jose至4.1.6，版本13.2.5+cs.s2；模块校验、编译、完整镜像新库扫描均通过（0 HIGH/CRITICAL）。保留官方UI和固定Prometheus插件，不删认证组件或扫描清单。源码/模块均有哈希或Go checksum数据库验证；国内代理EOF后启用可信官方源fallback，校验未关闭。修复镜像六查询、401和浏览器渲染已通过；新远端CI仍待执行。
- 当前验收分支源码`69a6b5c6823d0997e5d2abedd5dd216329858b45`，运行[40788755603](https://github.com/cclucaslhc/customer_service/actions/runs/40788755603)。包含Grafana安全重建和显式OIDC信任策略，仍在运行；不把旧轮四作业成功合并成新轮全绿。
- 本轮已确认后端、前端、NGINX及PG/Redis四作业成功。PG/Redis最终日志232 passed/1显式本地权重deselected/0 skipped（748.31秒）。已下载SBOM归档并核对GitHub返回SHA256，实际库存14 Debian/145 Python；CI config为`sha256:9e6089bf353a3985ff583b7fe6868a987f0a851e21a5113614e80a7b03ee3cf8`，不同于本机制品，不混用摘要。该轮Grafana构建及新库扫描成功（0 HIGH/CRITICAL），随后数据源健康检查超时而失败，未签发最终证明。
- 已在真实Linux容器权限下复现Prometheus无法穿过宿主0700证据目录；改为单独只读绑定两份合成配置后，同一默认非root镜像正常就绪，目录未放宽。Collector同类输出权限问题也被复现，改用非root宿主UID/GID后在0700目录持久化真实三层Span；首次探针仅查空文件过早失败，追加真实producer后才通过，不把该首次探针计为成功。修复后的完整六查询/401、Collector过滤/暂停恢复再次通过，新增身份/挂载回归通过；待新CI验证。
- 新验收源码`b083e2fcc3753d1632da329b6c5f5ebd9eac552d`，运行[40791886920](https://github.com/cclucaslhc/customer_service/actions/runs/40791886920)已创建，尚未完成。不将此前四作业成功与本轮结果合并成全绿。
- 该轮后端/前端/NGINX/PG-Redis通过（PG232 passed/1 deselected/0 skipped，542.26秒），Grafana真实六查询与401已通过，随后Collector新库扫描发现`CVE-2026-116587`（go-jose4.1.5→4.1.6）而失败，未签发证明。最新本机独立新库复现单项HIGH；官方最新0.164.0尚未修复。现按官方固定源码commit及完整组件manifest、固定OBI生成源码、启用Go checksum数据库重建，不删认证或其他组件；仍在构建，不能声称修复完成。
- 已补另一次真实DeepSeek→生产ObservedModel/PG账本及预算链路：1 passed/0 skipped（5.69秒），input21/output3，预调用started与预留已提交、Token逐项匹配，第二次在发送前拒绝。密钥未复制，合成价格不当合同，首次未知不被覆盖；详见[真实调用记录](s2-deepseek-live-acceptance.md)。
- 完整Collector补丁候选`568bbb15b04b146001781148194ba6eff8198c53`已推送，[40874823586](https://github.com/cclucaslhc/customer_service/actions/runs/40874823586)与本机构建并行验收，尚未完成。CI只在完整重建、严格扫描及真实Collector/接收器功能门禁后签发证明，不以静态契约18 passed代替实际二进制结果。
- 本机首轮完整Collector构建在依赖校验阶段因goproxy.cn的sumdb接口EOF退出，未产生通过制品；已改为明确直连官方`sum.golang.org`（HTTP200已核对），保持签名校验并加入限定本构建的依赖/编译缓存。新构建仍在运行。另加入实际组件清单比对，包含108 receivers、36 processors、48 exporters、41 extensions、13 connectors、9 providers；只有与官方全部255项及组件模块一致才通过，不删认证扩展来躲避扫描。Grafana/Alertmanager用最新库复扫仍0 HIGH/CRITICAL。
- 当前完整清单/官方校验候选`08e5f1ce74ea0aaea2b346a5d4184b9d7309deac`，CI [40876670521](https://github.com/cclucaslhc/customer_service/actions/runs/40876670521)正在执行。本机重建已进入Getting go modules，未完成编译/扫描，旧候选运行不替代本轮结果。
- 本机Go1.27.3补丁版随后编译成功、完整扫描0 HIGH/CRITICAL，但`--version`/`components`在旧SermoDigital/jose初始化时panic，功能失败，**不准作为完成制品**。失败manifest `sha256:e70fc02fd1fcd150ac17a1fe7ab6babdf93097f06a69cda286adcb885106b66b`，保留原归档及报告。核对官方原二进制实际Go1.26.10与go.dev当前受支持稳定补丁列表后，改用Go1.26.10固定摘要重建；不删组件、不关闭密码学保护，go-jose4.1.6仍保留，新编译正在运行。
- Go1.26.10兼容候选`45cb0d4af6fc6dcd59233c2db95e6280a25c98a3`，CI [40879749202](https://github.com/cclucaslhc/customer_service/actions/runs/40879749202)已启动；本机已复用校验缓存、再次通过全部模块校验并进入编译，尚未取得运行通过。旧Go1.27候选不作为本次发布依据。
- 本轮续查node2：API/edge/Worker/Collector四服务active；Collector目录0700、专用cs-s2-otel属主，非提权读取traces.json被拒绝。Git新增/修改候选没有data、环境文件、私钥、数据库或归档；两份历史tracked日志仅取消跟踪，本机文件仍保留。

## 发布信任

新增GitHub OIDC构建证明只在push作业、全部安全/功能门禁后为绑定清单签发。没有生成或复用长期发布私钥；消费者须校验仓库、工作流、source-ref/source-digest、GitHub托管runner及SLSA类型，再执行`ops/release/evidence.py check-binding`核对OCI/SBOM/扫描原始字节。只有签名有效或只运行check-binding均不足以建立完整信任。当前实际证明/下载验证仍待该CI成功，不使用测试Ed25519身份冒充正式发布信任。

最终main提交与其CI成功前，不勾选完整S2。不宣称已部署生产、双物理机HA、真实业务工具或RAG盲测通过。
