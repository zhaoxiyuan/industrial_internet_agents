"""Durable fixed-interval schedules, shared by the administrator UI and Agent tools.

SQLite serializes the due check, overlap check, task insertion and cursor advance.
This module never calls a model or network service from the scheduler thread.
"""
import json
import time
from datetime import datetime, timezone

from .common import Problem, digest, dumps, text, uid, validate
from .task_store import TERMINAL


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


DEFAULT = {'enabled': False, 'interval_seconds': 60, 'description': '', 'input': {},
           'first_run': 'after_interval', 'overlap_policy': 'skip', 'overlap_key': '', 'overlap_scope':'self', 'timeout_seconds':None}
CONFIG = set(DEFAULT) - {'enabled'}


class Schedules:
    def __init__(self, service, clock=time.time):
        self.service, self.store, self.clock = service, service.store, clock
        with self.store.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY, target TEXT NOT NULL,
                    status TEXT NOT NULL, due REAL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS schedules_due ON schedules(status,due);
                CREATE TABLE IF NOT EXISTS schedule_requests(owner TEXT NOT NULL, request_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(owner,request_id));
                CREATE TABLE IF NOT EXISTS schedule_firings(schedule_id TEXT NOT NULL, due REAL NOT NULL,
                    task_id TEXT NOT NULL UNIQUE, PRIMARY KEY(schedule_id,due));
            ''')

    def get(self, id, db=None):
        if db is None:
            with self.store.db() as connection:
                return self.get(id, connection)
        row = db.execute('SELECT payload FROM schedules WHERE id=?', (id,)).fetchone()
        if not row:
            raise Problem('SCHEDULE_NOT_FOUND', '心跳计划不存在', 404)
        return json.loads(row[0])

    def list(self):
        with self.store.db() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM schedules ORDER BY rowid')]

    def write(self, db, value):
        value = {**value, 'version': value.get('version', 0)+1, 'updated_at': iso(self.clock())}
        db.execute('INSERT INTO schedules VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET target=excluded.target,status=excluded.status,due=excluded.due,payload=excluded.payload',
                   (value['id'], value['agent_id'], value['status'], value['next_run_at'], dumps(value)))
        return value

    def configuration(self, data, agent):
        if not isinstance(data, dict) or set(data) - set(DEFAULT):
            raise Problem('PARAMETER_INVALID', '心跳配置存在未知字段')
        value = {**DEFAULT, **data}
        if type(value['enabled']) is not bool:
            raise Problem('PARAMETER_INVALID', '心跳启用须为布尔值')
        period = value['interval_seconds']
        if type(period) is not int or not 1 <= period <= 86400:
            raise Problem('PARAMETER_INVALID', '心跳周期须为1–86400整数秒')
        if value['first_run'] not in ('immediate', 'after_interval') or value['overlap_policy'] != 'skip':
            raise Problem('PARAMETER_INVALID', '首次执行选项无效；当前防重叠仅支持skip')
        if value['overlap_scope'] not in ('self','chain'):raise Problem('PARAMETER_INVALID','overlap_scope须为self或chain')
        timeout=value['timeout_seconds']
        if timeout is not None and (type(timeout) is not int or not 1<=timeout<=agent.get('timeout',180)):
            raise Problem('PARAMETER_INVALID','单轮超时不得超过Agent配置上限')
        if not isinstance(value['input'], dict):
            raise Problem('PARAMETER_INVALID', '心跳input须为JSON对象')
        if not isinstance(value['overlap_key'], str) or len(value['overlap_key']) > 128:
            raise Problem('PARAMETER_INVALID', 'overlap_key须为最多128字符的字符串')
        value['overlap_key'] = value['overlap_key'].strip()
        if value['enabled']:
            value['description'] = text(value['description'], '周期任务描述', 10000)
            validate(value['input'], agent.get('input_schema', {'type':'object'}))
        elif not isinstance(value['description'], str) or len(value['description']) > 10000:
            raise Problem('PARAMETER_INVALID', '心跳任务描述无效')
        return value

    def authorized(self, owner, target):
        if owner is None:
            return None
        caller = self.store.get('agents', owner)
        if not caller.get('enabled', True):
            raise Problem('SCHEDULE_OWNER_DISABLED', '计划所有者Agent已停用', 409)
        self.service.tasks.authorize(owner, target, 'schedule')
        return next(g['id'] for g in self.store.list('agent_grants') if g['enabled'] and g['caller_agent_id'] == owner
                    and g['target_agent_id'] == target and 'schedule' in g['permissions'])

    def access(self, plan, caller):
        if caller is not None:
            if plan['owner_agent_id'] != caller:
                raise Problem('SCHEDULE_FORBIDDEN', '只能管理自己创建的计划', 403)
            self.authorized(caller, plan['agent_id'])

    def ready(self, plan):
        text(plan['description'], '周期任务描述', 10000)
        grant = self.authorized(plan['owner_agent_id'], plan['agent_id'])
        if plan['owner_agent_id'] and grant != plan['grant_id']:
            raise Problem('SCHEDULE_GRANT_CHANGED', '原计划授权关系已替换，需明确恢复计划', 403)
        from .workflow import ensure_open
        ensure_open(plan.get('business_scope'))
        agent = self.store.get('agents', plan['agent_id'])
        if not agent.get('enabled', True):
            raise Problem('AGENT_DISABLED', '计划目标Agent已停用', 409)
        if not agent.get('model') or not agent.get('base_url') or not self.store.secret(agent['secret_ref']):
            raise Problem('MODEL_NOT_CONFIGURED', '目标Agent模型尚未配置', 409)
        validate(plan['input'], agent.get('input_schema', {'type':'object'}))
        return agent

    def default(self, agent_id):
        with self.store.db() as db:
            row = db.execute('SELECT payload FROM schedules WHERE id=?', ('default_'+agent_id,)).fetchone()
        if not row:
            return dict(DEFAULT)
        plan = json.loads(row[0])
        return {**{k: plan.get(k,DEFAULT[k]) for k in CONFIG}, 'enabled': plan['status'] == 'active',
                'schedule_id': plan['id'], 'version': plan['version'], 'status': plan['status']}

    def save_default(self, agent_id, config):
        return self.save({'agent_id': agent_id, **config}, default=True)

    def save(self, data, caller=None, run_id=None, default=False):
        if not isinstance(data, dict) or set(data) - (set(DEFAULT) | {'agent_id','schedule_id','version','request_id'}):
            raise Problem('PARAMETER_INVALID', '计划存在未知字段或不是JSON对象')
        target = text(data.get('agent_id'), '目标Agent ID', 128)
        agent = self.store.get('agents', target)
        config = self.configuration({k: data[k] for k in DEFAULT if k in data}, agent)
        grant_id = self.authorized(caller, target)
        id = 'default_'+target if default else data.get('schedule_id')
        request = data.get('request_id')
        if not default:
            request = text(request, 'request_id', 128)
        fingerprint = digest([target, config, id, data.get('version')])
        from .workflow import scope_for_agent
        business_scope=scope_for_agent(self.service,target)
        if business_scope and not business_scope.get('job_id'):raise Problem('WORK_PERMIT_REQUIRED','绑定作业后才能创建心跳计划',409)
        now = self.clock()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if request:
                previous = db.execute('SELECT fingerprint,response FROM schedule_requests WHERE owner=? AND request_id=?', (caller or 'administrator', request)).fetchone()
                if previous:
                    if previous[0] != fingerprint:
                        raise Problem('SCHEDULE_IDEMPOTENCY_CONFLICT', '相同request_id对应不同计划内容', 409)
                    return json.loads(previous[1])
            old = None
            if id:
                row = db.execute('SELECT payload FROM schedules WHERE id=?', (id,)).fetchone()
                old = json.loads(row[0]) if row else None
                if not default and not old:
                    raise Problem('SCHEDULE_NOT_FOUND', '计划不存在', 404)
            if old:
                self.access(old, caller)
                if old['agent_id'] != target:
                    raise Problem('SCHEDULE_TARGET_IMMUTABLE', '更新不能更换目标Agent', 409)
                # Admin edits retain the original owner's directed authorization.
                grant_id = self.authorized(old['owner_agent_id'], target) if config['enabled'] else old['grant_id']
                if not default and (type(data.get('version')) is not int or data['version'] != old['version']):
                    raise Problem('SCHEDULE_CONFLICT', '计划已更新，请查询当前version后重试', 409)
                # Unchanged Agent saves must preserve the trigger cursor and counters.
                same = all(old.get(k,DEFAULT[k]) == config[k] for k in CONFIG) and (old['status'] == 'active') == config['enabled']
                if same:
                    result = old
                else:
                    restart = old['status'] != 'active' or any(old[k] != config[k] for k in ('interval_seconds', 'first_run'))
                    due = now + (0 if config['first_run'] == 'immediate' else config['interval_seconds']) if restart else old['next_run_at']
                    result = self.write(db, {**old, **{k:config[k] for k in CONFIG}, 'grant_id': grant_id,
                        'status':'active' if config['enabled'] else 'paused', 'next_run_at':due if config['enabled'] else old['next_run_at'], 'last_error':None})
            else:
                if default and not config['enabled']:
                    return None  # Old/default-disabled Agents create no plans.
                result = self.write(db, {**{k:config[k] for k in CONFIG}, 'id':id or uid(), 'agent_id':target,
                    'business_scope':business_scope, 'source':'agent-default' if default else 'agent-tool' if caller else 'administrator',
                    'owner_agent_id':caller, 'created_by_task_id':run_id, 'grant_id':grant_id, 'created_at':iso(now),
                    'status':'active' if config['enabled'] else 'paused',
                    'next_run_at':now+(0 if config['first_run']=='immediate' else config['interval_seconds']),
                    'last_task_id':None, 'last_error':None, 'skipped_count':0, 'missed_count':0, 'fired_count':0})
            if request:
                db.execute('INSERT INTO schedule_requests VALUES(?,?,?,?)', (caller or 'administrator', request, fingerprint, dumps(result)))
            return result

    def control(self, id, action, caller=None):
        if action not in ('pause', 'resume', 'stop'):
            raise Problem('PARAMETER_INVALID', '计划操作须为pause/resume/stop')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            plan = self.get(id, db)
            self.access(plan, caller)
            if action == 'resume':
                plan['grant_id'] = self.authorized(plan['owner_agent_id'], plan['agent_id'])
                self.ready(plan)
                if plan['status'] == 'active':
                    return plan
                plan.update(status='active', next_run_at=self.clock()+(0 if plan['first_run']=='immediate' else plan['interval_seconds']), last_error=None)
            elif action == 'pause':
                if plan['status'] == 'paused': return plan
                plan['status'] = 'paused'
            else:
                if plan['status'] == 'stopped': return plan
                plan.update(status='stopped', next_run_at=None)
            return self.write(db, plan)

    def delete(self, id, version):
        """Administrator removal: preserve firing/task history and never cancel running work."""
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            plan = self.get(id, db)
            if type(version) is not int or version != plan['version']:
                raise Problem('SCHEDULE_CONFLICT', '计划已更新，请刷新后重试', 409)
            db.execute('DELETE FROM schedules WHERE id=?', (id,))
            return {'id': id, 'deleted': True, 'running_tasks_unchanged': True}

    def overlap(self, plan):
        return 'shared:'+plan['overlap_key'] if plan['overlap_key'] else 'agent:'+plan['agent_id']

    def occupied(self, db, plan, now):
        rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM tasks')]
        if plan.get('overlap_scope','chain')=='self':
            roots={t['id'] for t in rows if t['executor_agent_id']==plan['agent_id']}
        else:
            roots = {t['id'] for t in rows if t.get('schedule_id') and
                     (t['schedule_id'] == plan['id'] or t.get('schedule_overlap_key') == self.overlap(plan))}
        def matches(t):return t['id'] in roots if plan.get('overlap_scope','chain')=='self' else t['root_task_id'] in roots
        for task in rows:
            if not matches(task):
                continue
            if task['status'] not in TERMINAL or task.get('executor_active') or task.get('executor_recovery_until', 0) > now:
                return task['id']
        for row in db.execute("SELECT data FROM entities WHERE kind='execution_holds'"):
            hold = json.loads(row[0])
            held = hold.get('state') == 'uncertain' or (hold.get('state') == 'executing' and hold['until'] > now)
            if held and any(t['id'] == hold['run_id'] and matches(t) for t in rows):
                return hold['run_id']
        return None

    def acknowledge_exit(self, id, task_id, confirmed):
        """Administrator recovery after independently verifying a lost executor exited."""
        if confirmed is not True:
            raise Problem('EXECUTION_CONFIRMATION_REQUIRED', '请先核验容器内执行器已经退出', 409)
        self.get(id)
        task = self.service.tasks.repo.get(task_id)
        root = self.service.tasks.repo.get(task['root_task_id'])
        if root.get('schedule_id') != id or task['status'] not in TERMINAL or task.get('executor_active'):
            raise Problem('EXECUTOR_STILL_ACTIVE', '任务范围不符或平台执行器尚未退出', 409)
        for hold in self.store.list('execution_holds'):
            if hold['run_id'] == task_id and hold['state'] != 'exited':
                self.store.put('execution_holds', {**hold,'state':'exited','acknowledged_at':iso(self.clock()),'acknowledged_by':'administrator'},hold['id'])
        if task.get('executor_recovery_until'):
            self.service.tasks.repo.update(task, executor_recovery_until=0)
        return {'ok':True,'task_id':task_id,'note':'仅解除已核验的执行占用；未重试旧任务'}

    def tick(self):
        now = self.clock()
        for candidate in self.list():
            if candidate['status'] != 'active' or candidate['next_run_at'] > now:
                continue
            # Same lock order as one-shot Tasks.submit; no model/network inside.
            with self.service.tasks.lock, self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                plan = self.get(candidate['id'], db)
                if plan['status'] != 'active' or plan['next_run_at'] > now:
                    continue
                try:
                    self.ready(plan)
                except Problem as error:
                    self.write(db, {**plan, 'status':'stopped' if error.code=='JOB_ALREADY_CLOSED' else 'blocked', 'last_error':error.as_dict()})
                    continue
                due, period = plan['next_run_at'], plan['interval_seconds']
                intervals = int((now-due)//period)+1
                plan['next_run_at'] = due + intervals*period
                plan['missed_count'] += intervals-1
                busy = self.occupied(db, plan, now)
                if busy:
                    holds = [json.loads(r[0]) for r in db.execute("SELECT data FROM entities WHERE kind='execution_holds'")]
                    error = {'code':'EXECUTION_EXIT_UNCONFIRMED','message':'执行退出未确认，需管理员核验后解除占用'} if any(h['run_id']==busy and h['state']=='uncertain' for h in holds) else plan['last_error']
                    self.write(db, {**plan, 'skipped_count':plan['skipped_count']+1, 'last_skip_at':iso(now), 'blocking_task_id':busy,'last_error':error})
                    continue
                task = self.service.tasks.submit(plan['agent_id'], plan['description'], plan['input'],
                    timeout_seconds=plan.get('timeout_seconds'), _schedule={**plan, 'scheduled_for':due, 'triggered_at':now, 'overlap':self.overlap(plan)}, _db=db)
                db.execute('INSERT INTO schedule_firings VALUES(?,?,?)', (plan['id'], due, task['id']))
                self.write(db, {**plan, 'last_task_id':task['id'], 'fired_count':plan['fired_count']+1,
                    'last_triggered_at':iso(now), 'last_error':None, 'blocking_task_id':None})

    def execution_guard(self, task):
        if task.get('schedule_id'):
            # Pause/stop affects future triggers, not a task already accepted.
            plan = self.get(task['schedule_id'])
            self.ready({**plan, 'input':task['input'], 'description':task['description']})

    def detail(self, id, caller=None):
        plan = self.get(id)
        self.access(plan, caller)
        results = caller is None
        if caller:
            try:
                self.service.tasks.authorize(caller, plan['agent_id'], 'result')
                results = True
            except Problem:
                pass
        tasks = [t for t in self.service.tasks.repo.list() if t.get('schedule_id') == id][-20:]
        runs = []
        for task in reversed(tasks):
            duration = None
            if task.get('started'):
                end = datetime.fromisoformat(task['finished']).timestamp() if task.get('finished') else self.clock()
                duration = max(0, end-datetime.fromisoformat(task['started']).timestamp())
            run = {k:task.get(k) for k in ('task_id','status','phase','scheduled_for','triggered_at','started','finished','error','executor_active','executor_recovery_until')}
            run['duration_seconds'] = duration
            if results:
                run.update(result=task.get('result'), artifacts=task.get('artifacts', []))
            runs.append(run)
        return {**plan, 'recent_runs':runs, 'last_execution_error':runs[0]['error'] if runs else None,
                'last_duration_seconds':runs[0]['duration_seconds'] if runs else None,
                'throughput_warning':bool(runs and runs[0]['duration_seconds'] is not None and runs[0]['duration_seconds'] > plan['interval_seconds']),
                'business_backlog':None, 'backlog_note':'平台不读取业务消费游标；积压需由业务工具查询，skip不表示丢弃窗口'}

    def call(self, caller, name, args, run_id):
        if name == 'set_heartbeat_schedule':
            return self.save(args, caller, run_id)
        if name == 'get_heartbeat_schedule':
            if args.get('schedule_id'):
                return self.detail(args['schedule_id'], caller)
            plans = []
            for p in self.list():
                if p['owner_agent_id'] == caller:
                    try: plans.append(self.detail(p['id'], caller))
                    except Problem: continue
            return {'schedules':plans}
        return self.control(args['schedule_id'], args['action'], caller)
