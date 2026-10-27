"""Knowledge polling retries infrastructure checks, never blind job replay."""
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError, TimeoutError

from app.knowledge_worker import KnowledgeWorker


@pytest.mark.parametrize('stage',['reap','claim'])
@pytest.mark.parametrize('error_type',[OperationalError,InterfaceError,TimeoutError])
def test_poll_recovers_without_skipping_authority_checks(stage,error_type,caplog):
    worker=object.__new__(KnowledgeWorker)
    worker.log=logging.getLogger('knowledge-recovery-test')
    calls=[]
    failed=False
    stopping=False
    delays=[]
    def check(name):
        nonlocal failed
        calls.append(name)
        if name==stage and not failed:
            failed=True
            if error_type is TimeoutError:
                raise error_type('private-url')
            raise error_type('sensitive-sql',{'secret':'never-log'},Exception('private-url'))
    def once():
        nonlocal stopping
        check('claim')
        stopping=True
        return True
    worker.queue=SimpleNamespace(reap_expired=lambda:check('reap'))
    worker.run_once=once
    stop=SimpleNamespace(is_set=lambda:stopping,wait=lambda delay:delays.append(delay))
    worker.run(stop)
    assert calls==(['reap'] if stage=='reap' else ['reap','claim'])+['reap','claim']
    assert delays==[1]
    assert error_type.__name__ in caplog.text
    assert all(value not in caplog.text for value in ('sensitive-sql','never-log','private-url'))


def test_poll_backoff_caps_and_remains_stoppable():
    worker=object.__new__(KnowledgeWorker)
    worker.log=logging.getLogger('knowledge-recovery-test')
    delays=[]
    def fail(): raise OperationalError('synthetic',{},Exception())
    worker.queue=SimpleNamespace(reap_expired=fail)
    worker.run_once=Mock()
    stop=SimpleNamespace(is_set=lambda:len(delays)>=8,wait=lambda delay:delays.append(delay))
    worker.run(stop)
    assert delays==[1,2,4,8,16,30,30,30]
    worker.run_once.assert_not_called()


def test_programming_failure_remains_fatal():
    worker=object.__new__(KnowledgeWorker)
    worker.log=logging.getLogger('knowledge-recovery-test')
    def fail(): raise ProgrammingError('synthetic',{},Exception())
    worker.queue=SimpleNamespace(reap_expired=fail)
    worker.run_once=Mock()
    with pytest.raises(ProgrammingError):
        worker.run(SimpleNamespace(is_set=lambda:False))
    worker.run_once.assert_not_called()
