"""User-authorized reset/update of the existing Mac 18770 platform, never a new instance."""
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from verify_platform import RUNTIME, PS, Service, import_package


def deploy():
    expected = Path.home()/'IndustrialAgents/p_skill_validation/source/P_skill/.runtime'
    if RUNTIME.resolve() != expected.resolve(): raise RuntimeError('WRONG_PLATFORM_ROOT')
    # Stop only this exact current platform entrypoint before snapshot/cleanup.
    entry = str(PS/'scripts/serve_validation.py')
    lines = subprocess.check_output(['ps','-axo','pid,command'],text=True).splitlines()
    pids = [int(line.strip().split(None,1)[0]) for line in lines if entry in line and 'python' in line.lower()]
    for pid in pids: os.kill(pid,signal.SIGTERM)
    for _ in range(30):
        remaining=[]
        for pid in pids:
            try:os.kill(pid,0);remaining.append(pid)
            except ProcessLookupError:pass
        if not remaining:break
        time.sleep(1)
    else:raise RuntimeError('PLATFORM_DID_NOT_STOP')
    backup=RUNTIME/'backups'/('timeflow-v4-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True)
    for name in ('platform','shared','player'):shutil.copytree(RUNTIME/name,backup/name)
    player=json.load(urlopen('http://127.0.0.1:18090/v2/player/status'))
    if player['state'] in ('running','stopping'):raise RuntimeError('PLAYER_STILL_RUNNING')
    req=Request('http://127.0.0.1:18090/v2/player/clear-history',data=b'{"confirm":"CLEAR_PLAYER_HISTORY"}',headers={'Content-Type':'application/json'})
    cleared=json.load(urlopen(req))
    db=sqlite3.connect(RUNTIME/'platform/data/platform.db')
    keep={'agents','skills','bindings','connections','agent_grants','http_services','cli_services','skill_runtimes','skill_storage','risk_card_scopes','tool_candidates'}
    tables=[x[0] for x in db.execute("select name from sqlite_master where type='table'")]
    for table in tables:
        if table=='entities':
            db.execute('delete from entities where kind not in ('+','.join('?' for _ in keep)+')',tuple(keep))
        elif table not in ('secrets','sqlite_sequence'):db.execute('delete from "'+table+'"')
    db.commit();db.close()
    for name in ('checkpoints.sqlite3','checkpoints.sqlite3-wal','checkpoints.sqlite3-shm'):
        (RUNTIME/'platform/data'/name).unlink(missing_ok=True)
    for name in ('artifacts','skill_views'):
        target=RUNTIME/'platform/data'/name
        if target.exists():
            shutil.move(str(target),str(backup/('retired-'+name)));target.mkdir()
    db=sqlite3.connect(RUNTIME/'shared/P6_P7.sqlite3')
    cfg=json.loads(db.execute("select data from acquisition_objects where kind='config' and id='source'").fetchone()[0])
    cfg['allowed_job_ids']=[]
    for table in ('objects','audit','acquisition_objects','acquisition_audit'):db.execute('delete from '+table)
    db.execute('insert into acquisition_objects values(?,?,?,?,?)',('industrial','config','source',cfg['version'],json.dumps(cfg)))
    db.commit();db.close()
    for target in (RUNTIME/'shared').iterdir():
        if target.is_dir():shutil.move(str(target),str(backup/('retired-shared-'+target.name)))
    service=Service(RUNTIME/'platform')
    try:
        setup=json.loads((RUNTIME/'setup.json').read_text());agents=setup['agents']
        for role,folder,name in [('main','main','industrial-main'),('p6','P6','p6-monitor'),('p7','P7','p7-risk-assessment'),('p8p9','P8','p8p9-disposition')]:
            skill=import_package(service,PS/folder,name)
            relative=Path(skill['path']).relative_to('data/skills').as_posix()
            service.skill_tools.configure(skill['id'],{'container':'p-skill-python-executor','code_target':'/skills/'+relative,'storage':{'p6_p7':{'resource':'p6_p7','target':'/business'}}})
            schema={'type':'object','properties':{'start_time':{'type':'string'}} if role=='p6' else {},'additionalProperties':False}
            if role=='p8p9':schema={'type':'object','properties':{'mode':{'enum':['intake','review']},'report_id':{'type':'string'}},'required':['mode'],'additionalProperties':False}
            service.agents.save({'skill_ids':[skill['id']],'input_schema':schema,'concurrency':3 if role=='p6' else 1,
                'max_tokens':30000 if role=='p8p9' else 12000,'max_model_calls':60,'timeout':1800,
                'system_prompt':'你是工业作业'+role+'智能体。先读取绑定Skill，按新时间窗口工具执行。真实模型调用，数据与飞书均为模拟。依据实际工具回执汇报。'},agents[role])
            bindings=[b for b in service.store.list('bindings') if b['agent_id']==agents[role] and b['source'].startswith('skill:')]
            if len(bindings)!=1:raise RuntimeError('AMBIGUOUS_SKILL_BINDING:'+role)
            service.agents.bind({'agent_id':agents[role],'source':'skill:'+skill['id'],'source_id':'industrial','tools':[t['name'] for t in skill['tools']]},bindings[0]['id'])
            setup['skills'][role]=skill['id']
        mock=next(c for c in service.store.list('connections') if c['name']=='P-skill-MOCK')
        service.feishu.cards.configure(mock['id'],{'storage':str(RUNTIME/'shared'),'source_id':'industrial',
            'target':{'type':'group','id':'oc_mock'},'agents':[agents['p8p9']],'public_base':'https://ladylike-overripe-resource.ngrok-free.dev','simulation':True})
        for binding in service.store.list('bindings'):
            if binding['agent_id']==agents['p8p9'] and binding['source']=='builtin':
                service.agents.bind({'agent_id':agents['p8p9'],'source':'builtin','connection_ref':mock['id'],'tools':['feishu_sync_risk_card'],'allowed_targets':['oc_mock']},binding['id'])
        # Preserve P10 definition, remove its scheduling grant from this validation flow.
        for grant in service.store.list('agent_grants'):
            if grant.get('target_agent_id')==agents.get('p10'):service.store.delete('agent_grants',grant['id'])
        (RUNTIME/'setup.json').write_text(json.dumps(setup))
        result={'backup':str(backup),'player_cleared':cleared,'agents':agents,'tool_counts':{r:len(service.store.get('skills',setup['skills'][r])['tools']) for r in ('main','p6','p7','p8p9')}}
        (RUNTIME/'timeflow-reset.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False))
    finally:service.close()


if __name__=='__main__':deploy()
