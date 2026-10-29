# S2 Grafana 仪表盘：实机验收与交付边界

日期：2026-10-28。此项是 S2 的一部分，不是完整 S2 完成或生产部署批准。

## 本轮实现

`ops/grafana/customer-service-s2.json` 提供六个面板：实例抓取健康、HTTP P95、预算采集健康、预算硬拒绝速率、日志出口结果、Trace 出口结果。HTTP P95 明确不冒充模型 TTFT 或 SSE 连接时长。未提供币种金额或租户明细仪表盘，不用拒绝计数冒充费用。

共享数据库预算聚合只允许选择单个 instance，不对多个 API 上相同全库聚合相加。变量不提供租户、用户、模型或原始路径。查询没有 `or vector(0)`，缺失数据保留 NO DATA；预算健康面板明确提示 NOT HEALTHY。浏览器验收发现默认绿色会造成误读，已修正为空值灰色、0 红色、1 绿色，并刷新确认。

## 实际执行

`scripts/verify_grafana.py --app-image <本批显式构建镜像> --grafana-image <本批已扫描监控镜像> --keep` 创建 UUID 命名的独立 Docker 网络和三个测试容器，Grafana 仅映射宿主回环随机端口；Prometheus 和真实 private_metrics_listener 共用其网络命名空间且只监听 loopback，无独立宿主端口。当前脚本不再默认复用历史应用镜像；下述旧批次证据仍按当时运行时记录，不倒写成最新制品。开启专用合成认证，禁用匿名/注册和 Grafana 使用报告/更新检查。网络为独立 bridge，不声称实现出站防火墙；初次 internal 网络模式未能提供宿主发布端口，已清理失败批次后明确改为专用 bridge。

镜像固定：

- Grafana12.3.1：`sha256:2175aaa91c96733d86d31cf270d5310b278654b03f5718c59de12a865380a31f`
- Prometheus3.5.0：`sha256:63805ebb8d2b3920190daf1cb14a60871b16fd38bed42b857a3182bc621f4996`
- 应用依赖运行时采用旧 head0037 验证镜像 `sha256:4b543714cd17938c885e5cdc05ca3290daea07322114b59d3a0e781e6043a725`，但**只读挂载当前 app 源码与本批合成 fixture**。不是最新应用镜像构建证明。

实际成功批次 `cs-s2-grafana-2947a0497363`，本机 URL `http://127.0.0.1:50122`，数据仅为每秒生成的合成 HTTP 观测，无真实公司文档、模型请求或供应商凭据。六个面板的 PromQL 均通过真实 Grafana 数据源代理调用 Prometheus 并成功执行；up=1，P95 有真实样本；预算未配置数据库，结果为空，不能当作健康。无登录数据源 API 返回401。

浏览器实际登录合成账号并打开 `/d/customer-service-s2`，六面板渲染通过。预算无数据颜色修复后再次刷新截图确认。截图和 JSON 查询结果保留于本批仓库外临时证据目录 `C:/Users/56226/AppData/Local/Temp/cs-s2-grafana-2947a0497363-bdob19zv/`，不提交运行数据。成功栈暂保留供后续 S2 验证；默认不带 keep 的脚本会在 finally 删除自己创建的确切容器和网络，不删除全局资源或证据目录。

## 2026-10-28 修复版监控制品与复验

官方 `13.2.5-distroless-slim` 固定摘要 `sha256:84157cef34688322cd542b3137f4c6928d1f491143da4228be1f764aa643de52` 基础扫描通过，但初次功能验收暴露它会在运行时下载未锁插件；该结果不作为完整制品证明。现提供 `ops/grafana/Dockerfile`：官方构建阶段下载 Prometheus13.2.5 amd64 插件，SHA256 `d24df3eaff9ae61f00d7e857f508fc306912dcf50d657150d1e472e851799fc4` 校验；只加入该插件，关闭插件预安装、自动更新和更新检查，未放宽插件签名检查。插件目录为镜像内 `/opt/customer-service-plugins`，不是旧可写目录。zip解压丢失可执行位的首次版本未通过功能检查；已给精确backend二进制0555权限并重建，未忽略错误。

最终监控 manifest `sha256:1b03b2eb88da8ff5c4994742070cfcffd9b7fdde2a6fcd27727d7f88219255d8`，config `sha256:39769cf5f449464879ddf87122463c94e72b31d974fa13b51001e78928e904a4`。固定 Trivy 对完整镜像（含可执行插件）扫描 exit0、0 HIGH/CRITICAL；报告在实验室 `s2-r2-output-20261028/grafana-pinned-plugin-r2-vulnerabilities.json`。先前无可执行位版本的扫描不替代此报告。

成功批次 `cs-s2-grafana-fbca3b683c22`，回环端口57476；应用显式采用当前 `customer-service:s2-verify-20261028-r2`。实际 Grafana→Prometheus→私有出口查询六面板全部成功，up=1及P95有样本；缺失预算/出口数据仍为空。未登录返回401；浏览器确认六面板与灰色 NOT HEALTHY 提示，无插件错误。结果和截图在仓库外 `C:/Users/56226/AppData/Local/Temp/cs-s2-grafana-fbca3b683c22-9wzfg8zi/`。CI新增专用监控构建、完整镜像严格扫描及真实查询步骤；只验证本机等价步骤，未宣称远端CI已运行。

## 历史失败候选与仍需完成

后续 Prometheus 安全收尾：旧3.5.0镜像补扫未通过（110个包级 HIGH/CRITICAL 发现、52个不同CVE），已改为官方3.15.1固定摘要 `sha256:fa3302279710be2f21a8301ec17a8b4f2301fd829bd113839b3b6b02b6f191a2`，扫描exit0。报告 `s2-r2-output-20261028/prometheus-current-vulnerabilities.json`。修复版Grafana搭配新Prometheus实际重跑六查询及401全部通过，新成功批次 `cs-s2-grafana-49576b228e24`、回环56510，证据 `C:/Users/56226/AppData/Local/Temp/cs-s2-grafana-49576b228e24-l4egs59g/`；结果记录三个实际容器image ID。前批57476栈已精确移除，保留截图和JSON，避免继续运行旧Prometheus。Grafana镜像未变，前批浏览器截图仍是该镜像的渲染证据；本次Prometheus更新是实际API查询复验，不倒写成再次浏览器验收。

完整Grafana镜像另以 `docker save` 归档、无Docker socket挂载的Trivy输入扫描复验exit0；报告 `grafana-pinned-plugin-r2-archive-vulnerabilities.json`，明确包括Grafana主程序和可执行Prometheus插件的gobinary目标。CI也加入两个固定Prometheus/Collector镜像的独立严格扫描及实际Collector隐私/故障恢复步骤，未声称托管CI实际执行。

2026-10-28 补充安全扫描：上述12.3.1扫描 exit=1，存在 HIGH/CRITICAL，不得作为生产批准。另解析并拉取官方 latest 的固定 manifest `sha256:488984b716c0862639f91a698adbe2862677a5a07bad42da5f03f3030883b205`，同样扫描 exit=1，存在 Go stdlib、golang.org/x/net 和 Tempo 依赖 HIGH；未替换部署、未忽略漏洞。报告分别保存在仓库外 `D:/codex_project/customer_service_vm_lab_20261026/s2-r2-output-20261028/grafana-vulnerabilities.json` 和 `grafana-current-vulnerabilities.json`。latest 是本次候选解析来源，绝不作为浮动部署引用；最新候选也不能作为通过证据。需取得包含依赖修复的官方发行物或可审核重建制品，再扫描和重复功能/浏览器验收。

本轮补丁后完整本地后端：513 passed / 370 skipped，30.76秒。未设置外部集成环境的跳过不算通过；该数不替代上述实际容器/浏览器链路，也不证明真实生产采集或新镜像。Dashboard契约含缺失值颜色和固定镜像/CLI错误脱敏检查。

- 正式 Grafana 的访问角色、配置供应/持久化、TLS入口与受控发布，不沿用合成管理员或默认凭据。
- 本批修复版已完成镜像扫描和功能复验；生产容量/正式角色批准仍需独立完成，固定摘要与本机通过不等于生产批准。
- 各出口、真实预算数据与熔断/告警的持久部署、权限与故障验收；当前缺数据面板不是覆盖证明。
- Grafana/Collector/日志平台整合、留存策略及值班送达；真实账单与其他 S2 清单仍独立验收。

因此本轮仅将“无可导入/执行/显示的仪表盘”缺口推进为已具备可重复实机证据，不勾选完整 S2，也不提交宣称 S2 完成的第二笔 Git。

## 2026-10-29 新漏洞库修复复验

远端CI检出主程序go-jose/v4 4.1.4的CVE-2026-116587，故此前扫描通过不代表新库通过。现从官方13.2.5固定commit `ce471d869a4810cd83c987dd3521c4b67689dbb6`重建服务端，升级至4.1.6；源码SHA256和Go checksum数据库验证均开启，保留官方UI及固定签名插件。下游版本为13.2.5+cs.s2，不冒充未改动官方制品。

修复镜像manifest `sha256:9e74d012bebb72e982a1da7888d5886b01539b2b5fc4eed9f5b56f751af29aa3`，config `sha256:648ec7c55d4377b6765fcce160ae59b2072f384abf71df4971493a24a8d3e239`。完整归档新库扫描exit0、0 HIGH/CRITICAL，报告为实验室`s2-r2-output-20261028/grafana-security-vulnerabilities-20261029.json`。成功功能批次`cs-s2-grafana-7515f0e73b4d`，回环54789：六查询、up/P95样本、匿名401及浏览器渲染通过；缺失预算保持空值。JSON证据在仓库外临时目录`cs-s2-grafana-7515f0e73b4d-u0q8dp4h`。

远端40788755603已完成安全重建和0 HIGH/CRITICAL扫描，但实际数据源检查失败。Linux下mkdtemp目录0700使默认非root Prometheus无法读取目录整体挂载中的配置；真实Linux volume重现permission denied。改为单独绑定合成prometheus.yml及metrics-token到镜像已有/etc/prometheus路径，两文件只读、父证据目录保持0700、镜像默认非root用户不变。真实Linux权限探针新挂载启动通过；完整六查询/401复验批次`cs-s2-grafana-72946067cf6d`通过，证据目录`cs-s2-grafana-72946067cf6d-k2v9txvw`。这不是通过延长超时或改root掩盖故障，新CI仍待运行。
