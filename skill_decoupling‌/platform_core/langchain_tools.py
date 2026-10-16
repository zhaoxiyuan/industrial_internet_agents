"""LangChain Tools use the platform dispatch gate, never the CLI bridge."""
from langchain_core.tools import StructuredTool
from .common import Problem
from .contracts import envelope

def build_tools(service,agent,run_id):
 catalog,mapping=service.agents.catalog(agent)
 tools=[]
 for entry in catalog:
  spec=entry['function']; item=mapping[spec['name']]
  def callback(item):
   def invoke(**kwargs):
    try: return service.agents.execute(agent['id'],item,kwargs,run_id)
    except Problem as error: return envelope(error=error.as_dict())
   return invoke
  tools.append(StructuredTool(name=spec['name'],description=spec['description'],args_schema=spec['parameters'],func=callback(item)))
 return tools,mapping
