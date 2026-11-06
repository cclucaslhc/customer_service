# S2 SQLite 原生库修复证据

最终技术结论（2026-11-05）：最新CI 42052908399五作业全绿；实际源码/二进制/FTS5回归及CLI/恢复入口缺失检查通过，完整扫描与精确OpenVEX判定、真实OIDC/字节绑定均已核验。只有CLI代码不存在的CVE-2026-119586被明确标not_affected，原始发现保留，不是原始0发现；FTS5库漏洞通过真实3.54.0源码重建修复，不获豁免。main须另验自身身份；下方“待新CI”为历史过程，当前证据见[最终账本](s2-final-acceptance.md)。

## 2026-11-05 当前补验（覆盖下文“无VEX”历史口径）

CI 41461618079的后端、前端、NGINX、PG/Redis四作业成功；实际OCI独立重建、13 Debian/145 Python及上游SQLite库存、Trivy 0 HIGH/CRITICAL均通过。原生扫描本身退出0后，发布绑定失败：真实容器CDX会被Grype重建成`source.type=image`，而非合成最小CDX的`sbom-file`。已下载该运行SBOM并核对GitHub ZIP摘要`32a4eaa650c3ac99d3b6c12f21d91faf2bc79310e752a35b59e08f2825cca561`复现；验证改为将image.userInput绑定到同一SBOM容器名称，仍校验SBOM SHA256和真正OCI config，不能把Grype随机imageID当OCI摘要。

11月5日新库完整重扫发现`CVE-2026-119586` High；原始无VEX报告保留于实验室`grype-e7-20261105.json`。其[CVE原文](https://github.com/CVEProject/cvelistV5/blob/main/cves/2026/119xxx/CVE-2026-119586.json)仅影响SQLite **命令行shell的.recover输出**，不是sqlite3.c数据库共享库。Dockerfile仅编译官方固定sqlite3.c；不编译/安装shell.c、sqlite3recover.c或sqlite3 CLI。新实际镜像探针必须验证CLI不可发现且共享库无recover/shell入口；这不是关闭FTS5来回避此前真正的库漏洞。

因此新增限定到`pkg:generic/sqlite/sqlite@3.54.0`和这一条CLI CVE的OpenVEX `not_affected/vulnerable_code_not_present`。这是有源码与实际镜像检查约束的不适用判定，不是接受风险、版本升级或忽略所有SQLite告警。Grype处理后原始发现仍保存在ignoredMatches中，完整VEX与扫描报告原文均被最终发布清单绑定；清单显式列出not-affected项。发布验证拒绝其他CVE/包/版本/规则、额外忽略项、缺源码构建来源或被篡改VEX。**报告用语为“无未解决HIGH/CRITICAL；一项CLI代码不存在判定”，不声称原始发现数为0。**

真实本机对同一已验摘要SBOM重新执行固定Grype0.121.0：没有VEX时退出1；使用该精确OpenVEX时退出0。该旧SBOM尚不包含新library-only构建属性，不能用它冒充新探针/新清单完整验收。新候选仍须完整CI和实际OIDC/Git交付。下文无VEX承诺属于新CLI-only发现之前的历史策略，现只允许此明确判定，不给此前FTS5或其他发现豁免。

2026-11-02：CI 41458328755严格扫描实际OCI发现`CVE-2026-119373` HIGH，Debian包`libsqlite3-0 3.46.1-7+deb13u2`，没有FixedVersion。该轮失败，不准发布；Debian tracker的bookworm/trixie/forky/sid当日均open。不是Python依赖审计能解决的问题。

## 实际修复与来源

官方[CVE记录](https://github.com/CVEProject/cvelistV5/blob/main/cves/2026/119xxx/CVE-2026-119373.json)指出启用FTS5且版本低于3.54.0受影响。官方[3.54.0发布记录](https://sqlite.org/releaselog/3_54_0.html)提供源码ID及sqlite3.c SHA3；[上游补丁与回归](https://github.com/sqlite/sqlite/commit/e1c9934f9f11a499029abb020ea715a08970da80)说明负rowid前缀列表合并的越界。

Dockerfile从官方2026/sqlite-autoconf-3540000.tar.gz构建实际动态库，归档SHA256 `134ec0802dda5795816e25d25872d20b312cb3973438c49b30bc40b7705ea9ed`；解包sqlite3.c的SHA3-256已与官方值`8efd453d08a7cfc4c79de5923153036cdc4dcaa254d4f1e617be6a2a98828f9e`核对。编译器使用固定Go1.26.10-bookworm镜像内GCC；不在构建时从滚动apt拉编译器，不禁用FTS5或删除知识库功能。

最终镜像仅复制新库至/usr/local/lib/libsqlite3.so.0，不复制旧Debian库，也不伪造其dpkg版本。其余三个实际复制的Debian原生库继续保留dpkg来源；SQLite改为真实上游CycloneDX组件，含PURL、CPE、实际二进制SHA256、两份源码摘要和来源URL。实际容器离线探针核对加载版本、源码ID、文件摘要，并运行官方负rowid回归的none/column/full三种detail配置。

## 扫描覆盖而非隐藏包

Trivy仍扫描实际OCI中的Debian与Python组件，HIGH/CRITICAL门槛不变。上游原生组件显式进入最终SBOM，另由官方固定Grype0.121.0对同一完整SBOM扫描，`--fail-on high`；报告原文及SBOM SHA256嵌入最终扫描文件，再由发布清单/OIDC绑定。发布验证拒绝缺原生扫描、无效数据库、错误工具版本/来源、被忽略的结果或HIGH/CRITICAL。

本机官方Grype Windows包SHA256已与GitHub release digest核对。真实扫描控制：SQLite3.46.1检出CVE-2025-3277 Critical及其他HIGH，退出非零；3.54.0扫描退出0。CI也必须检测这个故意有漏洞的清单，防止“未识别组件所以0”的假成功。

当日Grype数据库尚未收录CVE-2026-119373：3.53.4探针未报该漏洞，数据库定向查询亦未命中。**不能声称Grype已验证该特定CVE**。该漏洞修复的直接依据是官方固定3.54.0源码/源ID及实际加载/上游回归；Grype负责其他已知原生漏洞的持续扫描。没有ignore/VEX豁免或删减扫描清单。

## 当前验收状态

后续CI 41460148327在源码修复后的独立镜像重建通过后，发现Trivy容器默认root创建SBOM，宿主runner在enrich时PermissionError；该轮失败，不能声称扫描完成。已将需要后处理的SBOM与应用扫描两容器显式设为runner的非root UID/GID，并给扫描缓存独立/tmp路径；不改chmod777、不使用提权写入。新增契约测试后相关34 passed。OIDC的官方action bundle-path也已加入最终制品包，验签不依赖本机GitHub CLI登录或从Git凭据助手取Token。修复仍待新的真实CI。

首次SQLite修复候选的CI 41460148327已通过实际加载/FTS5回归和解析文档检查，仍在运行。后续更严格的原生扫描数据库/控制用例门禁尚待新候选完整CI。普通Python3.12全量607 passed/384 skipped/6 warnings（29.92秒），相关专项33 passed；跳过不算通过。最终OCI扫描、OIDC验证和main交付完成前，不将本页当作S2完成声明。
