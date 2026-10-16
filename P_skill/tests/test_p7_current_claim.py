import time
import pytest
from test_pipeline import pipeline
from test_timeflow import temporal, save
from industrial_skills import timeflow,p7

def prepare(temporal):
    job,ctx,c=temporal
    actor=c('p6');save(actor,timeflow.read_current_window({},actor))
    reviewer=c('p7');claim=p7.claim_description({},reviewer)
    args={'assessments':[{'key':k,'risk_level':2,'basis':'test','advice':['check']} for k in claim['required_assessment_keys']]}
    return job,c,reviewer,claim,args

def test_current_claim_repeat_commit_and_renew(temporal):
    job,c,reviewer,claim,args=prepare(temporal)
    assert 'claim' not in claim and claim['current_claim']
    assert p7.claim_description({},reviewer)['record']['id']==claim['record']['id']
    assert p7.renew_description_lease({'lease_seconds':600},reviewer)['status']=='processing'
    receipt=p7.complete_assessment(args,reviewer)
    assert p7.complete_assessment(args,reviewer)==receipt
    with pytest.raises(ValueError,match='LEASE_INVALID'):p7.renew_description_lease({'lease_seconds':600},reviewer)

def test_missing_run_and_expired_claim(temporal):
    job,c,reviewer,claim,args=prepare(temporal)
    stranger={**reviewer,'run_id':'different-run'}
    with pytest.raises(ValueError,match='NO_CURRENT_P7_CLAIM'):p7.complete_assessment(args,stranger)
    with job.db() as db:
        row=job.get(db,'description',claim['record']['id'])
        job.put(db,'description',row['id'],{**row,'expiry':time.time()-1})
    with pytest.raises(ValueError,match='LEASE_INVALID'):p7.complete_assessment(args,reviewer)
    with pytest.raises(ValueError,match='LEASE_INVALID'):p7.renew_description_lease({'lease_seconds':600},reviewer)

def test_replaced_token_rejects_old_run(temporal):
    job,c,reviewer,claim,args=prepare(temporal)
    with job.db() as db:
        row=job.get(db,'description',claim['record']['id'])
        job.put(db,'description',row['id'],{**row,'token':'new-token','owner_run_id':'new-run'})
    with pytest.raises(ValueError,match='LEASE_INVALID'):p7.complete_assessment(args,reviewer)
