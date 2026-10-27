"""Bounded parser subprocess; database/object publication stays in the parent."""

import base64
from contextlib import contextmanager
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from app.knowledge_models import ContentBlock, DocumentParseError, EmbeddedAsset, ParsedDocument


MAX_RESULT_BYTES = 128 * 1024 * 1024


@contextmanager
def _parser_directory():
    """Retry Windows handle-release races, never silently discard cleanup errors.

    TemporaryDirectory owns the exact path; no computed broad cleanup target.
    Windows' rmtree permission handler can mask a sharing violation as error
    267 while trying to recurse into the still-locked ordinary output file.
    """
    temporary=tempfile.TemporaryDirectory(prefix='cs-parser-')
    try:
        yield temporary.name
    finally:
        for attempt in range(8):
            try:
                temporary.cleanup()
                break
            except OSError as exc:
                if getattr(exc,'winerror',None) not in (5,32,267) or attempt==7:
                    raise
                time.sleep(min(.05*(2**attempt),.25))


class IsolatedDocumentParser:
    def __init__(self, *, timeout_seconds=120, memory_mib=2048):
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 600
                or type(memory_mib) is not int or not 512 <= memory_mib <= 8192):
            raise ValueError('Invalid parser resource limits')
        self.timeout_seconds, self.memory_mib = timeout_seconds, memory_mib

    def __call__(self, filename, data, *, execution_check=None):
        from app.settings import MAX_UPLOAD_BYTES
        if execution_check is not None:
            execution_check()
        if not isinstance(data, bytes) or len(data) > MAX_UPLOAD_BYTES:
            raise DocumentParseError('文档超过解析输入限制')
        with _parser_directory() as directory:
            root = Path(directory)
            source, output = root/'input', root/'result.json'
            source.write_bytes(data)
            env = {'PYTHONPATH': str(Path(__file__).resolve().parents[1]),
                   'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONIOENCODING': 'utf-8',
                   'HOME': directory, 'TMPDIR': directory, 'TMP': directory,
                   'TEMP': directory, 'PATH': os.defpath, 'LD_LIBRARY_PATH': '/usr/local/lib',
                   'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'}
            for name in ('SystemRoot', 'WINDIR'):
                if name in os.environ:
                    env[name] = os.environ[name]
            command = [sys.executable, '-m', 'app.isolated_parser', '--child', str(source),
                       Path(filename).name, str(self.memory_mib), str(math.ceil(self.timeout_seconds))]
            with output.open('wb') as destination:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=destination,
                    stderr=subprocess.DEVNULL, env=env, cwd=directory, close_fds=True,
                    start_new_session=sys.platform == 'linux',
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                deadline = time.monotonic() + self.timeout_seconds
                try:
                    while True:
                        if execution_check is not None:
                            execution_check()
                        if output.stat().st_size > MAX_RESULT_BYTES:
                            raise DocumentParseError('文档解析结果超过大小限制')
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise DocumentParseError('文档解析超时，请拆分文档后重试')
                        if sys.platform == 'linux':
                            # Keep the child's PID reserved until its process
                            # group is cleaned, including a crashed parser's
                            # surviving converters. WNOWAIT avoids PID reuse.
                            completed = os.waitid(os.P_PID, process.pid,
                                os.WEXITED | os.WNOHANG | os.WNOWAIT)
                            if completed is not None:
                                result = (completed.si_status if completed.si_code == os.CLD_EXITED
                                          else -completed.si_status)
                                break
                            time.sleep(min(remaining, .1))
                            continue
                        try:
                            result = process.wait(timeout=min(remaining, .1))
                            break
                        except subprocess.TimeoutExpired:
                            continue
                    if result:
                        raise DocumentParseError('文档解析失败或超过资源限制')
                finally:
                    if sys.platform == 'linux':
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    elif process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)
            if output.stat().st_size > MAX_RESULT_BYTES:
                raise DocumentParseError('文档解析结果超过大小限制')
            if execution_check is not None:
                execution_check()
            try:
                return _decode(json.loads(output.read_text(encoding='utf-8')), Path(filename).name)
            except (ValueError, TypeError, KeyError):
                raise DocumentParseError('文档解析结果无效') from None


def _decode(value, filename):
    if (not isinstance(value, dict) or value.get('filename') != filename
            or not isinstance(value.get('document_type'), str)
            or not isinstance(value.get('metadata'), dict)
            or not isinstance(value.get('warnings'), list)
            or not all(isinstance(item, str) for item in value['warnings'])
            or not isinstance(value.get('blocks'), list) or len(value['blocks']) > 100000
            or not isinstance(value.get('assets'), list) or len(value['assets']) > 1000):
        raise ValueError('Invalid parser result')
    blocks, assets = [], []
    for item in value['blocks']:
        block = ContentBlock(**item)
        if (not all(isinstance(field, str) for field in (block.block_type, block.text, block.location))
                or block.title is not None and not isinstance(block.title, str)
                or not isinstance(block.metadata, dict)):
            raise ValueError('Invalid block')
        blocks.append(block)
    for item in value['assets']:
        asset = EmbeddedAsset(**{**item, 'data': base64.b64decode(item['data'], validate=True)})
        if (asset.media_type != 'image/png' or asset.extension != '.png'
                or any(type(number) is not int or number < 0 for number in
                       (asset.asset_index, asset.width, asset.height))
                or not all(isinstance(field, str) for field in (asset.location, asset.alt_text))
                or not isinstance(asset.metadata, dict)):
            raise ValueError('Invalid asset')
        assets.append(asset)
    return ParsedDocument(filename, value['document_type'], blocks, value['metadata'],
                          value['warnings'], assets)


def _child(source, filename, memory_mib, cpu_seconds):
    if sys.platform == 'linux':
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_AS, (memory_mib * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_RESULT_BYTES,) * 2)
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        from app.parser_sandbox import restrict_parser
        restrict_parser(Path(source).resolve().parent)
    # Third-party parsers can print notices to stdout (including native FD 1
    # writes). Keep that noise out of the single JSON result protocol.
    result_fd = os.dup(sys.stdout.fileno())
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    with os.fdopen(result_fd, 'w', encoding='utf-8') as result_stream:
        from app.knowledge_parsers import parse_document
        from app.settings import MAX_UPLOAD_BYTES
        with Path(source).open('rb') as stream:
            data = stream.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError('Input exceeds limit')
        document = parse_document(filename, data)
        payload = asdict(document)
        for item in payload['assets']:
            item['data'] = base64.b64encode(item['data']).decode('ascii')
        json.dump(payload, result_stream, ensure_ascii=False, allow_nan=False)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 6 or sys.argv[1] != '--child':
            raise ValueError('Explicit parser child arguments required')
        _child(sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]))
    except Exception:
        raise SystemExit(2) from None
