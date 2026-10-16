import shutil
from pathlib import Path
from platform_core.service import Service
from platform_core.monitoring import handle
from industrial_skills.store import initialize
from test_pipeline import config


def test_source_binding_injects_source_identity_without_job_id(tmp_path):
    service=Service(tmp_path/'platform')
    try:
        package=service.root/'packages/p6-monitor';package.parent.mkdir(parents=True)
        source=Path(__file__).resolve().parents[1]/'P6'
        shutil.copytree(source,package,ignore=shutil.ignore_patterns('__pycache__'))
        skill=service.skills.import_skill({'source':'packages/p6-monitor'})
        shared=tmp_path/'shared';shared.mkdir();service.skill_tools.configure_storage('p6_p7',{'directory':str(shared)})
        runtime=service.skill_tools.configure(skill['id'],{'container':'test-tools','code_target':'/skills/p6','storage':{'p6_p7':{'resource':'p6_p7','target':'/business'}}})
        actor=service.agents.save({'name':'P6','skill_ids':[skill['id']]})
        binding=service.agents.bind({'agent_id':actor['id'],'source':'skill:'+skill['id'],'source_id':'industrial','tools':['read_current_window','fail_current_window']})
        assert binding['job_id']=='' and binding['source_id']=='industrial'
        captured={}
        service.external_cli.run=lambda row,command,payload,run_id: captured.update(payload) or {'status':'no_work'}
        tool=next(t for t in skill['tools'] if t['name']=='read_current_window')
        service.skill_tools.run(skill,runtime,tool,{},binding,'run')
        assert captured['context']['source_id']=='industrial' and 'job_id' not in captured['context']
    finally:service.close()


def test_admin_source_registration_is_separate_from_business_scope(tmp_path):
    service=Service(tmp_path/'platform')
    try:
        shared=tmp_path/'shared';shared.mkdir();service.skill_tools.configure_storage('p6_p7',{'directory':str(shared)})
        p6=service.agents.save({'name':'P6'});main=service.agents.save({'name':'Main'})
        cfg=config('http://player:8090');cfg['agents']['p6']=[p6['id']];cfg['agents']['main']=[main['id']]
        initialize(shared,'job-1',cfg)
        value={'agents':{'p6':[p6['id']],'main':[main['id']]},'rules':cfg['rules'],'player_url':cfg['player_url'],
               'allowed_job_ids':['job-1'],'default_window_seconds':3}
        status,result=handle(service,'POST',['api','acquisition-sources','industrial'],value)
        assert status==200 and 'job_id' not in result and 'consumer_id' not in result
        assert handle(service,'GET',['api','acquisition-sources','industrial'],{})[1]['default_window_seconds']==3
    finally:service.close()
