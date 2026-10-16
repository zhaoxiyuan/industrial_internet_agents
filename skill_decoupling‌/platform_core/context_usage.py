"""Current model context size; estimates are explicitly labelled in the task table."""
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages.utils import count_tokens_approximately


def message_tokens(messages, tools=None):
    return count_tokens_approximately(messages, tools=tools)


class ContextUsageMiddleware(AgentMiddleware):
    """Run after prompt/tool middleware to count the actual request view."""
    def __init__(self, runtime, task):
        self.runtime, self.task = runtime, task

    def wrap_model_call(self, request, handler):
        messages = ([request.system_message] if request.system_message else []) + list(request.messages)
        repo = self.runtime.service.tasks.repo
        checkpoint = repo.checkpoint(self.task['id'])
        checkpoint['context_tokens'] = message_tokens(messages, request.tools)
        checkpoint['context_estimated'] = True
        repo.update(repo.get(self.task['id']), checkpoint)
        return handler(request)
