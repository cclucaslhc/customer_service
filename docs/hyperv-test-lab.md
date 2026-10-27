# 本机双Linux测试环境

用户于2026-10-26明确允许创建两台隔离VM并继续S2–S6。资源均位于仓库外`D:\codex_project\customer_service_vm_lab_20261026`，VM名`cs-s2s6-node1`、`cs-s2s6-node2`，每台2 vCPU/4GiB固定内存/32GiB逻辑差分VHDX。只连接既有Internal Default Switch（NAT出站），未改交换机、未建外部桥接、端口转发或公网监听。两台同宿主guest不证明物理宿主容灾。

## 镜像和访问

- 官方Ubuntu24.04 Noble通用镜像SHA256：`04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94`。源为Canonical cloud-images官网current，2026-10-17构建；下载后按官方SHA256SUMS逐字校验。
- 首试Azure专用VHD包SHA256：`cde3ca6a5aabe89702456cc5af69e2336600ce13c18898aba27e7212e3474e3f`，校验通过且能启动/获取IP，但SSH未开放，不能称初始化成功，已保留原盘。
- 通用QCOW2经QEMU11.1.0 check无错误后转换VHDX；转换工具来自QEMU官网链接的Weilnetz Windows发行，只解压、不安装系统。官方SHA512核验为`5bcf9eed634e8575a37b74f445af41a2fe4106da512d0c30c368301d4c105037fdfab40a5287367a28a957624cddebbc8c07e16c88ab6634f554cdf3d16bf543`。Windows Authenticode显示UnknownError，不声称证书链通过；采用官网HTTPS及独立发布hash校验。首次checksum网络获取失败后未执行工具，重新获取并核验后才执行。
- cidata ISO由Windows内置IMAPI生成，7-Zip核对包含meta-data/user-data；PowerShell脚本语法检查通过。SSH仅使用本批新生成密钥，私钥在仓库外access受限ACL目录，不记录内容，不复用历史密钥。Linux用户labops，锁定密码、禁止root/密码SSH，仅内部密钥访问；NOPASSWD sudo仅用于此隔离测试机。

## 当前实测状态（双VM就绪，不是应用HA验收）

最终node1和node2均通过专用SSH密钥实际登录，cloud-init状态done、errors空、NoCloud来自/dev/sr0。node1当前172.24.168.13，node2当前172.24.168.125，两向各2次ping均0%丢包；两个guest执行`sshd -T`确认permitrootlogin/passwordauthentication/kbdinteractiveauthentication均no，allowusers仅labops。没有公网转发；NAT允许guest按需出站下载签名依赖，并非完全断网。应用尚未部署，因此没有S3故障切换通过结论。

node2已换通用盘，SSH实际登录成功，cloud-init status done且errors为空，DataSourceNoCloud来自/dev/sr0；hostname为cs-s2s6-node2、Ubuntu24.04.5 LTS、kernel6.8.0-147-generic。实测内部IP172.24.168.125，仅为当前DHCP地址，重启后必须重新查询。Python venv和PostgreSQL16.15客户端/Git已通过Ubuntu签名软件源安装；未部署客服应用，未配置任何模型密钥。

node1首轮Azure盘的Hyper-V guest shutdown job曾长时间处于running；RequestStateChange取消返回32770（不支持），断电请求返回32775（当前状态不允许）。等待中的PowerShell换盘进程已终止，避免后台换盘。随后主动复核发现任务已结束、VM回到Normal；确认无活跃shutdown job后才重新断电、换成generic差分盘并启动，最终SSH与cloud-init也通过。未重启vmms服务、未终止其他VM或System进程。

## 后续操作约束

后续持久网络补验已完成：node2 create-only networkd drop-in 保留旧测试地址和到 node1 PG 的 PreferredSource，原 DHCP/默认网关不变；真实重启后旧172.24.168.125自动恢复，Windows SSH也再次可用，新DHCP地址为172.24.165.252。旧地址/路由不再依赖手动ip命令。服务仍按实验室约定手动启动。下段“非持久配置”仅记录本补验之前的状态，当前证据以运行验收最新段落为准。

2026-10-27 node2 已单独升级 Ubuntu 官方签名 kernel7.0.0-38-generic，旧6.8保留；实际 Landlock ABI8 及六类解析/沙箱拒绝通过。node1仍6.8。node2重启后DHCP为172.24.160.96，管理SSH需用该地址并指定HostKeyAlias=172.24.168.125校验原有主机身份；guest内暂加回旧172.24.168.125及到PG的源路由，维持原SAN/HBA的跨guest链路，非持久配置。不能沿用“所有管理路径均为旧IP”的假设；后续先解决可复验的持久地址/重启恢复。详细实测与限制见[two-node-runtime-acceptance](two-node-runtime-acceptance.md)。

不要再次运行全新VM创建脚本覆盖已有资源。来宾停机无响应时须检查真实任务及VM状态，不能因观察超时重复建机或结束全局vmms服务。此次任务自行结束后使用原Hyper-V组权限即可恢复，未使用System worker强制终止方案，也不再保留该未执行的临时恢复helper。

后续仍需应用部署、故障切换、监控/证据验收。当前没有S3双机通过结论。实验室镜像及工具占用额外磁盘，未删除或清理；清理须另行确认精确目标，不能递归删除工作区。VM为手动启动、不随宿主自动启动；退出本轮未停止它们，因为后续测试仍需复用，当前各占4GiB内存。
