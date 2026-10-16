import pytest
from pathlib import Path
from test_pipeline import pipeline
from industrial_skills import main
from industrial_skills.acquisition import initialize_source
from industrial_skills.store import Store


def empty_context(pipeline):
    old,ctx=pipeline;cfg=ctx.source.config();value={k:v for k,v in cfg.items() if k not in ('id','job_id','version','source_id')}
    with ctx.source.db() as db:ctx.source.put(db,'active_job','current',{'job_id':None})
    value['allowed_job_ids']=[];value['agents']=old.config()['agents'];initialize_source(old.root,'industrial',value)
    return old,ctx,{'agent_id':'main','source_id':'industrial','job_id':'','run_id':'bind-run','storage':{'p6_p7':str(old.root)}}


def test_empty_permit_never_falls_back_and_binding_preserves_exact_id(pipeline):
    old,ctx,context=empty_context(pipeline)
    required=main.query_pipeline({},context)
    assert required['status']=='work_permit_required' and required['queues']=={}
    args={'job_id':'1111111111111111','permit_id':'1111111111111111','start_time':old.config()['start_time'],
        'person_ids':[old.config()['rules'][0]['subject_id']],'sensor_ids':[],'basis':'User provided original permit and subject range'}
    receipt=main.bind_work_permit(args,context);assert receipt['job_id']==args['job_id']
    assert main.bind_work_permit(args,context)==receipt
    query=main.query_pipeline({},context);assert query['job']['job_id']==args['job_id']
    assert ctx.source.config()['allowed_job_ids']==[args['job_id']]
    new=Store(old.root,args['job_id']);assert new.config()['test_mode'] is False
    assert 'consumer_id' not in new.config()
    with pytest.raises(ValueError,match='WORK_PERMIT_ID_MISMATCH'):main.bind_work_permit({**args,'permit_id':'DIFFERENT'},context)


def test_empty_ticket_does_not_invent_subjects_or_default_id(pipeline):
    old,ctx,context=empty_context(pipeline)
    args={'job_id':'','permit_id':'REAL','start_time':old.config()['start_time'],'person_ids':[],'sensor_ids':[],'basis':'Provided'}
    with pytest.raises(ValueError,match='WORK_PERMIT_ID_INVALID'):main.bind_work_permit(args,context)
    with pytest.raises(ValueError,match='MONITORING_SUBJECTS_REQUIRED'):main.bind_work_permit({**args,'job_id':'REAL'},context)
    assert main.query_pipeline({},context)['status']=='work_permit_required'


def test_platform_workflow_context_is_pinned_for_parent_and_children(tmp_path):
    import base64
    from platform_core.service import Service
    from industrial_skills.acquisition import source_store
    service=Service(tmp_path/'platform')
    try:
        shared=tmp_path/'shared';shared.mkdir()
        service.skill_tools.configure_storage('p6_p7',{'directory':str(shared)})
        archive=(Path(__file__).resolve().parents[1]/'dist/industrial-main.zip').read_bytes()
        skill=service.skills.import_skill({'archive':base64.b64encode(archive).decode()})
        actor=service.agents.save({'name':'main','skill_ids':[skill['id']]})
        runtime=service.skill_tools.configure(skill['id'],{'container':'generic-python','code_target':'/skills/main','storage':{'p6_p7':{'resource':'p6_p7','target':'/business'}}})
        binding=service.agents.bind({'agent_id':actor['id'],'source':'skill:'+skill['id'],'source_id':'industrial','tools':['query_pipeline']})
        assert binding['job_id']==''
        source=source_store(shared,'industrial')
        payloads=[];service.external_cli.run=lambda row,command,payload,run_id:payloads.append(payload) or {'status':'ok'}
        service.tasks.repo.get=lambda run_id:{'root_task_id':'chain-2' if run_id=='new-root' else 'chain-1'}
        tool=next(t for t in skill['tools'] if t['name']=='query_pipeline')
        service.skill_tools.run(skill,runtime,tool,{},binding,'root')
        assert payloads[-1]['context']['job_id']==''
        with source.db() as db:source.put(db,'active_job','current',{'job_id':'actual-user-job'})
        service.skill_tools.run(skill,runtime,tool,{},binding,'root')
        with source.db() as db:source.put(db,'active_job','current',{'job_id':'other-user-job'})
        service.skill_tools.run(skill,runtime,tool,{},binding,'child')
        assert payloads[-1]['context']['job_id']=='actual-user-job'
        service.skill_tools.run(skill,runtime,tool,{},binding,'new-root')
        assert payloads[-1]['context']['job_id']=='other-user-job'
    finally:service.close()
