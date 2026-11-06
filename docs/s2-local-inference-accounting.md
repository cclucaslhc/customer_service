# S2 离线 Embedding/Rerank 尝试与用量证据

日期：2026-10-28。本轮新增 `app/local_inference_usage.py`，并接入 LocalEmbeddingGateway、LocalReranker、KnowledgeService（包括每Run路由clone）和 RagIndexWorker。未新增在线模型供应商，不读取环境历史密钥。

## 为什么不复用聊天账本

现有model_usage_attempts仅允许purpose=chat且要求真实Run租约。索引构建不是聊天Run，离线分类重排也不能伪造供应商output_tokens。本地尝试因此采用已受保护的audit_logs追加started/finished回执，沿用既有RLS/运行角色不可UPDATE/DELETE、备份恢复及维护保护，不改写历史迁移或为其放宽聊天约束。

用途为embedding_query、embedding_document或rerank。模型仓库标识和权重SHA固定；开始前写入UUID回执，随后才执行tokenizer/模型。记录项数和最大输入Token边界；实际输入来自tokenizer attention_mask之和，包含真实提示前后缀、排除padding，并受截断后边界约束。不使用字符数冒充Token。

结束使用由attempt_id派生的确定性UUID，事务advisory lock保证并发只有一个终态；相同结果幂等，不同结果冲突。没有对audit行使用FOR UPDATE，因为正式运行角色被明确撤销UPDATE权限。失败/取消/未知分开，Token未知为NULL，不填0。提交终态失败不返回“已记账”的结果；失权/数据库/路由及记账错误不能当作普通模型离线故障降级。进程失联留下无终态started，不能当成功或自动删除。

## 归属与隐私

知识查询按原认证principal重新鉴权并复查固定RAG route；每个KnowledgeService/route clone持有独立计量器，不在共享模型对象上修改全局租户状态。索引Worker复查固定profile，沿用数据库控制域归属：actor为实际DB角色、actor_id为空，metadata明确target_tenant_id，不假造租户用户。

不保存query、文档正文、向量、分数、权重目录或任何凭据。purpose、模型标识/摘要、批大小、边界、Token、状态均为固定结构。账本中的model_id来自固定离线manifest，不是用户任意文本。

本地操作标记`api_billing=not_applicable_offline_v1`；`infrastructure_amount=NULL`。这只说明没有在线供应商API调用，**不说明CPU/GPU/电费/折旧为零或已结算**。没有将其并入聊天货币预留或宣称基础设施硬预算完成。现有生产装配只支持固定本地RAG模型；未来收费在线Embedding/Rerank必须另加报价、原子预算预留、供应商账单证据及外发授权，不能直接套用此offline标记。

未绑定数据库的独立本地模型诊断脚本不当作生产调用计费证明；真实模型加载/Token结果复验、对账查询/聚合、费用治理和账单输入仍需收尾。本轮不是“每个成本项已全面核算”。

## 已验证与限制

- 本地计量/真实PG回执/归属/幂等并发/失权/索引Worker专项17 passed，7.05秒。索引测试实际入PG向量并核对控制域回执，但模型和Tokenizer观测使用合成stub，不证明实际权重质量或真实Tokenizer准确性。
- 真实专用`::1:55433`隔离数据库最小权限角色组1 passed，26.93秒：API及Worker均无审计UPDATE/DELETE权限而可成功追加计量；没有修改正式grants。
- 检索异常分类及本地入账联合35 passed，7.06秒：鉴权/路由/数据库/记账错误不静默降级。初次新增异常测试误用AuthenticationError构造参数导致收集失败，修正测试后通过；未改异常语义以迁就测试。

本段之后已补实际权重/Tokenizer及汇总告警的增量，见后续记录；长时/失联与备份恢复定向证明、在线账单与基础设施报价仍不能用合成数据代替。S2仍未全部完成，第二笔Git提交尚未进行。

本轮最终完整本地后端：540 passed / 376 skipped，27.38秒。新增真实PG测试在未提供连接的此轮全量中跳过，其直接证据来自上列显式隔离数据库专项，不能简单相加为一次全量零跳过。

## 后续低基数汇总与未知告警

`LocalInferenceMetrics` 已接入显式启用的私有metrics端口。只读事务、750ms statement timeout/100ms lock timeout、外层采集deadline；从追加回执聚合固定3种purpose的尝试/完成/失败、tokenizer观测输入量、终态未知Token、无终态及超过5分钟无终态。全库聚合只能选单个target或明确去重，不能对多API实例相同账本相加。

采集失败/无效用途/孤立终态/重复终态等显示collection_success=0并省略所有未知计数，不输出SQL或异常内容。已观测Token的总和不等于所有尝试总Token；必须同时查看unknown_token_usage_total和unresolved，不得把未知部分计为零或已结算。指标不含租户、用户、模型、权重、金额或ID标签，也不导出任何正文。

规则 `ops/prometheus/local-inference-alerts.yml` 对缺采集/采集失败/过期证据及陈旧未终结尝试告警，附本操作文档作为runbook。真实promtool检查/场景评估2 passed，0.43秒，验证firing与条件恢复；实际私有指标/装配相关12 passed / 2 skipped，7.47秒，其中跳过是未在该专项设置真实Prometheus路径的既有采集测试。陈旧测试通过新增明确合成旧回执模拟，不修改已有证据或数据库时钟，不声称真实等待5分钟。

快速查询（需要专用私有采集target）：

```promql
customer_service_local_inference_collection_success{job="customer-service-private"}
customer_service_local_inference_stale_unresolved{job="customer-service-private"}
customer_service_local_inference_unknown_token_usage_total{job="customer-service-private"}
```

处置先核对采集健康，再定位授权账本中的started/finished；不要删除started来清告警，不重放未知外部操作，不通过修改时间戳伪造恢复。确实运行中的长任务也可能超过5分钟，告警只是需要核对，不构成自动重跑/释放授权。

真实Prometheus→Alertmanager→回环合成webhook的本地推理专项：1 passed / 2 deselected，38.22秒，收到firing和resolved。只对隔离规则副本缩短pending时长，仓库2分钟规则未改；指标由合成端点模拟陈旧状态，本项不单独证明PG账本到真实值班渠道全链，PG聚合由前述专项覆盖。

本批完整本地后端：541 passed / 382 skipped，26.98秒。新增真实PG与Prometheus用例未配置环境时会跳过，不能把本地全量数字当成外部集成全部通过。

## 实际固定权重与 Tokenizer 入账复验

显式使用此前隔离目录 `C:/Users/56226/AppData/Local/Temp/cs-rag-models-20261019`，两份权重由现有manifest核对大小及SHA256；不下载、不读取公司文档、不调用在线服务。本机torch2.9.1+cu128、CUDA可用，实际执行Qwen3-Embedding-0.6B与Qwen3-Reranker-0.6B。

`tests/test_local_inference_weights.py` 在真实隔离PG创建授权租户，运行1次查询Embedding、1次双文档Embedding和1次双文档重排。 independently按实际tokenizer逐条编码计算查询指令与重排前后缀，排除padding并考虑max_length截断，与3条持久化终态回执逐项精确相等；向量维度1024、合成相关文档相似度及重排顺序通过。3条均completed/tokenizer_observed，基础设施金额仍NULL。

结果 **1 passed / 0 skipped，18.78秒**。这是实际权重计算与记账联动，不再仅是stub；但不证明盲测质量、生产负载/延迟、Token供应商计价或完整模型目录的密码学签名。测试后释放模型/GPU缓存，随机测试数据库由fixture精确清理。
