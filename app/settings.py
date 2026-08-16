from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
SECRETS_DIR = DATA_DIR / "secrets"
DATABASE_PATH = DATA_DIR / "app.db"
MASTER_KEY_PATH = SECRETS_DIR / "master.key"
FRONTEND_DIST = ROOT / "frontend" / "dist"

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".md", ".txt"}


def ensure_directories() -> None:
    for path in (DATA_DIR, UPLOAD_DIR, SECRETS_DIR):
        path.mkdir(parents=True, exist_ok=True)
