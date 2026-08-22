# 服务并发与运行指南

当前版本按“单实例、单 Uvicorn worker、异步并发模型调用”设计。SQLite、运行取消令牌和会话运行表都位于单进程内，因此不要通过 `--workers` 启动多个进程；需要横向扩容时，应先把数据库迁移到 PostgreSQL，并把运行协调、限流状态迁移到 Redis 等共享组件。

## 已有保护

- SQLite 使用 WAL、5 秒 busy timeout，并为消息、运行记录和管理查询建立索引。短事务可并发排队，读取不会被普通写事务长期阻塞。
- 模型调用默认最多同时执行 8 个。等待舱位超过 2 秒时，SSE 返回 `service_busy`，客户端应按 `retry_after` 延迟并加入随机抖动后重试。
- 单次模型运行默认 120 秒超时，超时返回 `model_timeout`；供应商无输出期间每 15 秒发送一次 `ping` SSE 事件。
- 客户端断开、主动取消、服务关闭和异常重启都会把遗留运行更新为 `interrupted`，避免会话永久显示运行中。
- 模型 HTTP 请求复用连接池，默认最多 16 条连接、8 条空闲长连接，减少高并发下的握手开销和临时端口消耗。
- 上传按 1 MiB 分块读取，超过 50 MiB 立即返回 HTTP 413，不再无上限占用内存。
- `/health/live` 用于进程存活探测，`/health/ready` 用于数据库就绪探测；`/health` 额外返回 WAL 模式和模型并发舱位。所有 HTTP 响应包含 `X-Request-ID`。

## 推荐启动参数

PowerShell 示例：

```powershell
$env:MAX_CONCURRENT_MODEL_RUNS = "8"
$env:MODEL_QUEUE_TIMEOUT_SECONDS = "2"
$env:MODEL_RUN_TIMEOUT_SECONDS = "120"
$env:SSE_HEARTBEAT_SECONDS = "15"
$env:MODEL_HTTP_MAX_CONNECTIONS = "16"
$env:MODEL_HTTP_MAX_KEEPALIVE = "8"
$env:DATABASE_BUSY_TIMEOUT_MS = "5000"
$env:UVICORN_LIMIT_CONCURRENCY = "200"
$env:UVICORN_BACKLOG = "512"
.\.venv\Scripts\python.exe main.py
```

模型并发上限应从供应商 RPM/TPM、平均响应时间和机器资源反推。初始值可按 `允许 RPM × P95 响应秒数 ÷ 60` 估算，再取供应商并发限制与本机安全值中的最小值。提高 Uvicorn 并发上限不会提高模型吞吐，只会增加排队连接和内存。

## 反向代理要求

SSE 路径必须关闭响应缓冲和压缩，代理读取超时应大于 `MODEL_RUN_TIMEOUT_SECONDS`。应用已发送 `X-Accel-Buffering: no`，但代理仍应显式配置。不要缓存 `/api/chat/`，并把 `/health/live`、`/health/ready` 用作探针。

当前服务默认只接受本机来源，这是安全边界。若未来对外提供服务，不能只删除本机校验；必须同时加入身份认证、租户隔离、按用户/IP/租户的持久化限流、可信代理配置、TLS、审计脱敏和上传恶意文件扫描。

## 容量验收

发布前至少观察以下指标：并发运行数和等待数、`service_busy` 比例、模型 P50/P95/P99 时延、超时率、供应商 429/5xx、SQLite busy/locked 错误、SSE 主动断开率、进程 RSS 和文件描述符/Windows 句柄数。当前 `/health` 提供即时舱位信息；长期趋势仍需接入 Prometheus/OpenTelemetry 或现有监控平台。
