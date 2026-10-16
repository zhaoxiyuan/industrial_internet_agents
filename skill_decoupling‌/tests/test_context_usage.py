from types import SimpleNamespace
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from platform_core.context_usage import ContextUsageMiddleware, message_tokens
from platform_core.runtime import Runtime
from platform_core.runtime_middleware import PlatformMiddleware


def test_request_includes_final_system_tools_and_resets_instead_of_accumulating():
    runtime=MagicMock(); checkpoint={'messages':[],'context_tokens':999999}
    runtime.service.tasks.repo.checkpoint.return_value=checkpoint
    request=SimpleNamespace(system_message=SystemMessage(content='Final injected instructions'),
        messages=[HumanMessage(content='Current request')],tools=[{'type':'function','function':{
            'name':'read_data','description':'Read a sensor','parameters':{'type':'object','properties':{}}}}])
    handler=MagicMock();middleware=ContextUsageMiddleware(runtime,{'id':'task'})
    middleware.wrap_model_call(request,handler)
    expected=message_tokens([request.system_message,*request.messages],request.tools)
    assert checkpoint['context_tokens']==expected
    assert expected>message_tokens(request.messages)
    assert checkpoint['context_estimated'] is True
    middleware.wrap_model_call(request,handler)
    assert checkpoint['context_tokens']==expected
    handler.assert_called_with(request)


def test_tool_reply_immediately_increases_current_context_and_keeps_estimate_label():
    runtime=Runtime.__new__(Runtime); runtime.service=MagicMock()
    checkpoint={'messages':[],'context_tokens':1200,'context_estimated':False}
    message={'role':'tool','tool_call_id':'read','content':'evidence '*100}
    runtime.append({'id':'task'},checkpoint,message)
    assert checkpoint['context_tokens']==1200+message_tokens([message])
    assert checkpoint['context_estimated'] is True
    assert checkpoint['messages']==[message]


def test_actual_model_usage_replaces_estimate_without_becoming_cumulative():
    runtime=MagicMock(); checkpoint={'context_tokens':9000}
    runtime.service.tasks.repo.checkpoint.return_value=checkpoint
    middleware=PlatformMiddleware(runtime,{'id':'task'})
    message=AIMessage(content='Done',usage_metadata={'input_tokens':1200,'output_tokens':100,'total_tokens':1300})
    middleware.after_model({'messages':[message]},None)
    assert checkpoint['context_tokens']==1300
    assert checkpoint['context_estimated'] is False
