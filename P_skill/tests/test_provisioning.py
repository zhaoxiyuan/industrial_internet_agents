"""Failed setup may be rerun without multiplying Agents, HTTP services or bindings."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from verify_platform import ensure_agent,ensure_binding,ensure_http
from platform_core.service import Service


def test_repeated_partial_setup_reuses_definitions(tmp_path):
    service=Service(tmp_path/'platform')
    try:
        def provision():
            agent=ensure_agent(service,{'name':'P-skill validation p10'})
            provider=ensure_http(service,{'name':'P10 status','description':'existing P10','endpoint':'http://127.0.0.1:18091/status',
                'input_schema':{'type':'object'},'output_schema':{'type':'object'}})
            binding=ensure_binding(service,{'agent_id':agent['id'],'source':'http:'+provider['id'],'tools':['call']})
            return agent['id'],provider['id'],binding['id']
        first=provision()
        assert provision()==first
        assert [len(service.store.list(k)) for k in ('agents','http_services','bindings')]==[1,1,1]
        service.agents.save({'name':'P-skill validation p10'})
        with pytest.raises(RuntimeError,match='DUPLICATE_AGENT'):provision()
    finally:service.close()
