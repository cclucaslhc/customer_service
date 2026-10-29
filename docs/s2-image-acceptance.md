# S2 最新应用制品验收（进行中）

## 当前完整源码制品（优先于下述r2历史批次）

包含通知适配器/安全配置/认证接收端的最终应用OCI：manifest `sha256:d36d3436f1682eaac9e4488b1c2b2ef5c72583dc532ccb1575200dde741f0b70`，config `sha256:78656f2603de3b28bae50374c6cdb15aecf2e89c8723487c2199a4ad1de72127`。默认builder与独立cs-s2-repro-20261028 builder显式no-cache重建逐manifest/config/layer字节相同；首次独立构建因锁定logfire官方wheel下载超时失败，明确终态后另一次构建成功，不把失败当通过、不换不明源、不改锁版本。

完整OCI扫描0 HIGH/CRITICAL、SBOM14 Debian＋143 Python，通过绑定验证。归档/重建位于实验室 `s2-final-20261028.oci.tar` / `s2-final-rebuild-20261028.oci.tar`，报告 `s2-r2-output-20261028/s2-final-{vulnerabilities.json,sbom.cdx.json}`。普通Docker载入制品 `customer-service:s2-final-20261028` manifest `sha256:3534a192196f056617ad51cec1f0445452dde9ff10f03c35aa45d835dc072d4a`；真实非root65532/只读/无网络导入全部新增模块通过。普通载入镜像与规范OCI编码不同，不能把两个manifest混写。

同OCI已完成新disposable测试身份签名/验证/篡改拒绝，测试私钥已销毁；公钥pin `d176a672e9195c7d2c0b39da248859c76bc726ab371f427eb1d8895dd6b914d6`，证据 `C:/Users/56226/AppData/Local/Temp/cs-s2-release-signing-nyearfzo`。仍不是正式组织发布签名。

补扫官方Alertmanager0.34.1发现Go1.26.8、x/net0.58.0及grpc1.83.1高危；未豁免。新增 `ops/alertmanager/Dockerfile` 固定官方源码commit/哈希、经官方sha256sums核对的UI哈希、Go1.27.3及x/net0.60.0/grpc1.83.2，Go checksum数据库保持启用。最终只复制两二进制、许可证及实际go.mod/go.sum到非rootdistroless；不是冒充官方镜像。安全重建manifest `sha256:a8e129b6881a18cba6a38cb867674cfd69082b8c6861729d5357b555cb59a0df`，config `sha256:b4065fadc5f2ff3f196d5bf589623656f21d17a66487915e47263843d20635d0`。完整归档扫描exit0、0 HIGH/CRITICAL，报告 `alertmanager-security-rebuild-vulnerabilities.json`。提取同二进制在隔离Linux node2实际认证接收触发/恢复1 passed（1.49秒），未发送外部邮件；暂存目录 `s2-notification-probe-20261028`，未改现有服务或配置。

应用/监控全部扫描门禁及真实接收端测试已加入CI配置，待远端实际运行；真实告警送达按用户决定跳过而不算通过。

日期：2026-10-28。本页区分首次构建与漏洞修复后的制品，旧镜像不冒充最新源码通过。

## 首次构建与限制测试

新代码首次镜像 `customer-service:s2-verify-20261028` 构建成功。仅使用Dockerfile固定基础摘要及uv.lock；扩大.dockerignore，排除根node_modules、egg-info及常见凭据配置/私钥文件，构建上下文约262KiB，不含运行data或模型权重。

实际非root UID65532、只读根、无network/capabilities下新计量模块导入通过。`verify_container_limits.py` 对该确切镜像验证pids上限（15子进程后拒绝）、tmpfs满/CPU throttle、128MiB内存OOM exit137且OOMKilled=true；仅自建随机测试容器，没有给宿主施压或触碰其他服务。

首次普通加载镜像manifest为`sha256:d38ecd435140286c693043634658585ab445c4419cd195a45b5b97280901df41`。标准时间戳重写OCI制品manifest为`sha256:c8845a2c4205848e736626240f7a4223940f88d5a2c324dc00f08fb477cf1c2b`；两者导出方式不同，不作为独立重建相等证明。

## 扫描发现：未通过

官方固定Trivy0.66.0摘要`sha256:086971aaf400beebd94e8300fd8ea623774419597169156cec56eec5b00dfb1e`生成CycloneDX1.6 SBOM；verify_sbom核对14 Debian和143 Python组件，包括复制的native runtime来源。SBOM生成不等于漏洞扫描。

随后下载当日漏洞数据库并执行HIGH/CRITICAL严格扫描，退出1：发现Authlib1.9.0的HIGH `CVE-2026-114616`，固定版本1.9.1。未忽略、未降级阈值；首次制品不准通过。报告留在仓库外实验室artifact目录，不提交镜像或原始运行数据。

## 定向修复与后续

已用uv仅升级Authlib至1.9.1，uv.lock变更3行新增/3行删除，其他包未升级。本机旧venv实际为1.7.2，已定向安装1.9.1，不把本机venv其他依赖当作与镜像完全同构的证明。修复后`customer-service:s2-verify-20261028-r2`已重建，manifest为`sha256:7196bd9002026a571f0c84167bb0a7c7200872a91673817beed0839ac1ed7f48`；后续需完成标准OCI导出、SBOM与新库扫描，当前还不能标为修复后全通过。本机回归541 passed / 383 skipped，31.22秒，跳过不作通过。

独立干净缓存重建、签名验证与CI流程的后续进展见下节；监控镜像安全核验及正式发布信任仍需收尾。此页不是S2完成声明，尚未创建第二笔Git提交。

r2后续实际复验：UID65532/只读根下Authlib版本精确为1.9.1，新计量模块可导入；PID上限、tmpfs/CPU throttle、OOMKilled=true/exit137再次通过。未用首次镜像的资源结论替代r2实测；当时待执行的复扫已由下节补齐。

## r2 复扫与独立缓存重建完成

后续标准OCI manifest为`sha256:47408a01619c0e56e41f55be961906d5b3992af051f1db066818a35a3e8906f4`，config为`sha256:58a9ea3a38023b03872c73d8da34391dec00723aa6da852dc7f78c8532ed34a4`。独立BuildKit docker-container builder `cs-s2-repro-20261028` 使用官方固定BuildKit摘要 `sha256:cec9f139f45e93c5c69c60f8b07cfad9f43f4ef6b6a6cd917527fea5ff2e3dea`，空缓存且no-cache构建；与默认builder相互独立缓存，但仍为同物理宿主。两个OCI经verify_image_rebuild逐manifest/config/layer内容摘要复核相等。不是另一台物理主机或远程CI已运行。

r2使用当日下载漏洞库的HIGH/CRITICAL扫描exit0、结果0；重新生成SBOM，14 Debian/143 Python组件，验证通过。扫描报告及SBOM均确认ImageID匹配上述config。扫描只挂载该次只读OCI目录及专用输出目录，无Docker socket；旧首次制品HIGH记录不删除，历史“复扫待执行”由本段覆盖。

## 证据签名与 CI 装配

`ops/release/evidence.py` 可将OCI、SBOM和严格通过的扫描报告绑定为一份清单；拒绝SBOM/扫描不属于该config、缺OS/语言扫描覆盖和HIGH/CRITICAL。使用cryptography标准Ed25519签名，验证时必须显式提供独立信任的公钥SHA256，不从包内公钥自动建立信任；核对签名后再核对所有原始文件摘要与OCI blob。没有环境密钥发现、自动生成正式身份、上传或公网调用。

实际r2三个文件已由NEW disposable测试密钥完成签名/验证与篡改拒绝；测试公钥指纹`625983d983117cdbb072fe66dc5c21b124f1dd55b25f8351333a631766d33ad4`，证据保留于仓库外 `C:/Users/56226/AppData/Local/Temp/cs-s2-release-signing-xe9mxay9`。它**不是正式发布身份**，不能把生成后自验当成组织签名批准。工具默认不生成key；fixture仅用于隔离测试，成功后销毁其新测试私钥。签名/篡改/错身份/错误报告及CI契约联合10 passed，0.32秒。

CI现准备在扫描通过后生成同绑定清单并上传明确列出的OCI/SBOM/扫描/清单，保留7天；失败制品不通过always分支发布，不上传私钥或整个临时目录。workflow未推送/未在远端执行、没有自动获得生产签名权。正式发布身份/KMS、公钥信任分发和实际托管CI验收仍需用户资源或明确交付边界。

本轮完整本地回归549 passed / 383 skipped，29.57秒，未配置的集成环境跳过仍不算通过。第二笔S2完成提交尚未进行。

## 2026-10-29 当前制品与发布信任

以上r2及“workflow未推送”为历史。托管CI已经真实运行，发现OTel/Vite新漏洞和Linux构建环境问题后均有修复；当前结果以[最终验收账本](s2-final-acceptance.md)为准。当前应用OCI manifest `sha256:5d9fae63aec890b1dc5ce4d46d08cac8db58601815d17580ee660f99e1f7d8ae`，config `sha256:17238a5c0c91c77066a53e610b5fbe49b8667debdda4ba3a9127077a2091a4ee`；本机独立重建逐blob一致，SBOM14 Debian/145 Python，完整扫描0 HIGH/CRITICAL。最终远端制品必须使用自己的实际摘要，不预先声称与本机摘要相等。

推送CI使用GitHub短期OIDC身份为绑定清单签发SLSA v1证明；不生成长期组织私钥。清单显式标注`github-oidc-slsa-v1`，与默认离线`explicit-pinned-ed25519-v1`互不替代。消费者先验证身份和清单，再验证字节绑定：

```powershell
# 固定到实际成功运行的提交；验收分支不能冒充main发布。
gh attestation verify <绝对路径/customer-service-release.json> --repo cclucaslhc/customer_service --signer-workflow cclucaslhc/customer_service/.github/workflows/verify.yml --source-ref refs/heads/main --source-digest <实际main提交SHA> --deny-self-hosted-runners
.\.venv\Scripts\python.exe ops/release/evidence.py check-binding --signature-policy github-oidc-slsa-v1 --manifest <绝对路径/customer-service-release.json> --oci <绝对路径/customer-service-image.oci.tar> --sbom <绝对路径/customer-service-sbom.cdx.json> --scan <绝对路径/customer-service-vulnerabilities.json>
```

`check-binding`只验证原始文件摘要/OCI内容/SBOM与扫描覆盖，不验证签名。`gh --bundle`可以离线验证已下载证明，但不能单独检查仓库重命名，应同时用受认证仓库API核对repository ID `1347515734`及精确运行SHA。成功验签不代表受控生产部署批准，也不是独立审计员已审阅源码。当前新轮CI及实际OIDC下载验证仍待完成。
