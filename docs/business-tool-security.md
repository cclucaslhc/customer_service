# 真实业务工具鉴权、审计与幂等规范

状态：设计方案。当前 lookup_order、lookup_shipping、check_refund_eligibility 是模拟工具；show_contact_form 不创建真实工单。本方案实施并通过沙箱验收前不接通生产写操作。

首个正式适配器选**只读本人订单查询**。接入前需有业务系统沙箱提供的接口定义、租户与用户到订单的权威归属关系、字段分级/脱敏规则、短时服务凭据供应方式和审计关联 ID。不能把当前示例订单号加手机号后四位当作本人订单的授权证明；缺少这些条件时，认证会话继续安全拒绝并引导人工处理。沙箱可用后先验证跨租户/他人订单拒绝、重复查询限额、凭据撤销、响应字段白名单和外部故障，不扩展到退款写操作。

## 服务端工具边界

模型只选择工具和业务参数。权限、租户、凭据、审批和重试政策由工具网关执行。建议调用链：PydanticAI → ToolService → PolicyEngine → BusinessAdapter → 业务 API；ToolService 使用 Repository 持久化操作、审批、审计和 Outbox。

ToolContext 从可信登录态/Worker 任务中构建：tenant_id、actor_id、roles/scopes、conversation_id、run_id、attempt_id、tool_call_id、request_id、operation_intent_id。模型不能输入或覆盖这些字段。客户、客服代理和系统任务分别记 actor 与 represented_user，限制代办权限；异步执行和审批恢复时重新验证权限撤销和资源归属。

业务系统采用短时服务凭据/OAuth/mTLS，并验证租户/用户/订单访问范围。内部服务凭据不等于客户有权查看全部订单。订单号和手机号后四位不足以作为生产认证。

已实现的认证前置底座：0003 提供可撤销数据库身份，0004–0007 提供令牌绑定的业务 Repository，0008 提供跨实例登录预算与两个 scrypt 并发租约。内部登录应走 PostgreSQLRepositories.login/LoginService；失租签发拒绝、DB 故障失败关闭。公开 HTTP、CSRF/可信代理、前端登录、Agent/ToolContext 身份传递尚未接通；工具仍为模拟数据，不能从这些底座推断真实订单访问已鉴权。准入细节见 [迁移与认证底座](postgresql-migration.md)。

## 工具分级

| 级别 | 示例 | 控制 |
| --- | --- | --- |
| 只读 | 查询本人订单/物流/退款条件 | 身份与资源归属、字段脱敏、查询限额、禁止批量枚举 |
| 低风险写 | 创建售后诉求/工单 | 确认必要信息、幂等键、审计、外部流水和结果查询 |
| 高风险写 | 退款/取消订单/改地址 | 参数绑定审批、限额/时效、权限复查、幂等与对账 |

未知工具默认拒绝。参数使用 Pydantic 约束，金额用 Decimal/货币最小单位，枚举和最大长度受限。PolicyEngine 校验状态、归属、金额上限、业务规则和审批；工具错误归一为 not_found/denied/validation_failed/temporarily_unavailable/unknown 等，不能把原始敏感响应交给模型。

## 可恢复审批

使用 PydanticAI requires_approval/DeferredToolRequests；保存完整 PydanticAI 消息（含 tool_call_id）、已校验参数、Prompt/模型配置版本和工作流 ID。当前文本历史不能恢复待执行工具。

approval_request 保存 operation_id、参数哈希、批准对象/金额/影响、过期时间、审批者权限、状态/version。用户界面显示确定性摘要，不能仅显示模型生成的“是否继续”。批准/拒绝用 compare-and-set 单次提交，修改参数使旧批准失效；恢复时复查审批与资源状态，通过 DeferredToolResults 接续工具循环。每次恢复为新 run/attempt，关联同一 workflow/operation，避免复用旧 run_id。

等待期间不占并发槽/长 HTTP 连接。服务重启从数据库恢复待处理状态；长流程日后可交给 Temporal，保持 operation_id 与幂等语义不变。

## 幂等与操作状态

持久化 tool_operations：operation_id、tenant_id、tool_name、operation_intent_id、idempotency_key、payload_hash、state、外部流水、结果摘要、租约/fencing token、重试数、时间。唯一约束为 (tenant_id, tool_name, idempotency_key)。同一个用户确认的业务意图跨模型尝试、网络重试、Worker 接管使用同一 key；用户明确发起新的有效操作才生成新 intent。不能用每次新生成的 run_id 充当稳定幂等键。

状态建议：pending_approval → ready → executing → succeeded / failed / unknown；denied/expired 为审批终态。unknown 表示外部执行可能成功而本地无法确认，需要 reconciliation，不自动视为 failed。

流程：

1. 鉴权、校验和政策判断；规范化参数生成 hash。
2. 事务插入操作与审计/Outbox，唯一冲突时读取原操作。
3. 相同 key 不同 payload 返回冲突；已成功返回原结果；executing 返回处理中，不再次调用。
4. Worker 短事务领取，网络调用在事务外执行，传同一幂等键给外部业务系统。
5. 收到确定结果后 CAS 保存状态、流水、审计和待发送事件。
6. 网络超时或崩溃后标记/发现 unknown，按外部 key/流水查询，再确认成功或安全重试。

本地唯一约束只能防止本地重复调度，不能保证跨系统 exactly-once。必须依赖外部 API 幂等协议或可查询业务意图。若两者均不提供，超时后只能人工对账，不自动重试写操作。即使有 fencing token，已经发出的外部请求也不能被撤回。

取消 Agent 只终止后续模型/工具调度；已执行退款不会自动回滚。取消后仍保留对账任务。补偿操作是独立、有权限和幂等的业务动作，不能在失败回调中盲目执行。

## 审计与 Outbox

工具操作和审计在同一事务写入；记录实际 actor、represented_user、工具/参数摘要、政策版本、审批、模型/Prompt、外部流水、状态变更和 trace。敏感参数采用不可逆/受控摘要，完整结果如必须留存则加密并限制访问。

审计追加写入，应用角色不能 UPDATE/DELETE，独立归档支持完整性校验与留存；普通管理员不得改历史。数据库管理员仍有能力变更数据，因此高要求审计需异地/WORM 归档，不能仅声称 SQL 权限提供防篡改保证。

Outbox 保证本地事务与事件登记一致；dispatcher 至少一次投递，消费者以 event_id 去重。Outbox 不使外部退款调用与本地数据库变成一个事务，外部成功后本地崩溃仍需要幂等查询与对账。

## 验收场景

- 越权租户/他人订单/撤销客服权限：拒绝且审计，不向模型暴露资源内容。
- 模型伪造 actor、恶意参数、Prompt 注入：不能提升权限或更改已批准参数。
- 同 key 多次/并发调用：外部只有一个效果；同 key 不同参数返回冲突。
- 审批重复/过期/拒绝/权限撤销：不能执行；审批修改必须重新确认。
- 外部成功后本地崩溃/超时：对账确认，不能重复退款。
- Worker 失租后迟到：旧提交被拒；外部调用通过同 key 去重。
- Agent 取消、模型切换、数据库 PITR：操作结果保留/对账，不能重放过去写操作。
- 审计归档与通知失败：有重试/告警；Outbox 消费者重复投递无重复效果。

仅使用授权沙箱和合成身份，结果记录操作计数/状态，不输出真实联系方式。关联：[架构](production-architecture.md)、[迁移](postgresql-migration.md)、[观测](observability.md)、[Runbook](deployment-dr-runbook.md)。
