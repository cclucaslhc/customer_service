"""Synthetic publication, conflict, recovery and filesystem failure invariants."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
import sqlalchemy as sa

import app.knowledge as module
from app.database import utc_now
from app.knowledge import KnowledgeError, KnowledgeService
from app.knowledge_jobs import KnowledgeJobService
from app.knowledge_terms import custom_synonym_groups, save_custom_synonym_groups
from app.persistence.knowledge_repository import KnowledgeRepository, KnowledgeWriteConflict
from test_chat_repository import repository


@pytest.fixture
def stored(repository, tmp_path, monkeypatch):
    managed = tmp_path / "knowledge"
    assets = tmp_path / "assets"
    managed.mkdir()
    assets.mkdir()
    monkeypatch.setattr(module, "KNOWLEDGE_DIR", managed)
    monkeypatch.setattr(module, "KNOWLEDGE_ASSET_DIR", assets)
    return KnowledgeRepository(repository.engine)


def document(version=1, digest="a", key="doc"):
    return {"id": key, "display_name": "Synthetic", "stored_name": "synthetic.txt", "extension": ".txt", "size": 10,
            "sha256": digest * 64, "status": "ready", "chunk_count": 1, "source_path": "synthetic-source", "version": version,
            "warnings_json": [], "metadata_json": {"synthetic": True}, "created_at": utc_now()}


def chunk(content="synthetic evidence", key="doc"):
    return {"document_id": key, "chunk_index": 0, "content": content, "tokenized": "synthetic evidence", "metadata_json": {"page": 1}}


def test_publication_replacement_rollback_and_version_conflict(stored):
    stored.publish_document(document(), [chunk()], [])
    with pytest.raises(sa.exc.IntegrityError):
        stored.publish_document(document(2, "b"), [chunk(None)], [], expected_version=1)
    assert stored.document("doc")["version"] == 1
    assert stored.document_chunks("doc")[0]["content"] == "synthetic evidence"
    stored.publish_document(document(2, "b"), [chunk("replacement")], [], expected_version=1)
    with pytest.raises(KnowledgeWriteConflict):
        stored.publish_document(document(3, "c"), [chunk("stale")], [], expected_version=1)
    assert stored.document_chunks("doc")[0]["content"] == "replacement"
    assert not stored.delete_document("doc", 1)
    assert stored.delete_document("doc", 2)
    assert stored.document_chunks("doc") == []


def test_publication_manifest_cannot_attach_another_documents_chunks(stored):
    with pytest.raises(ValueError, match="manifest"):
        stored.publish_document(document(), [chunk(key="another-document")], [])
    assert stored.document("doc") is None
    with pytest.raises(ValueError, match="order"):
        stored.publish_document(document(), [{**chunk(), "chunk_index": 2}], [])
    assert stored.document("doc") is None


def test_repository_rebuild_terms_and_explicit_score_kind(stored):
    stored.publish_document(document(), [chunk()], [])
    assert stored.rebuild(lambda text: "synthetic evidence", "test-version") == 1
    assert stored.setting("knowledge_tokenizer_version") == "test-version"
    hits, fallback = stored.search_candidates(["synthetic"], 10)
    assert hits[0]["score_kind"] == "sqlite_bm25"
    assert fallback[0]["document_id"] == "doc"
    assert save_custom_synonym_groups([["Synthetic", "Evidence"]], repository=stored) == [["Synthetic", "Evidence"]]
    assert custom_synonym_groups(repository=stored) == [["Synthetic", "Evidence"]]


def test_local_job_recovery_and_update_whitelist(stored, monkeypatch):
    jid = str(uuid.uuid4())
    stored.create_job({"id": jid, "source_path": "synthetic-source", "recursive": True, "status": "running", "stage": "parsing", "created_at": utc_now()})
    stored.update_job(jid, {"result_json": {"synthetic": True}, "processed_count": 1})
    assert KnowledgeJobService._serialize(stored.job(jid))["result"] == {"synthetic": True}
    assert stored.recover_local_jobs() == [jid]
    assert stored.job(jid)["status"] == "pending"
    with pytest.raises(ValueError):
        stored.update_job(jid, {"source_path": "forbidden"})
    with monkeypatch.context() as patch:
        patch.setattr(stored.engine.dialect, "name", "postgresql")
        with pytest.raises(RuntimeError, match="Worker leases"):
            stored.recover_local_jobs()


def test_duplicate_concurrent_ingests_preserve_winning_file(stored, monkeypatch):
    service = KnowledgeService(repository=stored)
    original = module.parse_structured_document
    barrier = Barrier(2)
    def parse(filename, data):
        parsed = original(filename, data)
        barrier.wait(timeout=10)
        return parsed
    monkeypatch.setattr(module, "parse_structured_document", parse)
    def ingest(index):
        try:
            return service.ingest(f"synthetic-{index}.txt", b"Synthetic evidence document")
        except KnowledgeError:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(ingest, range(2)))
    assert sum(item is not None for item in outcomes) == 1
    docs = stored.list_documents()
    assert len(docs) == 1
    winner = module.KNOWLEDGE_DIR / docs[0]["stored_name"]
    assert winner.read_bytes() == b"Synthetic evidence document"
    assert list(module.KNOWLEDGE_DIR.iterdir()) == [winner]


def test_failed_publication_cleans_only_attempt_and_post_commit_cleanup_is_safe(stored, monkeypatch):
    service = KnowledgeService(repository=stored)
    first = service.ingest("first.txt", b"Synthetic old evidence")
    reparsed = service.reparse(first["id"])
    assert reparsed["display_name"] == "first.txt" and reparsed["version"] == 2
    before = set(module.KNOWLEDGE_DIR.iterdir())
    def fail_publish(*args, **kwargs):
        raise RuntimeError("synthetic failure")
    with monkeypatch.context() as patch:
        patch.setattr(stored, "publish_document", fail_publish)
        with pytest.raises(RuntimeError):
            service.ingest("second.txt", b"Synthetic new evidence")
    assert set(module.KNOWLEDGE_DIR.iterdir()) == before
    assert stored.document(first["id"])
    def fail_cleanup(*args):
        raise OSError("synthetic cleanup failure")
    monkeypatch.setattr(service, "_cleanup_asset_records", fail_cleanup)
    result = service.ingest("second.txt", b"Synthetic new evidence")
    path = module.KNOWLEDGE_DIR / stored.document(result["id"])["stored_name"]
    assert path.read_bytes() == b"Synthetic new evidence"
