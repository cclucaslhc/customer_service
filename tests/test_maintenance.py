"""Real maintenance barriers on owned disposable PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys
import time

from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa

from app.maintenance import Maintenance,MaintenanceHold,MaintenanceConflict
from app.persistence.maintenance_current import PROTECTED_TABLES,WRITE_GATE_KEY
from app.persistence.schema import metadata
from app.persistence.engine import build_engine
from app.backup import create_backup,restore_backup
from app.recovery import check_operational,RecoveryHold
from app.auth_http import AuthPolicy
from app.main import create_app
from app.security import SecretStore
from test_logical_backup import backup_setup,empty_database,postgres_database
from test_durable_chat import queue_setup,submit


def test_new_rag_tables_have_statement_guard_and_explicit_sealing_locks(queue_setup):
    bundle,_,_,_=queue_setup
    with bundle.engine.connect() as db:
        actual=set(db.execute(sa.text("SELECT c.relname FROM pg_trigger t JOIN pg_class c "
            "ON c.oid=t.tgrelid WHERE t.tgname='cs_maintenance_write'")).scalars())
        assert actual==set(PROTECTED_TABLES)
    manager=Maintenance(bundle.engine)
    observer=build_engine(bundle.engine.url.render_as_string(hide_password=False),pool_size=1)
    permit=manager.request()
    try:
        with manager.sealed(permit):
            # Only this disposable DB's superuser test connection bypasses
            # triggers, to prove the separate SHARE-lock protection exists.
            with observer.begin() as db:
                db.exec_driver_sql('SET LOCAL session_replication_role=replica')
                db.exec_driver_sql("SET LOCAL lock_timeout='200ms'")
                for name in ('embedding_profiles','knowledge_index_versions','knowledge_chunk_versions',
                             'knowledge_embeddings_1024','knowledge_admission_policy','rag_release_intents','rag_rollouts'):
                    with pytest.raises(sa.exc.DBAPIError,match='lock timeout'),db.begin_nested():
                        db.execute(metadata.tables[name].delete().where(sa.false()))
        manager.release(permit)
        permit=None
        with observer.connect() as db:
            assert db.exec_driver_sql('SHOW session_replication_role').scalar_one()=='origin'
    finally:
        observer.dispose()
        if permit is not None:
            manager.release(permit)


def test_real_statement_guard_covers_every_business_table_and_rejects_stale_isolation(backup_setup):
    bundle,common,package,tenant,token,revoked = backup_setup
    manager = Maintenance(bundle.engine)
    with bundle.engine.connect() as db:
        actual = set(db.execute(sa.text("SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE t.tgname='cs_maintenance_write'")).scalars())
        assert actual == set(PROTECTED_TABLES)
    with bundle.engine.connect().execution_options(isolation_level='REPEATABLE READ') as db:
        with pytest.raises(sa.exc.DBAPIError,match='read committed'):
            db.execute(metadata.tables['identity_tenants'].update().values(name='Synthetic stale writer'))
    permit = manager.request()
    with pytest.raises(MaintenanceHold):
        check_operational(bundle.engine)
    for name in ('identity_tenants','provider_configs','audit_logs','knowledge_chunks','agent_runs'):
        with bundle.engine.begin() as db:
            with pytest.raises(sa.exc.DBAPIError,match='maintenance write hold'):
                db.execute(metadata.tables[name].delete().where(sa.false()))
    manager.release(permit)
    check_operational(bundle.engine)


def test_real_hold_request_waits_for_prior_write_transaction_commit(backup_setup):
    bundle,common,package,tenant,token,revoked = backup_setup
    manager = Maintenance(bundle.engine)
    observer = build_engine(common['url'],pool_size=1)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool,bundle.engine.connect() as writer:
            tx = writer.begin()
            writer.execute(metadata.tables['identity_tenants'].update().values(name='Synthetic committed before hold'))
            future = pool.submit(manager.request)
            deadline = time.monotonic()+5
            waiting = False
            while time.monotonic() < deadline:
                with observer.connect() as db:
                    waiting = db.execute(sa.text("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND objid=:key AND NOT granted AND database=(SELECT oid FROM pg_database WHERE datname=current_database()))"),{'key':WRITE_GATE_KEY}).scalar_one()
                if waiting:
                    break
                time.sleep(.02)
            assert waiting and not future.done()
            tx.commit()
            permit = future.result(timeout=5)
        with bundle.engine.connect() as db:
            assert set(db.execute(sa.select(metadata.tables['identity_tenants'].c.name)).scalars()) == {'Synthetic committed before hold'}
        manager.release(permit)
    finally:
        observer.dispose()


def test_real_expiry_never_reopens_and_takeover_fences_old_owner(backup_setup):
    bundle,common,package,tenant,token,revoked = backup_setup
    manager = Maintenance(bundle.engine)
    old = manager.request()
    # Fault injection only into this fixture's trusted control table.
    with bundle.engine.begin() as db:
        db.execute(metadata.tables['maintenance_state'].update().values(lease_until=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
    for action in (lambda:manager.renew(old),lambda:manager.release(old),lambda:manager.request()):
        with pytest.raises(MaintenanceConflict):
            action()
    with pytest.raises(MaintenanceHold):
        check_operational(bundle.engine)
    new = manager.request(takeover_expired=True)
    assert new.fence == old.fence+1 and new.owner != old.owner
    with pytest.raises(MaintenanceConflict):
        manager.release(old)
    manager.release(new)


def test_real_sealing_requires_drained_leases_and_is_single_owner(queue_setup):
    bundle,root,owner,tokens = queue_setup
    sid,rid = submit(owner)
    claimed = root.claim_next()
    manager = Maintenance(bundle.engine)
    permit = manager.request()
    with pytest.raises(MaintenanceConflict,match='not drained'):
        with manager.sealed(permit):
            pytest.fail('Active execution was sealed')
    with pytest.raises(MaintenanceHold):
        root.claim_next()
    manager.release(permit)
    # Synthetic worker was never run; expire its DB lease while gate is open.
    with bundle.engine.begin() as db:
        db.execute(metadata.tables['agent_runs'].update().values(lease_until=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
    permit = manager.request()
    with manager.sealed(permit) as guard:
        guard()
        manager.renew(permit)
        guard()
        with pytest.raises(MaintenanceConflict,match='still active'):
            with manager.sealed(permit):
                pytest.fail('A second sealing connection entered')
    manager.release(permit)


def test_real_managed_snapshot_restore_invalidates_owner_but_retains_recovery_hold(backup_setup,tmp_path):
    bundle,common,package,tenant,token,revoked = backup_setup
    manager = Maintenance(bundle.engine)
    permit = manager.request()
    with manager.sealed(permit) as guard:
        report = create_backup(**common,destination=package,maintenance_confirmed=True,maintenance_guard=guard)
        assert report['files'] == 2
    manager.release(permit)
    with empty_database() as url:
        restored = restore_backup(**{**common,'url':url,'knowledge_root':tmp_path/'restored-docs',
            'asset_root':tmp_path/'restored-assets'},source=package,isolated_confirmed=True)
        assert restored['verified'] and restored['recovery_hold']
        engine = build_engine(url,pool_size=1)
        try:
            with pytest.raises(MaintenanceConflict):
                Maintenance(engine).check(permit)
            with pytest.raises(RecoveryHold):
                check_operational(engine)
            with engine.connect() as db:
                row = db.execute(sa.select(metadata.tables['maintenance_state'])).mappings().one()
                assert not row['blocked'] and row['owner'] is None and row['lease_until'] is None
        finally:
            engine.dispose()


def test_real_running_http_instance_and_independent_worker_observe_shared_hold(backup_setup,tmp_path):
    bundle,common,package,tenant,token,revoked = backup_setup
    policy = AuthPolicy('https://localhost')
    app = create_app(repositories=bundle,auth_policy=policy,secret_store=SecretStore(path=tmp_path/'unused.key'),
        knowledge_paths={'knowledge_dir':common['knowledge_root'],'asset_dir':common['asset_root'],'legacy_upload_dir':tmp_path/'legacy'})
    manager = Maintenance(bundle.engine)
    with TestClient(app,base_url=policy.origin) as client:
        assert client.get('/api/auth/config').status_code == 200
        permit = manager.request()
        for path in ('/api/auth/config','/api/chat/sessions','/admin/knowledge'):
            response = client.get(path)
            assert response.status_code == 503 and response.json()['code'] == 'maintenance_hold'
            assert response.headers['cache-control'] == 'no-store'
        args = [sys.executable,'scripts/knowledge_worker.py','--database-url',common['url'],'--once',
                '--knowledge-root',str(common['knowledge_root']),'--asset-root',str(common['asset_root']),
                '--legacy-root',str(tmp_path/'legacy')]
        result = subprocess.run(args,capture_output=True,text=True,timeout=20)
        assert result.returncode == 1 and 'MaintenanceHold' in result.stderr
        assert not (tmp_path/'unused.key').exists()
        manager.release(permit)
        assert client.get('/api/auth/config').status_code == 200


def test_real_backup_cli_seals_explicit_permit_without_implicitly_reopening(backup_setup,tmp_path,monkeypatch,capsys):
    import scripts.logical_backup as cli
    bundle,common,package,tenant,token,revoked = backup_setup
    manager = Maintenance(bundle.engine)
    permit = manager.request()
    monkeypatch.setenv('BACKUP_DATABASE_URL',common['url'])
    monkeypatch.setattr(sys,'argv',['logical_backup.py','create','--binary-dir',str(common['binary_dir']),
        '--package',str(package),'--knowledge-root',str(common['knowledge_root']),'--asset-root',str(common['asset_root']),
        '--key-path',str(common['key_path']),'--scratch-root',str(tmp_path),'--confirm-offline-maintenance',
        '--maintenance-owner',str(permit.owner),'--maintenance-fence',str(permit.fence)])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)['files'] == 2
    manager.check(permit)
    with pytest.raises(MaintenanceHold):
        check_operational(bundle.engine)
    manager.release(permit)


def test_real_lost_maintenance_permit_during_backup_publishes_no_package(backup_setup):
    bundle,common,package,tenant,token,revoked = backup_setup
    manager = Maintenance(bundle.engine)
    permit = manager.request()
    with pytest.raises(MaintenanceConflict):
        with manager.sealed(permit) as guard:
            calls = []
            def lose():
                calls.append(True)
                if len(calls) == 3:
                    # Explicit lease-loss injection in the disposable control row.
                    with bundle.engine.begin() as db:
                        db.execute(metadata.tables['maintenance_state'].update().values(
                            lease_until=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
                guard()
            create_backup(**common,destination=package,maintenance_confirmed=True,maintenance_guard=lose)
    assert not package.exists()
    with pytest.raises(MaintenanceHold):
        check_operational(bundle.engine)
