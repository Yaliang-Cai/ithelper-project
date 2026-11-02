import pytest
from app.domain import Domain
from app.storage import Store


def test_recording_cannot_finish_with_active_work_or_unsettled_charge(tmp_path):
    from scripts.record_walkthrough import ensure_finished
    path=tmp_path/'business.sqlite'
    domain=Domain(Store(path).initialize())
    chat=domain.create_conversation('alice.sales')['id']
    domain.submit_user_message('alice.sales',chat,'账号无法登录','first')
    with pytest.raises(RuntimeError,match='active'):
        ensure_finished(path,lambda: [])
    event=domain.claim_event('recorder-test')
    with pytest.raises(RuntimeError,match='active'):
        ensure_finished(path,lambda: [])
    domain.finish_event(event['id'],'recorder-test')
    with pytest.raises(RuntimeError,match='unsettled'):
        ensure_finished(path,lambda: [{'spent':None,'reserved':1000}])
    assert ensure_finished(path,lambda: [{'spent':500,'reserved':1000}]) is None
