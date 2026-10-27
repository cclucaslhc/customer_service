"""Synthetic Worker faults: real OOM or SIGKILL at publication commit boundaries."""
import argparse
import threading
from pathlib import Path
from app.knowledge_worker import KnowledgeWorker
from app.persistence.bundle import PostgreSQLRepositories


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url',required=True)
parser.add_argument('--fault',choices=('oom','before_commit','after_commit'),default='oom')
args = parser.parse_args()
bundle = PostgreSQLRepositories.from_url(args.url,pool_size=3)
try:
    bundle.check_schema()
    def exhaust_after_claim(filename,data):
        allocation = bytearray(1024*1024*1024)
        raise AssertionError('Synthetic OOM allocation unexpectedly survived')
    from app.isolated_parser import IsolatedDocumentParser
    if args.fault != 'oom':
        from app.persistence.durable_knowledge_repository import DurableKnowledgeRepository
        published = DurableKnowledgeRepository._publication_committed
        original_commit = bundle.engine.dialect.do_commit
        publication_thread = None
        def wait_for_external_kill():
            # Namespace PID 1 may ignore a self-sent fatal signal. The test
            # coordinator kills this exact owned container after this marker.
            Path('/scratch/fault-ready').write_text(args.fault,encoding='ascii')
            threading.Event().wait(60)
            raise AssertionError('Expected external container kill did not arrive')
        def publication(self,db,document):
            global publication_thread
            published(self,db,document)
            if args.fault == 'before_commit':
                wait_for_external_kill()
            publication_thread = threading.get_ident()
        def commit(connection):
            original_commit(connection)  # physical DBAPI commit, not before-commit event
            if publication_thread == threading.get_ident():
                wait_for_external_kill()
        DurableKnowledgeRepository._publication_committed = publication
        bundle.engine.dialect.do_commit = commit
    worker = KnowledgeWorker(bundle,{'knowledge_dir':Path('/scratch/knowledge'),
        'asset_dir':Path('/scratch/assets'),'legacy_upload_dir':Path('/scratch/legacy')},
        document_parser=exhaust_after_claim if args.fault == 'oom' else IsolatedDocumentParser())
    assert worker.run_once(), 'Synthetic job was not claimed'
finally:
    bundle.close()
