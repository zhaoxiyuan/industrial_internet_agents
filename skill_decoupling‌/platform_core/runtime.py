"""固定 Agent 的模型与工具执行循环，支持持久检查点和非阻塞子任务等待。

本模块不领取任务、不维护授权定义。调度器调用 advance 执行有限步骤，遇到
wait_tasks 保存工具调用位置后立即返回，子任务完成后从该位置继续。
"""
import json
import re

from .common import Problem, digest, dumps, now, validate, normalize_tool_values
from .contracts import envelope
from .http_client import request


class Runtime:
    """把模型对话、工具回执和缓存放入私有检查点，成果保存到公开任务记录。"""

    def __init__(self, service):
        """仅保留平台引用，多个任务各用自己的消息和命令缓存。"""
        self.service = service
        with service.store.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS runtime_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, payload TEXT)")

    def model(self, agent, messages, tools=None):
        """调用兼容模型接口；不在请求异常中公开 API Key 或完整远端响应。"""
        model = self.chat_model(agent)
        if tools:
            model = model.bind_tools(tools)
        from langchain_core.messages import convert_to_openai_messages
        return convert_to_openai_messages(model.invoke(messages))

    def chat_model(self, agent):
        from langchain_openai import ChatOpenAI
        key = self.service.store.secret(agent["secret_ref"])
        if not agent["model"] or not agent["base_url"] or not key:
            raise Problem("MODEL_NOT_CONFIGURED", "请配置模型、Base URL 和 API Key", 409)
        return ChatOpenAI(model=agent["model"], base_url=agent["base_url"], api_key=key,
                          max_tokens=agent["max_tokens"], timeout=agent.get("model_timeout",30), max_retries=0,
                          use_responses_api=False, model_kwargs={"parallel_tool_calls":False})

    def initial(self, agent, description, input, session_id):
        """提交时固定定义版本；密钥只保存引用，权限在工具执行时重新校验。"""
        instructions = [agent["system_prompt"],
            "使用 LangChain 工具，执行由平台授权。CLI 仅在本机 Docker 中执行。不能创建 Agent。",
            "单次任务需等待子任务结果时，实际调用 wait_tasks；持续心跳每轮有限调度后退出，由后续心跳续接。正文说将调用不算执行，不得猜测结果或反复查询。",
            "错误不是成功；外部动作结果未知时不得重发。"]
        if session_id:
            instructions.append("用户通过自然语言提出需求，不要求用户填写 JSON。调用子 Agent 前对照其输入契约整理参数；缺少必填信息时先向用户追问，不得编造参数。")
        for id in agent["skill_ids"]:
            skill = self.service.store.get("skills", id)
            if skill["enabled"]:
                instructions.append(f"可按需读取 Skill {skill['name']}，skill_id={id}：{skill['description']}")
        if agent.get("output_schema") and not session_id:
            instructions.append("最终回答必须是满足以下输出契约的 JSON：" + dumps(agent["output_schema"]))
        content = description + ("\n任务输入：" + dumps(input) if input else "")
        user = {"role": "user", "content": content}
        history = self.repair_history(self.service.store.messages(session_id)) if session_id else []
        if session_id:
            self.service.store.add_message(session_id, user)
        return {"agent": agent, "messages": [{"role": "system", "content": "\n".join(instructions)}, *history, user],
                "pending": [], "tool_index": 0, "cache": {}, "steps": 0, "wait_ids": []}

    def append(self, task, checkpoint, message):
        """记录本次上下文，并将会话根任务的标准消息同步给现有对话页面。"""
        checkpoint["messages"].append(message)
        if 'context_tokens' in checkpoint:
            from .context_usage import message_tokens
            checkpoint['context_tokens'] += message_tokens([message])
            checkpoint['context_estimated'] = True
        if task.get("session_id"):
            self.service.store.add_message(task["session_id"], message)

    def repair_history(self, history):
        """兼容旧版本或进程中断留下的未配对工具消息，不重放业务动作。

        补齐用于发送模型的上下文副本，原历史记录保留用于审计；结果明确为
        未确认，不能把缺失回执推断为成功，也不能触发自动重试。
        """
        result, pending = [], []
        def complete():
            """在新非工具消息前补齐上一轮尚无回执的调用 ID。"""
            for id in pending:
                result.append({"role": "tool", "tool_call_id": id, "content": dumps(envelope(error={
                    "code": "RESULT_UNKNOWN", "message": "历史执行回执不完整，外部动作需核对"}))})
            pending.clear()
        for message in history:
            if message["role"] != "tool":
                complete()
            result.append(message)
            if message["role"] == "assistant":
                pending.extend(c["id"] for c in message.get("tool_calls", []))
            if message["role"] == "tool" and message.get("tool_call_id") in pending:
                pending.remove(message["tool_call_id"])
        complete()
        return result

    def finish_pending(self, task, checkpoint, error):
        """失败或取消时关闭未完成的工具链，保持下一轮模型消息配对完整。"""
        for call in checkpoint["pending"][checkpoint["tool_index"]:]:
            self.append(task, checkpoint, {"role": "tool", "tool_call_id": call["id"],
                                          "content": dumps(envelope(error=error))})
        checkpoint["pending"], checkpoint["tool_index"], checkpoint["wait_ids"] = [], 0, []

    def guard(self, task):
        """网络调用前后检查取消与总时限；不声称能够撤销在途外部请求。"""
        from datetime import datetime
        current = self.service.tasks.repo.get(task["id"])
        if self.service.scheduler.stopping.is_set():
            raise Problem("PROCESS_STOPPING", "平台关闭，任务已中断，外部动作需核对", 409)
        if current.get("cancel_requested"):
            raise Problem("TASK_CANCELLED", "任务在安全边界取消；已发生的外部动作保留", 409)
        if current.get("deadline") and datetime.fromisoformat(now()) >= datetime.fromisoformat(current["deadline"]):
            raise Problem("TASK_TIMEOUT", "任务超过执行期限，外部动作结果需核对", 504)
        if current['status'] in ('failed','cancelled','interrupted'):
            raise Problem((current.get('error') or {}).get('code','TASK_NOT_RUNNING'),'任务执行资格已失效',409)
        from .workflow import ensure_open
        ensure_open(current.get('business_scope'))
        agent = self.service.store.get("agents", task["executor_agent_id"])
        if not agent.get("enabled", True):
            raise Problem("AGENT_DISABLED", "运行中的 Agent 已停用", 409)
        if task.get("caller_agent_id"):
            self.service.tasks.authorize(task["caller_agent_id"], task["executor_agent_id"], "submit")
        self.service.schedules.execution_guard(task)


    def advance(self, id):
        """Official create_agent owns the LLM/Tool loop and persistent graph state."""
        import sqlite3
        from contextlib import closing
        from langchain.agents import create_agent
        from deepagents.middleware.skills import SkillsMiddleware
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.types import Command
        from langchain_core.messages import convert_to_openai_messages
        from .langchain_tools import build_tools
        from .skill_backend import SkillBackend
        from .runtime_middleware import PlatformMiddleware
        from .context_usage import ContextUsageMiddleware
        repo = self.service.tasks.repo
        task, checkpoint = repo.get(id), repo.checkpoint(id)
        agent = checkpoint['agent']
        try:
            self.guard(task)
            tools, mapping = build_tools(self.service,agent,id)
            backend = SkillBackend(self.service,agent)
            if agent['skill_ids']: tools.append(backend.tool())
            middleware = [PlatformMiddleware(self,task)]
            if agent['skill_ids']:
                middleware.append(SkillsMiddleware(backend=backend,sources=['/skills/'],
                    system_prompt='## Skills\n{skills_locations}\n{skills_list}\n{skills_load_warnings}\n按需通过 read_file 读取 Skill；资源只读，脚本只能通过已授权 Docker CLI。'))
            middleware.append(ContextUsageMiddleware(self,task))
            with closing(sqlite3.connect(str(self.service.data/'checkpoints.sqlite3'),check_same_thread=False)) as connection:
                saver=SqliteSaver(connection)
                graph=create_agent(self.chat_model(agent),tools=tools,middleware=middleware,checkpointer=saver)
                config={'configurable':{'thread_id':id},'recursion_limit':120}
                previous=graph.get_state(config)
                if previous.values:
                    input=Command(resume=True)
                else:
                    messages=list(checkpoint['messages'])
                    if task.get('input_artifacts'):
                        messages.append({'role':'user','content':'上游显式交接的成果声明（仅声明，内容须用业务工具读取）：'+dumps(task['input_artifacts'])})
                    if task['depends_on']:
                        values=[{'task_id':d,'result':repo.get(d)['result'],'artifacts':repo.get(d)['artifacts']} for d in task['depends_on']]
                        messages.append({'role':'user','content':'前置任务成果：'+dumps(values)})
                    input={'messages':messages}
                for mode,event in graph.stream(input,config,stream_mode=['messages','updates']):
                    if mode=='messages':
                        chunk,meta=event
                        if getattr(chunk,'content',None) and meta.get('langgraph_node')=='model':
                            self.event(id,'text_delta',{'text':chunk.content})
                snapshot=graph.get_state(config)
                current=repo.get(id); checkpoint=repo.checkpoint(id)
                if snapshot.next:
                    self.event(id,'waiting',{'wait_ids':checkpoint.get('wait_ids',[])})
                    return repo.update(current,checkpoint,status='waiting',phase='等待子任务；LangGraph 已保存中断点')
                self.guard(task)
                messages=snapshot.values['messages']
                if messages[-1].response_metadata.get('finish_reason')=='length':
                    raise Problem('MODEL_OUTPUT_TRUNCATED','模型输出达到Token上限，未确认完整结果',502)
                content=messages[-1].content or ''
                if not isinstance(content,str): content=dumps(content)
                content=re.sub(r'<think>.*?</think>','',content,flags=re.S).strip()
                result={'content':content,'run_id':id}
                if agent.get('output_schema') and not task.get('session_id'):
                    fenced=re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```',content,flags=re.S)
                    payload=fenced.group(1) if fenced else content
                    try: result=json.loads(payload)
                    except ValueError: raise Problem('OUTPUT_INVALID','最终结果不是 JSON',502)
                    result=normalize_tool_values(result,agent['output_schema'])
                    field=agent.get('artifact_output_field')
                    if field and result.get('artifact_id'):
                        ref=self.service.artifacts.accessible(agent['id'],id,result['artifact_id'])
                        if ref['task_id']!=id:raise Problem('OUTPUT_ARTIFACT_INVALID','最终成果须登记于本任务',502)
                        result[field]=ref['record_ref']
                    elif field and result.get('status')=='completed':
                        raise Problem('OUTPUT_ARTIFACT_REQUIRED','完成结果缺少已登记成果',502)
                    validate(result,agent['output_schema'])
                directory=self.service.data/'artifacts'/id
                directory.mkdir(parents=True,exist_ok=True)
                (directory/'result.json').write_text(dumps(result),encoding='utf-8')
                self.guard(task)
                self.event(id,'completed',{})
                reported = result
                if isinstance(result, dict) and 'content' in result:
                    try: reported = json.loads(result['content'])
                    except (ValueError, TypeError): pass
                self.service.skill_tools.finish_run(id, 'model_failed' if isinstance(reported, dict) and reported.get('status') in ('failed', 'error') else 'assessment_not_committed')
                completed = repo.update(repo.get(id),checkpoint,status='succeeded',phase='完成',result=result,
                    artifacts=[*repo.get(id)['artifacts'], {'path':str((directory/'result.json').relative_to(self.service.root)).replace('\\','/'),'purpose':'任务结构化结果'}],finished=now())
                self.archive_execution(completed)
                return completed
        except Problem as error:
            return self.fail_task(task,error)
        except Exception as error:
            # Never expose SDK exception strings: they may contain remote request content.
            import logging
            logging.getLogger(__name__).error('Runtime failure: %s',type(error).__name__)
            from openai import APITimeoutError
            timed_out = isinstance(error, (APITimeoutError, TimeoutError))
            return self.fail_task(task,Problem('MODEL_TIMEOUT' if timed_out else 'RUNTIME_ERROR','模型调用超时' if timed_out else '框架执行失败，请核对任务与服务日志',502))

    def fail_task(self,task,error):
        reason = {'MODEL_TIMEOUT':'model_timeout', 'TASK_TIMEOUT':'task_timeout', 'TASK_CANCELLED':'task_cancelled', 'PROCESS_STOPPING':'execution_interrupted'}.get(error.code, 'model_failed')
        from .workflow import fence_run
        fence_run(self.service,task,reason)
        self.service.skill_tools.finish_run(task['id'], reason)
        repo=self.service.tasks.repo
        checkpoint=repo.checkpoint(task['id'])
        self.finish_pending(task,checkpoint,error.as_dict())
        status='cancelled' if error.code=='TASK_CANCELLED' else 'interrupted' if error.code=='PROCESS_STOPPING' else 'failed'
        for child in repo.list():
            if child.get('parent_task_id')==task['id'] and child['status'] not in {'succeeded','failed','cancelled','interrupted'}:
                self.service.tasks.cancel(child['id'])
        self.event(task['id'],'error',{'error':error.as_dict()})
        completed = repo.update(repo.get(task['id']),checkpoint,status=status,phase='执行结束',error=error.as_dict(),finished=now())
        self.archive_execution(completed)
        return completed

    def archive_execution(self, task):
        """Terminal execution journal, independent of mutable task rows. No model secrets."""
        from pathlib import Path
        def redact(value):
            if isinstance(value,dict):
                return {k: ('[redacted]' if any(s in k.lower() for s in ('token','secret','api_key','authorization')) else redact(v)) for k,v in value.items()}
            if isinstance(value,list): return [redact(v) for v in value]
            return value
        runs=[r for r in self.service.store.list('tool_runs') if r.get('task_id')==task['id']]
        with self.service.store.db() as connection:
            events=[json.loads(r[0]) for r in connection.execute('SELECT payload FROM task_events WHERE task_id=? ORDER BY seq',(task['id'],))]
        body=redact({'task':task,'state_events':events,'tool_runs':runs})
        directory=self.service.data/'artifacts'/task['id'];directory.mkdir(parents=True,exist_ok=True)
        target=directory/'execution.json';temporary=target.with_suffix('.tmp')
        temporary.write_text(dumps(body),encoding='utf-8');temporary.replace(target)

    def event(self,id,type,data):
        with self.service.store.db() as db:
            db.execute('INSERT INTO runtime_events(task_id,payload) VALUES(?,?)',(id,dumps({'type':type,**data})))

    def events(self,id,after=0):
        with self.service.store.db() as db:
            return [dict(json.loads(r['payload']),id=r['seq']) for r in db.execute('SELECT seq,payload FROM runtime_events WHERE task_id=? AND seq>? ORDER BY seq LIMIT 200',(id,after))]
