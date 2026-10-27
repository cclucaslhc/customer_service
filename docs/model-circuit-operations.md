# 共享模型熔断操作规范

状态：`0025_model_circuit` 已在隔离 PostgreSQL 和合成模型上验证；正式双机、数据库切主、真实供应商和告警接收人尚未验收。

## 机制与数据边界

- 每租户、供应商、模型一条 PostgreSQL 状态。60 秒内 3 次 429、408/5xx 或类型化传输故障打开 30 秒；关闭状态下的 401/403、预算拒绝和取消不计作供应商健康故障。half-open 探针若未证明健康，则保守重新打开。
- 打开时不进入供应商、不建付费 attempt；有合格备用时仍须分别通过租户授权、目标配置、价格、Token 合同、总预算及 Run 租约。未授权的在线供应商不会收到用户消息或知识片段。
- 冷却后只放一个 owner/generation fenced half-open 探针。探针租约长于本节点配置的 Run 总时限；生产双节点必须使用相同超时配置。成功关闭，健康故障重新打开；过期探针可接管，旧结果不得关闭新状态。
- PostgreSQL 不可用时许可获取失败，模型请求不得绕过共享门控；Redis 不参与本熔断状态。数据库恢复可能回滚短期熔断历史，必须按恢复 Runbook 核对并重新做故障演练。

## 私有指标和告警

仅在显式认证的 loopback `/metrics` 提供 `customer_service_model_circuit_collection_success`、`customer_service_model_circuit_collection_timestamp_seconds`、`customer_service_model_circuit_states{state=closed|open|half_open}` 和 `customer_service_model_circuit_openings_total`。无租户、模型、用户、金额、Prompt 或密钥标签。数据库只读聚合设短超时；失败导出 `success=0`，不回显异常。多个 API 实例查询同一数据库会得到相同全局计数，Prometheus 必须**只指定一个**该聚合采集 target 或做明确去重，不能相加。

[规则](../ops/prometheus/model-circuit-alerts.yml)覆盖采集失败/停更、5 分钟内新打开和 half-open 长时间停留。`promtool check rules` 与 `promtool test rules` 使用[固定场景](../ops/prometheus/model-circuit-alerts.test.yml)验证；隔离 Prometheus→Alertmanager→回环合成 webhook 已收到 half-open firing/resolved。该测试不等于正式值班接收人收到通知；部署时必须指定并演练值班接收人及恢复通知。

## 值班处置

1. 先确认私有采集 target 和 PostgreSQL 健康；采集失败时不得把全零指标解读为全部模型健康。
2. `Opened` 告警只提供总量；由授权的受控只读数据库角色按租户归属查询 `model_circuit_state` 的 `state`、`updated_at`、`open_until` 和 `generation`，不得把查询结果或模型配置写入公开标签/日志。
3. 同时核对对应 Run 的 `model_usage_attempts`、预算预留与供应商状态页/合同限额。不要因 429 盲目重放整个 Agent、工具或未知送达请求。
4. 无合格本机模型、未获授权的在线备用或预算不足时保持安全失败；不要以临时改写 `model_circuit_state`、删除行或打开跨供应商授权来“消除告警”。
5. 供应商恢复后观察单个 half-open 探针、实际模型与费用账本；若持续异常，保留证据并按事故流程处理。数据库切主/恢复后的探针及旧 owner 要另做 fencing 检查。

生产阈值、费用政策、容量、接收人和恢复条件必须由产品/运维/安全共同确认，当前固定阈值仅为隔离实现，不授权上线。
