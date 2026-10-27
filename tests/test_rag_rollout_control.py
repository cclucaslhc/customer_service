from types import SimpleNamespace
import pytest
from scripts.rag_rollout_control import dispatch,BASE
from test_rag_rollout_watch import control


@pytest.mark.parametrize('action,method,keys',[
    ('observe','observe',BASE),('pause','pause',BASE),('withdraw','rollback',BASE),
    ('start','start',tuple(control())),('advance','advance',tuple(control())),
    ('prepare-reversion','prepare_reversion',(*BASE,'expected_release_version','expected_body_sha256','new_index_id')),
])
def test_explicit_actions_pass_only_required_receipts(action,method,keys):
    seen=[]
    def call(**args):
        seen.append(args)
        return {'state':'synthetic'}
    result=dispatch(SimpleNamespace(**{method:call}),action,control(),confirmed=True,
        new_index_id='new' if action=='prepare-reversion' else None)
    assert result=={'state':'synthetic'} and set(seen[0])==set(keys)


@pytest.mark.parametrize('action',('start','advance','pause','withdraw','prepare-reversion'))
def test_mutations_cannot_run_without_explicit_confirmation(action):
    with pytest.raises(ValueError,match='confirmation'):
        dispatch(None,action,control())


def test_extra_reversion_id_cannot_be_ignored():
    with pytest.raises(ValueError,match='fresh index'):
        dispatch(None,'observe',control(),new_index_id='unexpected')
