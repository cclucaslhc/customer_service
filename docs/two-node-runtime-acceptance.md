# 双guest真实服务部署与当前验收边界

本批沿用用户授权的两台Hyper-V Ubuntu测试VM，资源与镜像来源见[环境记录](hyperv-test-lab.md)。不是生产环境、不是不同物理主机；全部用户、问题、模型响应和数据库凭据均为本批合成材料。

## 已运行的拓扑

- node1（当前172.24.168.13）：PostgreSQL16.15＋pgvector0.6.0；API、NGINX HTTPS和合成模型ChatWorker。
- node2（当前172.24.168.125）：API、NGINX HTTPS和合成模型ChatWorker，直接跨guest连接node1的PG。
- PG只监听loopback和node1私有IP，HBA仅允许两个guest IP及loopback的指定测试库/角色TLS连接；应用URL强制sslmode=verify-full并使用本批7天测试CA核验。不是SSL关闭或忽略证书的连接。
- PG实际pg_stat_activity/pg_stat_ssl联合核验：两个guest各4条当前连接，全部ssl=true、TLSv1.3；不以“配置了TLS”代替真实链路证据。
- 数据库角色owner/API/Worker/backup各自NOINHERIT、非超级用户，当前head0037迁移及正式grant-runtime脚本已执行；API/Worker启动均严格检查运行角色。
- API仍只监听127.0.0.1:8000，只有同guest的NGINX接入HTTPS443；canonical origin为https://cs-lab.internal。未修改Windows hosts、系统DNS、现有交换机或公网转发。测试客户端明确指定内部IP、正确Host/Origin并验证证书。
- 三个cs-lab-api/edge/worker专用systemd单元在两台均active、Transient=no；未enable，不随guest启动。PG是单主单点，Redis仅安装在node1、尚未接入本批准入策略；没有PG/Redis HA结论。

API使用生产serve_api.py入口；Worker使用tests/fixtures/linux_lab_worker.py，在真实ChatWorker执行链上注入FunctionModel，且校验固定guest名称、初始化marker、测试库名、远端IP及verify-full。没有放宽生产chat_worker.py的synthetic loopback门禁，也没有连接真实在线模型。OS账户当前共用labops，仅数据库角色分离，不宣称生产OS/KMS/凭据隔离完成。

## 版本与依赖证据

初始源码白名单归档SHA256为`2e1ba411171ecf46ebda7c8339fc3d9c02aa0de54fd0f70b7f8cdf8ac1e2f4e6`；只含app/scripts/migrations/ops/tests/frontend构建及锁文件，不含data、.env、历史密钥或Git目录。两台均核对后解包。

海外uv下载缓慢，明确终止本批两个下载进程后，由现有uv.lock导出全闭包哈希清单，以清华镜像和--require-hashes安装。--no-deps避免再解析被有意替换的GUI OpenCV；保留与Dockerfile一致的锁定headless替代，不生成新的依赖版本。142包在两台安装完成；不是新镜像的字节重建证明。

真实部署发现并修复代理客户端IP被旧local-only中间件拒绝的问题。默认create_app仍拒绝远端和伪造X-Forwarded-*；仅显式allow_authenticated_proxy_clients=True、secure AuthPolicy及strict API数据库角色同时具备时允许代理客户端IP。生产serve_api入口开启该选项，但绑定127.0.0.1、可信转发源仅127.0.0.1、Host白名单、TLS/Cookie/CSRF与资源鉴权都不放宽。

该修复已同步两guest，Windows与node2实际文件SHA256一致：app/main.py=`22e82940ecfdd4ba920b6d42eed38a92b478a6716ebc9ecd606bdc80e0cdcaad`；scripts/serve_api.py=`e46e6f975bf5d4785089f735cafc8e5b4e66e119deea56258c6380eec7a5cde3`。这是初始归档之后的补丁，不声称仍与原归档整体相同；前批OCI镜像也不含此补丁。

## 已实际通过

1. 两guest API和HTTPS边缘启动，未登录请求返回401，证书核验成功。
2. node1登录、node2复用同一Cookie和CSRF；node2入队、node1消费SSE、node2按Last-Event-ID重放，history恰一条user＋一条assistant、只有一次done；run=`1d795ffa-f25c-4cc0-bfd1-ffa95e6376af`。
3. 停止node1 API后，node2仍完成登录/入队/SSE/history，只有一个assistant；run=`fe0e6f8e-4435-42b0-8f2b-86a5f5bf315a`。这是显式选择存活入口，不是自动LB/VIP切换。
4. 初用transient单元stop后被systemd回收，已改专用已安装单元；Linux systemd-analyze verify通过。再次停止node1 API时，node1边缘返回502、node2返回200；恢复后独立核验node1单元active、Transient=no及HTTPS200。
5. Windows针对代理边界/API入口/客服基础48项通过；Linux代理边界/入口/部署guard首批16项通过，新增持久单元guard后Windows相关17项通过。各组重叠不累加。

空会话ID在首次enqueue前不落库是既有设计，探针已调整为先跨节点校验/api/auth/me、首次入队后再校验会话；未将预期404改成业务成功。API重启active不等于ready，初轮出现的临时502不算通过，后续实际TLS响应才作为证据。

## 跨guest Worker失联与迟到写入（已实际注入）

固定使用现有共享TLS PG、两个真实guest与数据库30秒租约，不回填时间、不缩短生产租约。先停两侧正常Worker；只在node1启动本批故障Worker，以SIGKILL真实终止其进程，再由node2正常Worker处理。systemd证据均为ExecMainCode=2、ExecMainStatus=9。API进程及其reaper保持运行。

| 失联点 | 最终状态/fence | 模型attempt | 助手/done | 自动重排队 | 旧租约迟到写入 |
| --- | --- | --- | --- | --- | --- |
| 领取后、模型请求前 | complete / 3 | 恰1个，由接管者创建 | 各1 | 恰1次 | RunLeaseLost拒绝 |
| FunctionModel请求已进入、started账本已提交 | interrupted / 2 | 保留原1个started未知结果 | 均0 | 0次 | RunLeaseLost拒绝 |

首例run=`eee3331d-910b-49b4-aea7-ca2f7b54bfa5`；真实生成内容的节点标识核对为node2，仅一个终态snapshot。第二例run=`41f22c56-ecd1-4d15-9c8b-5ed0716f8948`；杀进程后即时观察到running/fence1/started attempt1，真实过期后error_code=lease_expired、一个interrupted及一个snapshot，没有伪造done或把未知外部结果当成功。FunctionModel是合成执行，并非真实供应商I/O或计费，但执行使用真实ChatWorker、用量账本和PG租约路径。

状态oracle使用node1本批测试库的本机受控DBA连接，只读取显式UUID run的计数，未修改业务表或时钟；不据此声称普通API用户拥有DBA读取权限。提交由受控fixture直接调用认证仓库，不是又一次HTTP登录/准入测试；HTTP跨guest路径由前节单独证明。旧Worker的delegated receipt只留在guest私有文件，未打印；两例迟到写入被拒绝后，撤销对应合成提交会话，未删除历史证据。

故障单元失败状态已在记录证据后清理，两个正常Worker及API/边缘均恢复active。恢复后的真实HTTPS/Cookie/CSRF/SSE/重放复验通过，run=`2d1daaa0-b966-4c84-bd47-d25e5a7c052c`，仍仅一个助手消息。无业务代码在本批为通过测试而放宽重试或fencing规则。

## 跨节点取消与真实 SSE 订阅断开

只在 node1 启动延迟 8 秒的合成 Worker，两个正常 Worker 暂停；探针使用真实 TLS、登录 Cookie 和 CSRF，并在结束时 logout。没有真实供应商调用。

- 运行中取消：run=`1f77c030-b091-485e-b61a-bc4a38a12d5a`。node2 入队，node1 执行并收到 provider frame 后，由 node2 取消。约 2.027 秒后 interrupted；数据库核对 attempt=1、assistant=0、done=0、interrupted=1、snapshot=1、requeue=0。此耗时仅为该合成样本，不是生产 SLO。当前 error_code 为 `lease_lost`：取消使租约续期被拒绝，watcher 将其统一分类为失租；停止执行的安全性通过，但用户取消与实际失租的原因分类仍待修正，不宣称该问题已修复。
- 订阅断线：run=`750f9170-22ac-430d-b1dd-c1565e6bf2a7`。node1 收到 provider frame、确认 running 后，主动 shutdown 尚未完成的 TLS SSE socket。node2 snapshot 仍为 running 且 cancel_requested=False；携带 Last-Event-ID 从 node2 重连，返回事件 ID 严格大于 cursor、单调无重复。最终 attempt=1、assistant=1、done=1、snapshot=1、requeue=0。证明订阅者断开不取消后台任务，并可跨 API 恢复；不证明网络黑洞、分区或自动负载切换。

测试后已停止临时延迟 Worker 并恢复正常单元。本地完整后端回归该批为 487 passed / 369 skipped，跳过项不是通过证据。

## 2026-10-27 运行状态复核与恢复

重新通过专用 SSH 检查时，两侧 API/edge 及 node1 Worker active，但 node2 Worker 为 failed。systemd 记录退出码 1，日志仅记录 `OperationalError`；现有日志不能证明具体数据库/网络根因，不推断为取消测试导致。该测试单元配置 Restart=no，故没有自行恢复。

显式启动既有 node2 Worker 后返回 active；随后真实跨节点 HTTPS/Cookie/CSRF、入队、SSE、重放及单助手消息探针再次通过，run=`6d3f5a78-71d9-4dce-bd50-0aae0f38b7f1`。这证明恢复后的链路可用，不证明故障根因消除或自动恢复完成。需继续完善可恢复数据库故障处理、进程监管与故障注入验收。

后续已修复轮询恢复边界：清理过期租约、清理待领取队列、领取入口遇到 OperationalError / InterfaceError / 数据库连接池超时，均停止本轮并按 1/2/4/8/16/30 秒封顶退避；成功轮询重置退避。等待可由停止信号中断。未放宽执行租约，没有自动重放已领取任务；编程/模式错误不作为临时故障隐藏，启动配置检查仍快速失败。日志只记录异常类型，不输出 SQL、参数或连接串。

补丁同步两 guest、重启 Worker 后，在 node1 专用测试 PG cluster 上真实执行 stop/start（停库约 5 秒，设置退出恢复 trap）。两侧 Worker 均存活：node1 MainPID 前后为 21344，node2 为 17588，恢复后均 active；node2 日志明确出现 3 次 OperationalError 退避。随后跨节点 TLS/登录/CSRF/入队/SSE/重放再次通过，run=`99d56a48-e999-4706-be6e-f34db7ca401b`，单助手消息。该证据证明空闲轮询的短时停库恢复，不证明长时间数据库失联、在途外部调用恢复、PG HA 或最初异常的根因。单元恢复测试及共享准入相关测试为 9 passed / 6 skipped。

本次补丁后完整后端回归：495 passed / 369 skipped，30.19 秒；未设置真实外部集成环境的跳过项不算通过。部署补丁 app/chat_worker.py SHA256=`6fb4a1abf2c01db70f35ff92cec6dbdd04296224b0aae38785735ae692f2d02f`。全工作区 diff 检查仍提示既有 docs/api-contracts.md 文件末尾空行，本次未修改该无关位置。

## 取消分类修复后的双节点复验

renew_lease 的默认布尔接口保持兼容；执行检查和 watcher 显式启用取消分类。只有在当前 owner/fence、数据库时钟确认未过期且会话有效时，持久化取消标记才产生 RunCancellationRequested；不续期、不允许结果提交。错误持有者、过期或终态仍不获得执行权。watcher 与同步执行检查分别记录 cancelled，真实失租仍为 lease_lost。

两 guest 同步补丁并重启 API 后，仅 node1 延迟 8 秒 Worker 执行，node2 通过真实 TLS/CSRF 在运行中取消。run=`ddc0de0a-67e8-40d8-a207-fcc674917951`：数据库核对 interrupted / error_code=cancelled / fence=1，attempt=1、assistant=0、done=0、interrupted=1、requeue=0、snapshot=1，约 2.026 秒终止。此前 lease_lost 记录是历史版本证据，不再代表当前分类。

同批真实断开尚未完成的 SSE socket 后跨节点续传也通过：run=`289b6a6e-6e88-4e0a-a41a-060cc2e1c9b7`，断开时仍 running、没有取消、最终仅一条助手和一次 done。临时 Worker 已停止，两侧正常 API/edge/worker 均恢复 active。针对性本地测试 42 passed / 10 skipped，watcher 与轮询恢复测试 10 passed；真实 PG 测试中的错误 owner 分类断言已补，但本地未提供该测试连接所以仍跳过，不冒充已运行。

取消分类补丁后完整后端回归：497 passed / 369 skipped，26.85 秒；未连接的集成测试仍未验收。

## 私有固定入口的被动故障转移

新增独立 `ops/nginx/customer-service-lb.conf.template`：入口到两个现有 HTTPS edge 强制 CA 与服务名验证，primary/backup、失败暂避、连接超时 2 秒、最多两个上游尝试、SSE 无缓冲。未启用 non_idempotent；已向上游发送的 POST 不因不确定响应而透明重放。外部写操作依旧需要应用 request-id 对账。

通过 create-only fixture 在 node1 私有 IP 的 8443 启动独立 `cs-lab-lb`，没有修改既有 443 edge、Windows hosts、交换机或公网转发。该单入口与 node1 同机，因此不是入口自身 HA，也不是 VIP。

正常入口完整链路 run=`d626b43a-bb1e-4b9f-b8bc-4bc1daee2803`。随后实际停止 node1 API，保留 node1 edge（此时上游会返回 502）；同一 8443 入口 GET /api/auth/me 自动尝试备节点，返回预期未登录 401，随后同入口登录、Cookie/CSRF、入队、SSE、Last-Event-ID 重放与 history 成功，run=`b3c28046-ea5c-4b1b-b733-826b7f3b3135`，只有一个助手。整个故障场景没有改客户端入口，也未手动选择 node2。node1 API 在完整探针结束后才恢复，设置退出恢复 trap；恢复后 API/edge/worker/lb 均 active。

这是**先由幂等 GET 触发被动摘除，再发 POST**的验收，不保证首个请求恰为 POST 时无错误，也未测已输出 SSE 的中途透明迁移。生产需要明确客户端 request-id 重试/对账及重连策略。LB 模板和实验室边界测试 17 passed；不代替真实在途 POST 断连无重放测试。`--load-balancer` 探针输出的 enqueue/sse/replay IP 为固定入口而非后端身份。

LB 模板与探针补丁后完整后端回归：499 passed / 369 skipped，29.01 秒。真实 NGINX 配置检查、正常链路和停主 API 故障链路均在上述两 guest 实际执行；跳过项仍不作为通过。

## 已接收 POST 后丢失响应：真实代理不重放

`tests/fixtures/linux_lb_post_fault.py` 在授权 node1 的回环随机端口启动两个合成 TLS 后端和独立 NGINX，使用实际 LB 模板，验证入口证书、上游 CA 和服务名；未更改既有 API/edge/LB。主后端完整读入请求体并增加一次合成效果计数后，真实 shutdown 连接而不发响应。

实际结果：客户端 POST 返回 502；primary 效果计数=1，backup=0；随后 GET 经备节点返回 200，效果计数仍不变。因此模糊响应不能当作“写入失败，可以自动重发”，该模板没有把已发送 POST 透明重放到备节点。仅证明代理层合成响应丢失场景，不证明真实业务副作用幂等、Outbox、用户重试对账或真实工具资源授权。调用方仍须保留 request-id 并查询权威结果，不能看到 502 就新建另一次业务写操作。

临时 NGINX 及两个后端在 finally 中停止并回收，仅清理该探针的临时目录；既有服务未停止。相关实验室 guard/契约测试 18 passed。没有把相关测试替代完整后端或真实业务验收。

## 知识 Worker 接入前的内核能力门禁

知识 Worker 轮询也已补与 ChatWorker 对齐的数据库故障退避：清理/领取失败停止本轮，1/2/4/8/16/30 秒封顶，可被停止信号打断，不自动重放已领取解析任务、不释放未知容量；编程错误仍退出。专项 8 passed / 16 skipped，补丁后全后端 508 passed / 369 skipped，28.02 秒。

node2 同步该补丁并用真实生产 knowledge_worker.py、strict worker 数据库角色及隔离解析器启动时，启动 TXT 探针失败，单元退出为 DocumentParseError，未领取文档。进一步运行只读 `linux_parser_capability.py` 得到 kernel=6.8.0-147-generic、Landlock ABI=4，而项目强制 ABI>=5。该节点当前**不能**作为受支持的知识解析节点，不能把已有 ChatWorker 成功当作知识链路成功；不禁用或降低沙箱门禁。

已核对 Ubuntu 官方 noble-updates/security 的 HWE 候选与 apt 模拟安装计划；node2 /boot 约 756 MiB 可用、根分区约 28 GiB 可用。尚未安装或重启、尚未完成内核升级，不能据候选包存在宣称能力已补齐。失败的临时知识单元已清理失败状态，既有 API/ChatWorker 未停。后续先升级隔离 node2 的受支持内核并复验沙箱，再接共享对象存储和跨节点文档链路。

## node2 受支持内核升级与解析复验

2026-10-27 通过 Ubuntu 官方 noble-updates 签名 apt 元数据安装精确包 `linux-image-7.0.0-38-generic`（7.0.0-38.38~24.04.4）及其 4 个依赖，下载约 185 MB。未安装整个 HWE 元包及无关完整 firmware 集，未删除旧 6.8 内核；initramfs/GRUB 正常生成，旧内核仍在启动菜单。只重启授权 node2，未重启 Windows、node1 或其他 VM。

启动后实际 kernel=7.0.0-38-generic、Landlock ABI=8，达到项目 ABI>=5 门禁；未关闭 SecureBoot 或降低 sandbox 要求。`parser_sandbox_check.py` 实际通过文件/网络/进程组及后代继承拒绝，`parser_format_check.py` 的 TXT/DOCX/XLSX/PPTX/PDF/PNG 六类合成输入全部通过。生产 `scripts/knowledge_worker.py --require-app-role --once` 在真实共享 PG、隔离解析器和 2048 MiB 限制下正常退出；当前只证明启动与空闲领取检查，不证明跨节点文档已经导入。

重启导致 DHCP 改为 172.24.160.96，使用 HostKeyAlias=172.24.168.125 验证同一已知 SSH 主机密钥后登录，未关闭主机验证或信任新身份。为保持本批既有 TLS SAN、PG HBA 和探针拓扑，在 **node2 guest 内**临时加回 172.24.168.125/20，并为 node1 PG 的 172.24.168.13/32 指定该旧地址为源。未改宿主交换机、PG HBA、证书或公网转发。这不是持久网络配置；下次重启需复核。Windows 侧旧地址 SSH 仍不可达，管理连接使用新 DHCP 地址及旧 HostKeyAlias；guest 间旧地址链路实测可用，不能混称所有路径都恢复。

两侧既有 API/edge/ChatWorker 已恢复 active；升级后跨guest TLS/Cookie/CSRF/入队/SSE/重放再次通过，run=`c93076e5-8928-49ad-a5e4-34af1f92e75f`，仅一个助手。知识 Worker 的旧 ABI 环境阻塞已解除；共享对象存储、跨节点文档导入、持久地址与重启恢复、node1 解析节点内核能力仍待完成。node1 未升级，不宣称两个节点均能解析。

## node2 测试身份跨重启恢复

create-only `linux_lab_persist_network.py` 在固定 node2 hostname、实验室 marker、eth0 精确 MAC 和预期 netplan/networkd 文件均匹配后，安装 `/etc/systemd/network/10-netplan-eth0.network.d/50-cs-lab-address.conf`。只加旧测试地址 172.24.168.125/20 与到 node1 PG 172.24.168.13/32 的 PreferredSource；不替换原 netplan、不关闭 DHCP、不改默认网关、宿主交换机、证书或 HBA。不是可泛用的生产静态网络配置。

networkctl reload/reconfigure 后 DHCP 从 .160.96 变为 .171.92，原 SSH 客户端因地址变化未返回；已通过固定 HostKeyAlias 验证新连接、确认配置存在，再真实重启 node2。重启后启动时间为 2026-10-27 11:45:11 UTC，测试旧地址自动保留，新的 DHCP 地址为 172.24.165.252；到 PG 的路由自动使用源 172.24.168.125。**没有再次手动 ip addr/route 恢复**，Windows 对旧地址 SSH 本轮也已成功。

恢复既有手动启动 API/edge/ChatWorker，三单元 active。新内核 ABI8、strict-role 知识 Worker 单次启动再次通过；跨guest TLS/Cookie/CSRF/入队/SSE/重放 run=`d2bfb9e5-4f49-4cb1-b7b1-5360b1d190c3`，单助手消息。相关 guard/配置测试 20 passed。完成确认后仅关闭已失效、命令行精确匹配该安装任务的 Windows SSH 客户端，未终止其他会话或 Linux 服务。先前“仅临时地址”是本段之前的历史状态；服务仍按实验室约定不自动启动，不能宣称无人值守启动恢复或物理故障 HA。

## 未完成，不能扩大结论

尚未进行真实供应商网络I/O中途失联、网络黑洞/分区、LB/代理自身HA、真实业务 POST 幂等/未知结果对账、PG/Redis切主/fencing、共享对象文档路径、正式Collector/值班告警、浏览器UI现场、真实模型/费用/200条独立业务盲测。在途数据库故障与长时恢复仍需完善。当前测试CA、合成Worker、共用OS账户、手动单元和单主PG不构成生产就绪。S2–S6目标仍未完成。

运行材料在两guest的/home/labops/cs-lab/state，均受限权限；转移包只复制本批必要测试材料，CA私钥未转移。不得读取或公开这些材料内容，不得将其归档到Git。后续复用现有部署，不重复运行拒绝覆盖的bootstrap或新建VM脚本。
