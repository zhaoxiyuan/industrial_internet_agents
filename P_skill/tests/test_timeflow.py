import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_pipeline import pipeline, assess
from industrial_skills import timeflow, source, p7
from mock_player.windows import list_windows


@pytest.fixture
def temporal(pipeline, monkeypatch):
    job, ctx = pipeline
    with ctx.source.db() as db:
        ctx.source.put(db, 'active_job', 'current', {'job_id':job.job_id})
    original = source.call
    def call(cfg, path, body=None, **query):
        if path == '/v4/data/windows': return list_windows(ctx.player, **query)
        if path == '/v2/data/window' and 'from_time' in query: return ctx.player.window(**query)
        return original(cfg,path,body,**query)
    monkeypatch.setattr(source,'call',call)
    def context(role): return {**ctx(role),'source_id':'industrial'}
    timeflow.configure_schedule({'window_seconds':2,'max_parallel':3},context('main'))
    return job, ctx, context


def save(ctx, result):
    return timeflow.save_current_description({'summary':'窗口调度契约测试','descriptions':[{'observation':i,'state':'unknown','explanation':'本测试验证调度与提交，不做风险判断'} for i in result['required_observations']], 'limitations':[]},ctx)


def test_parallel_order_and_one_description_per_run(temporal):
    job,ctx,c=temporal
    contexts=[c('p6') for _ in range(3)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies=list(pool.map(lambda x:timeflow.read_current_window({},x),contexts))
    assert len({tuple(r['time_range']) for r in replies})==3
    ordered=sorted(zip(contexts,replies),key=lambda p:p[1]['time_range'])
    assert timeflow.read_current_window({},ordered[0][0])['time_range']==ordered[0][1]['time_range']
    save(*ordered[2])
    assert p7.claim_description({},ctx('p7'))['reason']=='earlier_p6_window_unfinished'
    before=timeflow.list_time_windows({},c('main'))
    assert before['continuous_completed_through']==job.config()['start_time']
    receipt=save(*ordered[0]);assert save(*ordered[0])==receipt
    assess(ctx)
    assert p7.claim_description({},ctx('p7'))['status']=='blocked'
    save(*ordered[1]);assess(ctx);assess(ctx)
    with job.db() as db:
        assert len(job.rows(db,'description'))==3
        assert len(job.rows(db,'window'))==3
    assert timeflow.close_time_monitoring({'reason':'explicit end at acquired tail'},c('main'))['status']=='monitoring_closed'


def test_failure_recovery_and_stale_run(temporal):
    job,ctx,c=temporal;first=c('p6')
    result=timeflow.read_current_window({},first)
    timeflow.fail_current_window({'reason':'test failure'},first)
    timeflow.retry_time_window({'start_time':result['time_range'][0],'reason':'test retry'},c('main'))
    second=c('p6');again=timeflow.read_current_window({'start_time':result['time_range'][0]},second)
    assert again['time_range']==result['time_range']
    with pytest.raises(ValueError,match='P6_RUN_REPLACED'):save(first,result)
    save(second,again)
    with pytest.raises(ValueError,match='TIME_WINDOW_RETRY_NOT_ALLOWED'):
        timeflow.retry_time_window({'start_time':result['time_range'][0],'reason':'duplicate'},c('main'))


def test_pagination_read_only_and_scope(temporal):
    job,ctx,c=temporal
    first=timeflow.list_time_windows({'limit':1},c('main'))
    second=timeflow.list_time_windows({'limit':1,'start_time':first['next_start_time']},c('main'))
    assert first['windows'][0]['time_range'][1]==second['windows'][0]['time_range'][0]
    with job.db() as db: assert not job.rows(db,'p6_window')
    with ctx.player.db() as db: assert db.execute('select count(*) from processing_tasks').fetchone()[0]==0
    with job.db() as db:
        job.put(db,'monitoring_scope','current',{'start_time':second['windows'][0]['time_range'][0],'end_time':None,'person_ids':[ctx.player.data_status()['persons'][0]['person_id']],'sensor_ids':[],'area_ids':[],'basis':'test'})
    assert len(timeflow.list_time_windows({},c('main'))['windows'])==2


def test_incomplete_and_changed_content_rejected(temporal):
    job,ctx,c=temporal;actor=c('p6');reply=timeflow.read_current_window({},actor)
    with pytest.raises(ValueError,match='ASSESS_ALL_MONITORING_CHANNELS'):
        timeflow.save_current_description({'summary':'bad','descriptions':[],'limitations':[]},actor)
    save(actor,reply)
    with pytest.raises(ValueError,match='IDEMPOTENCY_CONFLICT'):
        timeflow.save_current_description({'summary':'changed','descriptions':[],'limitations':[]},actor)
    assert timeflow.close_time_monitoring({'reason':'user stop before completion'},c('main'))['status']=='monitoring_closed'


def test_user_stop_with_inflight_no_source_and_partial_cutoff(temporal, monkeypatch):
    job,ctx,c=temporal; actor=c('p6')
    reply=timeflow.read_current_window({},actor)
    monkeypatch.setattr(source,'call',lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('offline')))
    cutoff='2026-10-13T00:00:01.123456+00:00'
    result=timeflow.close_time_monitoring({'reason':'human requested immediate stop','cutoff':cutoff},c('main'))
    assert result['status']=='monitoring_closed'
    assert result['cutoff']==cutoff and result['unfinished_window_count']==1
    with job.db() as db:
        assert job.rows(db,'p6_window')[0]['status']=='processing'
        assert job.get(db,'job','state')['status']!='closed'
    with pytest.raises(ValueError,match='JOB_MONITORING_CLOSED'):
        save(actor,reply)
    assert timeflow.close_time_monitoring({'reason':'repeat'},c('main'))['cutoff']==cutoff
