"""Remove only unused duplicate validation definitions, using the platform deletion API.

The authoritative workflow comes from setup.json AND the initialized business database.
Before applying, take a private SQLite backup. Never remove tasks, reports or business data.
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import Request,build_opener,ProxyHandler

ROOT=Path(__file__).resolve().parents[2]
RUNTIME=ROOT/'P_skill/.runtime'
JOB='20261013000000001'


def run(apply=False):
    opener=build_opener(ProxyHandler({}))
    def api(path,method='GET'):
        with opener.open(Request('http://127.0.0.1:18770/api/'+path,method=method),timeout=20) as response:return json.load(response)
    saved=json.loads((RUNTIME/'setup.json').read_text())
    keep=saved['agents'];keep_ids=set(keep.values())
    assert set(keep)=={'main','p6','p7','p8p9','p10'} and len(keep_ids)==5
    with sqlite3.connect(f'file:{RUNTIME / "shared/P6_P7.sqlite3"}?mode=ro',uri=True) as db:
        config=json.loads(db.execute("SELECT data FROM objects WHERE job_id=? AND kind='config' AND id='job'",(JOB,)).fetchone()[0])
        assert all(config['agents'][role]==[ident] for role,ident in keep.items()),'BUSINESS_ROLE_CONFLICT'
    overview=api('overview')
    by_id={a['id']:a for a in overview['agents']}
    assert keep_ids<=set(by_id)
    duplicates=[a for a in overview['agents'] if a['id'] not in keep_ids and a['name'] in {by_id[i]['name'] for i in keep_ids}]
    duplicate_ids={a['id'] for a in duplicates}
    with sqlite3.connect(f'file:{RUNTIME / "platform/data/platform.db"}?mode=ro',uri=True) as db:
        tasks=[json.loads(r[0]) for r in db.execute('SELECT payload FROM tasks')]
        assert not any(t.get('executor_agent_id') in duplicate_ids or t.get('caller_agent_id') in duplicate_ids for t in tasks),'DUPLICATE_HAS_HISTORY'
        task_count=len(tasks)
    previews=[api('agents/'+a['id']+'/deletion') for a in duplicates]
    assert all(p['can_delete'] for p in previews),'DELETION_BLOCKED'
    old_sources={b['source'] for b in overview['bindings'] if b['agent_id'] in duplicate_ids}
    live_sources={b['source'] for b in overview['bindings'] if b['agent_id'] not in duplicate_ids}
    old_http=[p for p in overview['http_services'] if 'http:'+p['id'] in old_sources-live_sources and
              any(q['name']==p['name'] and q['endpoint']==p['endpoint'] and 'http:'+q['id'] in live_sources for q in overview['http_services'])]
    result={'keep':keep,'delete':[p['agent_id'] for p in previews],'remove_http':[p['id'] for p in old_http],'tasks_before':task_count,'applied':False}
    if not apply:return result
    backup_dir=RUNTIME/'backups';backup_dir.mkdir(exist_ok=True);os.chmod(backup_dir,0o700)
    backup=backup_dir/('before-agent-dedup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
    with sqlite3.connect(RUNTIME/'platform/data/platform.db') as source,sqlite3.connect(backup) as target:source.backup(target)
    os.chmod(backup,0o600)
    for row in duplicates:api('agents/'+row['id'],'DELETE')
    for provider in old_http:api('http-services/'+provider['id'],'DELETE')
    after=api('overview')
    assert {a['id'] for a in after['agents']}==keep_ids,'UNEXPECTED_AGENT_SET'
    assert len(after['agent_grants'])==4 and all(g['caller_agent_id']==keep['main'] and g['target_agent_id'] in keep_ids-{keep['main']} for g in after['agent_grants'])
    assert len(after['bindings'])==7,'EXPECTED_FOUR_SKILL_TWO_HTTP_ONE_CARD_BINDINGS'
    assert len({(b['agent_id'],b['source'],b.get('connection_ref'),b.get('job_id')) for b in after['bindings']})==7
    assert any(b['agent_id']==keep['p8p9'] and 'feishu_sync_risk_card' in b['tools'] for b in after['bindings'])
    with sqlite3.connect(f'file:{RUNTIME / "platform/data/platform.db"}?mode=ro',uri=True) as db:
        assert db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]==task_count,'HISTORY_CHANGED'
    result.update(applied=True,backup=str(backup),agents_after=len(after['agents']),bindings_after=len(after['bindings']),grants_after=len(after['agent_grants']))
    (RUNTIME/'agent-dedup-result.json').write_text(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true')
    print(json.dumps(run(parser.parse_args().apply),ensure_ascii=False))
