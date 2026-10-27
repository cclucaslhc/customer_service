"""Synthetic identities and evidence on disposable real PostgreSQL, not business acceptance."""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import uuid
import time
from threading import Event
import pytest
import sqlalchemy as sa
from app.identity import AuthenticationError, AuthorizationError, DelegatedSession, token_hash
from app.persistence.schema import metadata
from app.rag_release_service import RagReleaseService
from app.backup import invalidate_restored_release_approvals
from test_durable_chat import queue_setup
from test_postgresql_integration import postgres_database
from test_rag_release_audit import staged
from test_rag_release_gate import sample


@pytest.fixture
def release(queue_setup):
    bundle, _, _, tokens = queue_setup
    first = bundle.identity.resolve(tokens['synthetic.admin'])
    second = bundle.identity.provision_user('synthetic.reviewer', 'synthetic-password-only', first.tenant_id, role='admin')
    token = bundle.identity.login('synthetic.reviewer', 'synthetic-password-only', first.tenant_id).token
    service = RagReleaseService(bundle.engine)
    _, previous = staged(service.admin, first.tenant_id)
    service.admin.seal(tenant_id=first.tenant_id, index_id=previous)
    service.admin.activate(tenant_id=first.tenant_id, index_id=previous, expected_active_index_id=None)
    _, candidate = staged(service.admin, first.tenant_id)
    service.admin.seal(tenant_id=first.tenant_id, index_id=candidate)
    indexes = metadata.tables['knowledge_index_versions']
    with bundle.engine.connect() as db:
        manifest = db.execute(sa.select(indexes.c.manifest_sha256).where(indexes.c.index_id == candidate)).scalar_one()
    policy, evidence, binding = sample()
    binding.update(tenant_id=first.tenant_id, index_id=str(candidate), manifest_sha256=manifest)
    evidence.update(binding)
    evidence.update(completed_at=datetime.now(timezone.utc).isoformat(), reviewer_refs=[first.user_id, second])
    receipt = service.create(release_id=uuid.uuid4(), previous_index_id=previous, policy=policy,
        evidence=evidence, binding=binding, lifetime_seconds=600)
    args = dict(tenant_id=first.tenant_id, release_id=receipt['release_id'], expected_version=0,
        expected_body_sha256=receipt['body_sha256'], current_binding=binding)
    return bundle, service, args, [tokens['synthetic.admin'], token], first, second, tokens


def approve_both(service, args, tokens):
    first = service.approve(**args, token=tokens[0])
    assert first['valid_approvals'] == 1 and first['state'] == 'pending'
    args['expected_version'] = first['version']
    second = service.approve(**args, token=tokens[1])
    args['expected_version'] = second['version']
    return second


def test_operator_entrypoint_requires_two_live_reviewers_before_execution(release):
    from scripts.rag_release_control import dispatch
    bundle,service,args,tokens,*_=release
    first=dispatch(service,'approve',args,confirmed=True,prompt=lambda _:tokens[0])
    args['expected_version']=first['version']
    assert first['state']=='pending'
    with pytest.raises(AuthorizationError):
        dispatch(service,'execute',args,confirmed=True)
    second=dispatch(service,'approve',args,confirmed=True,prompt=lambda _:tokens[1])
    args['expected_version']=second['version']
    assert dispatch(service,'check',args)['valid_approvals']==2
    assert dispatch(service,'execute',args,confirmed=True)['state']=='executed'


def test_two_distinct_approvals_receipts_and_no_implicit_execution(release):
    bundle, service, args, tokens, *_ = release
    with pytest.raises(AuthorizationError):
        service.check_ready(**args)
    approved = approve_both(service, args, tokens)
    assert approved['state'] == 'approved' and approved['version'] == 2
    assert service.check_ready(**args) == approved
    assert service.approve(**args, token=tokens[1]) == approved
    assert all(token not in repr(approved) and token_hash(token) not in repr(approved) for token in tokens)
    assert 'body' not in approved
    indexes = metadata.tables['knowledge_index_versions']
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(indexes.c.status).where(indexes.c.index_id == uuid.UUID(args['current_binding']['index_id']))).scalar_one() == 'ready'


@pytest.mark.parametrize('kind', ['logout', 'password', 'membership', 'user', 'tenant', 'expiry'])
def test_revocation_invalidates_readiness(release, kind):
    bundle, service, args, tokens, first, second, _ = release
    approve_both(service, args, tokens)
    if kind == 'logout':
        bundle.identity.logout(tokens[1])
    elif kind == 'password':
        bundle.identity.reset_password(second, 'synthetic-new-password-only')
    elif kind == 'membership':
        bundle.identity.update_membership(first.tenant_id, second, role='customer', active=True)
    elif kind == 'user':
        bundle.identity.set_user_active(second, False)
    elif kind == 'tenant':
        bundle.identity.set_tenant_active(first.tenant_id, False)
    else:
        sessions = metadata.tables['identity_sessions']
        with bundle.engine.begin() as db:
            db.execute(sessions.update().where(sessions.c.token_hash == token_hash(tokens[1])).values(expires_at=sa.func.clock_timestamp()))
    with pytest.raises(AuthorizationError):
        service.check_ready(**args)


def test_reject_customer_delegated_token_changed_receipt_and_context(release):
    _, service, args, tokens, _, _, all_tokens = release
    for token in (all_tokens['synthetic.customer'], DelegatedSession(token_hash(tokens[0]))):
        with pytest.raises((AuthenticationError, AuthorizationError)):
            service.approve(**args, token=token)
    for changes in ({'expected_version': 1}, {'expected_body_sha256': 'b'*64},
                    {'current_binding': {**args['current_binding'], 'prompt_sha256': 'b'*64}}):
        with pytest.raises(ValueError):
            service.approve(**{**args, **changes}, token=tokens[0])


def test_audit_failure_rolls_back_approval(release, monkeypatch):
    bundle, service, args, tokens, *_ = release
    def fail(*a, **kw):
        raise RuntimeError('synthetic audit outage')
    monkeypatch.setattr(service.admin, '_audit', fail)
    with pytest.raises(RuntimeError, match='audit outage'):
        service.approve(**args, token=tokens[0])
    table = metadata.tables['rag_release_intents']
    with bundle.engine.connect() as db:
        row = db.execute(sa.select(table)).mappings().one()
        assert row['version'] == 0 and row['approval_a_session_hash'] is None


def test_same_revision_has_one_concurrent_winner(release):
    _, service, args, tokens, *_ = release
    def approve(token):
        try:
            return service.approve(**args, token=token)
        except ValueError as exc:
            assert 'receipt changed' in str(exc)
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(approve, tokens))
    assert sum(result is not None for result in results) == 1
    assert next(result for result in results if result)['version'] == 1


def test_restored_approval_never_becomes_ready(release):
    bundle, service, args, tokens, *_ = release
    approve_both(service, args, tokens)
    with bundle.engine.begin() as db:
        assert invalidate_restored_release_approvals(db) == 1
    args['expected_version'] += 1
    with pytest.raises(ValueError, match='fresh review'):
        service.check_ready(**args)


def test_execution_consumes_approvals_and_cannot_replay(release):
    bundle, service, args, tokens, *_ = release
    approve_both(service, args, tokens)
    result = service.execute(**args)
    assert result['state'] == 'executed' and result['version'] == 3
    with pytest.raises(ValueError, match='unavailable'):
        service.execute(**args)
    indexes = metadata.tables['knowledge_index_versions']
    with bundle.engine.connect() as db:
        active = db.execute(sa.select(indexes.c.index_id).where(indexes.c.status == 'active')).scalar_one()
        assert str(active) == args['current_binding']['index_id']
        audit = metadata.tables['audit_logs']
        assert db.execute(sa.select(sa.func.count()).select_from(audit).where(audit.c.action == 'rag.index.release_execute')).scalar_one() == 1


def test_final_audit_failure_rolls_back_activation_and_consumption(release, monkeypatch):
    bundle, service, args, tokens, *_ = release
    approve_both(service, args, tokens)
    original = service.admin._audit
    def fail(db, tenant, index, action, details):
        if action == 'release_execute':
            raise RuntimeError('synthetic final audit outage')
        return original(db, tenant, index, action, details)
    monkeypatch.setattr(service.admin, '_audit', fail)
    with pytest.raises(RuntimeError, match='final audit'):
        service.execute(**args)
    assert service.check_ready(**args)['state'] == 'approved'
    indexes = metadata.tables['knowledge_index_versions']
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(indexes.c.status).where(indexes.c.index_id == uuid.UUID(args['current_binding']['index_id']))).scalar_one() == 'ready'


def test_execution_rechecks_revocation_instead_of_trusting_readiness(release):
    bundle, service, args, tokens, *_ = release
    approve_both(service, args, tokens)
    service.check_ready(**args)
    bundle.identity.logout(tokens[1])
    with pytest.raises(AuthorizationError):
        service.execute(**args)


@pytest.mark.parametrize('kind', ['membership', 'user', 'tenant', 'session'])
def test_committed_revocation_wins_over_waiting_publication(release, kind):
    bundle, service, args, tokens, first, second, _ = release
    approve_both(service, args, tokens)
    table = metadata.tables[{'membership': 'identity_memberships', 'user': 'identity_users',
        'tenant': 'identity_tenants', 'session': 'identity_sessions'}[kind]]
    predicate = {'membership': lambda: table.c.user_id == uuid.UUID(second),
        'user': lambda: table.c.id == uuid.UUID(second),
        'tenant': lambda: table.c.id == uuid.UUID(first.tenant_id),
        'session': lambda: table.c.token_hash == token_hash(tokens[1])}[kind]()
    with bundle.engine.connect() as revoker:
        transaction = revoker.begin()
        pid = revoker.execute(sa.select(sa.func.pg_backend_pid())).scalar_one()
        revoker.execute(table.update().where(predicate).values(**(
            {'revoked_at': sa.func.clock_timestamp()} if kind == 'session' else {'active': False})))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(service.execute, **args)
            try:
                deadline = time.monotonic() + 5
                with bundle.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer:
                    while not observer.execute(sa.text('SELECT EXISTS (SELECT 1 FROM pg_stat_activity '
                            'WHERE :pid = ANY(pg_blocking_pids(pid)))'), {'pid': pid}).scalar_one():
                        assert not future.done(), 'Publication finished before revocation lock was observed'
                        assert time.monotonic() < deadline, 'Expected identity lock wait not observed'
                        time.sleep(.01)
                transaction.commit()
                with pytest.raises(AuthorizationError):
                    future.result(timeout=5)
            finally:
                if transaction.is_active:
                    transaction.rollback()
    indexes = metadata.tables['knowledge_index_versions']
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(indexes.c.status).where(indexes.c.index_id == uuid.UUID(args['current_binding']['index_id']))).scalar_one() == 'ready'


def test_publication_identity_locks_serialize_membership_revocation(release, monkeypatch):
    bundle, service, args, tokens, first, second, _ = release
    approve_both(service, args, tokens)
    reached, proceed, revoke_started = Event(), Event(), Event()
    original = service.admin._activate
    def pause(db, **kw):
        reached.set()
        assert proceed.wait(5), 'Test did not release publication'
        return original(db, **kw)
    monkeypatch.setattr(service.admin, '_activate', pause)
    def revoke():
        revoke_started.set()
        bundle.identity.update_membership(first.tenant_id, second, role='customer', active=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        execution = pool.submit(service.execute, **args)
        assert reached.wait(5)
        revocation = pool.submit(revoke)
        try:
            assert revoke_started.wait(5)
            deadline = time.monotonic() + 5
            with bundle.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer:
                while not observer.execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE wait_event_type='Lock' AND query LIKE 'UPDATE identity_memberships%')")).scalar_one():
                    assert not revocation.done(), 'Revocation bypassed held identity locks'
                    assert time.monotonic() < deadline
                    time.sleep(.01)
            proceed.set()
            assert execution.result(timeout=5)['state'] == 'executed'
            revocation.result(timeout=5)
        finally:
            proceed.set()
    with pytest.raises((AuthenticationError, AuthorizationError)):
        bundle.identity.resolve(tokens[1]).require('config.manage')


@pytest.mark.parametrize('approved', [False, True])
def test_designated_reviewer_can_terminally_withdraw_release(release, approved):
    _, service, args, tokens, *_ = release
    if approved:
        approve_both(service, args, tokens)
    result = service.reject(**args, token=tokens[0])
    assert result['state'] == 'rejected'
    args['expected_version'] = result['version']
    for operation in (service.execute, service.check_ready):
        with pytest.raises(ValueError, match='unavailable'):
            operation(**args)
    with pytest.raises(ValueError, match='unavailable'):
        service.approve(**args, token=tokens[1])


def test_withdrawal_requires_designated_live_admin_and_atomic_audit(release, monkeypatch):
    bundle, service, args, tokens, first, _, all_tokens = release
    bundle.identity.provision_user('synthetic.nondesignated', 'synthetic-password-only', first.tenant_id, role='admin')
    other = bundle.identity.login('synthetic.nondesignated', 'synthetic-password-only', first.tenant_id).token
    for token in (other, all_tokens['synthetic.customer'], DelegatedSession(token_hash(tokens[0]))):
        with pytest.raises((AuthenticationError, AuthorizationError)):
            service.reject(**args, token=token)
    def fail(*a, **kw):
        raise RuntimeError('synthetic withdrawal audit failure')
    monkeypatch.setattr(service.admin, '_audit', fail)
    with pytest.raises(RuntimeError, match='withdrawal audit'):
        service.reject(**args, token=tokens[0])
    table = metadata.tables['rag_release_intents']
    with bundle.engine.connect() as db:
        row = db.execute(sa.select(table)).mappings().one()
        assert row['state'] == 'pending' and row['version'] == 0


def test_withdrawal_and_execution_have_only_one_terminal_winner(release):
    bundle, service, args, tokens, *_ = release
    approve_both(service, args, tokens)
    def run(operation):
        try:
            return (service.execute(**args) if operation == 'execute'
                    else service.reject(**args, token=tokens[0]))['state']
        except ValueError as exc:
            assert 'unavailable' in str(exc)
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ['execute', 'reject']))
    assert sum(result is not None for result in results) == 1
    table, indexes = metadata.tables['rag_release_intents'], metadata.tables['knowledge_index_versions']
    with bundle.engine.connect() as db:
        state = db.execute(sa.select(table.c.state)).scalar_one()
        status = db.execute(sa.select(indexes.c.status).where(indexes.c.index_id == uuid.UUID(args['current_binding']['index_id']))).scalar_one()
        assert (state, status) in [('rejected', 'ready'), ('executed', 'active')]
