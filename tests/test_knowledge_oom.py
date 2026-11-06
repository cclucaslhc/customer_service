"""Opt-in real Worker OOM and publication-boundary kills, not Python exceptions."""
import json
import os
import subprocess
import time
from pathlib import Path
import uuid
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from app.knowledge_admission import KnowledgeAdmissionAdmin
from app.knowledge_jobs import KnowledgeJobService
from app.persistence.durable_knowledge_repository import DurableKnowledgeRepository, KnowledgeLease, KnowledgeLeaseLost
from app.persistence.schema import metadata
from scripts.verify_container_limits import command
from test_durable_chat import queue_setup
from test_durable_knowledge import setup, expire
from test_postgresql_integration import postgres_database


pytestmark = pytest.mark.skipif(not (os.environ.get('TEST_RUNTIME_IMAGE') and
    os.environ.get('TEST_IMAGE_PG_CONTAINER')),reason='Explicit owned image and PostgreSQL container required')


@pytest.mark.parametrize('fault',['oom','before_commit','after_commit'])
def test_real_worker_fault_retains_capacity_and_requires_confirmed_release(queue_setup,postgres_database,tmp_path,fault):
    bundle,tokens,paths,service,source = setup(queue_setup,tmp_path)
    pg = os.environ['TEST_IMAGE_PG_CONTAINER']
    assert pg.startswith('cs-oom-pg-'), 'Only task-owned OOM acceptance infrastructure'
    container = json.loads(command('inspect',pg).stdout)[0]
    bindings = container['NetworkSettings']['Ports']['5432/tcp']
    host = make_url(postgres_database)
    assert any(binding['HostIp'] == '127.0.0.1' and int(binding['HostPort']) == host.port
               for binding in bindings), 'Docker PostgreSQL must match the isolated fixture instance'
    image = json.loads(command('image','inspect',os.environ['TEST_RUNTIME_IMAGE']).stdout)[0]['Id']
    local_url = host.set(host='127.0.0.1',port=5432).render_as_string(hide_password=False)
    jobs = KnowledgeJobService(service,durable=True)
    data = b'Synthetic queued input survives a Worker cgroup OOM.'
    job = jobs.create_upload('synthetic.txt',data)
    owned=[]
    # A native Linux bind mount keeps host UID ownership (unlike Desktop's
    # Windows sharing). Use the unprivileged fixture UID, not root or chmod777.
    # Image default UID65532 is independently checked by the artifact gate.
    user_options=[]
    if os.name=='posix':
        assert os.geteuid()!=0,'Use an unprivileged host fixture identity'
        user_options=['--user',f'{os.geteuid()}:{os.getegid()}']
    def peer(arguments,*,kill_at_marker=False):
        name = 'cs-oom-worker-'+uuid.uuid4().hex
        fixture = Path(__file__).resolve().parent/'fixtures'
        command('create','--pull=never','--name',name,'--network','container:'+pg,'--read-only',*user_options,
            '--memory=768m','--memory-swap=768m','--pids-limit=64','--cpus=1',
            '--cap-drop=ALL','--security-opt=no-new-privileges','--tmpfs=/tmp:rw,size=64m',
            '--mount',f'type=bind,source={tmp_path.resolve()},target=/scratch',
            '--mount',f'type=bind,source={fixture},target=/checks,readonly',
            '--env','PYTHONPATH=/opt/customer-service','--entrypoint',
            '/opt/customer-service/.venv/bin/python',image,*arguments)
        owned.append(name)
        if kill_at_marker:
            attached=subprocess.Popen(['docker','start','--attach',name],stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,text=True)
            try:
                deadline=time.monotonic()+30
                marker=tmp_path/'fault-ready'
                while not marker.exists():
                    if attached.poll() is not None:
                        stdout,stderr=attached.communicate(timeout=5)
                        raise AssertionError('Worker exited before commit boundary: '+stderr)
                    if time.monotonic()>=deadline:
                        raise AssertionError('Worker did not reach commit boundary')
                    time.sleep(.1)
                assert marker.read_text(encoding='ascii')==fault
                command('kill','--signal=KILL',name)
                stdout,stderr=attached.communicate(timeout=10)
                result=subprocess.CompletedProcess(attached.args,attached.returncode,stdout,stderr)
            finally:
                if attached.poll() is None:
                    command('kill','--signal=KILL',name,check=False)
                    attached.communicate(timeout=10)
        else:
            result = command('start','--attach',name,check=False,timeout=60)
        return json.loads(command('inspect',name).stdout)[0],result
    try:
        stopped,result = peer(['/checks/oom_knowledge_worker.py','--url',local_url,'--fault',fault],
            kill_at_marker=fault!='oom')
        assert result.returncode != 0, result.stderr + str(jobs.get(job['id']))
        assert stopped['State']['OOMKilled'] is (fault == 'oom')
        assert stopped['State']['Running'] is False and stopped['State']['ExitCode'] == 137
        table=metadata.tables['knowledge_jobs']
        with bundle.engine.connect() as db:
            row=db.execute(sa.select(table).where(table.c.id==uuid.UUID(job['id']))).mappings().one()
            chunk_count=db.execute(sa.select(sa.func.count()).select_from(
                metadata.tables['knowledge_chunks'])).scalar_one()
        assert row['status']=='running' and row['execution_slot_held'] is True
        expected = 1 if fault == 'after_commit' else 0
        documents = service.list_documents()
        assert row['imported_count']==expected and len(documents)==expected
        assert chunk_count==expected  # the synthetic document has exactly one chunk
        assert row['processed_count']==expected
        if expected:
            assert row['result_json']['imported'][0]['id']==documents[0]['id']
        items=metadata.tables['knowledge_job_items']
        with bundle.engine.connect() as db:
            item_status=db.execute(sa.select(items.c.status).where(
                items.c.job_id==uuid.UUID(job['id']))).scalar_one()
        assert item_status == ('imported' if expected else 'pending')
        lease=KnowledgeLease(job['id'],str(row['lease_owner']),row['lease_fence'])
        expire(bundle,job['id'])  # accelerate the DB deadline, not the OOM event
        root=DurableKnowledgeRepository(bundle.engine)
        assert root.reap_expired()==1
        assert jobs.get(job['id'])['stage']=='lease_expired'
        with pytest.raises(KnowledgeLeaseLost):
            root.authenticated(tokens['synthetic.admin']).with_lease(lease).renew()
        retry=jobs.create_upload('synthetic.txt',data)
        assert root.claim_next() is None  # the abandoned slot is still held
        admin=KnowledgeAdmissionAdmin(bundle.engine)
        with pytest.raises(ValueError,match='Confirm'):
            admin.release_abandoned(job_id=lease.job_id,owner=lease.owner,fence=lease.fence)
        assert admin.release_abandoned(job_id=lease.job_id,owner=lease.owner,fence=lease.fence,
            parser_stopped_confirmed=True)  # Docker terminal/OOM evidence verified above
        normal,result=peer(['scripts/knowledge_worker.py','--database-url',local_url,
            '--knowledge-root','/scratch/knowledge','--asset-root','/scratch/assets',
            '--legacy-root','/scratch/legacy','--once'])
        assert result.returncode==0 and normal['State']['OOMKilled'] is False, result.stderr
        assert jobs.get(job['id'])['status']=='failed'  # never replay the old task
        assert jobs.get(retry['id'])['status']=='complete'
        assert len(service.list_documents())==1
        if expected:
            assert jobs.get(retry['id'])['skipped_count']==1
            assert service.list_documents()[0]['id']==documents[0]['id']
            assert service.list_documents()[0]['version']==1
        assert admin.snapshot()['held']==[]
    finally:
        for name in reversed(owned):
            command('rm','-f',name)
