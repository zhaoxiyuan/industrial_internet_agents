import copy
import json
from industrial_skills.compact_view import observation_view, description_view
from industrial_skills import source
from test_pipeline import pipeline
from test_timeflow import temporal


def segment(a,b,value,state='unassessed'):
    return {'time_range':[a,b],'value':value,'state':state,'evidence_refs':[{'table':'sensor_readings','id':int(a)+1}]}


def test_changes_gaps_unknown_and_sample_indices_are_preserved():
    o={'key':'risk','segments':[segment('0','1',0),segment('1','2',0),segment('2','3',1),segment('3','4',None,'unknown'),segment('5','6',None,'unknown')]}
    before=copy.deepcopy(o)
    view=observation_view(o,number=1)
    assert [(s['from_sample'],s['to_sample']) for s in view['segments']]==[(1,2),(3,3),(4,4),(5,5)]
    assert view['segments'][0]['time_range']==['0','2']
    assert 'key' not in view and view['sample_count']==5
    assert o==before
    decisions=[{'observation':1,'state':'normal','explanation':'逐段证据','exceptions':[{'from_sample':3,'to_sample':3,'state':'abnormal'}]}]
    classified=source.classify_channels([o],decisions)[0]
    assert [s['state'] for s in classified['segments']]==['normal','normal','abnormal','unknown','unknown']
    assert classified['segments'][2]['evidence_refs']==o['segments'][2]['evidence_refs']


def test_compaction_does_not_merge_boolean_with_numeric_or_mutate_record():
    record={'payload':{'observations':[{'key':'k','segments':[segment('0','1',False),segment('1','2',0)]}],'evidence':[{'id':1}]}}
    before=copy.deepcopy(record);view=description_view(record)
    assert len(view['payload']['observations'][0]['segments'])==2
    assert view['payload']['observations'][0]['key']=='k'
    assert view['payload']['evidence_count']==1 and record==before


def test_repeated_samples_shrink_without_losing_edges():
    o={'key':'k','segments':[segment(str(i),str(i+1),0) for i in range(30)]}
    view=observation_view(o,number=1)
    assert len(json.dumps(view))<len(json.dumps(o))/5
    assert view['segments'][0]['time_range']==['0','30']
    assert (view['segments'][0]['from_sample'],view['segments'][0]['to_sample'])==(1,30)


def test_compact_read_save_and_serial_p7_preserve_full_storage(temporal):
    from industrial_skills import timeflow, p7
    job,ctx,c=temporal
    intervals=[]
    for _ in range(2):
        context=c('p6');view=timeflow.read_current_window({},context)
        assert view['monitoring_data']['format']=='channel_runs_v1'
        assert view['required_observations']==[o['observation'] for o in view['observations']]
        receipt=timeflow.save_current_description({'summary':'全部通道保守未知','descriptions':[{'observation':i,'state':'unknown','explanation':'此测试仅校验接口，不做安全判断'} for i in view['required_observations']],'limitations':['test']},context)
        assert receipt['description_saved'];intervals.append(receipt['time_range'])
    reviewer=ctx('p7')
    for interval in intervals:
        claim=p7.claim_description({},reviewer)
        assert claim['status']=='processing'
        assert claim['record']['time_range']==interval
        assert 'evidence_count' in claim['record']['payload']
        assert claim['required_assessment_keys']==[]
        receipt=p7.complete_assessment({'assessments':[]},reviewer)
        assert receipt['closed_events']==[] and '下一步立即claim_description' in receipt['next_step']
    with job.db() as db:
        assert job.get(db,'job','state')['p7_through']==intervals[-1][1]
        assert all(r['status']=='completed' for r in job.rows(db,'description'))
        assert all('evidence' in r['payload'] for r in job.rows(db,'description'))
