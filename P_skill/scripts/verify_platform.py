"""Shared provisioning helpers for the existing public Mac platform."""
import argparse
import base64
import os
import json
import shutil
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
PS=ROOT/'P_skill'
PLATFORM=ROOT/'platform' if (ROOT/'platform').is_dir() else next(ROOT.glob('skill_decoupling*'))
sys.path[:0]=[str(PLATFORM),str(PS/'runtime'),str(PS/'tests'),str(PS/'Docker/mock_player/src')]
from platform_core.service import Service
from platform_core.langchain_tools import build_tools
from industrial_skills.store import initialize,Store
from industrial_skills.acquisition import initialize_source
from test_cards import MockFeishu

RUNTIME=PS/'.runtime'
JOB=os.environ.get('P_SKILL_TEST_JOB_ID','')  # Explicit isolated fixture only; never a production default.
EMPTY_INPUT={'type':'object','properties':{},'additionalProperties':False}
P6_INPUT={'type':'object','properties':{'start_time':{'type':'string'}},'additionalProperties':False}
P89_INPUT={'type':'object','properties':{'mode':{'enum':['intake','review']},'report_id':{'type':'string'}},'required':['mode'],'additionalProperties':False}


def ensure_agent(service, data):
    """Resume a partially provisioned workflow instead of creating another named Agent."""
    candidates=[a for a in service.store.list('agents') if a['name']==data['name']]
    if len(candidates)>1:raise RuntimeError('DUPLICATE_AGENT_REQUIRES_RECONCILIATION:'+data['name'])
    return service.agents.save(data,candidates[0]['id'] if candidates else None)


def ensure_binding(service, data):
    keys=('agent_id','source','connection_ref')
    candidates=[b for b in service.store.list('bindings') if all((b.get(k) or '')==(data.get(k) or '') for k in keys)]
    if len(candidates)>1:raise RuntimeError('DUPLICATE_BINDING_REQUIRES_RECONCILIATION')
    return service.agents.bind(data,candidates[0]['id'] if candidates else None)


def ensure_http(service, data):
    candidates=[p for p in service.store.list('http_services') if p['name']==data['name'] and p['endpoint']==data['endpoint']]
    if len(candidates)>1:raise RuntimeError('DUPLICATE_HTTP_REQUIRES_RECONCILIATION:'+data['name'])
    return service.direct.save(data,candidates[0]['id'] if candidates else None)


def import_package(service,directory,name):
    archive=PS/'dist'/(name+'.zip')
    if directory in (PS/'main',PS/'P6',PS/'P7',PS/'P8') and archive.is_file():
        return service.skills.import_skill({'archive':base64.b64encode(archive.read_bytes()).decode()})
    target=service.root/'packages'/name
    target.mkdir(parents=True,exist_ok=True)
    for child in ('SKILL.md','tools.json','scripts','references'):
        source=directory/child
        if source.is_dir():shutil.copytree(source,target/child,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        elif source.is_file():shutil.copy2(source,target/child)
    return service.skills.import_skill({'source':'packages/'+name})


if __name__ == "__main__":
    raise SystemExit("Legacy fixture runner removed. Use the existing public platform; see LIVE_PLATFORM_ACCEPTANCE.md.")
