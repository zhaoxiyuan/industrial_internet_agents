import json
from langchain_core.messages import ToolMessage, AIMessage
from platform_core.commit_guard import pending_commit


def result(tool,value,ok=True):
 return ToolMessage(name='cap_test__'+tool,tool_call_id='x',content=json.dumps({'ok':ok,'isError':not ok,'structuredContent':value}))


def test_p6_narrative_does_not_commit_and_actual_save_does():
 history=[result('read_current_window',{'status':'processing'}),AIMessage(content='save_current_description 已完成')]
 assert pending_commit(history)[0]=='cap_test__save_current_description'
 assert pending_commit(history+[result('save_current_description',{'status':'completed'},False)])
 assert pending_commit(history+[result('save_current_description',{'status':'completed'})]) is None


def test_no_work_or_recorded_failure_needs_no_reminder():
 assert pending_commit([result('read_current_window',{'status':'no_work'})]) is None
 assert pending_commit([result('read_current_window',{'status':'processing'}),result('fail_current_window',{'status':'failed'})]) is None


def test_p7_claim_is_not_released_by_blocked_reread():
 history=[result('claim_description',{'status':'processing','claim':{'id':'one'}}),result('claim_description',{'status':'blocked'})]
 assert pending_commit(history)[0]=='cap_test__complete_assessment'
 assert pending_commit(history+[result('complete_assessment',{'status':'completed'})]) is None


def test_middleware_reminder_is_bounded_and_real_tool_calls_are_allowed():
 from unittest.mock import MagicMock
 import pytest
 from platform_core.runtime_middleware import PlatformMiddleware
 from platform_core.common import Problem
 runtime=MagicMock();runtime.service.tasks.repo.checkpoint.return_value={}
 middleware=PlatformMiddleware(runtime,{'id':'task'})
 history=[result('read_current_window',{'status':'processing'}),AIMessage(content='Only a narrative')]
 for attempts in (0,1):
  update=middleware.after_model({'messages':history,'commit_reminders':attempts},None)
  assert update['jump_to']=='model' and update['commit_reminders']==attempts+1
  assert 'cap_test__save_current_description' in update['messages'][0].content
 with pytest.raises(Problem) as error:
  middleware.after_model({'messages':history,'commit_reminders':2},None)
 assert error.value.code=='BUSINESS_COMMIT_REQUIRED'
 history[-1]=AIMessage(content='',tool_calls=[{'name':'cap_test__save_current_description','args':{},'id':'commit'}])
 assert middleware.after_model({'messages':history,'commit_reminders':2},None) is None


def test_invalid_wait_is_tool_error_not_parent_failure():
 from unittest.mock import MagicMock
 from types import SimpleNamespace
 from platform_core.runtime_middleware import PlatformMiddleware
 from platform_core.common import Problem
 runtime=MagicMock();runtime.service.tasks.repo.checkpoint.return_value={'tool_index':0}
 runtime.service.agents.catalog.return_value=([],{'wait_tasks':{'schema':{'type':'object'}}})
 runtime.service.tasks.call.side_effect=Problem('TASK_NOT_FOUND','任务不存在',404)
 middleware=PlatformMiddleware(runtime,{'id':'task','executor_agent_id':'main'})
 handler=MagicMock()
 reply=middleware.wrap_tool_call(SimpleNamespace(tool_call={'name':'wait_tasks','args':{'task_ids':['wrong']},'id':'wait'}),handler)
 assert reply.status=='error'
 assert json.loads(reply.content)['error']['code']=='TASK_NOT_FOUND'
 handler.assert_not_called()


def test_p7_continuation_is_bounded_and_respects_stop_receipts():
 from unittest.mock import MagicMock
 from platform_core.runtime_middleware import PlatformMiddleware
 runtime=MagicMock();runtime.service.tasks.repo.checkpoint.return_value={'agent':{'max_model_calls':60}}
 middleware=PlatformMiddleware(runtime,{'id':'task'})
 history=[result('complete_assessment',{'status':'completed','closed_events':[],'next_step':'claim_description({})'}),AIMessage(content='done')]
 assert middleware.after_model({'messages':history},None)['continuation_reminders']==1
 assert middleware.after_model({'messages':history,'continuation_reminders':3},None) is None
 assert middleware.after_model({'messages':history,'platform_steps':58},None) is None
 for status in ['blocked','no_work']:
  assert middleware.after_model({'messages':history+[result('claim_description',{'status':status}),AIMessage(content='stop')]},None) is None
