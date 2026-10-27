"""Synthetic-model worker on owned guests; production code/guards stay unchanged."""
import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import socket
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
STATE=Path('/home/labops/cs-lab/state')


def crash_with_lease(job):
    """Test-only durable receipt. Never print delegated session references."""
    path=STATE/('fault-lease-'+job['run_id']+'.json')
    with path.open('x',encoding='utf-8') as output:
        path.chmod(0o600)
        json.dump({'run_id':job['run_id'],'session_id':job['session_id'],
            'lease':asdict(job['lease']),'delegated_session_hash':job['credential'].digest},output)
        output.flush()
        os.fsync(output.fileno())
    os.kill(os.getpid(),signal.SIGKILL)


async def main(delay,mode='normal'):
    if socket.gethostname() not in ('cs-s2s6-node1','cs-s2s6-node2') or not Path('/var/lib/cs-lab-ready').is_file():
        raise RuntimeError('Owned disposable guest required')
    from app.runtime_files import read_database_url
    from sqlalchemy.engine import make_url
    url=read_database_url(STATE/'worker-url')
    parsed=make_url(url)
    if (parsed.database!='cs_test_two_nodes_20261026' or parsed.host!='172.24.168.13'
            or parsed.query.get('sslmode')!='verify-full' or not 0<=delay<=60
            or mode not in ('normal','pre-dispatch-crash','model-crash')):
        raise RuntimeError('Exact TLS disposable database and bounded delay required')
    from app.persistence.bundle import PostgreSQLRepositories
    from app.chat_worker import ChatWorker
    from app.security import SecretStore
    from app.providers import ProviderSnapshot
    from pydantic_ai.models.function import FunctionModel
    class Gateway:
        def __init__(self,job): self.job=job
        def snapshot(self):
            return ProviderSnapshot(self.job['provider'],self.job['model'],'unused-synthetic-only')
        def build_model(self,snapshot):
            async def synthetic(messages,info):
                if mode=='model-crash':
                    crash_with_lease(self.job)
                yield 'Synthetic answer on '+socket.gethostname()+'. '
                await asyncio.sleep(delay)
                yield 'Durable completion.'
            return FunctionModel(stream_function=synthetic)
    bundle=PostgreSQLRepositories.from_url(url,pool_size=3,strict_role=True,runtime_role='worker')
    bundle.check_schema()
    data=Path('/home/labops/cs-lab/data')
    class FaultWorker(ChatWorker):
        async def _execute(self,job,permit):
            if mode=='pre-dispatch-crash':
                crash_with_lease(job)
            return await super()._execute(job,permit)
    worker=FaultWorker(bundle,SecretStore(path=STATE/'master.key'),{
        'knowledge_dir':data/'docs','asset_dir':data/'assets','legacy_upload_dir':data/'legacy'},
        gateway_factory=lambda credential,job:Gateway(job))
    stop=asyncio.Event()
    for signum in (signal.SIGTERM,signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum,stop.set)
    try:
        await worker.run(stop)
    finally:
        bundle.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delay',type=float,default=1)
    parser.add_argument('--mode',choices=('normal','pre-dispatch-crash','model-crash'),default='normal')
    try:
        args=parser.parse_args()
        asyncio.run(main(args.delay,args.mode))
    except Exception as exc:
        print('Lab worker failed: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(1) from None
