"""Bounded fault oracle for the owned lab; no real model, no clock backdating."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
STATE=Path('/home/labops/cs-lab/state')
DATABASE='cs_test_two_nodes_20261026'


def guard():
    if socket.gethostname() not in ('cs-s2s6-node1','cs-s2s6-node2') or not Path('/var/lib/cs-lab-ready').is_file():
        raise RuntimeError('Owned disposable guest required')


def lab_bundle(role):
    from app.runtime_files import read_database_url
    from app.persistence.bundle import PostgreSQLRepositories
    from sqlalchemy.engine import make_url
    url=read_database_url(STATE/(role+'-url'))
    parsed=make_url(url)
    if parsed.database!=DATABASE or parsed.host!='172.24.168.13' or parsed.query.get('sslmode')!='verify-full':
        raise RuntimeError('Exact disposable TLS database required')
    return PostgreSQLRepositories.from_url(url,pool_size=1,strict_role=True,runtime_role=role)


def submit():
    if socket.gethostname()!='cs-s2s6-node2': raise RuntimeError('Producer must be node2')
    from app.persistence.durable_chat_repository import DurableChatRepository
    public=json.loads((STATE/'public.json').read_text())
    if public['database']!=DATABASE: raise RuntimeError('Disposable database required')
    bundle=lab_bundle('api')
    try:
        # Controlled fixture provisioning, not an HTTP login-admission test.
        token=bundle.identity.login('synthetic.lab.customer','synthetic-password-only',public['tenant_id']).token
        sid,rid=str(uuid.uuid4()),str(uuid.uuid4())
        queue=DurableChatRepository(bundle.engine).authenticated(token)
        queue.enqueue(session_id=sid,request_id=rid,content='Synthetic worker fault only.',title='Synthetic fault',
            provider='deepseek',model='synthetic',prompt_version_id=None,instructions='Answer the synthetic question.')
        path=STATE/('fault-submit-'+rid+'.json')
        with path.open('x',encoding='utf-8') as output:
            path.chmod(0o600)
            json.dump({'run_id':rid,'session_id':sid,'token':token},output)
        return {'run_id':rid,'session_id':sid}
    finally:
        bundle.close()


def audit(rid):
    if socket.gethostname()!='cs-s2s6-node1': raise RuntimeError('Oracle must be node1')
    rid=str(uuid.UUID(rid))
    sql=f"""SELECT json_build_object('run_id',r.id,'status',r.status,'fence',r.lease_fence,
      'error_code',r.error_code,
      'attempts',(SELECT count(*) FROM model_usage_attempts a WHERE a.run_id=r.id),
      'started_attempts',(SELECT count(*) FROM model_usage_attempts a WHERE a.run_id=r.id AND a.state='started'),
      'assistants',(SELECT count(*) FROM messages m WHERE m.session_id=r.session_id AND m.role='assistant'),
      'node2_answer',(SELECT coalesce(bool_or(m.content LIKE '%cs-s2s6-node2%'),false) FROM messages m
          WHERE m.session_id=r.session_id AND m.role='assistant'),
      'done',(SELECT count(*) FROM chat_run_events e WHERE e.run_id=r.id AND e.event='done'),
      'interrupted',(SELECT count(*) FROM chat_run_events e WHERE e.run_id=r.id AND e.event='interrupted'),
      'requeues',(SELECT count(*) FROM chat_run_events e WHERE e.run_id=r.id AND e.event='run_queued'
          AND e.data->>'reason'='pre_dispatch_lease_expired'),
      'snapshots',(SELECT count(*) FROM chat_run_snapshots s WHERE s.run_id=r.id))
      FROM agent_runs r WHERE r.id='{rid}'::uuid"""
    result=subprocess.run(['sudo','-u','postgres','psql','-X','-At','-v','ON_ERROR_STOP=1',
        '-d',DATABASE,'-c',sql],capture_output=True,text=True,timeout=10)
    if result.returncode: raise RuntimeError('Bounded lab oracle query failed')
    return json.loads(result.stdout)


def wait(rid,case):
    deadline=time.monotonic()+75
    while True:
        row=audit(rid)
        if row['status'] not in ('queued','running'): break
        if time.monotonic()>=deadline: raise RuntimeError('Real lease expiry deadline exceeded')
        time.sleep(1)
    if case=='pre':
        assert (row['status'],row['fence'],row['attempts'],row['assistants'],row['done'],
            row['requeues'],row['snapshots'],row['node2_answer'])==('complete',3,1,1,1,1,1,True),row
    else:
        assert (row['status'],row['fence'],row['attempts'],row['started_attempts'],row['assistants'],
            row['done'],row['interrupted'],row['requeues'],row['snapshots'],row['error_code'])==(
            'interrupted',2,1,1,0,0,1,0,1,'lease_expired'),row
    return row


def stale(rid):
    from app.persistence.durable_chat_repository import DurableChatRepository
    from app.identity import DelegatedSession
    from app.run_leases import RunLease,RunLeaseLost
    row=json.loads((STATE/('fault-lease-'+str(uuid.UUID(rid))+'.json')).read_text())
    bundle=lab_bundle('worker')
    try:
        owner=DurableChatRepository(bundle.engine).authenticated(
            DelegatedSession(row['delegated_session_hash'])).with_lease(RunLease(**row['lease']))
        try:
            owner.append_event(row['run_id'],'token',{'text':'Synthetic stale late write'})
        except RunLeaseLost:
            return {'run_id':rid,'stale_write_rejected':True}
        raise AssertionError('Expired worker was able to append')
    finally:
        bundle.close()


def close(rid):
    if socket.gethostname()!='cs-s2s6-node2': raise RuntimeError('Producer cleanup must be node2')
    row=json.loads((STATE/('fault-submit-'+str(uuid.UUID(rid))+'.json')).read_text())
    bundle=lab_bundle('api')
    try:
        bundle.identity.logout(row['token'])
        return {'run_id':rid,'fixture_credential_revoked':True}
    finally:
        bundle.close()


def main():
    guard()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('submit','audit','wait','stale','close'))
    parser.add_argument('--run-id')
    parser.add_argument('--case',choices=('pre','model'))
    args=parser.parse_args()
    if args.action!='submit' and args.run_id is None:
        parser.error('--run-id required for this action')
    if args.action=='wait' and args.case is None:
        parser.error('--case required for wait')
    if args.action=='submit': result=submit()
    elif args.action=='audit': result=audit(args.run_id)
    elif args.action=='wait': result=wait(args.run_id,args.case)
    elif args.action=='stale': result=stale(args.run_id)
    else: result=close(args.run_id)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':
    main()
