"""Encrypted pg_dump/restore with approved running rollout and pinned run."""
import os
from pathlib import Path
import pytest
import sqlalchemy as sa
from app.backup import create_backup,restore_backup
from app.persistence.bundle import PostgreSQLRepositories
from app.persistence.schema import metadata
from app.recovery import RecoveryHold
from test_logical_backup import empty_database
from test_rag_rollout_advance import (
    advancing,short_plan,seed_run,planned,staged_plan,release,queue_setup,postgres_database,
)

pytestmark=pytest.mark.parametrize('planned',[{'rollout_plan':short_plan()}],indirect=True)


def test_encrypted_restore_invalidates_approved_rollout_and_preserves_run_snapshot(advancing,tmp_path):
    binaries=os.environ.get('TEST_POSTGRES_BIN')
    if not binaries:
        pytest.skip('TEST_POSTGRES_BIN unset; encrypted RAG restoration unverified')
    bundle,controller,args,tokens=advancing
    seed_run(bundle,args,tokens,pending=True)
    knowledge,assets=tmp_path/'knowledge',tmp_path/'assets'
    knowledge.mkdir()
    assets.mkdir()
    keys=tmp_path/'keys'
    keys.mkdir()
    key=keys/'backup.key'
    key.write_bytes(os.urandom(32))
    if os.name=='posix':
        key.chmod(0o600)
    package=tmp_path/'encrypted-package'
    common=dict(binary_dir=Path(binaries),key_path=key,scratch_root=tmp_path)
    create_backup(**common,url=bundle.engine.url.render_as_string(hide_password=False),
        knowledge_root=knowledge,asset_root=assets,destination=package,maintenance_confirmed=True)
    with empty_database() as target_url:
        result=restore_backup(**common,url=target_url,knowledge_root=tmp_path/'restored-knowledge',
            asset_root=tmp_path/'restored-assets',source=package,isolated_confirmed=True)
        assert result['verified'] and not result['workers_enabled']
        restored=PostgreSQLRepositories.from_url(target_url,pool_size=1)
        try:
            with pytest.raises(RecoveryHold):
                restored.check_schema()
            with bundle.engine.connect() as source,restored.engine.connect() as target:
                rollouts=metadata.tables['rag_rollouts']
                before=source.execute(sa.select(rollouts)).mappings().one()
                after=target.execute(sa.select(rollouts)).mappings().one()
                assert before['state']=='running'
                assert after['state']=='recovery_review' and after['basis_points']==0
                assert after['version']==before['version']+1
                assert after['plan']==before['plan'] and after['step_started_at']==before['step_started_at']
                releases=metadata.tables['rag_release_intents']
                original=source.execute(sa.select(releases).where(releases.c.state=='approved')).mappings().one()
                invalid=target.execute(sa.select(releases).where(releases.c.release_id==original['release_id'])).mappings().one()
                assert invalid['state']=='recovery_review' and invalid['version']==original['version']+1
                assert invalid['body']==original['body']
                assert invalid['approval_a_session_hash']==original['approval_a_session_hash']
                runs=metadata.tables['agent_runs']
                assert target.execute(sa.select(runs.c.rag_route).where(runs.c.rag_route.is_not(None))).scalar_one()==source.execute(
                    sa.select(runs.c.rag_route).where(runs.c.rag_route.is_not(None))).scalar_one()
        finally:
            restored.close()
