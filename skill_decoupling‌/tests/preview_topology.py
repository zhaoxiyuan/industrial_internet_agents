"""Isolated visual fixture; never calls a model or external service.

Run from platform: python tests/preview_topology.py --port 18771
All records live in a fresh temporary directory; existing platform data is untouched.
"""
import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import make_server
from platform_core.service import Service
from platform_core.common import uid


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=18771)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='agent-topology-preview-') as directory:
        service=Service(Path(directory))
        # Explicit UI fixture: persisted statuses are synthetic; the scheduler must not run them.
        service.scheduler.start=lambda:None
        agents=[]
        for name,description in [('Main · 演示调度','按有方向授权调度子 Agent；本页面为隔离演示数据。'),('P6 · 演示监测','采集窗口与风险事实'),('P7 · 演示研判','连续事件聚合与风险研判'),('P8 / P9 · 演示处置','平台卡片与材料审核'),('P10 · 演示报告','复用现有报告服务')]:
            agents.append(service.agents.save({'name':name,'description':description,'model':'UI fixture · no model calls',
                'base_url':'https://example.invalid/v1','api_key':'fixture-only','enabled':not name.startswith('P10')}))
        for i,agent in enumerate(agents[1:]):
            service.tasks.grant({'caller_agent_id':agents[0]['id'],'target_agent_id':agent['id'],
                'permissions':['submit','status','result'] if i<3 else ['status','overview'],'enabled':i!=3})
        for index,status in [(0,'waiting'),(1,'running'),(2,'queued')]:
            service.tasks.repo.create({'id':uid(),'executor_agent_id':agents[index]['id'],
                'caller_agent_id':agents[0]['id'] if index else None,'parent_task_id':None,'status':status,
                'description':'演示任务 <script>alert(1)</script>：内容应仅作为文本显示',
                'phase':'隔离 UI 夹具，无模型调用','artifacts':[]}, {})
        server=make_server(service,args.port)
        print(f'Isolated UI fixture: http://127.0.0.1:{server.server_port}',flush=True)
        try:server.serve_forever()
        finally:server.server_close();service.close()


if __name__=='__main__':main()
