"""Trusted source registration and source-scoped P6 access, independent of jobs."""
import re
from .store import Store, public, canonical, stamp, digest, atomic


class AcquisitionStore(Store):
    """Separate source-keyed tables; raw acquisition records have no job_id."""
    def __init__(self,root,source_id):
        self.source_id=source_id
        super().__init__(root,source_id)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS acquisition_objects(source_id TEXT,kind TEXT,id TEXT,version INTEGER,data TEXT,PRIMARY KEY(source_id,kind,id))')
            db.execute('CREATE TABLE IF NOT EXISTS acquisition_audit(seq INTEGER PRIMARY KEY AUTOINCREMENT,source_id TEXT,action TEXT,object_id TEXT,at TEXT,data TEXT)')

    def get(self,db,kind,key,required=True):
        import json
        row=db.execute('SELECT data FROM acquisition_objects WHERE source_id=? AND kind=? AND id=?',(self.source_id,kind,str(key))).fetchone()
        if not row and required:raise ValueError('SOURCE_RECORD_NOT_FOUND')
        return json.loads(row[0]) if row else None

    def rows(self,db,kind):
        import json
        return [json.loads(r[0]) for r in db.execute('SELECT data FROM acquisition_objects WHERE source_id=? AND kind=? ORDER BY id',(self.source_id,kind))]

    def put(self,db,kind,key,value,expected=None):
        old=self.get(db,kind,key,False);version=old['version'] if old else 0
        if expected is not None and expected!=version:raise ValueError('VERSION_CONFLICT')
        value={**value,'id':str(key),'source_id':self.source_id,'version':version+1}
        db.execute('INSERT INTO acquisition_objects VALUES(?,?,?,?,?) ON CONFLICT(source_id,kind,id) DO UPDATE SET version=excluded.version,data=excluded.data',(self.source_id,kind,str(key),version+1,canonical(value)))
        return value

    def audit(self,db,action,key,value):
        db.execute('INSERT INTO acquisition_audit(source_id,action,object_id,at,data) VALUES(?,?,?,?,?)',(self.source_id,action,str(key),stamp(),canonical(value)))

    def archive(self,stage,key,value):
        relative='executions/P6_source/'+digest([self.source_id,key])+'.json';atomic(self.root/relative,value);return relative

    def config(self):
        with self.db() as db:return self.get(db,'config','source')


def source_store(root,source_id):
    if not isinstance(source_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',source_id):raise ValueError('SOURCE_ID_INVALID')
    return AcquisitionStore(root,source_id)


def initialize_source(root,source_id,config):
    if set(config)-{'agents','player_url','rules','allowed_job_ids','default_window_seconds','max_attempts'}:raise ValueError('SOURCE_CONFIGURATION_FIELDS_INVALID')
    if not {'agents','player_url','rules','allowed_job_ids','default_window_seconds'}<=set(config):raise ValueError('SOURCE_CONFIGURATION_REQUIRED')
    if type(config['default_window_seconds']) is not int or not 1<=config['default_window_seconds']<=300:raise ValueError('WINDOW_SECONDS_INVALID')
    if not isinstance(config['allowed_job_ids'],list) or any(not isinstance(x,str) or not x for x in config['allowed_job_ids']):raise ValueError('JOB_ALLOWLIST_INVALID')
    if not isinstance(config['rules'],list):raise ValueError('SOURCE_RULES_INVALID')
    if not isinstance(config['agents'],dict) or any(not isinstance(ids,list) or any(not isinstance(a,str) or not a for a in ids) for ids in config['agents'].values()):raise ValueError('SOURCE_AGENTS_INVALID')
    from .source import OPS
    import math
    for rule in config['rules']:
        if not {'source','subject_id','risk_item','field','operator','threshold','recovery_seconds'}<=set(rule) or rule['source'] not in ('ppe','sensor','position') or rule['operator'] not in OPS:raise ValueError('SOURCE_RULE_INVALID')
        if not isinstance(rule['threshold'],(int,float)) or not math.isfinite(rule['threshold']) or not isinstance(rule['recovery_seconds'],(int,float)) or not math.isfinite(rule['recovery_seconds']) or rule['recovery_seconds']<=0:raise ValueError('SOURCE_RULE_INVALID')
    store=source_store(root,source_id)
    with store.db() as db:
        old=store.get(db,'config','source',False)
        value={**config,'source_id':source_id}
        if old and {k:v for k,v in old.items() if k not in ('id','job_id','version')}!=value:
            if any(w['status']!='completed' for w in store.rows(db,'window')):raise ValueError('SOURCE_WINDOWS_UNFINISHED')
            store.put(db,'config','source',value)
        elif not old:store.put(db,'config','source',value)
        store.audit(db,'register_source',source_id,{'allowed_job_ids':config['allowed_job_ids']})
    return source_public(store.config())


def context_source(context,role='p6'):
    store=source_store(context['storage']['p6_p7'],context['source_id'])
    config=store.config()
    if context['agent_id'] not in config.get('agents',{}).get(role,[]):raise ValueError('ROLE_FORBIDDEN')
    return store,config


def source_public(row):return {k:v for k,v in public(row).items() if k!='job_id'}


def rules_for(window,policies):
    rules=[]
    for rule in policies:
        if rule['subject_id']!='*':rules.append(rule);continue
        field='sensors' if rule['source']=='sensor' else 'persons'
        key='sensor_id' if field=='sensors' else 'person_id'
        rules.extend({**rule,'subject_id':item[key]} for item in window.get(field,[]))
    return rules
