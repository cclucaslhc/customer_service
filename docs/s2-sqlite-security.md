# S2 SQLite 原生库修复证据

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

首次SQLite修复候选的CI 41460148327已通过实际加载/FTS5回归和解析文档检查，仍在运行。后续更严格的原生扫描数据库/控制用例门禁尚待新候选完整CI。普通Python3.12全量607 passed/384 skipped/6 warnings（29.92秒），相关专项33 passed；跳过不算通过。最终OCI扫描、OIDC验证和main交付完成前，不将本页当作S2完成声明。
