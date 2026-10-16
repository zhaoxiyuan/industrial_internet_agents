"""Continue acceptance in the existing public platform, without reset or seed DB."""
import json,time
from urllib.request import Request,urlopen
from pathlib import Path
from verify_platform import Service,RUNTIME,PS,import_package
from industrial_skills.store import Store
from industrial_skills.disposition import apply_human_action


def post(path,value):
    return json.load(urlopen(Request('http://127.0.0.1:18770'+path,data=json.dumps(value).encode(),headers={'Content-Type':'application/json'})))


def main():
    assert RUNTIME.resolve()==(Path.home()/'IndustrialAgents/p_skill_validation/source/P_skill/.runtime').resolve()
    tasks=json.load(urlopen('http://127.0.0.1:18770/api/tasks'))['tasks']
    if any(t['status'] in ('running','queued','waiting') for t in tasks):raise RuntimeError('WAIT_FOR_CURRENT_TASKS')
    s=Service(RUNTIME/'platform')
    try:
        setup=json.loads((RUNTIME/'setup.json').read_text());agents=setup['agents']
        for role,folder,name in [('main','main','industrial-main'),('p6','P6','p6-monitor'),('p7','P7','p7-risk-assessment'),('p8p9','P8','p8p9-disposition')]:
            skill=import_package(s,PS/folder,name)
            relative=Path(skill['path']).relative_to('data/skills').as_posix()
            s.skill_tools.configure(skill['id'],{'container':'p-skill-python-executor','code_target':'/skills/'+relative,'storage':{'p6_p7':{'resource':'p6_p7','target':'/business'}}})
            s.agents.save({'skill_ids':[skill['id']],'max_tokens':30000 if role=='p7' else 30000 if role=='p8p9' else 12000},agents[role])
            bindings=[b for b in s.store.list('bindings') if b['agent_id']==agents[role] and b['source'].startswith('skill:')]
            assert len(bindings)==1
            s.agents.bind({'agent_id':agents[role],'source':'skill:'+skill['id'],'source_id':'industrial','tools':[t['name'] for t in skill['tools']]},bindings[0]['id'])
            setup['skills'][role]=skill['id']
        (RUNTIME/'setup.json').write_text(json.dumps(setup))
        business=Store(RUNTIME/'shared','202610150001')
        mock=next(c for c in s.store.list('connections') if c['name']=='P-skill-MOCK')
        with business.db() as db:rows=business.rows(db,'disposition')
        assert len(rows)==3 and all(r['delivery']['status']=='simulated' for r in rows)
        ids=[]
        for row in rows:
            apply_human_action(business,'live-sim-accept-'+row['id'],'ou_worker',row['id'],row['version'],'acknowledge_disposition',{})
            with business.db() as db:row=business.get(db,'disposition',row['id'])
            token=s.feishu.cards.sign({'connection':mock['id'],'report_id':row['id'],'version':row['version'],'actor':'ou_worker','purpose':'upload','expires':time.time()+1800})
            material=s.feishu.cards.upload(token,'simulated-rectification.txt','text/plain','模拟整改材料：本次仅验证工具流程。模拟处置人报告已提醒佩戴防护用品；没有现场照片、连续正常监测证据或人工现场验收记录，不能据此证明持续风险已经恢复。'.encode())
            apply_human_action(business,'live-sim-submit-'+row['id'],'ou_worker',row['id'],row['version'],'submit_rectification_materials',{'material_ids':[material['id']]})
            ids.append(row['id'])
    finally:s.close()
    prompt='继续本次真实验收。3张处置的模拟人工接取和模拟文字材料已经经业务入口提交，材料明确标记为模拟、缺少现场证明。请分别调度P8/P9 mode=review，report_id如下：'+','.join(ids)+'。每项读取实际材料、保存复核意见、同步P-skill-MOCK原卡片，等待实际回执。不可替代人工终审，不伪造风险恢复。随后本次用户授权的模拟播放验收结束，请close_time_monitoring仅传reason、不传cutoff，由工具取精确数据末尾；核对窗口连续完成。最后明确报告首个P7任务输出截断但业务已提交这一事实，不重复研判已完成窗口。禁止调用P10、真实飞书或心跳。'
    result=post('/api/sessions/b34527bdf5814f5f8c98c32955431d02/messages',{'content':prompt})
    (RUNTIME/'timeflow-review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
