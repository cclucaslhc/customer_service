# S2 日志命名空间、留存与读取边界

日期：2026-10-28。服务现有 `--json-http-logs` 已使用 BoundedJsonLogSink 输出白名单 JSON；本轮补齐 Linux systemd/journald 装配，而不是新增绕过白名单的日志文件。

## 正式部署模板

`ops/systemd/customer-service-api.service.template` 使用专用无特权 API OS 账户、不可变 current 发布目录、显式数据库/包装密钥/采集凭据文件和批准存储目标。未使用 EnvironmentFile 或历史模型密钥。配置私有 metrics 与同机 OTLP 出口、受限写目录、最小 capabilities、重启限流；安装前必须提供真实受控路径、账户和 HTTPS origin。

`LogNamespace=customer-service` 将 stdout/stderr 交给专属 journald。将 `ops/systemd/journald-customer-service.conf` 安装至 `/etc/systemd/journald@customer-service.conf.d/10-retention.conf`，只影响该 namespace，不替换全局 journald.conf。关键策略：

- persistent、压缩与 sealing；`SplitMode=none`，不将日志拆成允许原 UID 阅读的用户 journal。
- `MaxRetentionSec=14day`、`MaxFileSec=1day`、`SystemMaxUse=512M`、保留1GiB系统空闲、runtime64MiB上限。
- 不向 syslog/kmsg/控制台转发；限速丢弃仍是容量损失，不等于日志完整性保证。
- 应用账户不加入 adm/systemd-journal，不配置 sudo。平台读取使用独立受审计的提权流程；禁止为了方便给普通客服、开发或 Grafana 账户原始 journal 读取权。

日志角色、主机磁盘与 namespace ACL 仍须安装后复查。Seal=yes 只是请求 sealing，不证明密钥已初始化或抗篡改链验证完成；没有据此声称不可抵赖。

## node2 实际部署验收

仅在授权隔离 node2、固定测试数据库和既有 cs-lab-api 单元增加 create-only namespace drop-in，使用测试 namespace `cs-s2-lab`。未修改 node1、全局 journald、其他服务或公网端口。真实 HTTPS 请求经 NGINX 到 API，记录经过当前白名单 sink 后写入 journal。

最初两次路径状态断言假设404过窄；实际上未认证 API 在路由前统一401，已按既有认证边界调整探针，不修改业务路由。更重要的是初次权限检查发现默认按UID拆分导致labops可读自己的journal，验收确实失败。已新增 SplitMode=none；升级fixture只接受确切旧配置，停止本测试API及其命名空间journald，对该namespace中已生成的user-1000日志精确改属主root/权限0600，保留记录、不删除，再恢复服务。未修改其他namespace日志权限。

修复后实际结果：10条有效白名单 JSON；query/header/未匹配API路径中的合成敏感标记均不在 journal；非提权labops读取无日志记录；API active。随后 namespace sync、重启API并复验得到11条记录、相同脱敏与拒绝读取断言通过。journalctl报告本namespace归档/活动共16MiB；API/edge/ChatWorker均active。关联本地测试12 passed，3.13秒。

labops 是隔离环境管理账户且有sudo，本证据只证明其**不提权时**不能读取日志；不能把它当作正式专用服务账户分离完成。14天到期删除、完整磁盘容量/限速、平台读取角色和日志采集端故障仍需对应证据；未人为改业务时钟或宣称已观察14天。

## 复用约束

### 加速留存机制实测（2026-10-28）

新增 `tests/fixtures/linux_lab_journal_retention.py`，只在授权node2新建UUID命名的合成namespace，策略2秒留存/1秒文件期限/32MiB上限，不修改实际客服namespace、系统时钟或全局journald。实际成功 `cs-s2-retention-81a99f431a13`：先确认旧合成标记可读，轮转归档，等待4秒，重新轮转后写入新标记；旧归档已由配置期限自动清除、新标记仍可读。未调用manual vacuum。探针修复了短生命周期journal退出、`--pipe`把输出旁路journal及文件级轮转时序问题；失败批次不计通过。

该机制是**归档文件级保留，不是逐条消息TTL**，因此不能保证每条消息严格在14天整删除；生产模板还设置1天文件期限。成功证明真实配置到期清理生效，不证明已实际观察14天，也不证明512MiB生产容量或限流完整性。测试finally停止其holder/socket/service，合成配置与剩余日志保留作证据；没有清理实际客服日志。真实生产读取角色与容量/限速/平台故障仍需独立验收。

fixture `tests/fixtures/linux_lab_journal.py` 首次安装拒绝覆盖；后续用 `--verify-only`。`--harden-existing` 只用于从该已知旧配置升级，完成后不能再次套用。所有路径、hostname、marker和测试库门禁都须保留。不要对全局 journal 执行 vacuum 来让测试通过，也不要读取其他服务/真实生产日志。

本项不替代 Collector/日志平台、真实值班送达、费用路径、新制品或整个S2的验收。

本轮后端完整本地回归：516 passed / 370 skipped，27.46秒。未配置的外部集成项仍未验证，不将跳过计为成功。当前尚未创建S2完成提交。
