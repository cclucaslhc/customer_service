# S2 正式通知接入

## 当前验收决定（2026-10-28，优先于下述历史待配置说明）

用户已明确要求跳过真实邮箱告警验证，不再要求Gmail应用密码或OAuth；三种出站接口保留。真实邮件/企业微信/钉钉送达统一标为**未验证/本次豁免**，不能冒充通过。原8011本机安全配置服务已按确切进程身份停止，未读取或删除任何保存配置；8000客服服务不受影响。

`app/alert_receiver.py`及显式CLI已实现独立同机认证接收端：loopback来源、精确Host、独立Bearer、64KiB请求体、注册告警名、最多32实例并按告警名聚合为至多firing/resolved两封通知；转发时丢弃所有原始labels/annotations。单在途通知槽、忙时429；请求断开/取消不提前释放仍在发信的线程。指标仅固定outcome计数，健康接口不冒充SMTP已验证。`alertmanager-notifications.yml.template`提供group_by alertname、send_resolved、私有token文件及禁止重定向配置。接收端目前不是主客服API的公共路由，也没有启用真实发送。

真实本机Alertmanager0.28.1→当前认证接收端→合成回调已验证firing与resolved，未授权请求403，敏感annotations未进入回调；加上取消/并发保护共7 passed，2.83秒。此项不发送邮箱，不绕过用户的跳过决定。SDK/离线SMTP和表单专项23 passed；最新后端全量584 passed /384 skipped，29.14秒。模板测试副本缩短等待/超时，仅用于机制验收。

发送适配器本身不自动重试，但Alertmanager可能对503/超时重试并产生重复邮件。没有持久化通知去重或exactly-once承诺；发送失败保留未知并计数，不能据此自动重放业务操作。生产70秒接收超时仍需真实邮件链路验证，本轮已被用户豁免。

更新：2026-10-29。用户确认保留企业微信、钉钉、邮箱三个接入，随后明确跳过本轮真实外部告警送达验证；不再等待Gmail凭据或继续OAuth。真实联系方式不写本文档、测试或日志。跳过为用户豁免，不能记为送达通过。

## 当前交付与边界

`app/alert_notifications.py` 提供 `send_wecom`、`send_dingtalk`、`send_email` 三个出站接口，不是公开HTTP接口。`app/alert_receiver.py`及`scripts/serve_alert_receiver.py`提供显式启动的私有接收服务：回环peer/Host/Bearer鉴权，64KiB请求上限、单组最多32实例、单一真实在途发送；取消请求不会提前释放仍在运行的SMTP线程。只从受控配置选择目的地，禁止由入站载荷指定URL/收件人。模板见`ops/prometheus/alertmanager-notifications.yml.template`；没有自动启用真实通知。

只发送仓库规则中注册的告警名称与firing/resolved状态，不发送原始labels、annotations、租户、用户、文档、模型Prompt、日志或异常内容。规则名新增时必须同步注册表及测试。provider_accepted仅表示供应商接受，不表示收件人已读；正式验收仍需用户确认收到触发及恢复通知。

- 企业微信机器人：仅官方HTTPS域名与精确路径，拒绝额外query/重定向，显式配置机器人密钥；不自动探测或复用历史配置。
- 钉钉机器人：同样限制官方目的地，要求显式签名secret，使用timestamp/HMAC-SHA256；原access_token与签名同时保留。不默认@所有人。
- 邮件：SMTP_SSL465，验证服务器证书/主机名，显式登录；单一发件人/收件人，拒绝CRLF注入。不支持隐式明文SMTP或自动降级TLS。

机器人响应有16KiB限制，非0 errcode/非200拒绝；网络异常保留delivery_unknown且不自动重试。邮件网络/提交异常同样保留未知，不把超时当作未发出；任何上游重试均可能造成重复通知，不能宣称exactly-once。密钥、签名URL与SMTP密码不进入安全错误文本。

## 后续可选 Gmail 配置（本轮不要求）

使用 `smtp.gmail.com:465`。Gmail账户通常需要由用户启用两步验证，并生成可撤销的应用专用密码；不能在聊天中提供密码，也不将普通登录密码用于SMTP。组织账户若禁用应用密码，应由管理员批准OAuth或受控邮件中继，不关闭TLS来绕过。

已实现加密本机配置及回环安全入口，但用户取消后8011配置服务已停止。没有真实邮件送达证据，不要求用户继续提供密码。真实原生Linux Alertmanager→当前认证receiver→合成callback的firing/resolved协议已通过；这不是向邮箱或机器人实际送达。provider_accepted、receiver存活和合成测试均不等于收件确认。后续要启用真实通知，仍需用户安全提供独立凭据、批准目标并另验触发/恢复送达。
