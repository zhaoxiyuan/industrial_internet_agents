"""Single-ticket temporal scope, authorization and termination invariants."""
import pytest
from test_pipeline import pipeline, monitor, assess
from industrial_skills import p6, main
from industrial_skills.monitoring import configure


def test_raw_events_not_consumed_by_p6(pipeline):
    job,ctx=pipeline
    monitor(ctx)
    with ctx.player.db() as db:
        assert db.execute('select count(*) from processing_tasks').fetchone()[0]==0
    with job.db() as db:assert len(job.rows(db,'description'))==1


def test_closed_job_blocks_p6(pipeline):
    job,ctx=pipeline
    with job.db() as db:
        state=job.get(db,'job','state');job.put(db,'job','state',{**state,'monitoring_closed':True})
    with pytest.raises(ValueError,match='JOB_MONITORING_CLOSED'):p6.read_current_window({},ctx('p6'))


def test_scope_and_actor_checked_before_capture(pipeline):
    job,ctx=pipeline
    bad={**ctx('p6'),'agent_id':'other'}
    with pytest.raises(ValueError,match='ROLE_FORBIDDEN'):p6.read_current_window({},bad)
    with ctx.source.db() as db:ctx.source.put(db,'active_job','current',{'job_id':None})
    with pytest.raises(ValueError,match='WORK_PERMIT_REQUIRED'):p6.read_current_window({},ctx('p6'))


def test_window_size_locked_after_first_claim(pipeline):
    job,ctx=pipeline;monitor(ctx)
    with pytest.raises(ValueError,match='WINDOW_SIZE_LOCKED'):main.configure_schedule({'window_seconds':3},ctx('main'))


def test_monitoring_close_preserves_player_and_business(pipeline):
    job,ctx=pipeline
    for _ in range(3):monitor(ctx);assess(ctx)
    status=ctx.player.get()
    assert main.close_time_monitoring({'reason':'explicit end'},ctx('main'))['status']=='monitoring_closed'
    assert ctx.player.get()==status
    main.reopen_monitoring({'reason':'explicit resume'},ctx('main'))
    with job.db() as db:assert not job.get(db,'job','state')['monitoring_closed']


def test_scope_rejects_consumer_configuration(pipeline):
    with pytest.raises(ValueError):configure(pipeline[0],{'start_time':'2026-10-13T00:00:00+00:00','person_ids':['P11'],'basis':'approved','consumer_id':'unused'})
