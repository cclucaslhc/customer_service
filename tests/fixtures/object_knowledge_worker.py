"""Synthetic loopback acceptance harness, not a production credential loader."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.object_store import S3Credentials, S3ObjectStore
from app.persistence.bundle import PostgreSQLRepositories
from app.knowledge_worker import KnowledgeWorker
from app.isolated_parser import IsolatedDocumentParser

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('url')
parser.add_argument('bucket')
parser.add_argument('root',type=Path)
parser.add_argument('key_path',type=Path,nargs='?')
parser.add_argument('--otlp-traces-endpoint')
args = parser.parse_args()
url, bucket, root = args.url, args.bucket, args.root
store = None
tracing = None
bundle = PostgreSQLRepositories.from_url(url, pool_size=3)
try:
    bundle.check_schema()
    configuration = None
    if args.key_path is not None:
        from app.storage_config import StoragePolicy, StorageTarget, StorageConfiguration
        from app.security import SecretStore
        configuration = StorageConfiguration(bundle.engine,SecretStore(path=args.key_path),
            StoragePolicy([StorageTarget('approved','http://127.0.0.1:59000',bucket,'us-east-1')],loopback_tests=True))
    else:
        store = S3ObjectStore(endpoint='http://127.0.0.1:59000',bucket=bucket,region='us-east-1',
            credentials=S3Credentials('synthetic-s3-access','synthetic-s3-secret-only'),
            allow_loopback=True,test_skip_public_block_api=True)
    if args.otlp_traces_endpoint is not None:
        from app.otlp_export import PrivateOtlpTracing
        tracing = PrivateOtlpTracing(args.otlp_traces_endpoint)
    KnowledgeWorker(bundle, {'knowledge_dir':root/'docs', 'asset_dir':root/'assets',
        'legacy_upload_dir':root/'legacy', 'object_store':store},storage_configuration=configuration,
        tracing=tracing,document_parser=IsolatedDocumentParser()).run_once()
finally:
    bundle.close()
    if store:
        store.close()
    if tracing:
        tracing.flush(2)
        tracing.close()
