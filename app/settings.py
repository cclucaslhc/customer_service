import os
from pathlib import Path


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是整数") from exc
    if value <= 0:
        raise RuntimeError(f"{name} 必须大于 0")
    return value


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是数字") from exc
    if value <= 0:
        raise RuntimeError(f"{name} 必须大于 0")
    return value


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
KNOWLEDGE_DIR = DATA_DIR / "knowledge"
# 保留旧名称供已有调用方兼容；新文档统一由 knowledge 目录托管。
UPLOAD_DIR = KNOWLEDGE_DIR
LEGACY_UPLOAD_DIR = DATA_DIR / "uploads"
SECRETS_DIR = DATA_DIR / "secrets"
DATABASE_PATH = DATA_DIR / "app.db"
MASTER_KEY_PATH = SECRETS_DIR / "master.key"
FRONTEND_DIST = ROOT / "frontend" / "dist"

DATABASE_BUSY_TIMEOUT_MS = _positive_int("DATABASE_BUSY_TIMEOUT_MS", 5000)
MAX_CONCURRENT_MODEL_RUNS = _positive_int("MAX_CONCURRENT_MODEL_RUNS", 8)
MODEL_HTTP_MAX_CONNECTIONS = _positive_int("MODEL_HTTP_MAX_CONNECTIONS", MAX_CONCURRENT_MODEL_RUNS * 2)
MODEL_HTTP_MAX_KEEPALIVE = _positive_int("MODEL_HTTP_MAX_KEEPALIVE", MAX_CONCURRENT_MODEL_RUNS)
MODEL_QUEUE_TIMEOUT_SECONDS = _positive_float("MODEL_QUEUE_TIMEOUT_SECONDS", 2.0)
MODEL_RUN_TIMEOUT_SECONDS = _positive_float("MODEL_RUN_TIMEOUT_SECONDS", 120.0)
SSE_HEARTBEAT_SECONDS = _positive_float("SSE_HEARTBEAT_SECONDS", 15.0)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".wps", ".rtf", ".odt",
    ".xls", ".xlsx", ".et", ".ods", ".csv", ".tsv",
    ".ppt", ".pptx", ".dps", ".odp",
    ".md", ".txt", ".html", ".htm",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp",
}


def ensure_directories() -> None:
    for path in (DATA_DIR, KNOWLEDGE_DIR, SECRETS_DIR):
        path.mkdir(parents=True, exist_ok=True)
