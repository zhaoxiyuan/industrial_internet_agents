import json
import pytest
from test_pipeline import pipeline,monitor,assess
from test_cards import MockFeishu
from industrial_skills import disposition,p7
from platform_core.service import Service


def initialize_next(ctx):
    claim=disposition.claim_risk_report({},ctx('p8p9'))
    return claim,disposition.initialize_disposition({'claim':claim['claim']},ctx('p8p9'))


def test_open_event_push_coalesces_updates_and_closes_same_card(pipeline,tmp_path):
    store,ctx=pipeline;monitor(ctx);assess(ctx)
    with store.db() as db:
        assert all(e['state']=='open' for e in store.rows(db,'event'))
        assert not store.rows(db,'card_intent') and not store.rows(db,'report')
    claim,row=initialize_next(ctx)
    assert claim['claim']['kind']=='event_notification' and row['risk_state']=='open'
    assert disposition.initialize_disposition({'claim':claim['claim']},ctx('p8p9'))['id']==row['id']
    mock=MockFeishu();service=Service(tmp_path/'platform')
    try:
        actor=service.agents.save({'name':'p8'})
        c=service.feishu.save({'name':'mock','app_id':'mock','app_secret':'mock','verification_token':'mock','api_base':mock.url,'groups':{'oc_test':{'name':'test'}},'users':{}})
        cards=service.feishu.cards
        cards.configure(c['id'],{'storage':str(store.root),'job_id':store.job_id,'target':{'type':'group','id':'oc_test'},'agents':[actor['id']],'public_base':'http://localhost','simulation':True})
        first=cards.sync(c['id'],actor['id'],row['id']);assert first['status']=='simulated'
        assert '风险持续' in json.dumps(mock.cards[first['card_id']]['card'],ensure_ascii=False)
        disposition.apply_human_action(store,'ack','ou_worker',row['id'],row['version'],'acknowledge_disposition',{})
        # Finish both later windows before P8 runs: only latest event revision remains queued.
        for _ in range(2):monitor(ctx);assess(ctx)
        with store.db() as db:
            event=store.get(db,'event',row['event_id']);assert event['state']=='closed'
            before=store.get(db,'disposition',row['id']);assert before['event_version']==row['event_version']
        p7.report_closed_event({'event_id':event['id'],'summary':'Final event evidence'},ctx('p7'))
        while True:
            pending=disposition.claim_risk_report({},ctx('p8p9'))
            if pending['status']=='no_work':break
            updated=disposition.initialize_disposition({'claim':pending['claim']},ctx('p8p9'))
            if updated['id']==row['id']:row=updated
        assert row['status']=='acknowledged' and row['accepted_by']=='ou_worker' and row['risk_state']=='closed'
        second=cards.sync(c['id'],actor['id'],row['id']);assert second['card_id']==first['card_id'] and second['message_id']==first['message_id']
        cards.sync(c['id'],actor['id'],row['id'])
        assert len([c for c in mock.calls if '/messages?' in c[1]])==1
        assert '风险现象已结束' in json.dumps(mock.cards[first['card_id']]['card'],ensure_ascii=False)
        with store.db() as db:
            assert store.get(db,'report',row['id'])['payload']['is_final'] is True
            assert store.get(db,'disposition',row['id'])['last_synced_event_version']==row['event_version']
    finally:service.close();mock.close()


def test_update_during_p8_lease_uses_latest_and_failure_requires_main_retry(pipeline):
    store,ctx=pipeline;monitor(ctx);assess(ctx)
    claim=disposition.claim_risk_report({},ctx('p8p9'))
    monitor(ctx);assess(ctx)
    updated=disposition.initialize_disposition({'claim':claim['claim']},ctx('p8p9'))
    with store.db() as db:assert updated['event_version']==store.get(db,'event',updated['event_id'])['version']
    other=disposition.claim_risk_report({},ctx('p8p9'))
    disposition.fail_risk_report({'claim':other['claim'],'reason':'test failure'},ctx('p8p9'))
    monitor(ctx);assess(ctx)
    with store.db() as db:assert store.get(db,'event_notification',other['claim']['id'])['status']=='failed'
