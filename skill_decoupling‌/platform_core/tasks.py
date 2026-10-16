"""任务业务服务：固定 Agent 授权、任务关系、依赖、取消和成果交付。

调度与模型循环不在此实现。所有 Agent 调用都由 CLI 票据提供真实调用者和
当前任务 ID；模型不能任意改父任务、访问其他会话或创建新的 Agent 定义。
"""
import threading
import time

from .common import Problem, digest, dumps, now, text, uid, validate, normalize_tool_values
from .contracts import GRANT_PERMISSIONS, TASK_TOOLS
from .task_store import TaskStore, TERMINAL


class Tasks:
    """为管理 REST 和 Agent CLI 提供同一套任务规则。"""

    def __init__(self, service):
        """进程内锁保护提交去重；数据库版本更新继续防止取消/完成互相覆盖。"""
        self.service, self.store = service, service.store
        self.repo = TaskStore(self.store)
        self.lock = threading.RLock()

    def grant(self, data, id=None):
        with self.lock:
            return self._grant(data, id)

    def _grant(self, data, id=None):
        """管理员建立有方向的关系，禁止自调用和未知权限，不生成新 Agent。"""
        old = self.store.get("agent_grants", id) if id else {}
        value = {**old, **data}
        caller, target = value.get("caller_agent_id"), value.get("target_agent_id")
        self.store.get("agents", caller)
        self.store.get("agents", target)
        permissions = value.get("permissions", [])
        if (caller == target and not set(permissions).issubset({'schedule','status','result','overview'})) or not isinstance(permissions, list) or not permissions or not set(permissions).issubset(GRANT_PERMISSIONS):
            raise Problem("PARAMETER_INVALID", "自身仅可获心跳、状态、结果和概况权限；禁止递归提交")
        existing = next((g for g in self.store.list("agent_grants") if g["caller_agent_id"] == caller and g["target_agent_id"] == target), None)
        if existing and id and existing["id"] != id:
            raise Problem("GRANT_EXISTS", "这两个 Agent 已有授权关系", 409)
        return self.store.put("agent_grants", {"caller_agent_id": caller, "target_agent_id": target,
            "permissions": list(dict.fromkeys(permissions)), "enabled": bool(value.get("enabled", True))}, id or (existing or {}).get("id"))

    def authorize(self, caller, target, permission):
        """每次调用重新核对关系，因此运行中的授权撤销也会影响后续工具调用。"""
        self.store.get("agents", caller)
        self.store.get("agents", target)
        if not any(g["enabled"] and g["caller_agent_id"] == caller and g["target_agent_id"] == target
                   and permission in g["permissions"] for g in self.store.list("agent_grants")):
            raise Problem("AGENT_FORBIDDEN", "未获授权操作此 Agent", 403)

    def view(self, caller, task_id, permission, current_id):
        """默认只看自己提交或同一协作链中的后代；额外检查目标 Agent 操作权限。"""
        target = self.repo.get(task_id)
        current = self.repo.get(current_id)
        ancestor = target
        owned = target.get("parent_task_id") == current_id
        while ancestor.get("parent_task_id"):
            if ancestor["parent_task_id"] == current_id:
                owned = True
                break
            ancestor = self.repo.get(ancestor["parent_task_id"])
        same_job=bool(current.get('business_scope',{})) and current.get('business_scope')==target.get('business_scope') and bool((current.get('business_scope') or {}).get('job_id'))
        historical= same_job and (target.get('caller_agent_id')==caller)
        if not historical and (not owned or target["root_task_id"] != current["root_task_id"]):
            raise Problem("TASK_FORBIDDEN", "只能操作本次协作中自己的子任务或授权后代", 403)
        self.authorize(caller, target["executor_agent_id"], permission)
        return target

    def submit(self, agent_id, description, input=None, parent_id=None, depends_on=None, request_id="", session_id=None, artifact_ids=None, artifact_inputs=None, timeout_seconds=None, *, _schedule=None, _db=None):
        """创建总任务或子任务并保存 Agent 配置快照；依赖只允许同链已有任务。

        父任务执行者是可信调用者；请求指纹使相同调用不会创建重复子任务。
        调用链禁止祖先 Agent 再入，限制深度为 8，避免无限协作循环。
        """
        with self.lock:
            agent = self.store.get("agents", agent_id)
            if not agent.get("enabled", True):
                raise Problem("AGENT_DISABLED", "Agent 已停用", 409)
            if not agent.get("model") or not agent.get("base_url") or not self.store.secret(agent["secret_ref"]):
                raise Problem("MODEL_NOT_CONFIGURED", "执行前请完成目标 Agent 模型配置", 409)
            if timeout_seconds is not None and (type(timeout_seconds) is not int or not 1<=timeout_seconds<=agent.get('timeout',180)):
                raise Problem('PARAMETER_INVALID','任务超时不得超过Agent配置上限')
            description = text(description, "任务描述", 10000)
            input = normalize_tool_values({} if input is None else input, agent.get('input_schema', {}))
            # 用户会话提供自然语言，由模型追问和整理参数；结构化 API/子任务才校验调用契约。
            # session_id 必须指向这个 Agent 的既有会话，不能作为绕过子任务校验的标记。
            if session_id:
                session = self.store.get("sessions", session_id)
                if session.get('deleted_at'):
                    raise Problem('SESSION_DELETED', '会话已删除', 410)
                if parent_id or session["agent_id"] != agent_id:
                    raise Problem("TASK_CONTEXT_INVALID", "会话与执行 Agent 不匹配", 403)
            deps = depends_on or []
            if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
                raise Problem("PARAMETER_INVALID", "前置依赖必须为任务 ID 数组")
            parent = self.repo.get(parent_id) if parent_id else None
            caller = parent["executor_agent_id"] if parent else None
            if parent:
                if parent["status"] != "running" or parent.get("cancel_requested"):
                    raise Problem("TASK_NOT_RUNNING", "当前父任务不能再提交子任务", 409)
                self.authorize(caller, agent_id, "submit")
                chain, ancestor = [], parent
                while ancestor:
                    chain.append(ancestor["executor_agent_id"])
                    ancestor = self.repo.get(ancestor["parent_task_id"]) if ancestor.get("parent_task_id") else None
                if agent_id in chain or len(chain) >= 8:
                    raise Problem("TASK_RECURSION", "协作调用存在递归或超过嵌套上限", 409)
                for dep in deps:
                    row = self.repo.get(dep)
                    if row.get("parent_task_id") != parent_id:
                        raise Problem("DEPENDENCY_FORBIDDEN", "依赖必须是当前父任务已有的子任务", 403)
            elif deps:
                raise Problem("DEPENDENCY_FORBIDDEN", "总任务不能引用其他协作链的依赖")
            bindings=artifact_inputs or {}
            if not isinstance(bindings,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in bindings.items()):
                raise Problem("PARAMETER_INVALID","成果输入映射必须为字段名到artifact_id的对象")
            refs = list(dict.fromkeys([*(artifact_ids or []),*bindings.values()]))
            if refs and not parent:
                raise Problem("ARTIFACT_FORBIDDEN", "成果传递必须来自当前协作任务", 403)
            declarations = self.service.artifacts.transfer(caller, parent_id, refs) if refs else []
            if bindings:
                if not isinstance(input,dict):raise Problem("PARAMETER_INVALID","成果输入绑定要求对象输入")
                by_id={d['artifact_id']:d for d in declarations}
                input=dict(input,**{field:by_id[ref]['record_ref'] for field,ref in bindings.items()})
            if not session_id:
                validate(input,agent.get("input_schema", {"type":"object","additionalProperties":True}))
            fingerprint = digest([agent_id, description, input, deps, parent_id, session_id, refs, bindings, timeout_seconds])
            if request_id:
                previous = next((t for t in self.repo.list() if t.get("request_id") == request_id
                                 and t.get("parent_task_id") == parent_id), None)
                if previous:
                    if previous["fingerprint"] != fingerprint:
                        raise Problem("TASK_IDEMPOTENCY_CONFLICT", "同一请求 ID 对应不同任务", 409)
                    return previous
            if session_id and any(t.get("session_id") == session_id and t["status"] not in TERMINAL for t in self.repo.list()):
                raise Problem("TASK_BUSY", "会话已有未结束任务", 409)
            from .workflow import scope_for_agent, ensure_open
            scope=(parent.get('business_scope') or scope_for_agent(self.service,agent_id)) if parent else (_schedule or {}).get('business_scope') or scope_for_agent(self.service,agent_id)
            ensure_open(scope)
            if not parent and _schedule is None:
                for plan in self.service.schedules.list():
                    if plan['agent_id']==agent_id and plan.get('overlap_scope')=='self' and plan['status']=='active':
                        with self.store.db() as db:busy=self.service.schedules.occupied(db,plan,self.service.schedules.clock())
                        if busy:raise Problem('AGENT_BUSY','上一轮调度尚未结束，请等待下一次心跳',409)
            id = uid()
            value = {"id": id, "task_id": id, "root_task_id": parent["root_task_id"] if parent else id,
                "parent_task_id": parent_id, "caller_agent_id": caller, "executor_agent_id": agent_id,
                "description": description, "input": input, "depends_on": list(dict.fromkeys(deps)),
                "status": "queued", "phase": "等待依赖与执行资源", "result": None, "error": None,
                "artifacts": [], "artifact_ids": refs, "input_artifacts": declarations, "artifact_inputs": bindings, "request_id": request_id, "fingerprint": fingerprint,
                "business_scope":scope,"session_id": session_id, "attempt": 0, "timeout": timeout_seconds or agent.get("timeout", 180),
                "contract_version": agent.get("contract_version", 1), "config_version": agent.get("config_version", 1)}
            if _schedule is not None:
                # Internal schedule submission is a new root, never an old parent's child.
                if parent_id or session_id or _db is None:
                    raise Problem('TASK_CONTEXT_INVALID', '计划触发须在平台事务中创建独立根任务')
                value.update(schedule_id=_schedule['id'], schedule_version=_schedule['version'],
                    schedule_owner_agent_id=_schedule['owner_agent_id'], created_by_task_id=_schedule['created_by_task_id'],
                    schedule_overlap_key=_schedule['overlap'], scheduled_for=_schedule['scheduled_for'], triggered_at=_schedule['triggered_at'])
            checkpoint = self.service.runtime.initial(agent, description, input, session_id)
            return self.repo.create(value, checkpoint, db=_db)

    def cancel(self, id):
        """尚未启动立即取消；在途任务设置取消请求，等安全边界后实际结束。

        不杀死外部服务，也不把已发送飞书消息当作已撤回。后代同时接收取消。
        """
        with self.lock:
            task = self.repo.get(id)
            if task["status"] in TERMINAL:
                return task
            if task["status"] == "running":
                task = self.repo.update(task, cancel_requested=True, phase="取消已请求，等待在途调用结束")
            else:
                from .workflow import fence_run
                fence_run(self.service,task,'task_cancelled')
                self.service.skill_tools.finish_run(id,'task_cancelled')
                checkpoint = self.repo.checkpoint(id)
                self.service.runtime.finish_pending(task, checkpoint, {"code": "TASK_CANCELLED", "message": "等待任务已取消"})
                task = self.repo.update(task, checkpoint, status="cancelled", phase="已取消", finished=now())
            for child in self.repo.list():
                if child.get("parent_task_id") == id and child["status"] not in TERMINAL:
                    self.cancel(child["id"])
            return task

    def overview(self, agent_id):
        """运行概况从任务事实生成；配置可用不等于已主动探测模型健康。"""
        agent = self.store.get("agents", agent_id)
        rows = [t for t in self.repo.list() if t["executor_agent_id"] == agent_id]
        configured = bool(agent.get("model") and agent.get("base_url") and self.store.secret(agent["secret_ref"]))
        active = sum(t["status"] == "running" for t in rows)
        return {"agent_id": agent_id, "enabled": agent.get("enabled", True), "configured": configured,
                "running": active, "waiting": sum(t["status"] == "waiting" for t in rows),
                "concurrency": agent.get("concurrency", 2),
                "accepting": configured and agent.get("enabled", True) and active < agent.get("concurrency", 2)}

    def call(self, caller, name, args, run_id):
        """处理已校验协作命令，run_id 必须是平台签发的当前任务。"""
        current = self.repo.get(run_id)
        if current["executor_agent_id"] != caller or current["status"] != "running":
            raise Problem("TASK_CONTEXT_INVALID", "协作命令缺少有效执行上下文", 403)
        validate(args, TASK_TOOLS[name]["schema"])
        if name in ('set_heartbeat_schedule', 'get_heartbeat_schedule', 'control_heartbeat_schedule'):
            return self.service.schedules.call(caller, name, args, run_id)
        if name == "list_tasks":
            rows = []
            for task in self.repo.list():
                if task["id"] != run_id:
                    try:
                        self.view(caller, task["id"], "status", run_id)
                    except Problem as error:
                        if error.code in ("TASK_FORBIDDEN", "AGENT_FORBIDDEN"):
                            continue
                        raise
                brief = {k: task[k] for k in ("task_id", "root_task_id", "parent_task_id", "caller_agent_id", "executor_agent_id", "description", "depends_on", "status", "phase")}
                # 状态权限不等于成果权限；仅对自身或通过 result 授权的任务返回文件位置。
                if task["id"] == run_id:
                    brief["artifacts"] = task.get("artifacts", [])
                else:
                    try:
                        self.view(caller, task["id"], "result", run_id)
                        brief["artifacts"] = task.get("artifacts", [])
                    except Problem as error:
                        if error.code not in ("TASK_FORBIDDEN", "AGENT_FORBIDDEN"):
                            raise
                rows.append(brief)
            return {"tasks": rows[-100:]}
        if name == "submit_task":
            request_id = args.get("request_id") or digest([run_id, name, args])
            return self.submit(args["agent_id"], args["description"], args.get("input"), run_id,
                               args.get("depends_on"), request_id, artifact_ids=args.get("artifact_ids"), artifact_inputs=args.get("artifact_inputs"),timeout_seconds=args.get("timeout_seconds"))
        if name == "agent_status":
            self.authorize(caller, args["agent_id"], "overview")
            return self.overview(args["agent_id"])
        if name == "wait_tasks":
            if not args["task_ids"]:
                raise Problem("PARAMETER_INVALID", "请选择至少一个等待任务")
            rows = [self.view(caller, id, "status", run_id) for id in args["task_ids"]]
            # 等待同时返回成果，因此必须同时拥有 result 权限。
            for row in rows:
                self.authorize(caller, row["executor_agent_id"], "result")
            if any(t["status"] not in TERMINAL for t in rows):
                return {"waiting": True, "task_ids": args["task_ids"]}
            return {"waiting": False, "tasks": rows}
        permission = {"get_task_status": "status", "get_task_result": "result", "cancel_task": "cancel"}[name]
        row = self.view(caller, args["task_id"], permission, run_id)
        if name == "cancel_task":
            return self.cancel(row["id"])
        if name == "get_task_result" and row["status"] not in TERMINAL:
            raise Problem("TASK_NOT_FINISHED", "任务尚未结束", 409)
        return row if name == "get_task_result" else {k: row[k] for k in ("task_id", "status", "phase", "executor_agent_id", "error")}

    def wait(self, id, timeout=240):
        """仅供兼容同步调用和测试，调度线程不使用阻塞等待。"""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            row = self.repo.get(id)
            if row["status"] in TERMINAL:
                return row
            time.sleep(.05)
        raise Problem("TASK_WAIT_TIMEOUT", "等待任务超时，请通过任务 ID 查询", 504)
