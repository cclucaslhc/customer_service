"""Private runtime file supply never falls back to a remote argv URL."""
import os

import pytest

from app.runtime_files import read_database_url, worker_database_url, worker_redis_url


def test_worker_database_url_requires_file_for_remote_target(tmp_path):
    local = 'postgresql+psycopg://synthetic:only-for-test@127.0.0.1:55432/test'
    remote = 'postgresql+psycopg://synthetic:only-for-test@db.example.test/test?sslmode=verify-full'
    assert worker_database_url(local,None) == local
    with pytest.raises(ValueError):
        worker_database_url(remote,None)
    with pytest.raises(ValueError):
        worker_database_url(local + '?hostaddr=192.0.2.4',None)
    with pytest.raises(ValueError):
        worker_database_url(None,None)

    file = tmp_path/'database-url'
    file.write_text(remote + '\n',encoding='utf-8')
    file.chmod(0o600)
    assert worker_database_url(None,file) == remote
    with pytest.raises(ValueError):
        worker_database_url(local,file)


def test_private_database_file_rejects_multiline_symlink_and_broad_mode(tmp_path):
    file = tmp_path/'database-url'
    file.write_text('one\ntwo',encoding='utf-8')
    file.chmod(0o600)
    with pytest.raises(ValueError):
        read_database_url(file)
    file.write_text('postgresql+psycopg://synthetic@127.0.0.1/test\n',encoding='utf-8')
    link = tmp_path/'link'
    link.symlink_to(file)
    with pytest.raises(ValueError):
        read_database_url(link)
    if os.name == 'posix':
        file.chmod(0o644)
        with pytest.raises(ValueError):
            read_database_url(file)


def test_worker_redis_url_uses_private_file_for_remote_or_credentials(tmp_path):
    assert worker_redis_url('redis://127.0.0.1:6379/0',None) == 'redis://127.0.0.1:6379/0'
    with pytest.raises(ValueError):
        worker_redis_url('rediss://redis.example.test:6379/0',None)
    with pytest.raises(ValueError):
        worker_redis_url('redis://:synthetic@127.0.0.1:6379/0',None)
    file = tmp_path/'redis-url'
    file.write_text('rediss://:synthetic@redis.example.test:6379/0\n',encoding='utf-8')
    file.chmod(0o600)
    assert worker_redis_url(None,file) == 'rediss://:synthetic@redis.example.test:6379/0'
    with pytest.raises(ValueError):
        worker_redis_url('redis://127.0.0.1:6379/0',file)
