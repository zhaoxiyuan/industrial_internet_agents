"""Global source consumption: seconds exist at ingestion, tasks only at claim."""
import hashlib
import json
import secrets
from .models import ToolError
from .storage import canonical,iso,now_us,parse_time
from . import results


def integer(value,minimum,maximum,name):
    if type(value) is not int or not minimum<=value<=maximum:raise ToolError(f'{name} must be {minimum}..{maximum}')


def task_view(row,include_token=False):
    value=dict(row)
    if not include_token:value.pop('lease_token',None)
    value['task_id']=value.pop('id');value['time_range']=[iso(value['window_start_us']),iso(value['window_end_us'])]
    value['lease_expires_at']=iso(value['lease_expires_at_us']) if value['lease_expires_at_us'] is not None else None
    value['error']=json.loads(value.pop('error_json')) if value['error_json'] else None
    value.pop('error_json',None);value.pop('failure_digest',None)
    return value


class Tasks:
    def __init__(self,store,clock=now_us):self.store=store;self.clock=clock

    def refresh(self,db):
        for task in db.execute("SELECT * FROM processing_tasks WHERE state='processing' AND lease_expires_at_us<=?",(self.clock(),)).fetchall():
            state='retryable' if task['attempt_count']-task['attempt_base']<task['max_attempts'] else 'failed'
            db.execute('UPDATE processing_tasks SET state=?,lease_token=NULL,worker_id=NULL,lease_expires_at_us=NULL,error_json=?,available_at_us=?,updated_at_us=? WHERE id=?',(state,canonical({'code':'lease_expired','message':'Worker lease expired'}),self.clock(),self.clock(),task['id']))
            db.execute('UPDATE acquisition_seconds SET state=? WHERE task_id=?',(state,task['id']))

    def _row(self,db,task_id):
        row=db.execute('SELECT * FROM processing_tasks WHERE id=?',(task_id,)).fetchone()
        if not row:raise ToolError('Unknown task',404,'not_found')
        return row

    def _lease(self,task,token):
        if task['state']!='processing' or not isinstance(token,str) or not secrets.compare_digest(task['lease_token'] or '',token):raise ToolError('Task lease no longer belongs to this worker',409,'lease_conflict')
        if task['lease_expires_at_us']<=self.clock():raise ToolError('Lease expired',409,'lease_expired')

    def claim(self,window_seconds,worker_id='platform',lease_seconds=60,request_id=None):
        integer(window_seconds,1,300,'window_seconds');integer(lease_seconds,1,3600,'lease_seconds')
        if not isinstance(worker_id,str) or not worker_id.strip() or len(worker_id)>200:raise ToolError('Invalid worker_id')
        if request_id is not None and (not isinstance(request_id,str) or not request_id or len(request_id)>200):raise ToolError('Invalid request_id')
        with self.store.db(write=True) as db:
            self.refresh(db)
            if request_id:
                previous=db.execute('SELECT * FROM processing_tasks WHERE request_id=?',(request_id,)).fetchone()
                if previous:
                    if previous['worker_id']!=worker_id or (previous['request_window_seconds'] or previous['window_seconds'])!=window_seconds:raise ToolError('Request identity mismatch',409,'conflict')
                    if previous['state']=='processing':return {'status':'claimed','task':task_view(previous,True),'duplicate':True}
                    return {'status':'no_work','reason':'request_already_finished','task':task_view(previous)}
            active=db.execute("SELECT * FROM processing_tasks WHERE state='processing' ORDER BY window_start_us LIMIT 1").fetchone()
            if active:return {'status':'blocked','reason':'window_in_progress','task':task_view(active)}
            earlier=db.execute("SELECT * FROM processing_tasks WHERE state IN('failed','retryable') ORDER BY window_start_us,id LIMIT 1").fetchone()
            if earlier:
                if earlier['state']=='failed':return {'status':'blocked','reason':'failed_predecessor','task':task_view(earlier)}
                if earlier['available_at_us']>self.clock():return {'status':'no_work','reason':'retry_backoff','task':None}
                ident=earlier['id']
            else:
                rows=db.execute("SELECT * FROM acquisition_seconds WHERE state='pending' ORDER BY window_start_us LIMIT ?",(window_seconds,)).fetchall()
                if not rows:return {'status':'no_work','reason':'no_unconsumed_seconds','task':None}
                selected=[rows[0]]
                for row in rows[1:]:
                    if row['window_start_us']!=selected[-1]['window_end_us']:break
                    selected.append(row)
                left,right=selected[0]['window_start_us'],selected[-1]['window_end_us']
                player=self.store.get(db)
                latest=db.execute('SELECT MAX(window_end_us) FROM acquisition_seconds').fetchone()[0]
                active_start=parse_time(player['started_at']) if player.get('started_at') else None
                if len(selected)<window_seconds and player['state'] in {'running','stopping'} and right==latest and active_start is not None and left>=active_start:
                    return {'status':'no_work','reason':'await_full_window','task':None}
                now=self.clock();cutoff=db.execute('SELECT next_sequence FROM player_state WHERE id=1').fetchone()[0]
                ident=db.execute("INSERT INTO processing_tasks(window_seconds,window_start_us,window_end_us,cutoff_sequence,state,created_at_us,updated_at_us) VALUES(?,?,?,?,'processing',?,?)",(window_seconds,left,right,cutoff,now,now)).lastrowid
                db.execute("UPDATE acquisition_seconds SET state='processing',task_id=? WHERE window_start_us>=? AND window_start_us<? AND state='pending'",(ident,left,right))
            token=secrets.token_urlsafe(32);now=self.clock()
            db.execute("UPDATE processing_tasks SET state='processing',worker_id=?,request_id=?,request_window_seconds=?,lease_token=?,lease_expires_at_us=?,attempt_count=attempt_count+1,failure_digest=NULL,updated_at_us=? WHERE id=?",(worker_id,request_id,window_seconds,token,now+lease_seconds*1000000,now,ident))
            db.execute("UPDATE acquisition_seconds SET state='processing' WHERE task_id=?",(ident,))
            return {'status':'claimed','task':task_view(self._row(db,ident),True)}

    def heartbeat(self,task_id,lease_token,lease_seconds=60):
        integer(lease_seconds,1,3600,"lease_seconds")
        with self.store.db(write=True) as db:
            task=self._row(db,task_id); self._lease(task,lease_token)
            expiry=max(task["lease_expires_at_us"],self.clock()+lease_seconds*1_000_000)
            db.execute("UPDATE processing_tasks SET lease_expires_at_us=?,updated_at_us=? WHERE id=?",(expiry,self.clock(),task_id))
            return task_view(self._row(db,task_id))

    def _progress(self,db,task):
        db.execute("UPDATE acquisition_seconds SET state=? WHERE task_id=?",(task['state'],task['id']))

    def complete(self,task_id,lease_token,value):
        with self.store.db(write=True) as db:
            task=self._row(db,task_id)
            if task["state"]=="completed":
                if not isinstance(lease_token,str) or not secrets.compare_digest(task["lease_token"] or "",lease_token): raise ToolError("Completion belongs to another lease",409,"lease_conflict")
                if not isinstance(value,dict) or value.get("result_id")!=task["completion_result_id"]:raise ToolError("Completed task already has a different result",409,"conflict")
                receipt=results.insert(self.store,db,value,task)
                return dict(task=task_view(task),receipt=receipt)
            self._lease(task,lease_token)
            receipt=results.insert(self.store,db,value,task)
            db.execute("UPDATE processing_tasks SET state='completed',completion_result_id=?,error_json=NULL,updated_at_us=? WHERE id=?",(receipt["result_id"],self.clock(),task_id))
            self._progress(db,self._row(db,task_id))
            return dict(task=task_view(self._row(db,task_id)),receipt=receipt)

    def fail(self,task_id,lease_token,error,retry_delay_seconds=0,terminal=False):
        integer(retry_delay_seconds,0,86400,"retry_delay_seconds")
        if not isinstance(error,dict) or not error: raise ToolError("Provide a non-empty structured error")
        if type(terminal) is not bool: raise ToolError("terminal must be boolean")
        fingerprint=hashlib.sha256(canonical([error,retry_delay_seconds,terminal]).encode()).hexdigest()
        with self.store.db(write=True) as db:
            task=self._row(db,task_id)
            if task["state"] in {"retryable","failed"} and task["lease_token"]==lease_token and task["failure_digest"]==fingerprint:
                return dict(task=task_view(task),duplicate=True)
            self._lease(task,lease_token)
            maximum=task['max_attempts']
            state="failed" if terminal or task["attempt_count"]-task["attempt_base"]>=maximum else "retryable"
            current=self.clock()
            db.execute("UPDATE processing_tasks SET state=?,lease_expires_at_us=NULL,error_json=?,failure_digest=?,available_at_us=?,updated_at_us=? WHERE id=?",(state,canonical(error),fingerprint,current+retry_delay_seconds*1_000_000,current,task_id))
            self._progress(db,self._row(db,task_id))
            return dict(task=task_view(self._row(db,task_id)),duplicate=False)

    def retry(self,task_id):
        with self.store.db(write=True) as db:
            task=self._row(db,task_id)
            if task["state"]!="failed": raise ToolError("Manual retry is only allowed for failed tasks",409,"conflict")
            db.execute("UPDATE processing_tasks SET state='retryable',attempt_base=attempt_count,lease_token=NULL,worker_id=NULL,failure_digest=NULL,available_at_us=?,updated_at_us=? WHERE id=?",(self.clock(),self.clock(),task_id))
            self._progress(db,self._row(db,task_id))
            return task_view(self._row(db,task_id))

    def list(self,state=None,limit=100):
        integer(limit,1,1000,'limit')
        if state is not None and state not in {'processing','completed','retryable','failed'}:raise ToolError('Invalid task state')
        with self.store.db(write=True) as db:
            self.refresh(db)
            rows=db.execute('SELECT * FROM processing_tasks'+(' WHERE state=?' if state else '')+' ORDER BY id DESC LIMIT ?',([state] if state else [])+[limit]).fetchall()
            return {'items':[task_view(r) for r in rows]}

    def get(self,task_id):
        with self.store.db(write=True) as db:
            self.refresh(db)
            return task_view(self._row(db,task_id))

    def seconds(self,limit=100):
        integer(limit,1,1000,'limit')
        with self.store.db(write=True) as db:
            self.refresh(db)
            counts={s:0 for s in ['pending','processing','completed','retryable','failed']}
            counts.update({r[0]:r[1] for r in db.execute('SELECT state,COUNT(*) FROM acquisition_seconds GROUP BY state')})
            rows=db.execute('SELECT * FROM acquisition_seconds ORDER BY window_start_us DESC LIMIT ?',(limit,)).fetchall()
            first=db.execute("SELECT MIN(window_start_us) FROM acquisition_seconds WHERE state!='completed'").fetchone()[0]
            pending=db.execute("SELECT * FROM acquisition_seconds WHERE state!='completed' ORDER BY window_start_us LIMIT 1").fetchone()
            through=db.execute("SELECT MAX(window_end_us) FROM acquisition_seconds WHERE state='completed'"+(' AND window_end_us<=?' if first is not None else ''),[first] if first is not None else []).fetchone()[0]
            return {'counts':counts,'completed_through':iso(through) if through is not None else None,
                    'first_outstanding':{'time_range':[iso(pending['window_start_us']),iso(pending['window_end_us'])],'state':pending['state'],'task_id':pending['task_id']} if pending else None,
                    'items':[{'time_range':[iso(r['window_start_us']),iso(r['window_end_us'])],'state':r['state'],'task_id':r['task_id']} for r in rows]}
