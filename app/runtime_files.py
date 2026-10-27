"""Explicit private runtime files; never discover environment or historical keys."""
import os
from pathlib import Path
import stat


def require_private_file(path: Path, *, maximum: int, exact_size: int | None = None) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('An absolute, non-symlink operator file is required')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum or (exact_size is not None and info.st_size != exact_size):
        raise ValueError('Invalid operator file')
    if os.name == 'posix' and stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError('Operator file permissions must exclude group and others')
    return path


def read_private_line(path: Path, *, maximum: int) -> bytes:
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('An absolute, non-symlink operator file is required')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError('Invalid operator file')
        if os.name == 'posix' and stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('Operator file permissions must exclude group and others')
        raw = source.read(maximum + 1)
        if len(raw) > maximum:
            raise ValueError('Operator file exceeds size limit')
        value = raw.strip()
    if not value or b'\n' in value or b'\r' in value:
        raise ValueError('Invalid single-line operator file')
    return value


def read_database_url(path: Path) -> str:
    value = read_private_line(path, maximum=4096).decode('utf-8')
    if any(character.isspace() for character in value):
        raise ValueError('Invalid PostgreSQL URL file')
    return value


def worker_database_url(direct: str | None, path: Path | None) -> str:
    """Keep remote production credentials out of process argument lists."""
    if (direct is None) == (path is None):
        raise ValueError('Select exactly one explicit Worker database URL source')
    if path is not None:
        return read_database_url(path)
    from sqlalchemy.engine import make_url
    selected = make_url(direct)
    if selected.host not in {'localhost','127.0.0.1','::1'} or any(
            name in selected.query for name in ('host','hostaddr','service','servicefile')):
        raise ValueError('Remote Worker database URL requires a private file')
    return direct


def worker_redis_url(direct: str | None, path: Path | None) -> str | None:
    if direct is not None and path is not None:
        raise ValueError('Select one Redis URL source')
    if path is not None:
        value = read_private_line(path, maximum=4096).decode('utf-8')
        if any(character.isspace() for character in value):
            raise ValueError('Invalid Redis URL file')
        return value
    if direct is not None:
        from urllib.parse import urlsplit
        parsed = urlsplit(direct)
        if parsed.hostname not in {'localhost','127.0.0.1','::1'} or parsed.username or parsed.password:
            raise ValueError('Remote or credentialed Redis URL requires a private file')
    return direct
