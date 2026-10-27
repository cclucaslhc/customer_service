"""Real PostgreSQL concurrent grants and conservative abandoned reservations."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import subprocess
import sys
import uuid
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.knowledge_admission import KnowledgeAdmissionAdmin
from app.knowledge import KnowledgeService
from app.persistence.durable_knowledge_repository import DurableKnowledgeRepository
from app.persistence.schema import metadata
from test_durable_chat import queue_setup
from test_durable_knowledge import setup, enqueue, expire
from test_postgresql_integration import postgres_database
from test_logical_backup import backup_setup, empty_database


def test_capacity_upgrade_requires_drained_knowledge_workers(postgres_database):
    from app.persistence.engine import build_engine
    command.upgrade(Config('alembic.ini'),'0028_rag_vector')
    engine = build_engine(postgres_database,pool_size=1)
    try:
        with engine.begin() as db:
            db.execute(sa.text("INSERT INTO knowledge_jobs(id,source_path,recursive,status,stage,"
                "created_at,execution_mode,lease_owner,lease_until,lease_fence) VALUES "
                "(:id,'synthetic',true,'running','parsing',clock_timestamp(),'queued',:owner,"
                "clock_timestamp()+interval '60 seconds',1)"),dict(id=uuid.uuid4(),owner=uuid.uuid4()))
        with pytest.raises(sa.exc.DBAPIError,match='Drain knowledge Workers'):
            command.upgrade(Config('alembic.ini'),'head')
        with engine.begin() as db:
            assert db.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() == '0028_rag_vector'
            db.exec_driver_sql('DELETE FROM knowledge_jobs')
        command.upgrade(Config('alembic.ini'),'head')
        with engine.connect() as db:
            assert db.exec_driver_sql('SELECT capacity,tenant_capacity FROM knowledge_admission_policy').one() == (2,1)
    finally:
        engine.dispose()


def test_global_grants_stay_bounded_after_expiry_until_acknowledged(queue_setup,tmp_path):
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    admin = KnowledgeAdmissionAdmin(bundle.engine)
    admin.configure(capacity=2,tenant_capacity=2)
    for _ in range(3):
        enqueue(service,source)
    jobs = metadata.tables['knowledge_jobs']
    with bundle.engine.begin() as db:
        with pytest.raises(sa.exc.DBAPIError,match='requires a capacity reservation'):
            with db.begin_nested():
                db.execute(jobs.update().values(status='running',lease_owner=uuid.uuid4(),
                    lease_fence=1,lease_until=sa.func.clock_timestamp()+sa.text("interval '60 seconds'")))
    root = DurableKnowledgeRepository(bundle.engine)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = [job for job in pool.map(lambda _:root.claim_next(),range(4)) if job]
    assert len(claims) == 2
    snapshot = admin.snapshot()
    assert snapshot['capacity'] == 2 and len(snapshot['held']) == 2
    result = subprocess.run([sys.executable,'scripts/configure_knowledge_admission.py',
        '--database-url',bundle.engine.url.render_as_string(hide_password=False),'inspect'],
        capture_output=True,text=True,timeout=15)
    assert result.returncode == 0 and len(json.loads(result.stdout)['held']) == 2
    with pytest.raises(ValueError,match='Drain'):
        admin.configure(capacity=3,tenant_capacity=2)
    for claim in claims:
        expire(bundle,claim['lease'].job_id)
    assert root.reap_expired() == 2
    assert root.claim_next() is None
    lease = claims[0]['lease']
    assert not root.release_slot(replace(lease,fence=lease.fence+1))
    with pytest.raises(ValueError,match='Confirm'):
        admin.release_abandoned(job_id=lease.job_id,owner=lease.owner,fence=lease.fence)
    assert admin.release_abandoned(job_id=lease.job_id,owner=lease.owner,fence=lease.fence,
                                    parser_stopped_confirmed=True)
    assert not admin.release_abandoned(job_id=lease.job_id,owner=lease.owner,fence=lease.fence,
                                       parser_stopped_confirmed=True)
    replacement = root.claim_next()
    assert replacement and replacement['lease'].job_id not in [j['lease'].job_id for j in claims]
    assert root.claim_next() is None
    assert root.release_slot(replacement['lease'])
    assert root.release_slot(claims[1]['lease'])
    with bundle.engine.connect() as db:
        audit = metadata.tables['audit_logs']
        assert db.execute(sa.select(sa.func.count()).select_from(audit).where(
            audit.c.action == 'knowledge.capacity.release')).scalar_one() == 1


def test_tenant_cap_skips_busy_tenant_without_blocking_other_tenant(queue_setup,tmp_path):
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    for _ in range(2):
        enqueue(service,source)
    other = bundle.identity.create_tenant('Synthetic other parser tenant')
    bundle.identity.provision_user('synthetic.parser.other','synthetic-password-only',other,role='admin')
    token = bundle.identity.login('synthetic.parser.other','synthetic-password-only',other).token
    other_service = KnowledgeService(repository=bundle.knowledge.authenticated(token),**paths)
    other_source = other_service.knowledge_dir/'input'
    other_source.mkdir(parents=True)
    (other_source/'synthetic.txt').write_text('Synthetic other parser source')
    other_job = enqueue(other_service,other_source)
    root = DurableKnowledgeRepository(bundle.engine)
    first,second = root.claim_next(),root.claim_next()
    assert first and second and second['lease'].job_id == other_job['id']
    assert root.claim_next() is None
    assert root.release_slot(first['lease'])
    third = root.claim_next()
    assert third and third['lease'].job_id not in {first['lease'].job_id,second['lease'].job_id}


def test_capacity_administration_rejects_non_owner(queue_setup):
    bundle,_,_,_ = queue_setup
    role = 'cs_capacity_test_'+uuid.uuid4().hex
    admin = KnowledgeAdmissionAdmin(bundle.engine)
    with bundle.engine.begin() as db:
        db.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOINHERIT')
        db.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        db.exec_driver_sql(f'GRANT SELECT ON recovery_state,maintenance_state,knowledge_admission_policy TO "{role}"')
    try:
        with bundle.engine.begin() as db:
            db.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
            assert db.exec_driver_sql("SELECT has_table_privilege(current_user,"
                "'knowledge_admission_policy','UPDATE')").scalar_one() is False
            with pytest.raises(PermissionError,match='Migration owner'):
                admin._guard(db)
    finally:
        with bundle.engine.begin() as db:
            db.exec_driver_sql(f'REVOKE SELECT ON recovery_state,maintenance_state,knowledge_admission_policy FROM "{role}"')
            db.exec_driver_sql(f'REVOKE USAGE ON SCHEMA public FROM "{role}"')
            db.exec_driver_sql(f'DROP ROLE "{role}"')


def test_actual_backup_retains_capacity_and_unconfirmed_execution(backup_setup,tmp_path):
    from app.backup import create_backup, restore_backup
    from app.persistence.engine import build_engine
    from app.recovery import RecoveryHold
    bundle,common,package,tenant,token,_ = backup_setup
    KnowledgeAdmissionAdmin(bundle.engine).configure(capacity=3,tenant_capacity=2)
    root = DurableKnowledgeRepository(bundle.engine)
    root.authenticated(token).enqueue(str(uuid.uuid4()),'synthetic-offline-job',False,[])
    claim = root.claim_next()
    assert claim
    create_backup(**common,destination=package,maintenance_confirmed=True)
    with empty_database() as url:
        restore_backup(**{**common,'url':url,'knowledge_root':tmp_path/'restored-docs',
            'asset_root':tmp_path/'restored-assets'},source=package,isolated_confirmed=True)
        engine = build_engine(url,pool_size=1)
        try:
            with engine.connect() as db:
                assert db.exec_driver_sql('SELECT capacity,tenant_capacity FROM knowledge_admission_policy').one() == (3,2)
                assert db.exec_driver_sql('SELECT execution_slot_held FROM knowledge_jobs').scalar_one() is True
            with pytest.raises(RecoveryHold):
                DurableKnowledgeRepository(engine).claim_next()
        finally:
            engine.dispose()
