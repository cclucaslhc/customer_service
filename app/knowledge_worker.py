"""Independent parser Worker; no model clients, credentials or blind crash replay."""
import hashlib
import logging
from contextlib import nullcontext
from functools import partial
from pathlib import Path
import threading
from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError as DatabaseTimeout
from app.identity import AuthenticationError, AuthorizationError
from app.knowledge import KnowledgeService, KnowledgeDuplicateError, is_within
from app.persistence.durable_knowledge_repository import DurableKnowledgeRepository, KnowledgeLeaseLost
from app.settings import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES
from app.object_store import ObjectReference, ObjectStoreError


class KnowledgeWorker:
    def __init__(self,repositories,paths,*,storage_configuration=None,tracing=None,document_parser=None):
        self.repositories,self.paths = repositories,paths
        self.queue = DurableKnowledgeRepository(repositories.engine)
        self.log = logging.getLogger(__name__)
        self.storage_configuration = storage_configuration
        self.tracing = tracing
        self.document_parser = document_parser
        if tracing is not None:
            from app.tracing import install_database_tracing
            install_database_tracing(repositories.engine)

    def run_once(self):
        job = self.queue.claim_next()
        if job is None:
            return False
        trace_scope = self.tracing.scope('worker.knowledge',root=True,carrier=job.get('trace_context'),
            job_id=job['lease'].job_id) if self.tracing else nullcontext()
        with trace_scope as span:
            outcome = self._execute(job)
            if span is not None:
                try:
                    if outcome == 'completed':
                        span.set_attribute('outcome','completed')
                    else:
                        from app.tracing import mark_failure
                        mark_failure(outcome)
                except Exception:
                    self.tracing.processor.count('instrumentation_failed')
            return True

    def _execute(self,job):
        # Resolve again inside the guarded execution try, so revocation between
        # claim and execution stops this job without crashing the whole Worker.
        owned = DurableKnowledgeRepository(self.repositories.engine,token=job['credential'],lease=job['lease'])
        stop,lost = threading.Event(),threading.Event()
        def check_execution():
            if lost.is_set():
                raise KnowledgeLeaseLost('Knowledge heartbeat failed')
        def heartbeat():
            while not stop.wait(2):
                try:
                    owned.renew()
                except Exception:
                    lost.set()
                    return
        monitor = threading.Thread(target=heartbeat,daemon=True,name='knowledge-lease')
        monitor.start()
        owned_store = None
        outcome = 'completed'
        try:
            paths = dict(self.paths)
            if self.storage_configuration is not None:
                owned.require_manage()
                owned_store = self.storage_configuration.resolve(job['credential'])
                paths['object_store'] = owned_store
            for item in owned.manifest():
                if lost.is_set():
                    raise KnowledgeLeaseLost('Knowledge heartbeat failed')
                repo = owned.with_item(item['position'])
                from app.isolated_parser import IsolatedDocumentParser
                parser = self.document_parser
                if isinstance(parser, IsolatedDocumentParser):
                    parser = partial(parser, execution_check=check_execution)
                service = KnowledgeService(repository=repo,document_parser=parser,**paths)
                relative_path = item['relative_path'].replace('\\','/')
                path = (service.knowledge_dir/relative_path).resolve()
                display_name = item.get('display_name') or Path(relative_path).name
                progress_name = item.get('display_name') or item['relative_path']
                issue = {'path':item['relative_path'],'display_name':display_name}
                remote = item.get('input_object') is not None
                if path.suffix.lower() not in ALLOWED_EXTENSIONS:
                    repo.record('skipped',{**issue,'reason':'不支持的文件格式'})
                    continue
                if item['size'] > MAX_UPLOAD_BYTES:
                    repo.record('failed',{**issue,'reason':'文件超过单文件大小限制'})
                    continue
                if not remote and (not is_within(path,service.knowledge_dir) or not path.is_file()):
                    repo.record('failed',{**issue,'reason':'源文件不在托管目录或已移除'})
                    continue
                repo.renew()
                repo.progress({'stage':'reading','current_file':progress_name})
                try:
                    if remote:
                        if not service.object_store:
                            raise ObjectStoreError('Private input object store not configured')
                        reference = ObjectReference.from_dict(item['input_object'])
                        if (reference.size != item['size'] or reference.sha256 != item['sha256']
                                or not reference.key.startswith(f'tenants/{service._namespace}/knowledge/')):
                            raise ObjectStoreError('Private input reference disagrees with manifest')
                        repo.require_manage()
                        data = service.object_store.get(service._namespace, reference)
                        repo.require_manage()
                    else:
                        with path.open('rb') as file:
                            data = file.read(MAX_UPLOAD_BYTES+1)
                    if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                        repo.record('failed',{**issue,'reason':'源文件在排队期间已变化，请重新提交'})
                        continue
                    def progress(values):
                        if lost.is_set():
                            raise KnowledgeLeaseLost('Knowledge heartbeat failed')
                        repo.progress({'stage':values.get('stage','processing'),'current_file':progress_name})
                    replacement = ((str(item['replace_document_id']),item['replace_document_version'])
                                   if item.get('replace_document_id') is not None else None)
                    service.ingest(display_name,data,source_path=None if remote else path,progress=progress,immutable_copy=True,
                        force=replacement is not None,_replace_document=replacement,
                        _source_identity=f'object-import://{service._namespace}/{item["relative_path"]}' if remote else None)
                except KnowledgeDuplicateError:
                    repo.record('skipped',{**issue,'reason':'该文件已经建立索引且未变化'})
                except (KnowledgeLeaseLost,AuthenticationError,AuthorizationError):
                    raise
                except Exception as exc:
                    self.log.warning('knowledge item failed exception_type=%s',type(exc).__name__)
                    repo.record('failed',{**issue,'reason':'文档处理失败，请检查格式或重新提交'})
            if owned.finish() != 'complete':
                outcome = 'error'
        except KnowledgeLeaseLost:
            outcome = 'coordination_unavailable'
            self.log.info('knowledge import stopped: execution lease unavailable')
        except (AuthenticationError,AuthorizationError):
            outcome = 'identity_unavailable'
            self.log.info('knowledge import stopped: execution authority unavailable')
            # Revoked/expired owner cannot finalize through a privileged writer.
            # Bounded expiry maintenance terminalizes it without parsing again.
        except Exception as exc:
            outcome = 'error'
            self.log.warning('knowledge import stopped exception_type=%s',type(exc).__name__)
            try:
                owned.fail()
            except Exception:
                pass
        finally:
            stop.set()
            monitor.join(timeout=2)
            if owned_store:
                owned_store.close()
            try:
                self.queue.release_slot(job['lease'])
            except Exception:
                # Retain capacity on uncertain acknowledgement. Never replace
                # an unknown parser by silently freeing its reservation.
                self.log.warning('knowledge capacity release unconfirmed')
        return outcome

    def run(self,stop):
        database_failures = 0
        while not stop.is_set():
            delay = .5
            try:
                self.queue.reap_expired()
                worked = self.run_once()
                database_failures = 0
                if worked:
                    continue
            except (OperationalError, InterfaceError, DatabaseTimeout) as exc:
                # Never skip cleanup/claim checks or replay the failed job.
                # Uncertain parser capacity remains governed by its lease and
                # explicit reconciliation, not by this polling retry.
                database_failures = min(database_failures + 1, 6)
                delay = min(2 ** (database_failures - 1), 30)
                self.log.warning('knowledge database unavailable; execution deferred exception_type=%s',type(exc).__name__)
            stop.wait(delay)
