# 解析子进程隔离

## 适用范围与启动门槛

生产知识 Worker CLI 使用 `IsolatedDocumentParser`，在领取任务前用合成文本进行一次实际解析自检。Linux 子进程要求 **x86_64、Landlock ABI ≥ 5（通常 Linux ≥ 6.10，且内核启用 Landlock）、允许安装 seccomp filter**。不满足条件、安装策略失败或依赖不能在预算内加载时，Worker 拒绝启动；不提供自动降级到无沙箱的开关。升级前在目标内核执行合成验收，不只检查发行版名称。

Windows 仍是开发兼容模式，只有超时/输出控制；不声称具有此 Linux 沙箱。直接调用 `KnowledgeService` 或手工构造未传解析器的库级 Worker 不获得隔离能力，生产必须使用受控 CLI。沙箱与数据库租约、容量和权限校验互补，不代替它们。

## 边界

- Landlock 在解析第三方输入前限制文件访问；允许读取不可变的 Python/系统运行库、应用代码和明确列出的系统信息/配置文件，只允许在本次私有 `cs-parser-*` 目录创建、修改和删除普通临时文件/目录。`/dev/null` 单独授权；不开放服务数据根、凭据目录或其他任务工作区。工作区不允许创建设备节点、socket、FIFO 或直接执行临时文件。文件 metadata（例如 stat）并非完全保密。
- 运行库读取范围包含 `/usr`、`/lib`、`/lib64`、Python prefix 和 app 代码目录；这些目录必须由部署方保持只读、受信任且不放业务密钥。必要系统文件包括 `/etc/mime.types`、动态链接/时区/字体配置，以及 CPU/内存/当前子进程信息。不得把凭据挂载进允许读取的运行库或代码目录。
- seccomp 校验 x86_64 架构，拒绝 x32 syscall 编号，阻断 socket、io_uring、SysV/POSIX 消息 IPC、跨进程 FD/内存访问及若干信号/进程组逃逸入口。子进程不继承父进程其他 FD，标准输入为 null，只有输入副本/结果文件和 null 输出设备；不把 seccomp 描述为任意既存 socket FD 的通用读写隔离。
- `no_new_privs`、Landlock 和 seccomp 随 fork/exec 继承。Linux 解析进程在应用策略前已建立独立 session，策略阻止随后 setsid/setpgid 脱离清理组；父 Worker 不受子进程策略限制，仍可在失租、取消或超时后终止整个解析组。
- 现有地址空间/CPU/输出/FD/core 限额保留。容器或 systemd 的 cgroup、PID/CPU/物理内存、临时磁盘配额及内核补丁仍为部署要求；此实现不是 VM、容器逃逸防护或完整内核攻击面审计。

## 格式、结果与限制

第三方解析器的 stdout（含 FD 1 原生写入）被转向 stderr；独立 FD 只承载有界 JSON，避免 PyMuPDF 等库的提示文字污染结果。父进程继续校验结构、大小、租约及固定文档版本，不信任解析器返回的数据。

已使用合成 TXT、DOCX、XLSX、PPTX、PDF 和 PNG/OCR 验证。LibreOffice/旧 Office/WPS、外部字体和额外转换器未在新策略下逐一认证；需要 socket/额外目录的转换器可能被拒绝。不得为兼容直接关闭策略或开放整个 `/etc`、`/proc` 或服务数据根，应使用单独审阅的受限转换方案并补测。

## 验证与部署

容器资源限额专项入口：`python scripts/verify_container_limits.py --image <本机已有非root镜像>`。它按本地 inspect 得到的不可变 image ID 启动三个随机命名的独占临时容器，不拉镜像、不挂载宿主目录/凭据/Socket、不联网，固定 128 MiB 内存及总交换预算、16 PID、0.25 CPU、4 MiB tmpfs 和只读根。分别验证实际 cgroup OOM/137、fork EAGAIN、ENOSPC 与 CPU throttling；退出/异常只删除自身容器。128 MiB 是故障注入预算，**不是生产 Worker 配额建议**；正常 OCR 运行需要更大的实测预算。默认普通测试不自动触发资源故障，CI 的 Linux 镜像作业单独显式运行。

该专项证明本机 Docker cgroup 配额生效，不证明解析任务因 OOM 后的数据库一致性/补偿、宿主耗尽恢复、双机可用性、生产并发容量或 SLO。生产还需结合共享容量、parser rlimit、Worker 租约与实际文档规模压测。

另外的 `tests/test_knowledge_oom.py` 覆盖真实 Worker 领取后 OOM：显式设置 `TEST_RUNTIME_IMAGE`、`TEST_IMAGE_PG_CONTAINER`（仅接受 `cs-oom-pg-*` 专用实例）和匹配该容器回环映射的 `TEST_POSTGRES_URL`。fixture 创建独立 cs_test 数据库，以 768 MiB 容器配额运行镜像内真实 Worker，在进入解析后通过测试注入器申请 1 GiB 内存，确认 cgroup OOM/137 和容器终止，再检查无发布、占用保留、旧租约拒绝续期、新任务不能抢占。测试加速数据库租约截止并回收终态，但不伪造 OOM；经容器停止证据确认后才人工释放，再用正常生产 CLI 执行显式新任务，旧任务不重放、仅生成一个文档、容量归零。它共享本轮合成文件的专用临时挂载，不挂载服务数据或密钥。此用例不代表真实解析器内存峰值或跨宿主故障，正式预算仍须容量测试。

同文件还覆盖发布事务提交前和真实 DBAPI commit 返回后的外部容器 KILL。测试 fixture 只负责在边界写同步标记并等待，协调器确认标记后杀掉自有容器；不在生产代码增加故障开关。提交前数据库发布整体回滚；提交后文档及 item/计数整体保留，显式重提交按内容去重。两种情况仍保留容量到确认停止后释放，旧任务不自动重放。文件副本可能成为 orphan，不能把数据库回滚说成文件也回滚；不存在自动 GC 的验收结论。

`tests/test_isolated_parser.py` 包含真实子进程、PDF 结果协议、架构/旧 ABI 拒绝和 Linux 专项；Linux 专项不会在 Windows 假装通过。`tests/fixtures/parser_sandbox_check.py` 验证合成私有文件越界访问、socket/进程组/信号、x32/IPC 拒绝与后代继承；`parser_format_check.py` 验证六类样本。CI 在实际构建镜像中运行这些检查，托管结果尚待取得。

2026-10-25 首批 Linux 验收使用已有固定 distroless 运行环境，只读挂载当前 app 源码。随后已实际重建完整镜像（不挂载 app 代码），在最终 `sha256:614e82474618546f54fd335859ffafee2d150aa01bef07fc12ac2d9298f335d1` 上复验沙箱和六类格式通过，并在专用 PostgreSQL 上完成迁移→真实 Worker CLI 上传/重解析/取消及容量归零。内核 6.18.33.1 / Landlock ABI 7；非 root、cap-drop ALL、no-new-privileges，沙箱测试保留默认网络，非仅由 `--network none` 阻断 socket。格式检查另施加 3 GiB/PID 128/tmpfs 256 MiB。旧 ABI/架构为合成拒绝证据，不代替多内核现场验收。最终镜像扫描/SBOM 见验收记录；跨宿主重建、独立安全评审、额外格式与真实双机验证仍待完成。
