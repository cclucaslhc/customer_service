from pathlib import Path


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

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".md", ".txt"}


def ensure_directories() -> None:
    for path in (DATA_DIR, KNOWLEDGE_DIR, SECRETS_DIR):
        path.mkdir(parents=True, exist_ok=True)
