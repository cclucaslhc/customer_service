# 部署、回滚与灾难恢复 Runbook

状态：待实施和现场验收的操作模板；2026-10-15 更新部署目标。已有本地基础 CI/锁定依赖与部分备份能力，但没有正式 Linux 双机、反向代理、HA/告警平台或经过批准的生产恢复流程。文中的 PostgreSQL/Alembic/容器命令仅适用于未来配置完成的受控环境；本文件不授权公网发布、生产恢复、删除、密钥读取或真实退款。

## 使用前必须填写

发布负责人、值班人、数据库/安全负责人、通知渠道；云区域/恢复区域；镜像仓库与 digest；API/Worker 部署对象；PostgreSQL 主端点、备份 stanza/仓库和恢复角色；Redis 服务与切主策略；私有对象桶；Secret Manager 引用；指标地址与告警；业务 API 沙箱/对账流程。把操作入口与权限保存在运维平台，仓库不写密码、Token 或真实联系方式。

目标初值：月可用性 99.5%、业务 RPO ≤ 5 分钟、整体 RTO ≤ 60 分钟、应用摘流 ≤ 60 秒；负责人须按实测冻结。RTO 从宣布事故到服务验收通过计时，不能只计数据库启动。恢复窗口若无法达到目标，修正基础设施和演练流程，不修改报告掩盖缺口。

## 制品与环境准备

目标为自管 Linux 双实例＋反向代理，API、聊天 Worker 和知识 Worker 分进程部署到两台应用主机；不得把两进程同机称为双实例故障域。反向代理负责 HTTPS 入口与 SSE 透明转发，应用只接受受信代理连接；代理/VIP 自身不得成为未经处理的单点。PostgreSQL/Redis 必须另行配置主备、仲裁/切主与 fencing，不能仅靠两台应用主机声称完整 HA。Kubernetes、托管容器平台并非本次已选目标。

### 双机拓扑与放行前提

| 故障域 | 最小部署契约 | 放行证据 |
| --- | --- | --- |
| 应用主机 A/B | 每台运行独立 API、聊天 Worker、知识 Worker；API 仅绑定本机 `127.0.0.1`，每台各有同机 HTTPS 反向代理或受控本机代理；如启用本地模型，每台另运行仅回环监听的 OpenAI 兼容服务 | 分别杀死 A/B 的 API、Worker、可选本地模型和整机后，另一故障域仍可接受符合能力/政策的新请求；旧租约迟到写入被拒绝 |
| 入口 | 两个代理前的受控 VIP/负载均衡及健康检查；只转发外部 HTTPS，严格固定 Host 与代理转发头 | 单代理/单主机失效后在目标摘流时间内切走，证书、Cookie、Origin、SSE 重连及游标核对通过 |
| 状态依赖 | PostgreSQL 单写主＋有 fencing 的切主、Redis ACL/TLS＋独立主备切换、私有版本化对象存储 | 分别断主、延迟复制、网络分区和恢复，证明无双写、无越额领取、对象版本仍可读 |
| 观测 | 每台本地私有 OTLP Collector/指标采集，集中存储与告警接收端独立于应用主机 | 应用/Collector 故障时有界降级，告警触发和恢复均由指定接收人确认 |

两台应用主机不是 PostgreSQL/Redis 的仲裁多数，也不是备份故障域。若当前只有单个代理、单个数据库或单个 Redis，应明确标记为单点，禁止把“应用双机”写成“生产高可用”。部署图、地址、端口、防火墙、证书签发/轮换、主备仲裁和容量预算须由运维负责人填写并经预发布实测；本仓库不臆造现网值。

`scripts/serve_api.py` 是当前 API 显式装配入口，必须由运维提供已有私有文件，示意如下；占位路径和域名不得原样投产：

```bash
python scripts/serve_api.py \
  --database-url-file /run/customer-service/api-database-url \
  --master-key-path /run/customer-service/master-key \
  --storage-policy /etc/customer-service/storage-targets.json \
  --knowledge-root /var/lib/customer-service/knowledge \
  --asset-root /var/lib/customer-service/assets \
  --legacy-root /var/lib/customer-service/legacy \
  --origin https://customer.example.invalid \
  --port 8000 --json-http-logs \
  --metrics-token-file /run/customer-service/metrics-token --metrics-port 9107 \
  --otlp-traces-endpoint http://127.0.0.1:4318/v1/traces
```

API 只监听本机 `127.0.0.1:8000`；同机反向代理须设置可信 `X-Forwarded-Proto: https`、传递规范 Host、禁用 SSE 缓冲并限制原始请求日志。私有指标与 OTLP 示例同样仅限同机采集；跨主机不能把回环端口直接转发到公网。数据库文件须含已批准 API 角色 URL（远程连接要求 `sslmode=verify-full`），上述私有文件在 Linux 上只允许所属用户读取；两个节点不能各自生成不同包装密钥。该脚本不执行迁移、不供应账户或模型 Key。

启用本地模型时，须在**每台**应用主机分别启动仅绑定本机 `127.0.0.1` 的 OpenAI 兼容服务，并为该主机的 API 与聊天 Worker 均追加相同的 `--local-model-base-url http://127.0.0.1:<port>/v1`；端口由运维审批，不能将一台主机的回环地址当作另一台的端点。管理页只保存逻辑模型名，不提供任意 URL 或本地密钥输入。`/models` 连接测试仅表示当前 API 节点连通，不证明 Worker 或另一主机可用，更不证明工具调用、流式响应、上下文和费用能力。正式启用前须逐节点、逐进程完成这些验收；任一节点未装配时本地模型请求会失败关闭。

同机 NGINX 的待填充样板见 [`ops/nginx/customer-service.conf.template`](../ops/nginx/customer-service.conf.template)。它把外部 HTTPS 请求转发到本机回环 API，覆盖耐久聊天 202 回执及后续 SSE GET；禁用代理响应缓冲/缓存，并由代理覆盖客户端提交的转发协议和来源地址。显式允许 TLS 1.2/1.3、关闭会话票据并隐藏版本号。模板含故意未替换的域名、证书和上传大小占位值，**不能直接部署**。运维须确定入口/LB 到两台代理的 TLS 与健康检查、证书轮换、上传限额、代理错误日志脱敏和 SSE 超时；填充后在各节点运行 `nginx -t`、受控重载并以真实浏览器验证登录 Cookie、202→SSE、断线游标续读及单代理摘流。2026-10-25 已通过真实 Linux NGINX 的合成协议验收，但不是正式 HTTPS 或高可用验收。

可复现的隔离代理专项：先获取 `nginx@sha256:47928bf67eb62a1863cb08d9ecb52d3e704304ba614eaee17884204677702951` 及测试中固定的 Python 镜像，设置 `TEST_NGINX_IMAGE` 为该固定引用，运行 `python -m pytest -q tests/test_nginx_proxy.py`。测试只启动随机名 `cs-proxy-peer-*`/`cs-proxy-edge-*` 容器，端口仅绑定 127.0.0.1，使用临时合成证书、只读挂载和内存/PID 限额，结束只删除自身容器。NGINX 与合成后端共享网络命名空间以模拟同机回环。验证语法、未知证书拒绝/显式信任、伪造转发头覆盖、Last-Event-ID 透传、首条 SSE 在第二条延迟前到达、202 JSON 与 413 上传限额。它不运行真实 API、Cookie 登录或双实例，不能替代这些端到端验收。CI 已配置此专项及零跳过门槛，托管运行仍待完成。

聊天和知识 Worker 已提供 `--database-url-file`，聊天 Worker 还提供 `--redis-url-file`；正式部署使用独立 Worker 角色数据库文件和受控 `rediss://` URL 文件，不在进程参数中直接放凭据。现有 `--database-url`/`--redis-url` 仅为本机/测试兼容，远程数据库或带凭据 Redis 参数会被拒绝。两个 Worker 均需显式 `--require-app-role`、批准的存储 policy/包装密钥路径及受控 OTLP 目标；聊天 Worker 还需相同的准入 namespace/容量/启动率策略。以上仍只是装配契约，不是已批准的 systemd/NGINX 配置；正式 ACL/TLS 握手及多实例共享策略须现场验证。

镜像固定 digest 与锁定依赖，非 root、只读根文件系统、临时目录受限，上传解析独立受限 Worker。数据库、Redis、对象桶不公网开放；TLS、可信代理、管理鉴权与客户隔离先验证。模型凭据通过管理员管理入口提供，KMS 管理包装密钥；不从历史环境变量导入。

知识 Worker 的生产 CLI `scripts/knowledge_worker.py` 现在默认逐文档启动解析子进程，父进程保持租约心跳并负责数据库/对象发布。默认总时限 120 秒、JSON 结果上限 128 MiB；Linux 额外限制地址空间 2 GiB、CPU 时间、128 个文件描述符并禁止 core dump。可通过 `--parser-timeout-seconds`（1–600）和 `--parser-memory-mib`（512–8192）按文档规模配置，先用合成文档验收。OCR 每个解析器固定单线程，避免按主机 CPU 数建立线程池超过资源预算。超时/异常项目记录失败，不自动重放；原有 owner/fence 发布校验继续生效。

心跳一旦确认租约/协调权限失效，父进程会在解析等待循环中终止解析，并以原有失租语义退出，不记录可重试成功结果。Linux 为每次解析建立独立 session/进程组，超时、失权或解析器退出时清理同组转换进程，再回收解析主进程；等待使用 `waitid(WNOWAIT)` 保留 PID，防止清理前 PID 复用。新 seccomp 策略阻止解析器随后 setsid/setpgid 脱组。Windows 当前仍只终止直接子进程。durable 主动取消已接入，取消后禁止新发布、停止解析后释放容量；cgroup 等部署隔离仍须现场验收。

子进程仅接收文件副本和文件名，使用显式最小环境与有界 JSON 交换，不继承服务环境凭据或额外 FD。Linux x86_64 现在默认执行 Landlock ABI ≥ 5 文件授权与 seccomp socket/IPC/逃逸限制；Worker 领取任务前自检，不支持的节点失败关闭。系统/运行库和代码必须只读且不放密钥，临时写权限只给本次工作区。Windows 没有这些 Linux 限制。直接构造 `KnowledgeService` 或未显式传入解析器的库级 Worker 仍使用进程内解析；不得作为生产替代路径。详见[解析隔离](parser-isolation.md)。数据库共享容量已接入，物理内存/PID/CPU、内核安全、额外格式与双机现场验收仍缺，不能据此宣布完整 S3。

仓库现有 `Dockerfile` 是单一 API/Worker 运行制品：构建基底与 distroless 运行基底固定 registry digest，前端使用锁文件 `npm ci`，Python 使用 `uv sync --locked`；最终镜像 UID/GID 为 65532。`linux-image` CI 检查非 root/只读启动，运行解析沙箱和格式样本，比较双构建 OCI 并扫描同一制品、生成 SBOM。托管 CI 尚未运行；本机已有历史构建/扫描与本批挂载源码的沙箱证据，须区分对应批次，不能把旧镜像证明当作新源码的最终制品证明。固定 digest、扫描和 SBOM 不替代签名、跨宿主重建或正式部署。

此镜像不内置数据库 URL、对象凭据、包装密钥、模型 Key 或部署参数；`CMD` 只是 API 默认入口，Worker 必须由服务管理器显式覆盖命令。由于 API 固定监听回环，Linux 容器部署须让**同机受信代理**能连接该回环端口（例如受审计的 host network 与端口分配），不能将容器内 `127.0.0.1` 直接做普通端口映射后宣称可用。正式 systemd/容器权限、只读挂载、私有文件、tmpfs、节点端口隔离和代理配置仍须按实际环境审阅与演练；不得用 `--network host` 的示例取代网络安全审批。

CI 交付：后端回归、前端测试/构建、隔离检索评测、PostgreSQL迁移测试、分布式/工具专项测试、镜像与依赖扫描、SBOM、镜像 digest、发布 manifest。manifest 记录 Git、Schema、Prompt、路由、索引版本和回滚兼容区间。

## 发布步骤

1. 负责人确认制品、验收报告、当前/上一版本 manifest，生产备份可恢复时间在 RPO 内，依赖健康。
2. 设维护窗口或灰度，暂停破坏性知识操作；新增兼容 Schema 的 migration job 单次执行，不能每个实例各跑。
3. 执行 Alembic 并核对版本（工具未来交付）：

   ```bash
   alembic heads
   alembic upgrade head
   alembic current
   ```

4. 部署新 Worker/API，检查 readiness、数据库访问、模型配置、对象权限；不让 liveness 因供应商故障不断重启。
5. 运行合成账号冒烟：客户只见本人会话、管理员权限、SSE、取消、图片引用、沙箱工具、幂等/审批、检索与降级。
6. 小比例流量或内部灰度，观察至少一个有代表性的流量窗口（建议 30 分钟并满足样本量），检查错误、延迟、Token/费用与工具 unknown。
7. 达标后扩大流量；旧实例停止准入并排空，Worker 续租完成或安全移交，SSE 发终态/重连提示。
8. 保存发布结果和监控证据。contract 删除字段必须在回滚兼容窗口结束后的独立发布执行。

SSE 代理关闭缓冲/缓存，读取超时大于服务时限；压缩依实际流式验证配置。多实例必须验证 event_id 游标重放和数据库终态快照，不依赖粘性会话实现正确性。

## 应用、配置与索引回滚

触发：发布后错误/时延明显恶化、权限泄漏、工具重复效果或数据错误。

1. 记录事故时间与版本，停止放量；安全/写工具异常先封禁相关工具，保存证据。
2. 检查旧镜像支持当前 Schema，使用发布平台把流量切回上一 digest；不自动执行 Alembic downgrade。
3. 若 Schema 不兼容，保持维护/只读并前向修复；不得盲目还原数据库抹除新数据。
4. Prompt、模型路由、RAG index_version 分别切回已验收版本；运行中的 Run 继续用固定快照或安全终止，不半途混用配置。
5. 验证旧实例就绪、权限、SSE、取消、任务领取、工具对账和错误率恢复，再恢复流量。
6. 保存回滚报告，分析原因后再发布。

## 事故分级与共用操作

事故指挥人统一记录开始时间、影响、时间线、变更、目标恢复点和操作审批。高等级事故先控制影响，恢复后再清理。测试报警只进入测试 receiver；真实通知由配置渠道和负责人执行。

共用验证：检查 ingress/服务探针与依赖监控 → 按 request_id/run_id 查 Trace → 对照发布版本 → 判断能否安全只读/降级 → 保留日志/任务/外部流水。禁止清空 Redis 或数据库“试一下”。

## 场景 A：API 或 Worker 宕机

1. 负载均衡摘除不健康 API，把请求交给其他实例；核对活动会话不会越权。
2. 当前聊天 Worker 首次失租，且无模型调用账本、工具/输出事件、取消或会话失效，并且原排队期限未过时，回收器可提升 fence、最多重新排队**一次**；旧 Worker 不得开始模型调用或提交结果。已有模型尝试（包括结果 unknown）、第二次失租或其他不满足证明条件的 Run 标为 `interrupted/lease_expired`，**不会重放**。知识导入执行失租记为 `failed/lease_expired`，保留已发布项并先核对；从未领取的知识任务排队超时记为 `failed/queue_timeout`。知识任务不自动重跑解析。
3. 当前用户可在看到终态后显式重新提交新 Run/新知识任务，但不能复用旧执行身份宣称自动重试。未来只有在调用前可证明未产生外部效果、且独立 attempt/费用预算和状态机已实现并验收时，才允许自动重试；写工具或结果 unknown 必须先对账。
4. 值班人员可使用[只读终态队列清单](queue-review-operations.md)定位需人工核对的任务；清单本身不确认或重放死信。恢复服务前记录每个 `review_unknown` 的模型费用、外部工具和知识已发布状态。
4. 用原 run_id 从任意 API 发取消并核对数据库状态和实际停止。
5. 验证无双领取、无重复副作用、终态一致、实例摘流时间达标。现有全局启动恢复逻辑必须在多实例上线前移除。

## 场景 B：Redis 不可用或切主

1. 告警并检查托管服务/HA状态，冻结依赖 Redis 的新准入，或启用已验收的 PostgreSQL 协调模式。
2. 已执行 Run 在数据库检查取消和租约，完成后持久化结果；不能每实例各自放宽并发限制。
3. Redis Pub/Sub 消息丢失通过数据库取消标记补偿；事件流游标过期时返回持久化快照。
4. Redis 恢复后从数据库重建短期运行映射和缓存，不重放已成功业务操作。
5. 验证全局限流、取消、SSE、预算和队列，逐步解除冻结。缓存恢复不等于业务恢复。

## 场景 C：PostgreSQL 主库故障

1. 暂停写工具和新任务领取，核对主/备角色、复制延迟和故障时间。
2. 托管平台按既定流程切主；自管由 HA 管理器隔离旧主后提升备库，禁止同时存在两个可写主。
3. 更新统一端点，回收应用连接池；禁止把关键读转移到滞后副本来验证写结果。
4. 比较提交边界和外部流水，处理可能因异步复制丢失的本地操作记录。
5. 执行业务/权限/序列/任务/幂等验证与外部对账，逐步恢复写入；记录实际数据缺口与 RTO。

若无可用副本，进入 PITR 场景。故障切换不替代备份，副本同样会复制误删除。

## 场景 D：误删除或数据库损坏，PITR

1. 冻结所有写入和 Worker，保留故障库快照/WAL 与外部业务流水；确定破坏发生前的目标时间。
2. 从备份目录选择完整备份链和连续 WAL，确认恢复权限、密钥、对象副本和配置 manifest。
3. 在新隔离实例恢复，禁止覆盖故障实例。以 pgBackRest 为例，以下占位符由操作员替换，PGDATA 指向新的空恢复目录：

   ```bash
   pgbackrest --stanza=<configured-stanza> info
   pgbackrest --stanza=<configured-stanza> --type=time --target='<UTC-target-time>' --target-action=promote restore
   ```

   PostgreSQL 版本/扩展/参数必须与备份兼容；托管平台使用其恢复 API。具体启动、网络与连接切换命令由部署平台实施后附入此 Runbook。

4. 核对恢复 timeline/时间、Schema、FK、行数摘要、identity、应用角色、当前 Prompt/路由/索引指针。
5. 通过 manifest 恢复对应对象版本、图片与密钥引用。恢复旧数据库不得重新公开已删除或撤销权限的文档；对恢复点后的权限撤销/删除意图做前向补齐。
6. 暂停工具重放、Outbox 和任务自动恢复；对恢复点之后的外部操作按幂等键/流水重建状态，unknown 人工确认。
7. 在无出站环境运行合成业务回归，重建可派生索引；复核文件哈希与引用。
8. 负责人确认 RPO 缺口和对账结果后切端点，先只读再写入。故障实例保留取证，不立即删除。

pg_dump 补充恢复只进入新隔离库（未来工具配置）：

```bash
pg_restore --exit-on-error --no-owner --dbname=<isolated-empty-database> <encrypted-backup-after-authorized-decryption.dump>
```

该命令不提供时间点恢复；解密在受控流程中执行，不输出密钥。必须先安装相应 pgvector 扩展、设置角色并验证 dump 内容。

## 场景 E：模型供应商不可用

1. 确认错误属于超时/429/5xx/配置问题，查看共享熔断状态。
2. 路由仅选择已配置、能力与数据政策合格的备用；未配置 OpenAI 时不自动启用。
3. 当前未启用模型自动重试或供应商切换；模型失败应安全终结、保留 unknown 费用并让用户明确知晓。未来跨模型 attempt/预算合同及流式 `answer_reset` 验收完成后，才按固定总时限和费用上限开放受控重试；写操作未知状态先对账。
4. 无合格模型则返回权限内证据/人工入口。管理员修复凭据仍经管理页面，不读取历史环境密钥。
5. half-open 少量探测后恢复主模型，核对每次尝试用量和费用，避免切换后漏计重试。

## 场景 F：索引、对象或密钥故障

- 索引质量回退：原子回切上一 ready 版本，固定运行快照；检查权限、过期文档、负例和引用。向量/Reranker 不可用按 RAG 保守降级。
- 对象缺失：暂停相关文档发布，从版本化副本恢复 manifest 指定版本并校验哈希；禁止从公网随意抓取替代文件。
- 密钥不可用：停用受影响模型配置，通过 KMS 的授权恢复流程恢复；无主密钥无法恢复旧密文时请管理员重新录入，不绕过加密。

## 定时任务与灾备演练

0017已增加[全库维护/备份封存](maintenance-operations.md)基础：持久hold/owner/fence、事务写入屏障、排空有效执行租约及封存备份guard，期限过期不自动开放。可信CLI明确owner/fence可操作源维护，不提供租户HTTP控制；恢复库旧维护permit失效而recovery hold保留。自动续租/排空等待/scheduler/catalog/自动恢复仍未完成，不能无条件定时调用人工离线确认参数。维护期间SQL竞态统一错误/页面提示、生产权限与规模锁等待需另验收。

当前逻辑恢复入口已交付：[加密逻辑备份与隔离恢复](logical-backup-operations.md)。仅在合成/授权隔离环境演练；工具不启动 Worker/API，并要求先停写/排空。不自动执行本 Runbook 的生产切换、PITR、角色/KMS恢复或外部对账。恢复报告的耗时只涵盖工具步骤，不是整体 RTO。

远程包取回入口已增加：[私有远程备份](remote-backup-operations.md)。使用独立批准归档目标、授权 UUID namespace、可靠保存的回执、独立备份密钥与安全 stdin 凭据供应；取回确切版本并认证全包，不读 latest、不覆盖已有包。然后在新隔离数据库/不同新空业务对象桶执行逻辑恢复；取回 verified 不是数据库/权限/业务恢复成功。当前仍缺 catalog/回执灾备、自动 scheduler/恢复/freshness、PITR/异地和生产 IAM/TLS 验收，不能跳过 hold/前向对账。

0016 恢复的存储配置默认标记 recovery_rebind，旧源桶凭据在解密前拒绝使用；计数见报告 storage_rebind_required。必须保持 hold，核对恢复后的全部租户对象引用、新批准 endpoint/bucket/IAM 和包装密钥恢复，完成权限撤销/任务/业务前向对账。受控启用工具尚未交付，不能把合成测试中的数据库维护 SQL 当成生产解除步骤。经授权解除保护后，管理员在存储界面显式提供新凭据，目标桶须与本租户全部引用一致；随后验收读取/任务并重新检查权限。主密钥、TLS/IAM/KMS 和生产恢复联合演练仍未完成。

由单独 scheduler/平台任务执行备份、WAL检查、费用对账、unknown 工具对账、租约回收、过期审批和对象/索引垃圾回收；任务加唯一键与租约，不在每个 API 启动时执行。

多恢复点审阅可使用独立批准manifest+逐anchor摘要及明确cutoff/gap/minimum。默认要求先前实际restore证据；未知/不可读/分叉记录不静默回退，选择后取回再次核对anchor摘要。选择范围只是批准集合，不证明全目录最新；集合与摘要需独立可靠分发/防回滚。后续仍认证业务包/实际恢复/hold/权限和操作前向对账，不能以选择成功替代完整DR。操作见 [恢复点选择](backup-catalog-recovery.md)。

目录与回执灾难引导入口已增加：先从独立介质提供批准anchor/key/policy/namespace/计划指纹，通过catalog固定版本认证取回receipt，再从archive固定版本fetch并认证包，最后新PG/对象restore并保持hold。目录工具不连接源DB、不重建/重放执行队列；检查点可能早于源catalog终态，不能当作已确认业务执行结果。独立anchor分发、全目录/最新恢复点、真实S3联合演练与完整RPO/RTO仍待验收。详见 [目录恢复](backup-catalog-recovery.md)。

自动对象恢复验证入口现支持独立verification角色、批准创建前缀与每attempt私有版本桶（显式二次隔离确认）。成功/失败桶和确切版本保留供授权GC，不盲删或复用，验证器不启用恢复服务。实际PG/S3联合验收仍缺，不能把本地恢复或供应单测当对象DR通过。操作、凭据与未知资源精确定位见 [自动备份](backup-scheduler-operations.md)。

0018 本地逻辑备份scheduler及可选实际隔离恢复验证已实现，操作见 [自动备份](backup-scheduler-operations.md)。批准策略绑定恢复目标；失败保留任务 needs_review 和未知验证资源，不自动重试/解除维护。成功验证仅删除本次确切隔离数据库，明文文件保留等待批准GC。恢复包内的计划全部禁用、running备份任务待审、独立recovery hold保持。此处更新前述“scheduler/自动恢复待接通”的历史状态；不代表对象自动恢复、生产DR/freshness/PITR已完成。

每周恢复逻辑备份，每月物理 PITR/对象/密钥联合演练，每季度跨区域恢复与故障切换。演练无真实模型/写业务出站，使用合成账户和沙箱。报告包含备份 ID/时间、目标/实际恢复点、RPO/RTO、manifest、数据/权限校验、外部操作对账、告警触发恢复、失败项与修复复测。

关联：[架构](production-architecture.md)、[实施清单](production-roadmap.md)、[迁移备份](postgresql-migration.md)、[观测](observability.md)、[工具安全](business-tool-security.md)、[RAG](rag-upgrade.md)。
