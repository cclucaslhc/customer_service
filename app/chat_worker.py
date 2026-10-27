"""Separate queue consumer. HTTP subscribers never own or cancel this task."""
import asyncio
import json
import logging
from contextlib import aclosing, nullcontext
import sqlalchemy as sa
from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError as DatabaseTimeout
from app.chat import ChatOrchestrator, ChatRuntime
from app.identity import AuthenticationError, AuthorizationError
from app.knowledge import KnowledgeService
from app.persistence.durable_chat_repository import DurableChatRepository
from app.providers import ModelGateway
from app.admission import CoordinationUnavailable


class ChatWorker:
    def __init__(self,repositories,secrets,knowledge_paths,*,gateway_factory=None,admission=None,storage_configuration=None,tracing=None,local_model_endpoint=None,rag_embedding_gateway=None,rag_reranker=None):
        self.repositories,self.secrets,self.paths = repositories,secrets,knowledge_paths
        self.queue = DurableChatRepository(repositories.engine)
        self.gateway_factory = gateway_factory
        self.local_model_endpoint = local_model_endpoint
        self.rag_embedding_gateway = rag_embedding_gateway
        self.rag_reranker = rag_reranker
        self.admission = admission
        self.storage_configuration = storage_configuration
        self.tracing = tracing
        if tracing is not None:
            from app.tracing import install_database_tracing
            install_database_tracing(repositories.engine)
        from app.persistence.admission_repository import read_policy
        with repositories.engine.connect() as db:
            configured = read_policy(db)
        if configured != (admission.policy if admission else None):
            raise CoordinationUnavailable('Worker admission policy must match PostgreSQL')
        self.runtime = ChatRuntime(1)
        self.log = logging.getLogger(__name__)

    async def run_once(self):
        permit = None
        job = None
        if self.admission:
            pending = await asyncio.to_thread(self.queue.has_pending)
            if not pending:
                return False
            permit = await asyncio.to_thread(self.admission.acquire)
            if permit is None:
                return False
        try:
            job = await asyncio.to_thread(self.queue.claim_next,admission_policy=self.admission.policy if self.admission else None)
        except BaseException:
            if permit:
                try:
                    await asyncio.to_thread(self.admission.release,permit,refund=True)
                except CoordinationUnavailable:
                    pass
            raise
        if job is None:
            if permit:
                await asyncio.to_thread(self.admission.release,permit,refund=True)
            return False
        trace_scope = self.tracing.scope('worker.chat',root=True,carrier=job.get('trace_context'),run_id=job['run_id']) if self.tracing else nullcontext()
        with trace_scope:
            return await self._execute(job,permit)

    async def _execute(self, job, permit):
        credential = job['credential']
        owned = None
        gateway = None
        owned_store = None
        try:
            repository = self.queue.authenticated(credential)
            owned = repository.with_lease(job['lease'])
            if permit:
                await asyncio.to_thread(self.admission.require,permit)
            gateway = self.gateway_factory(credential,job) if self.gateway_factory else ModelGateway(self.secrets,
                repository=self.repositories.config.authenticated(credential),local_endpoint=self.local_model_endpoint)
            paths = dict(self.paths)
            if self.storage_configuration is not None:
                owned_store = await asyncio.to_thread(self.storage_configuration.resolve,credential)
                paths['object_store'] = owned_store
            knowledge = KnowledgeService(repository=self.repositories.knowledge.authenticated(credential),
                embedding_gateway=self.rag_embedding_gateway, reranker=self.rag_reranker, **paths)
            orchestrator = ChatOrchestrator(gateway,knowledge,repository=repository,runtime=self.runtime,
                max_concurrent_runs=1,identity_resolver=lambda:self.repositories.identity.resolve(credential),
                coordination_check=(lambda:self.admission.require(permit)) if permit else None)
            async with aclosing(orchestrator.stream(job['session_id'],job['content'],prepared=job)) as stream:
                async for frame in stream:
                    lines = frame.splitlines()
                    event = next(line[6:].strip() for line in lines if line.startswith('event:'))
                    if event not in {'provider','token','answer_reset','tool_start','tool_end'}:
                        continue  # Terminal/citations/contact events commit with the result.
                    data = json.loads(next(line[5:].strip() for line in lines if line.startswith('data:')))
                    # A terminal result may already have committed before its last
                    # visible events. Do not append or rewrite it after completion.
                    r = owned.tables['agent_runs']
                    with owned.engine.connect() as db:
                        status = db.execute(sa.select(r.c.status).where(r.c.id == owned.key(r,job['run_id']))).scalar_one()
                    if status == 'running':
                        await asyncio.to_thread(owned.append_event,job['run_id'],event,data)
            if owned:
                from app.database import utc_now
                await asyncio.to_thread(owned.finish_run,job['run_id'],'interrupted','worker_exit',utc_now())
        except (AuthenticationError,AuthorizationError):
            from app.tracing import mark_failure
            mark_failure('identity_unavailable')
            self.log.info('queued run stopped: identity unavailable')
        except asyncio.CancelledError:
            raise
        except CoordinationUnavailable:
            from app.tracing import mark_failure
            mark_failure('coordination_unavailable')
            if owned:
                from app.database import utc_now
                await asyncio.to_thread(owned.finish_run,job['run_id'],'interrupted','coordination_unavailable',utc_now())
        except Exception as exc:
            from app.tracing import mark_failure
            mark_failure('error')
            self.log.warning('queued run stopped exception_type=%s',type(exc).__name__)
            if owned:
                from app.database import utc_now
                try:
                    await asyncio.to_thread(owned.finish_run,job['run_id'],'error','worker_error',utc_now())
                except Exception:
                    pass  # Expiry cleanup owns recovery, never an unscoped writer.
        finally:
            teardown_ok = False
            try:
                try:
                    if gateway is not None and hasattr(gateway,'close'):
                        await gateway.close()
                finally:
                    if owned_store:
                        await asyncio.to_thread(owned_store.close)
                teardown_ok = True
            finally:
                if permit:
                    if teardown_ok:
                        try:
                            await asyncio.to_thread(self.queue.release_admission,job)
                        except Exception as exc:
                            self.log.warning('durable admission release deferred exception_type=%s',type(exc).__name__)
                    try:
                        await asyncio.to_thread(self.admission.release,permit)
                    except CoordinationUnavailable:
                        self.log.warning('shared admission release unavailable; permit expires')
        return True

    async def run(self,stop):
        database_failures = 0
        while not stop.is_set():
            delay = .25
            try:
                # Cleanup and claim require the same database authority. A failed
                # check must not fall through to new execution; retry only the
                # next polling cycle, never replay an already claimed job.
                await asyncio.to_thread(self.queue.reap_expired_leases)
                await asyncio.to_thread(self.queue.reap_pending)
                worked = await self.run_once()
                database_failures = 0
                if worked:
                    continue
            except (OperationalError, InterfaceError, DatabaseTimeout) as exc:
                database_failures = min(database_failures + 1, 6)
                delay = min(2 ** (database_failures - 1), 30)
                # SQL parameters / exception text may contain credentials or
                # customer content; emit only the exception class.
                self.log.warning('worker database unavailable; execution deferred exception_type=%s',type(exc).__name__)
            except CoordinationUnavailable:
                self.log.warning('shared admission unavailable; new execution refused')
                delay = 1
            try:
                await asyncio.wait_for(stop.wait(),timeout=delay)
            except asyncio.TimeoutError:
                pass
