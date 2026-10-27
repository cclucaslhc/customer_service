# Owner灰度控制CLI

## 发布审批入口

`scripts/rag_release_control.py`提供`approve/reject/check/execute`，数据库输入方式同下述灰度控制。`--receipt-file`为私有单行JSON，字段严格为tenant_id、release_id、expected_version、expected_body_sha256、current_binding，拒绝额外token/密码字段和重复JSON键。除check外须`--confirm-change`；approve/reject仅从当前交互终端隐藏提示获取指定管理员的当前session token，不从参数、环境、历史文件或管道获取。非交互环境、隐藏输入不可用时直接失败，不回显。token只在调用期间传递，未持久化；Python字符串清引用不构成内存安全擦除保证。

每次批准分别执行，各自输入不同指定管理员当前有效凭据，并核验新version后更新受控回执；同一管理员重复批准不能凑成两人。check仅核验当前可执行条件，execute仍在自身事务重复核验；带rollout_plan的发布禁止execute，必须使用灰度流程。该入口不自动创建管理员、登录会话、质量报告或新release，不改变原始凭据供应与业务批准责任。不要让普通管理员共享owner数据库文件；生产部署仍需受控执行身份或正式审核API来分离数据库控制权和人工审批权。当前CLI是受控运维入口，不是完整生产审批UI。

`scripts/rag_rollout_control.py`是显式运维入口，不是Web管理API。数据库owner凭据须通过`--database-url-file`私有文件提供；直接`--database-url`只允许回环测试。使用`--control-file`指定与[健康监控](rag-observation.md)同形状的私有单行JSON，含当前tenant/rollout/version/plan及release回执和可信实际runtime binding。启动要求恰为当前schema head，不从环境发现凭据。

动作：`observe`仅读取聚合窗口；`start`仅从staged进入第一阶段；`advance`仅推进下一冻结阶段，最终阶段同事务发布；`pause`运行中归零；`withdraw`撤回未完成灰度；`prepare-reversion --new-index-id UUID`从completed发布准备新回退候选，不直接切回。除observe外都必须给`--confirm-change`，这只是操作者明确意图，不代替审批授权。入口不创建审批、不恢复paused流量、不跳阶段或接收调用方统计报告。

示例（路径为目标Linux预先受控创建的文件，不含内联凭据）：

```sh
/opt/customer-service/venv/bin/python scripts/rag_rollout_control.py --database-url-file /etc/customer-service-control/owner-url --control-file /etc/customer-service-control/rag-health.json observe
/opt/customer-service/venv/bin/python scripts/rag_rollout_control.py --database-url-file /etc/customer-service-control/owner-url --control-file /etc/customer-service-control/rag-health.json --confirm-change advance
```

成功输出结构化receipt；observe输出聚合指标，无原始问题、答案或会话摘要。错误只打印异常类型，退出1；参数缺失退出2；人为中断130。不要将健康监控脚本的“2代表已暂停”语义套用到此CLI。回执变更直接失败，不自动重读数据库来追随；操作者核实实际状态和部署binding后更新受控文件，禁止简单复制旧审批定义。推进时旧watcher会因旧回执退出，必须协调停止、更新并显式重启，当前没有自动无缝接管保证。

审批创建/双人交互和可信质量报告来源仍需正式管理流程。owner CLI不会把不可信自报质量变成独立验收，不能替代业务盲测；使用前须遵循独立账户、文件ACL、TLS与值班审计要求。生产管理UI、正式部署与双机故障验收尚未完成。
