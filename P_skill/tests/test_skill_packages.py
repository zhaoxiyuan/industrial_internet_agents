"""ZIP imports execute complete tools with stdlib-only isolated Python."""
import base64
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
import pytest
from platform_core.service import Service
from platform_core.skill_tools import LOADER
from test_pipeline import pipeline

ROOT=Path(__file__).resolve().parents[1]
PACKAGES={'main':'industrial-main','p6':'p6-monitor','p7':'p7-risk-assessment','p8p9':'p8p9-disposition'}

@pytest.fixture
def imported(tmp_path):
    service=Service(tmp_path/'platform')
    skills={}
    try:
        for role,name in PACKAGES.items():
            archive=(ROOT/'dist'/(name+'.zip')).read_bytes()
            skills[role]=service.skills.import_skill({'archive':base64.b64encode(archive).decode()})
        yield service,skills
    finally:service.close()


def call(imported,role,name,args,context):
    service,skills=imported;skill=skills[role]
    tool=next(t for t in skill['tools'] if t['name']==name)
    payload={'root':str(service.root/skill['path']),'entry':tool['entry'],
        'hashes':skill['file_hashes'],'dependencies':tool['dependencies'],'arguments':args,'context':context}
    result=subprocess.run([sys.executable,'-I','-S','-X','utf8','-c',LOADER],input=json.dumps(payload),text=True,encoding="utf-8",capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
    response=json.loads(result.stdout)
    assert '__skill_error' not in response,response
    return response


def test_zip_has_transitive_code_and_no_preinstalled_business_dependency(imported):
    service,skills=imported;common={}
    for role,skill in skills.items():
        assert all(t['dependencies']==[] for t in skill['tools'])
        root=service.root/skill['path'];bundle=json.loads((root/'references/bundle.json').read_text())
        assert bundle['database_contract']=='p6-p7-timeflow-v4'
        for name,expected in bundle['module_sha256'].items():
            data=(root/'scripts/_business'/name).read_bytes()
            assert hashlib.sha256(data).hexdigest()==expected
            if name in common:assert common[name]==expected
            common[name]=expected
        assert not (root/'scripts/_business/p10_bridge.py').exists()
        assert not any(p.suffix in ('.sqlite3','.db','.pyc','.zip') for p in root.rglob('*'))
    probe=subprocess.run([sys.executable,'-I','-S','-c',"import importlib.util; assert importlib.util.find_spec('industrial_skills') is None"],capture_output=True)
    assert probe.returncode==0


def test_complete_zip_pipeline_shares_sqlite_without_installed_runtime(imported,pipeline):
    business,ctx=pipeline
    with ctx.source.db() as db:ctx.source.put(db,'active_job','current',{'job_id':business.job_id})
    main_context={**ctx('main'),'source_id':'industrial'}
    call(imported,'main','configure_schedule',{'window_seconds':2,'max_parallel':3},main_context)
    for _ in range(3):
        actor=ctx('p6')
        raw=call(imported,'p6','read_current_window',{},actor)
        call(imported,'p6','save_current_description',{'summary':'Window evidence','limitations':['mock'],
            'descriptions':[{'observation':number,'explanation':'Observed missing PPE'} for number in raw['required_observations']]},actor)
        claim=call(imported,'p7','claim_description',{},ctx('p7'))
        facts=claim['record']['payload']['observations']
        receipt=call(imported,'p7','complete_assessment',{'claim':claim['claim'],'assessments':[
            {'key':o['key'],'risk_level':3,'basis':'Evidence','advice':['Inspect PPE']}
            for o in facts if any(s['state']=='abnormal' for s in o['segments'])]},ctx('p7'))
        for event in receipt['closed_events']:
            call(imported,'p7','report_closed_event',{'event_id':event,'summary':'Closed risk'},ctx('p7'))
    query=call(imported,'main','query_pipeline',{},main_context)
    assert len(query['queues']['event'])==2
    assert all(r['status']=='completed' for r in query['queues']['description'])
    claim=call(imported,'p8p9','claim_risk_report',{},ctx('p8p9'))
    disposition=call(imported,'p8p9','initialize_disposition',{'claim':claim['claim']},ctx('p8p9'))
    assert disposition['report_id']
    call(imported,'p8p9','verify_job_closable',{},ctx('p8p9'))
    with business.db() as db:assert len(business.rows(db,'p6_window'))==3


def test_zip_hash_verification_covers_internal_implementation(imported,pipeline):
    service,skills=imported;skill=skills['p6'];file=service.root/skill['path']/'scripts/_business/p6.py'
    file.write_text(file.read_text()+'\n# changed\n')
    with pytest.raises(AssertionError,match='SKILL_CODE_CHANGED'):
        call(imported,'p6','read_current_window',{},pipeline[1]('p6'))
