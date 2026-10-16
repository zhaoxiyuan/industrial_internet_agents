"""Platform permissions, audit, limits and durable waits around official Agent nodes."""
import json, threading
from typing import NotRequired
from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import ToolMessage, HumanMessage, convert_to_openai_messages
from langgraph.types import interrupt
from .common import Problem, digest, dumps, now, normalize_tool_values
from .contracts import envelope
class PlatformState(AgentState):
 platform_steps: NotRequired[int]
 commit_reminders: NotRequired[int]
 continuation_reminders: NotRequired[int]
class PlatformMiddleware(AgentMiddleware):
 state_schema=PlatformState
 def __init__(self,runtime,task):
  self.runtime,self.task=runtime,task
  self.service=runtime.service
  self.lock=threading.RLock()
 def before_model(self,state,runtime):
  self.runtime.guard(self.task)
  steps=state.get('platform_steps',0)
  limit=self.service.tasks.repo.checkpoint(self.task['id'])['agent'].get('max_model_calls',12)
  if steps>=limit: raise Problem('AGENT_STEP_LIMIT','模型循环达到配置上限',409)
  return {'platform_steps':steps+1}
 @hook_config(can_jump_to=['model'])
 def after_model(self,state,runtime):
  self.runtime.guard(self.task)
  message=state['messages'][-1]
  if getattr(message,'invalid_tool_calls',[]): raise Problem('MODEL_RESPONSE_INVALID','模型工具参数不是有效 JSON',502)
  if len(getattr(message,'tool_calls',[]))>10: raise Problem('MODEL_RESPONSE_INVALID','单轮工具超过十个')
  with self.lock:
   repo=self.service.tasks.repo; cp=repo.checkpoint(self.task['id'])
   self.runtime.append(self.task,cp,convert_to_openai_messages(message))
   usage=getattr(message,'usage_metadata',None) or {}
   if isinstance(usage.get('total_tokens'),int) and usage['total_tokens']>0:
    cp['context_tokens']=usage['total_tokens']
    cp['context_estimated']=False
   cp['pending']=[{'id':c['id'],'function':{'name':c['name'],'arguments':dumps(c['args'])}} for c in getattr(message,'tool_calls',[])]
   cp['tool_index']=0
   repo.update(repo.get(self.task['id']),cp,phase='模型已返回；执行 LangChain Tool')
  if not getattr(message,'tool_calls',[]):
   from .commit_guard import pending_commit
   pending=pending_commit(state['messages'])
   if pending:
    attempts=state.get('commit_reminders',0)
    if attempts>=2:raise Problem('BUSINESS_COMMIT_REQUIRED','已领取但未实际提交；有限纠偏后仍未完成',409)
    reminder=HumanMessage(content='当前领取尚未提交。请实际调用 '+pending[0]+'，不要在正文/代码块模拟调用；确实无法完成则实际调用 '+pending[1]+' 记录失败。不能用最终文字结束。')
    self.runtime.event(self.task['id'],'commit_reminder',{'attempt':attempts+1,'required_tool':pending[0]})
    return {'messages':[reminder],'commit_reminders':attempts+1,'jump_to':'model'}
   from .commit_guard import pending_p7_step
   step=pending_p7_step(state['messages'])
   count=state.get('continuation_reminders',0)
   if step and count<3:
    limit=cp.get('agent',{}).get('max_model_calls',12)
    if state.get('platform_steps',0)+2<limit:
     self.runtime.event(self.task['id'],'continuation_reminder',{'attempt':count+1})
     return {'messages':[HumanMessage(content='本轮要求连续消费。当前提交成功，按回执继续：'+step+'；no_work/blocked或真实错误时退出，不猜ID。')],'continuation_reminders':count+1,'jump_to':'model'}
 def wrap_tool_call(self,request,handler):
  self.runtime.guard(self.task)
  call=request.tool_call; name,args=call['name'],call['args']
  current_agent=self.service.store.get('agents',self.task['executor_agent_id'])
  _,allowed=self.service.agents.catalog(current_agent)
  if name in allowed:
   args=normalize_tool_values(args,allowed[name].get('schema',{}))
  if name!='read_file' and name not in allowed:
   denied=ToolMessage(content=dumps(envelope(error={'code':'TOOL_FORBIDDEN','message':'工具已停用、撤权或修订'})),tool_call_id=call['id'],name=name,status='error')
   self.record_message(denied)
   return denied
  if name=='read_file':
   from .skill_backend import SkillBackend
   SkillBackend(self.service,current_agent).authorize(args.get('file_path',''))
  # Wait is a pure read. Interrupt before executing it, then re-evaluate on resume.
  if name=='wait_tasks':
   try:
    value=self.service.tasks.call(self.task['executor_agent_id'],name,args,self.task['id'])
   except Problem as error:
    denied=ToolMessage(content=dumps(envelope(error=error.as_dict())),tool_call_id=call['id'],name=name,status='error')
    self.record_message(denied)
    return denied
   if value.get('waiting'):
    repo=self.service.tasks.repo; cp=repo.checkpoint(self.task['id']); cp['wait_ids']=value['task_ids']
    repo.update(repo.get(self.task['id']),cp,phase='等待子任务')
    interrupt({'wait_ids':value['task_ids']})
  self.runtime.event(self.task['id'],'tool_start',{'tool':name,'call_id':call['id']})
  key=digest([self.task['id'],call['id']])
  with self.lock:
   old=self.service.store.get('tool_invocations',key,required=False)
   fingerprint=digest([name,args])
   if old and old['fingerprint']!=fingerprint: raise Problem('TOOL_CALL_CONFLICT','工具调用 ID 与参数不一致',409)
   if old:
    result=old.get('result') or envelope(error={'code':'RESULT_UNKNOWN','message':'上次调用结果未知，禁止自动重放'})
    return ToolMessage(content=dumps(result),tool_call_id=call['id'],name=name)
   self.service.store.put('tool_invocations',{'task_id':self.task['id'],'fingerprint':fingerprint,'status':'started'},key)
  try:
   message=handler(request)
  except Problem as error:
   message=ToolMessage(content=dumps(envelope(error=error.as_dict())),tool_call_id=call['id'],name=name,status='error')
  except (ValueError,TypeError):
   message=ToolMessage(content=dumps(envelope(error={'code':'PARAMETER_INVALID','message':'工具参数无效'})),tool_call_id=call['id'],name=name,status='error')
  self.runtime.guard(self.task)
  with self.lock:
   try: result=json.loads(message.content)
   except (ValueError,TypeError): result={'text':message.content}
   self.service.store.put('tool_invocations',{'task_id':self.task['id'],'fingerprint':fingerprint,'status':'completed','result':result},key)
   self.service.store.put('tool_runs',{'agent_id':self.task['executor_agent_id'],'session_id':self.task.get('session_id'),'task_id':self.task['id'],'run_id':self.task['id'],'transport':'langchain-tool','tool':name,'result':result})
   repo=self.service.tasks.repo; cp=repo.checkpoint(self.task['id'])
   self.runtime.append(self.task,cp,convert_to_openai_messages(message))
   cp['tool_index']+=1
   repo.update(repo.get(self.task['id']),cp,phase='LangChain Tool 回执已保存')
   self.runtime.event(self.task['id'],'tool_end',{'tool':name,'call_id':call['id'],'ok':not result.get('isError',False)})
  return message

 def record_message(self,message):
  with self.lock:
   repo=self.service.tasks.repo;cp=repo.checkpoint(self.task['id'])
   self.runtime.append(self.task,cp,convert_to_openai_messages(message))
   cp['tool_index']+=1
   repo.update(repo.get(self.task['id']),cp,phase='工具权限拒绝')
