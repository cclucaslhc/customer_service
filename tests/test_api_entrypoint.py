"""Fail-closed Linux API composition without using real services or secrets."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts import serve_api


def arguments(tmp_path):
    db = tmp_path / 'database-url'
    db.write_text('postgresql+psycopg://synthetic:only-for-test@127.0.0.1:55432/synthetic\n', encoding='utf-8')
    key = tmp_path / 'master-key'
    key.write_bytes(b'x' * 32)
    policy = tmp_path / 'storage-policy.json'
    policy.write_text('[]', encoding='utf-8')
    for path in (db, key, policy):
        path.chmod(0o600)
    return SimpleNamespace(database_url_file=db, master_key_path=key,
        origin='https://customer.example.test', knowledge_root=tmp_path/'knowledge',
        asset_root=tmp_path/'assets', legacy_root=tmp_path/'legacy',
        storage_policy=policy, port=8000, metrics_token_file=None, metrics_port=None,
        otlp_traces_endpoint=None, json_http_logs=False, event_retention_seconds=None,
        local_model_base_url=None)


def test_entrypoint_requires_https_private_files_and_local_metrics(tmp_path):
    args = arguments(tmp_path)
    assert serve_api.validate(args).origin == args.origin
    args.origin = 'http://127.0.0.1:8000'
    with pytest.raises(ValueError):
        serve_api.validate(args)
    args.origin = 'https://customer.example.test'
    args.metrics_port = 9107
    with pytest.raises(ValueError):
        serve_api.validate(args)
    args.metrics_port = None
    args.port = 0
    with pytest.raises(ValueError):
        serve_api.validate(args)
    args.port = 8000
    args.knowledge_root = Path('relative')
    with pytest.raises(ValueError):
        serve_api.validate(args)


def test_entrypoint_rejects_nonloopback_local_model_endpoint(tmp_path):
    args = arguments(tmp_path)
    args.local_model_base_url = 'http://example.test:11434/v1'
    with pytest.raises(ValueError, match='Local model endpoint'):
        serve_api.validate(args)
    args.local_model_base_url = 'http://127.0.0.1:11434/v1'
    assert serve_api.validate(args).origin == args.origin


def test_entrypoint_requires_explicit_pair_of_local_rag_models(tmp_path):
    args = arguments(tmp_path)
    args.rag_embedding_dir = tmp_path
    with pytest.raises(ValueError, match='experimental enablement'):
        serve_api.validate(args)
    args.enable_rag_experimental = True
    with pytest.raises(ValueError, match='both local model directories'):
        serve_api.validate(args)
    args.rag_reranker_dir = Path('relative')
    with pytest.raises(ValueError, match='absolute'):
        serve_api.validate(args)
    from app.main import create_app
    with pytest.raises(ValueError, match='authenticated PostgreSQL'):
        create_app(rag_embedding_gateway=object(), rag_reranker=object())


def test_entrypoint_rejects_symlink_or_broad_operator_file(tmp_path):
    args = arguments(tmp_path)
    link = tmp_path/'link'
    link.symlink_to(args.database_url_file)
    args.database_url_file = link
    with pytest.raises(ValueError):
        serve_api.validate(args)
    args.database_url_file = tmp_path/'database-url'
    if os.name == 'posix':
        args.database_url_file.chmod(0o644)
        with pytest.raises(ValueError):
            serve_api.validate(args)


def test_entrypoint_assembles_authenticated_durable_api_without_discovery(tmp_path,monkeypatch):
    args = arguments(tmp_path)
    calls = {}
    class FakeRepositories:
        @classmethod
        def from_url(cls,url,**kwargs):
            calls['url'] = url
            calls['role'] = kwargs
            return SimpleNamespace(close=lambda:None)
    class FakeApp:
        def add_middleware(self,middleware,**kwargs):
            calls['host'] = kwargs
    monkeypatch.setattr('app.persistence.bundle.PostgreSQLRepositories', FakeRepositories)
    monkeypatch.setattr('app.storage_config.StoragePolicy.from_file', lambda path: object())
    def create_app(**kwargs):
        calls['app'] = kwargs
        return FakeApp()
    monkeypatch.setattr('app.main.create_app', create_app)
    app,repositories,tracing,log_sink = serve_api.build_runtime(args)
    assert isinstance(app,FakeApp) and tracing is None and log_sink is None
    assert calls['role']['strict_role'] is True and calls['role']['runtime_role'] == 'api'
    assert calls['app']['durable_chat'] is True and calls['app']['durable_knowledge'] is True
    assert calls['app']['auth_policy'].secure_cookie is True
    assert calls['app']['allow_authenticated_proxy_clients'] is True
    assert calls['host'] == {'allowed_hosts':['customer.example.test']}
    assert calls['url'].endswith('/synthetic')


def test_entrypoint_fixed_loopback_and_trusted_proxy(tmp_path,monkeypatch):
    args = arguments(tmp_path)
    calls = {}
    monkeypatch.setattr(serve_api, 'parser', lambda:SimpleNamespace(parse_args=lambda:args))
    monkeypatch.setattr(serve_api, 'build_runtime', lambda _: (object(),SimpleNamespace(close=lambda:None),None,None))
    import uvicorn
    def config(app,**kwargs):
        calls.update(kwargs)
        return object()
    monkeypatch.setattr(uvicorn,'Config',config)
    monkeypatch.setattr(uvicorn,'Server',lambda config:SimpleNamespace(run=lambda:None))
    serve_api.main()
    assert calls['host'] == '127.0.0.1'
    assert calls['proxy_headers'] is True and calls['forwarded_allow_ips'] == '127.0.0.1'
    assert calls['access_log'] is False
