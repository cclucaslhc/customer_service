# S2 zlib 实际修复

2026-11-05：CI 42049116316实际OCI扫描被CVE-2026-76844 HIGH阻断：Debian zlib1g `1:1.3.dfsg+really1.3.1-1+b1`，无FixedVersion。它影响实际gzip写库，不能使用SQLite CLI的代码不存在判定；旧轮失败保留。

## 固定来源的真正替换

上游当日正式发布[zlib 1.4](https://github.com/madler/zlib/releases/tag/v1.4)，发布说明包含gzwrite缓冲区越界及相关安全修复。归档`zlib-1.4.tar.gz`固定SHA256 `02dd159783677a749c04adbdd02b55b8a2bcc7e054f51b76b549199a916ad584`，已核对官方GitHub release digest；编译器仍是固定摘要Go1.26.10-bookworm中的GCC。执行configure/shared、make、上游make test后，仅复制共享库，不复制构建工具或示例程序。

由于旧zlib来自distroless基础根，不能仅叠放新库、却留下旧版本记录或可加载文件。构建阶段复制固定基础镜像到新`/runtime-root`，受限脚本核对确切包名/文件布局/路径归属后，只删除其两个实际旧库文件（库及SONAME链接）和两个旧来源文件（dpkg状态及md5sums），写入新1.4实际库/SONAME及真实上游CycloneDX来源。最终从scratch复制这个完整受控根，避免旧易受攻击库仍留在最终镜像层中；其余基础库、证书、时区及文件模式保留。最终仍显式UID/GID65532、HOME=/tmp；不依赖复制后的home目录原属主。

脚本拒绝异常目录名、链接/目标越界、不同包布局、缺失文件或非ELF替换。它只用于新的构建根，不修改宿主或实际客服实例。Linux测试必须证明无关包保留、旧文件/来源消失、替换链接/字节及新来源一致；Windows本机不把POSIX链接用例跳过算作通过。

## 验收门槛

构建根复核还确认：复制根文件系统不会继承原基础镜像配置中的`SSL_CERT_FILE`；该基底仅提供/etc/ssl/certs/ca-certificates.crt，没有Python默认查找别名。最终Dockerfile现显式保留这个CA路径，离线镜像启动门禁实际执行ssl.create_default_context并要求加载非空信任库。没有关闭证书/主机名验证或改为明文模型端点；此增量专项27 passed/2 POSIX skipped，实际容器检查待新CI。

本机修改后独立Python3.12全量621 passed/386 skipped/6 warnings（30.12秒）；专项47 passed/2 skipped。新增两项POSIX构建根用例仅在Linux执行，未被算成本机通过；最终CI后端必须实际执行它们。

实际非root/只读/无网络镜像探针须验证库字节与原生清单、C层zlibVersion及Python ZLIB_RUNTIME_VERSION精确1.4、旧库/旧Debian记录均不存在，并完成gzip/deflate往返。随后仍运行六类文档、迁移/Worker、资源故障、独立OCI重建与完整扫描，不能用往返探针替代完整镜像回归。

SBOM同时核对固定上游SQLite和zlib组件，拒绝残留Debian zlib1g或漏掉新原生来源。Trivy继续扫描所有实际OS/Python包，Grype扫描完整增强清单及两个原生CPE；无zlib VEX或忽略规则。真实镜像/扫描/签名均待最新候选CI完成，此页不是S2完成声明。
