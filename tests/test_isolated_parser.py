"""Real parser subprocess and termination; all input and identity are synthetic."""

import subprocess
import sys
from pathlib import Path

import pytest

from app.isolated_parser import IsolatedDocumentParser
from app.knowledge_models import DocumentParseError


def test_windows_cleanup_handle_release_is_retried_without_hiding_parser_error(monkeypatch):
    import app.isolated_parser as module
    attempts=[]
    original=module.tempfile.TemporaryDirectory
    def temporary(**kwargs):
        instance=original(**kwargs)
        cleanup=instance.cleanup
        def retry():
            attempts.append(1)
            if len(attempts)<3:
                error=OSError('Synthetic output handle still closing')
                error.winerror=267
                raise error
            cleanup()
        instance.cleanup=retry
        return instance
    monkeypatch.setattr(module.tempfile,'TemporaryDirectory',temporary)
    monkeypatch.setattr(module.time,'sleep',lambda seconds:None)
    with pytest.raises(DocumentParseError,match='Synthetic original timeout'):
        with module._parser_directory():
            raise DocumentParseError('Synthetic original timeout')
    assert len(attempts)==3


def test_windows_cleanup_retry_is_bounded_and_other_errors_not_retried(monkeypatch):
    import app.isolated_parser as module
    attempts=[]
    class Temporary:
        name='synthetic-no-directory-created'
        def cleanup(self):
            attempts.append(1)
            error=OSError('Synthetic persistent cleanup failure')
            error.winerror=32
            raise error
    monkeypatch.setattr(module.tempfile,'TemporaryDirectory',lambda **kwargs:Temporary())
    monkeypatch.setattr(module.time,'sleep',lambda seconds:None)
    with pytest.raises(OSError,match='persistent'):
        with module._parser_directory():
            pass
    assert len(attempts)==8
    def other(self):
        raise OSError('Synthetic unrelated failure')
    monkeypatch.setattr(Temporary,'cleanup',other)
    with pytest.raises(OSError,match='unrelated'):
        with module._parser_directory():
            pass


def test_actual_parser_subprocess_returns_unicode_document():
    parsed = IsolatedDocumentParser(timeout_seconds=30)('synthetic.txt',
        '退货需要订单号。Synthetic parser input.'.encode())
    assert parsed.filename == 'synthetic.txt'
    assert parsed.has_text and '退货需要订单号' in parsed.blocks[0].text


def test_actual_pdf_parser_keeps_library_notices_out_of_json():
    import pymupdf
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72,72),'Synthetic parser PDF output protocol.')
        data = pdf.tobytes()
    parsed = IsolatedDocumentParser(timeout_seconds=30)('synthetic.pdf',data)
    assert parsed.has_text and 'Synthetic parser PDF' in parsed.blocks[0].text


def test_parser_timeout_kills_actual_child(monkeypatch):
    import app.isolated_parser as module
    original = subprocess.Popen
    processes = []
    def slow_child(command, **kwargs):
        process = original([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(module.subprocess, 'Popen', slow_child)
    with pytest.raises(DocumentParseError, match='超时'):
        IsolatedDocumentParser(timeout_seconds=1)('synthetic.txt', b'synthetic')
    assert len(processes) == 1 and processes[0].poll() is not None


def test_parser_child_does_not_inherit_service_environment(monkeypatch):
    import app.isolated_parser as module
    original = subprocess.Popen
    monkeypatch.setenv('CS_TEST_SERVICE_CREDENTIAL', 'synthetic-service-only')
    script = ('import os,json; print(json.dumps(dict(filename="synthetic.txt",'
        'document_type=".txt",blocks=[],assets=[],warnings=[],'
        'metadata={"inherited": "CS_TEST_SERVICE_CREDENTIAL" in os.environ})))')
    monkeypatch.setattr(module.subprocess, 'Popen',
        lambda command, **kwargs: original([sys.executable, '-c', script], **kwargs))
    parsed = IsolatedDocumentParser(timeout_seconds=10)('synthetic.txt', b'synthetic')
    assert parsed.metadata['inherited'] is False


def test_parser_rejects_oversized_output_before_json_decode(monkeypatch):
    import app.isolated_parser as module
    original = subprocess.Popen
    monkeypatch.setattr(module, 'MAX_RESULT_BYTES', 512)
    monkeypatch.setattr(module.subprocess, 'Popen', lambda command, **kwargs:
        original([sys.executable, '-c', 'print("x" * 1024)'], **kwargs))
    with pytest.raises(DocumentParseError, match='大小限制'):
        IsolatedDocumentParser(timeout_seconds=10)('synthetic.txt', b'synthetic')


def test_execution_authority_loss_terminates_parser_before_timeout(monkeypatch):
    import app.isolated_parser as module
    original = subprocess.Popen
    processes = []
    def slow_child(command, **kwargs):
        process = original([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        processes.append(process)
        return process
    def check():
        if processes:
            raise PermissionError('Synthetic execution authority lost')
    monkeypatch.setattr(module.subprocess, 'Popen', slow_child)
    with pytest.raises(PermissionError, match='authority lost'):
        IsolatedDocumentParser(timeout_seconds=20)('synthetic.txt', b'synthetic', execution_check=check)
    assert len(processes) == 1 and processes[0].poll() is not None


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux process-group semantics')
def test_linux_parser_timeout_and_authority_loss_stop_grandchildren():
    script = Path(__file__).parent/'fixtures'/'parser_tree_check.py'
    result = subprocess.run([sys.executable, str(script)], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors='replace')


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux Landlock/seccomp semantics')
def test_linux_parser_filesystem_network_and_process_group_sandbox():
    script = Path(__file__).parent/'fixtures'/'parser_sandbox_check.py'
    result = subprocess.run([sys.executable, str(script)], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors='replace')


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux sandbox format acceptance')
def test_linux_sandboxed_document_formats():
    script = Path(__file__).parent/'fixtures'/'parser_format_check.py'
    result = subprocess.run([sys.executable, str(script)], capture_output=True, timeout=180)
    assert result.returncode == 0, result.stderr.decode(errors='replace')


def test_sandbox_refuses_unsupported_architecture(monkeypatch,tmp_path):
    import app.parser_sandbox as sandbox
    monkeypatch.setattr(sandbox.platform,'machine',lambda:'unsupported')
    with pytest.raises(sandbox.SandboxUnavailable,match='Linux x86_64'):
        sandbox.restrict_parser(tmp_path)


def test_sandbox_refuses_old_landlock_without_installing_partial_policy(monkeypatch,tmp_path):
    import app.parser_sandbox as sandbox
    workspace = tmp_path/'cs-parser-synthetic'
    workspace.mkdir()
    class OldKernel:
        def syscall(self,*args):
            assert args == (444,0,0,1)
            return 4
    monkeypatch.setattr(sandbox.sys,'platform','linux')
    monkeypatch.setattr(sandbox.platform,'machine',lambda:'x86_64')
    monkeypatch.setattr(sandbox.ctypes,'CDLL',lambda *args,**kwargs:OldKernel())
    with pytest.raises(sandbox.SandboxUnavailable,match='ABI >= 5'):
        sandbox.restrict_parser(workspace)
