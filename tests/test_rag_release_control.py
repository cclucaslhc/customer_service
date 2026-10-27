from types import SimpleNamespace
import pytest
from scripts.rag_release_control import dispatch,load_receipt


@pytest.mark.parametrize('action',('approve','reject'))
def test_review_credential_only_from_explicit_hidden_prompt(action):
    seen=[]
    def call(**args):
        seen.append(args)
        return {'state':'reviewed'}
    assert dispatch(SimpleNamespace(**{action:call}),action,{},confirmed=True,
        prompt=lambda _: 'synthetic-review-only')=={'state':'reviewed'}
    assert seen==[{'token':'synthetic-review-only'}]


@pytest.mark.parametrize('action',('approve','reject','execute'))
def test_no_implicit_change_or_prompt(action):
    with pytest.raises(ValueError,match='confirmation'):
        dispatch(None,action,{},prompt=lambda _:pytest.fail('unexpected prompt'))


def test_noninteractive_review_cannot_fall_back_to_echo(monkeypatch):
    import sys
    monkeypatch.setattr(sys.stdin,'isatty',lambda:False)
    with pytest.raises(ValueError,match='interactive'):
        dispatch(None,'approve',{},confirmed=True)


def test_receipt_rejects_embedded_credentials_and_duplicate_fields(monkeypatch):
    import scripts.rag_release_control as module
    for raw in (b'{"token":"synthetic"}',b'{"tenant_id":1,"tenant_id":2}'):
        monkeypatch.setattr(module,'read_private_line',lambda *a,**kw:raw)
        with pytest.raises(ValueError): load_receipt(None)


def test_getpass_echo_fallback_is_a_hard_failure():
    import getpass
    import warnings
    def fallback(_):
        warnings.warn('Synthetic terminal has no echo control',getpass.GetPassWarning)
        pytest.fail('must fail before echoed input')
    with pytest.raises(getpass.GetPassWarning):
        dispatch(None,'approve',{},confirmed=True,prompt=fallback)
