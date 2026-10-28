# S2 最终验收账本

更新：2026-10-29。当前状态：**验收进行中，未宣布完成**。本页仅对应用户收窄后的S2；不把S3–S7或整体生产上线混为本轮完成标准。

## 用户明确调整

- 保留企业微信、钉钉、邮箱三个通知接口；跳过真实外部告警送达。不再要求Gmail密码/OAuth，8011安全配置页已停止。此项为豁免，不是送达通过。
- 跳过真实DeepSeek账单对账。实际生成和Token回执仍验证；首次探针取证失败保留未知，费用不能伪造为0。此项为豁免，不是供应商账单通过。
- 授权使用管理UI已有DeepSeek配置；未扫描环境历史Key，也未自动复制密钥到测试VM/PG。
- 授权推送现有公开GitHub仓库。先用验收分支跑CI；main保留基线7fd57f7，待收尾后第二笔提交。不要求用户合并。

## 六项门槛与证据

| 要求 | 当前证明 | 当前判定 |
| --- | --- | --- |
| 结构化日志、脱敏、留存、读取角色 | 白名单出口；node2 journald命名空间、非提权拒绝、重启与加速到期归档清理；14day/512MiB配置受限且无全局转发 | 机制/隔离实机已验证；不声称已等待14天或完成生产负载认证 |
| HTTP/Agent/model/tool/RAG/database OTel及关联ID | 私有Collector白名单/过滤/故障恢复；实际API→独立ChatWorker257 Span同链及唯一attempt；知识/对象/SQL真实组件专项 | 已有实机和组件证据；不声称已有生产Tempo查询平台 |
| 低基数Prometheus/Grafana/Alertmanager | 私有出口、真实查询/六面板/401/NO DATA；真实认证接收端firing/resolved与取消并发保护；修复后监控制品安全扫描 | 协议/机制已验证；外部送达按用户豁免 |
| 模型/Embedding/Rerank用量、版本价格、未知与对账 | PG追加账本；真实固定离线权重/tokenizer计量；DeepSeek合成生成input21/output3；首次取证错误未知保留 | 核心机制已验证；真实账单按用户豁免，离线基础设施金额仍NULL |
| 日/租户/Run预算、软硬门禁与原子预留 | 真实隔离PG并发/未知/恢复和角色测试；实际Prometheus→Alertmanager规则联动；离线推理不伪造在线收费 | 已有真实PG/规则证据，不将未知费用当0、不承诺未来在线Embedding可旁路预算 |
| 锁依赖、非root/不可变、扫描、可重建CI制品 | OTel1.45.1、Vite8.3.5；独立无缓存OCI一致、SBOM及严格扫描；GitHub真实CI正在修复/运行，OIDC证明已接线 | **待当前CI全绿、OIDC验证及最终Git交付** |

## 最新本机证据

- OTel升级后的跨模块集成：192 passed /0 skipped，276.39秒，仓库外JUnit `s2-after-ci-fixes-20261029.xml`。
- 普通后端前批585 passed /384 skipped（29.02秒）；后续增加绑定检查测试，待最终计数。前端40 passed、build成功、npm audit 0。
- 当前应用OCI manifest `sha256:5d9fae63aec890b1dc5ce4d46d08cac8db58601815d17580ee660f99e1f7d8ae`，config `sha256:17238a5c0c91c77066a53e610b5fbe49b8667debdda4ba3a9127077a2091a4ee`。默认/独立no-cache builder逐blob相同；0 HIGH/CRITICAL；SBOM14 Debian/145 Python。文件为实验室`s2-ci-fixed{-rebuild}-20261029.oci.tar`，报告在`s2-r2-output-20261028/s2-ci-fixed-*-20261029.*`。
- GitHub CI首轮暴露并已修复OTel新CVE、Vite高危、无关MinIO拉取、代理就绪竞态，以及Linux路径/导入/临时挂载所有权。没有关闭审计、改root或降低沙箱要求来通过。各失败批次不作最终通过证据。
- 当前验收分支源码`14f3e77359617fd5cb855149950c4f241798e3ed`，运行[40725358853](https://github.com/cclucaslhc/customer_service/actions/runs/40725358853)。前端、后端、NGINX通过，PG/Redis和制品作业仍在执行；这不是最终全绿声明。
- 后续该轮真实Worker OOM/before_commit/after_commit三项通过（54.28秒）；停在Docker默认driver不支持OCI exporter。已修复为两个独立、固定摘要的BuildKit docker-container builder并推送`92068b2e32a9c012f3d83f3b5ac25513cd86602b`。旧轮失败不作成功证明，也没有通过禁用沙箱、改root或跳过测试来绕过。
- 该修复轮[40726335704](https://github.com/cclucaslhc/customer_service/actions/runs/40726335704)已取得后端、前端、NGINX、PG/Redis四作业成功。真实PG/Redis前轮232 passed/1显式本地权重deselected/0 skipped（859.57秒），不与本机192组相加。镜像作业的实际沙箱/CLI/OOM、独立OCI重建、SBOM、应用扫描与清单绑定均通过，随后Grafana完整镜像在新漏洞库中检出`CVE-2026-116587`（go-jose/v4 4.1.4，修复4.1.6），因此该作业失败，未签发OIDC或发布最终制品。
- 本机以新独立漏洞库缓存复现该Grafana单项HIGH，旧0 HIGH结论只属于旧库时间点。已基于官方固定v13.2.5源码commit重建服务端并升级go-jose至4.1.6，版本13.2.5+cs.s2；模块校验、编译、完整镜像新库扫描均通过（0 HIGH/CRITICAL）。保留官方UI和固定Prometheus插件，不删认证组件或扫描清单。源码/模块均有哈希或Go checksum数据库验证；国内代理EOF后启用可信官方源fallback，校验未关闭。修复镜像六查询、401和浏览器渲染已通过；新远端CI仍待执行。
- 当前普通后端最终批次587 passed/384 skipped（30.74秒）；跨模块集成仍为上述192/0。未设置真实环境而跳过的384项仍不计通过。

## 发布信任

新增GitHub OIDC构建证明只在push作业、全部安全/功能门禁后为绑定清单签发。没有生成或复用长期发布私钥；消费者须校验仓库、工作流、source-ref/source-digest、GitHub托管runner及SLSA类型，再执行`ops/release/evidence.py check-binding`核对OCI/SBOM/扫描原始字节。只有签名有效或只运行check-binding均不足以建立完整信任。当前实际证明/下载验证仍待该CI成功，不使用测试Ed25519身份冒充正式发布信任。

最终main提交与其CI成功前，不勾选完整S2。不宣称已部署生产、双物理机HA、真实业务工具或RAG盲测通过。
