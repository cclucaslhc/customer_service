"""Real PG health-stop transactions; telemetry remains synthetic."""
import pytest
import sqlalchemy as sa
from app.persistence.schema import metadata
from test_rag_rollout_advance import (
    advancing,short_plan,seed_run,planned,staged_plan,release,queue_setup,postgres_database,
)

pytestmark=pytest.mark.parametrize('planned',[{'rollout_plan':short_plan()}],indirect=True)


def test_empty_and_pending_windows_hold_without_version_change(advancing):
    bundle,controller,args,tokens=advancing
    assert controller.check_health_and_pause(**args)['health']['decision']=='hold'
    seed_run(bundle,args,tokens,pending=True)
    result=controller.check_health_and_pause(**args)
    assert result['health']['decision']=='hold'
    assert result['version']==1 and result['basis_points']==100


def test_healthy_tick_never_promotes(advancing):
    bundle,controller,args,tokens=advancing
    seed_run(bundle,args,tokens)
    result=controller.check_health_and_pause(**args)
    assert result['health']['decision']=='healthy'
    assert result['version']==1 and result['basis_points']==100


def test_revocation_stops_traffic_with_atomic_audit(advancing):
    bundle,controller,args,tokens=advancing
    bundle.identity.logout(tokens[1])
    result=controller.check_health_and_pause(**args)
    assert result['state']=='paused' and result['basis_points']==0 and result['version']==2
    assert result['health']['reason_codes']==['approval_invalid']
    with bundle.engine.connect() as db:
        audit=metadata.tables['audit_logs']
        record=db.execute(sa.select(audit.c.metadata_json).where(
            audit.c.action=='rag.index.rollout_health_pause')).scalar_one()
        assert record['observation']['health']['decision']=='pause'
    with pytest.raises(ValueError,match='receipt changed'):
        controller.check_health_and_pause(**args)


def test_stale_release_receipt_is_not_a_health_failure(advancing):
    bundle,controller,args,tokens=advancing
    with pytest.raises(ValueError,match='receipt changed'):
        controller.check_health_and_pause(**{**args,'expected_release_version':0})
    with bundle.engine.connect() as db:
        assert db.execute(sa.select(metadata.tables['rag_rollouts'].c.state)).scalar_one()=='running'


def test_terminal_unknown_cost_stops_even_while_another_run_is_pending(advancing):
    bundle,controller,args,tokens=advancing
    seed_run(bundle,args,tokens)
    with bundle.engine.begin() as db:
        # A second completed attempt without pricing makes total cost unknown.
        attempts=metadata.tables['model_usage_attempts']
        row=dict(db.execute(sa.select(attempts)).mappings().one())
        import uuid
        row.update(attempt_id=uuid.uuid4())
        db.execute(attempts.insert().values(**row))
    seed_run(bundle,args,tokens,pending=True)
    result=controller.check_health_and_pause(**args)
    assert result['health']['decision']=='pause'
    assert 'unknown_terminal_cost' in result['health']['reason_codes']
    assert result['basis_points']==0


def test_changed_runtime_binding_stops_without_publishing(advancing):
    bundle,controller,args,tokens=advancing
    result=controller.check_health_and_pause(**{**args,'current_binding':{
        **args['current_binding'],'prompt_sha256':'b'*64}})
    assert result['state']=='paused' and result['basis_points']==0
    assert result['health']['reason_codes']==['release_context_invalid']


def test_audit_or_database_failure_is_not_reported_as_success(advancing,monkeypatch):
    bundle,controller,args,tokens=advancing
    bundle.identity.logout(tokens[1])
    def fail(*a,**kw):
        raise RuntimeError('synthetic audit outage')
    monkeypatch.setattr(controller.releases.admin,'_audit',fail)
    with pytest.raises(RuntimeError,match='audit outage'):
        controller.check_health_and_pause(**args)
    with bundle.engine.connect() as db:
        row=db.execute(sa.select(metadata.tables['rag_rollouts'])).mappings().one()
        assert row['state']=='running' and row['version']==1 and row['basis_points']==100


def test_sampling_database_error_propagates_without_false_pause(advancing,monkeypatch):
    bundle,controller,args,tokens=advancing
    import app.rag_rollout_observation as observation
    def fail(*a,**kw):
        raise sa.exc.OperationalError('synthetic query',{},RuntimeError('database outage'))
    monkeypatch.setattr(observation,'collect_window',fail)
    with pytest.raises(sa.exc.OperationalError):
        controller.check_health_and_pause(**args)
    with bundle.engine.connect() as db:
        row=db.execute(sa.select(metadata.tables['rag_rollouts'])).mappings().one()
        assert row['state']=='running' and row['version']==1


def test_monitor_cli_real_process_holds_then_stops_on_revocation(advancing,tmp_path):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    bundle,controller,args,tokens=advancing
    receipt=tmp_path/'health-control.json'
    receipt.write_text(json.dumps(args),encoding='utf-8')
    if os.name=='posix':
        receipt.chmod(0o600)
    command=[sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/rag_rollout_watch.py'),
        '--database-url',bundle.engine.url.render_as_string(hide_password=False),
        '--control-file',str(receipt)]
    first=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert first.returncode==0,first.stderr
    assert json.loads(first.stdout)['decision']=='hold'
    bundle.identity.logout(tokens[1])
    second=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert second.returncode==2,second.stderr
    assert json.loads(second.stdout)['state']=='paused'
