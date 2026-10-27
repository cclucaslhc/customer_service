"""Read-only queue review against a disposable PostgreSQL database and role."""

import json
import subprocess
import sys
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from app.persistence.durable_knowledge_repository import DurableKnowledgeRepository
from app.persistence.engine import build_engine
from app.queue_review import list_queue_failures
from test_durable_chat import queue_setup, submit
from test_postgresql_integration import postgres_database


def test_bounded_failure_inventory_requires_readonly_role_and_never_replays(queue_setup,postgres_database,tmp_path):
    bundle,chat,owner,tokens = queue_setup
    knowledge = DurableKnowledgeRepository(bundle.engine,token=tokens['synthetic.admin'])
    knowledge_root = DurableKnowledgeRepository(bundle.engine)
    run_queue = chat.tables['chat_run_queue']
    runs = chat.tables['agent_runs']
    knowledge_queue = knowledge.tables['knowledge_job_queue']
    knowledge_jobs = knowledge.tables['knowledge_jobs']

    sid,chat_queued = submit(owner,content='SECRET_CHAT_BODY')
    with bundle.engine.begin() as db:
        db.execute(run_queue.update().where(run_queue.c.run_id == uuid.UUID(chat_queued)).values(
            created_at=sa.func.clock_timestamp()-sa.text("interval '3 seconds'"),
            deadline=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
    assert chat.reap_pending() == 1
    _,chat_running = submit(owner)
    claimed = chat.claim_next()
    assert claimed['run_id'] == chat_running
    chat.authenticated(claimed['credential']).with_lease(claimed['lease']).model_meter(
        chat_running,provider='deepseek',model='synthetic').begin(str(uuid.uuid4()))
    with bundle.engine.begin() as db:
        db.execute(runs.update().where(runs.c.id == uuid.UUID(chat_running)).values(
            lease_until=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
    assert chat.reap_expired_leases() == 1
    cancel_sid,cancelled = submit(owner)
    assert owner.request_cancel(cancel_sid,cancelled)
    assert chat.reap_pending() == 1

    knowledge_queued,knowledge_running = str(uuid.uuid4()),str(uuid.uuid4())
    knowledge.enqueue(knowledge_queued,'SECRET_SOURCE_PATH',False,[])
    with bundle.engine.begin() as db:
        db.execute(knowledge_queue.update().where(knowledge_queue.c.job_id == uuid.UUID(knowledge_queued)).values(
            deadline=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
    assert knowledge_root.reap_expired() == 1
    knowledge.enqueue(knowledge_running,'SECRET_SOURCE_PATH',False,[])
    assert knowledge_root.claim_next()['lease'].job_id == knowledge_running
    with bundle.engine.begin() as db:
        db.execute(knowledge_jobs.update().where(knowledge_jobs.c.id == uuid.UUID(knowledge_running)).values(
            lease_until=sa.func.clock_timestamp()-sa.text("interval '1 second'")))
    assert knowledge_root.reap_expired() == 1

    with pytest.raises(PermissionError):
        list_queue_failures(bundle.engine)
    for invalid in (0,201,True):
        with pytest.raises(ValueError):
            list_queue_failures(bundle.engine,limit=invalid)

    role = 'cs_review_' + uuid.uuid4().hex
    password = 'synthetic-review-only'
    reviewer = None
    created_role = False
    database = make_url(postgres_database).database
    try:
        with bundle.engine.begin() as db:
            db.exec_driver_sql(f'CREATE ROLE "{role}" LOGIN PASSWORD \'{password}\' '
                'NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION')
            db.exec_driver_sql(f'GRANT CONNECT ON DATABASE "{database}" TO "{role}"')
            db.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
            db.exec_driver_sql(f'GRANT SELECT ON public.agent_runs, public.knowledge_jobs TO "{role}"')
        created_role = True
        url = make_url(postgres_database).set(username=role,password=password).render_as_string(hide_password=False)
        reviewer = build_engine(url,pool_size=1)
        rows = list_queue_failures(reviewer)
        by_id = {row['id']:row for row in rows}
        assert set(by_id) == {chat_queued,chat_running,knowledge_queued,knowledge_running}
        assert by_id[chat_queued]['disposition'] == by_id[knowledge_queued]['disposition'] == 'terminal_no_execution'
        assert by_id[chat_running]['disposition'] == by_id[knowledge_running]['disposition'] == 'review_unknown'
        assert by_id[knowledge_running]['reason'] == 'lease_expired'
        assert cancelled not in by_id
        assert len(list_queue_failures(reviewer,limit=2)) == 2
        encoded = json.dumps(rows)
        assert 'SECRET_' not in encoded and 'credential' not in encoded and 'content' not in encoded
        with bundle.engine.begin() as db:
            db.exec_driver_sql(f'GRANT UPDATE ON public.chat_sessions TO "{role}"')
        with pytest.raises(PermissionError):
            list_queue_failures(reviewer)
        with bundle.engine.begin() as db:
            db.exec_driver_sql(f'REVOKE UPDATE ON public.chat_sessions FROM "{role}"')
            db.exec_driver_sql(f'GRANT pg_monitor TO "{role}"')
        with pytest.raises(PermissionError):
            list_queue_failures(reviewer)
        with bundle.engine.begin() as db:
            db.exec_driver_sql(f'REVOKE pg_monitor FROM "{role}"')
            db.exec_driver_sql('ALTER TABLE public.knowledge_jobs ENABLE ROW LEVEL SECURITY')
        with pytest.raises(PermissionError):
            list_queue_failures(reviewer)
        with bundle.engine.begin() as db:
            db.exec_driver_sql('ALTER TABLE public.knowledge_jobs DISABLE ROW LEVEL SECURITY')
        source = tmp_path/'review-database-url'
        source.write_text(url,encoding='utf-8')
        cli = subprocess.run([sys.executable,'scripts/queue_review.py','--database-url-file',str(source),
            '--limit','5'],capture_output=True,text=True,timeout=15)
        assert cli.returncode == 0, cli.stderr
        assert {item['id'] for item in json.loads(cli.stdout)['tasks']} == set(by_id)
        assert 'SECRET_' not in cli.stdout and password not in cli.stdout
        assert chat.claim_next() is None and knowledge_root.claim_next() is None
    finally:
        if reviewer is not None:
            reviewer.dispose()
        if created_role:
            with bundle.engine.begin() as db:
                db.exec_driver_sql('ALTER TABLE public.knowledge_jobs DISABLE ROW LEVEL SECURITY')
                db.exec_driver_sql(f'REVOKE UPDATE ON public.chat_sessions FROM "{role}"')
                db.exec_driver_sql(f'REVOKE pg_monitor FROM "{role}"')
                db.exec_driver_sql(f'REVOKE SELECT ON public.agent_runs, public.knowledge_jobs FROM "{role}"')
                db.exec_driver_sql(f'REVOKE USAGE ON SCHEMA public FROM "{role}"')
                db.exec_driver_sql(f'REVOKE CONNECT ON DATABASE "{database}" FROM "{role}"')
                db.exec_driver_sql(f'DROP ROLE "{role}"')
