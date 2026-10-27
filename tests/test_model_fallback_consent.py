"""Consent is scoped, explicit, versioned, and audited without online calls."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from pydantic_ai.usage import RequestUsage
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.exceptions import ModelHTTPError

from app.identity import AuthorizationError
from app.auth_http import AuthPolicy
from app.main import create_app
from app.model_budget import ModelBudgetError
from app.model_fallback import FallbackModel
from app.model_observability import observe_model
from app.providers import ProviderError, ProviderSnapshot
from app.persistence.model_cost_repository import CostConflict
from app.persistence.model_usage_repository import ModelFallbackDenied
from app.persistence.schema import metadata
from app.security import SecretStore
from test_durable_chat import queue_setup, postgres_database
from test_model_usage import prepare
from test_model_cost import publish as publish_primary_price
from test_model_budget import activate as activate_primary_budget, request as budget_request
from test_durable_chat import submit


def test_fallback_consent_default_deny_publish_revoke_and_audit(queue_setup):
    bundle, _, _, tokens = queue_setup
    admin = bundle.config.authenticated(tokens['synthetic.admin'])
    consent = admin.model_fallbacks()
    assert consent.status('openai') == {
        'provider': 'openai', 'enabled': False, 'version': 0, 'state': 'valid'}
    with pytest.raises(ValueError, match='not configured'):
        consent.publish(provider='openai', enabled=True, expected_version=0,
                        publication_id=uuid4())
    admin.update_provider('openai', model='synthetic-online', now='2026-10-17T00:00:00+00:00',
                          encrypted_key=b'synthetic-ciphertext', nonce=b'synthetic-nonce', replace_key=True)
    first = uuid4()
    assert consent.publish(provider='openai', enabled=True, expected_version=0,
                           publication_id=first)['version'] == 1
    assert consent.publish(provider='openai', enabled=True, expected_version=0,
                           publication_id=first)['version'] == 1
    with pytest.raises(CostConflict):
        consent.publish(provider='openai', enabled=False, expected_version=0,
                        publication_id=first)
    with pytest.raises(CostConflict):
        consent.publish(provider='openai', enabled=False, expected_version=0,
                        publication_id=uuid4())
    assert consent.publish(provider='openai', enabled=False, expected_version=1,
                           publication_id=uuid4())['version'] == 2
    assert consent.status('openai')['enabled'] is False
    with bundle.engine.connect() as db:
        audit = metadata.tables['audit_logs']
        rows = db.execute(sa.select(audit.c.metadata_json).where(
            audit.c.action == 'model.fallback.consent.publish').order_by(audit.c.created_at)).scalars().all()
        assert len(rows) == 2
        assert [row['enabled'] for row in rows] == [True, False]
    with bundle.engine.begin() as db:
        settings = metadata.tables['app_settings']
        db.execute(settings.update().where(settings.c.key == 'online_fallback_consent:openai')
                   .values(value='{"enabled":true,"version":"corrupt"}'))
    assert consent.status('openai') == {
        'provider':'openai', 'enabled':False, 'version':None, 'state':'invalid'}


def test_fallback_consent_rejects_customer_and_unknown_provider(queue_setup):
    bundle, _, _, tokens = queue_setup
    customer = bundle.config.authenticated(tokens['synthetic.customer']).model_fallbacks()
    with pytest.raises(AuthorizationError):
        customer.status('openai')
    with pytest.raises(AuthorizationError):
        customer.publish(provider='openai', enabled=False, expected_version=0,
                         publication_id=uuid4())
    admin = bundle.config.authenticated(tokens['synthetic.admin']).model_fallbacks()
    with pytest.raises(ValueError, match='Unsupported'):
        admin.status('deepseek')
    with pytest.raises(ValueError, match='Unsupported'):
        admin.publish(provider='local', enabled=True, expected_version=0,
                      publication_id=uuid4())


def test_fallback_consent_is_tenant_scoped_and_concurrent_revision_is_serialized(queue_setup):
    bundle, _, _, tokens = queue_setup
    admin = bundle.config.authenticated(tokens['synthetic.admin'])
    admin.update_provider('openai', model='synthetic-online', now='2026-10-17T00:00:00+00:00',
                          encrypted_key=b'synthetic-ciphertext', nonce=b'synthetic-nonce', replace_key=True)
    consent = admin.model_fallbacks()
    def publish(_):
        try:
            return consent.publish(provider='openai', enabled=True, expected_version=0,
                                   publication_id=uuid4())['version']
        except CostConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(publish, range(2)), key=str) == [1, 'conflict']
    other = bundle.identity.create_tenant('Synthetic fallback isolation')
    bundle.identity.provision_user('synthetic.fallback.other', 'synthetic-password-only', other, role='admin')
    token = bundle.identity.login('synthetic.fallback.other', 'synthetic-password-only', other).token
    other_consent = bundle.config.authenticated(token).model_fallbacks()
    assert other_consent.status('openai')['enabled'] is False
    with pytest.raises(ValueError, match='not configured'):
        other_consent.publish(provider='openai', enabled=True, expected_version=0,
                              publication_id=uuid4())


def test_alternate_paid_attempt_checks_current_consent_and_records_actual_model(queue_setup):
    bundle, _, _, tokens, _, rid, _, chat, primary = prepare(queue_setup)
    admin = bundle.config.authenticated(tokens['synthetic.admin'])
    alternate = chat.model_meter(rid, provider='openai', model='synthetic-online')
    with pytest.raises(ModelFallbackDenied):
        alternate.begin(uuid4())
    assert primary.list_attempts() == []
    admin.update_provider('openai', model='synthetic-online', now='2026-10-17T00:00:00+00:00',
                          encrypted_key=b'synthetic-ciphertext', nonce=b'synthetic-nonce', replace_key=True)
    consent = admin.model_fallbacks()
    consent.publish(provider='openai', enabled=True, expected_version=0, publication_id=uuid4())
    wrong_model = chat.model_meter(rid, provider='openai', model='different')
    with pytest.raises(ModelFallbackDenied):
        wrong_model.begin(uuid4())
    with pytest.raises(ModelBudgetError):
        alternate.begin(uuid4())
    publish_primary_price(admin.model_costs())
    admin.model_costs().publish_price(publication_id=str(uuid4()),expected_version=0,
        provider='openai',model='synthetic-online',currency='CNY',source_sha256='a'*64,
        input_rate='2',output_rate='8',cache_read_rate='1',cache_write_rate='3')
    activate_primary_budget(bundle,tokens)
    admin.model_budgets().publish_route_policy(publication_id=uuid4(),expected_version=0,
        provider='openai',model='synthetic-online',currency='CNY',
        input_token_ceiling=100,output_token_limit=10,source_sha256='b'*64)
    attempt = uuid4()
    alternate.begin(attempt,request=budget_request())
    rows = primary.list_attempts()
    assert len(rows) == 1 and rows[0]['provider'] == 'openai' and rows[0]['model'] == 'synthetic-online'
    consent.publish(provider='openai', enabled=False, expected_version=1, publication_id=uuid4())
    with pytest.raises(ModelFallbackDenied):
        alternate.begin(uuid4())
    # Revocation blocks new transmissions, not billing evidence for an already-started request.
    alternate.finish(attempt, 'completed', RequestUsage(input_tokens=3, output_tokens=2))
    assert primary.list_attempts()[0]['state'] == 'completed'


def test_alternate_attempt_fails_closed_when_budget_only_covers_primary(queue_setup):
    bundle, _, _, tokens, _, rid, _, chat, primary = prepare(queue_setup)
    admin = bundle.config.authenticated(tokens['synthetic.admin'])
    publish_primary_price(admin.model_costs())
    activate_primary_budget(bundle, tokens)
    admin.update_provider('openai', model='synthetic-online', now='2026-10-17T00:00:00+00:00',
                          encrypted_key=b'synthetic-ciphertext', nonce=b'synthetic-nonce', replace_key=True)
    admin.model_fallbacks().publish(provider='openai', enabled=True,
                                    expected_version=0, publication_id=uuid4())
    alternate = chat.model_meter(rid, provider='openai', model='synthetic-online')
    with pytest.raises(ModelBudgetError):
        alternate.begin(uuid4())
    assert primary.list_attempts() == []


@pytest.mark.asyncio
async def test_request_route_uses_consent_and_budget_before_online_io(queue_setup):
    bundle, _, _, tokens, _, rid, _, chat, primary = prepare(queue_setup)
    admin = bundle.config.authenticated(tokens['synthetic.admin'])
    publish_primary_price(admin.model_costs())
    admin.model_costs().publish_price(publication_id=str(uuid4()), expected_version=0,
        provider='openai', model='synthetic-online', currency='CNY', source_sha256='a'*64,
        input_rate='2', output_rate='8', cache_read_rate='1', cache_write_rate='3')
    activate_primary_budget(bundle, tokens)
    admin.model_budgets().publish_route_policy(publication_id=uuid4(), expected_version=0,
        provider='openai', model='synthetic-online', currency='CNY',
        input_token_ceiling=100, output_token_limit=10, source_sha256='b'*64)
    admin.update_provider('openai', model='synthetic-online', now='2026-10-18T00:00:00+00:00',
        encrypted_key=b'synthetic-ciphertext', nonce=b'synthetic-nonce', replace_key=True)
    consent = admin.model_fallbacks()
    consent.publish(provider='openai', enabled=True, expected_version=0, publication_id=uuid4())
    outbound = []
    class SyntheticGateway:
        def fallback_snapshot(self, provider):
            if provider == 'local':
                raise ProviderError('local_endpoint_unavailable', 'synthetic unavailable')
            return ProviderSnapshot('openai', 'synthetic-online', 'synthetic-only')

        def build_model(self, snapshot):
            def online(messages, info):
                outbound.append(snapshot.provider)
                with bundle.engine.connect() as db:
                    rows = db.execute(sa.select(metadata.tables['model_usage_attempts']).where(
                        metadata.tables['model_usage_attempts'].c.run_id == UUID(rid))).mappings().all()
                    assert any(row['provider'] == 'openai' and row['state'] == 'started' for row in rows)
                return ModelResponse([TextPart('synthetic answer')],
                    usage=RequestUsage(input_tokens=2, output_tokens=1))
            return FunctionModel(online)

    def unavailable(messages, info):
        raise ModelHTTPError(503, 'synthetic')
    route = FallbackModel(observe_model(FunctionModel(unavailable), provider='deepseek', meter=primary),
        gateway=SyntheticGateway(), run_repository=chat, run_id=rid,
        primary_provider='deepseek', primary_model='synthetic')
    response = await route.request(*budget_request())
    assert response.parts[0].content == 'synthetic answer'
    assert (route.last_provider, route.last_model) == ('openai', 'synthetic-online')
    assert outbound == ['openai']
    rows = primary.list_attempts()
    assert [row['provider'] for row in rows] == ['deepseek', 'openai']
    assert len(admin.model_budgets().reservations(run_id=rid)) == 2
    consent.publish(provider='openai', enabled=False, expected_version=1, publication_id=uuid4())
    with pytest.raises(ModelHTTPError):
        await route.request(*budget_request())
    assert outbound == ['openai']
    assert [row['provider'] for row in primary.list_attempts()] == ['deepseek', 'openai', 'deepseek']


@pytest.mark.asyncio
async def test_durable_chat_reports_and_persists_actual_fallback_provider(queue_setup):
    from app.chat import ChatOrchestrator
    import json

    bundle, root, owner, tokens = queue_setup
    admin = bundle.config.authenticated(tokens['synthetic.admin'])
    publish_primary_price(admin.model_costs())
    admin.model_costs().publish_price(publication_id=str(uuid4()), expected_version=0,
        provider='openai', model='synthetic-online', currency='CNY', source_sha256='a'*64,
        input_rate='2', output_rate='8', cache_read_rate='1', cache_write_rate='3')
    activate_primary_budget(bundle, tokens)
    admin.model_budgets().publish_route_policy(publication_id=uuid4(), expected_version=0,
        provider='openai', model='synthetic-online', currency='CNY',
        input_token_ceiling=100, output_token_limit=10, source_sha256='b'*64)
    admin.update_provider('openai', model='synthetic-online', now='2026-10-18T00:00:00+00:00',
        encrypted_key=b'synthetic-ciphertext', nonce=b'synthetic-nonce', replace_key=True)
    admin.model_fallbacks().publish(provider='openai', enabled=True,
        expected_version=0, publication_id=uuid4())
    sid, rid = submit(owner)
    job = root.claim_next()
    tenant = bundle.identity.resolve(tokens['synthetic.customer']).tenant_id

    class SyntheticGateway:
        def snapshot(self):
            return ProviderSnapshot('deepseek', 'synthetic', 'synthetic-only', tenant)

        def fallback_snapshot(self, provider):
            if provider == 'local':
                raise ProviderError('local_endpoint_unavailable', 'synthetic unavailable')
            return ProviderSnapshot('openai', 'synthetic-online', 'synthetic-only', tenant)

        def build_model(self, snapshot):
            async def stream(messages, info):
                yield '合成备用回答。'
            model = FunctionModel(stream_function=stream)
            if snapshot.provider == 'deepseek':
                @asynccontextmanager
                async def unavailable(*args, **kwargs):
                    raise ModelHTTPError(503, 'synthetic')
                    yield  # pragma: no cover
                model.request_stream = unavailable
            return model

    orchestrator = ChatOrchestrator(SyntheticGateway(), object(),
        repository=root.authenticated(job['credential']),
        identity_resolver=lambda: bundle.identity.resolve(job['credential']))
    frames = [frame async for frame in orchestrator.stream(sid, job['content'], prepared=job)]
    providers = [json.loads(frame.split('data: ', 1)[1]) for frame in frames
        if frame.startswith('event: provider\n')]
    assert [item['provider'] for item in providers] == ['deepseek', 'openai']
    assert any(frame.startswith('event: done\n') for frame in frames)
    assert owner.history(sid)[-1]['provider'] == 'openai'
    assert owner.history(sid)[-1]['model'] == 'synthetic-online'


def test_authenticated_http_consent_default_deny_and_customer_rejected(queue_setup, tmp_path):
    bundle, _, _, tokens = queue_setup
    policy = AuthPolicy('https://localhost')
    app = create_app(repositories=bundle, secret_store=SecretStore(path=tmp_path/'unused.key'),
        knowledge_paths={'knowledge_dir':tmp_path/'docs', 'asset_dir':tmp_path/'assets',
                         'legacy_upload_dir':tmp_path/'legacy'}, auth_policy=policy)
    tenant = bundle.identity.resolve(tokens['synthetic.admin']).tenant_id
    with TestClient(app, base_url=policy.origin, client=('127.0.0.1', 50000)) as client:
        assert client.get('/api/admin/model-fallback/openai').status_code == 401
        for name in ('synthetic.customer', 'synthetic.admin'):
            login = client.post('/api/auth/login', headers={'Origin':policy.origin}, json={
                'username':name, 'password':'synthetic-password-only', 'tenant_id':tenant})
            assert login.status_code == 200
            headers = {'Origin':policy.origin, 'X-CSRF-Token':login.json()['csrf_token']}
            if name == 'synthetic.customer':
                denied = client.get('/api/admin/model-fallback/openai')
                assert denied.status_code == 403, denied.text
                assert client.put('/api/admin/model-fallback/openai', headers=headers, json={
                    'publication_id':str(uuid4()), 'expected_version':0, 'enabled':False}).status_code == 403
            else:
                status = client.get('/api/admin/model-fallback/openai')
                assert status.status_code == 200 and status.json()['enabled'] is False
                assert client.put('/api/admin/model-fallback/openai', headers=headers, json={
                    'publication_id':str(uuid4()), 'expected_version':0, 'enabled':True}).status_code == 422
                assert client.get('/api/admin/model-fallback/local').status_code == 422
                bundle.config.authenticated(tokens['synthetic.admin']).update_provider(
                    'openai', model='synthetic-online', now='2026-10-17T00:00:00+00:00',
                    encrypted_key=b'synthetic-ciphertext', nonce=b'synthetic-nonce', replace_key=True)
                enabled = client.put('/api/admin/model-fallback/openai', headers=headers, json={
                    'publication_id':str(uuid4()), 'expected_version':0, 'enabled':True})
                assert enabled.status_code == 200 and enabled.json()['version'] == 1
                assert client.get('/api/admin/model-fallback/openai').json()['enabled'] is True
                revoked = client.put('/api/admin/model-fallback/openai', headers=headers, json={
                    'publication_id':str(uuid4()), 'expected_version':1, 'enabled':False})
                assert revoked.status_code == 200 and revoked.json()['version'] == 2
                assert client.get('/api/admin/model-fallback/openai').json()['enabled'] is False
        assert not (tmp_path/'unused.key').exists()
