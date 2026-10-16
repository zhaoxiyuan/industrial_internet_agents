"""Topology projections use real persisted grants/tasks, without making model calls."""
import json
import tempfile
import unittest
from pathlib import Path

from platform_core.service import Service
from platform_core.topology import snapshot
from platform_core.collaboration_api import handle


class TopologyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Service(Path(self.temp.name))

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def agent(self, name, **values):
        return self.service.agents.save({'name': name, 'model': 'test-model',
            'base_url': 'https://example.invalid/v1', 'api_key': 'private-model-key', **values})

    def task(self, agent, status, caller=None, **values):
        from platform_core.common import uid
        return self.service.tasks.repo.create({'id': uid(), 'executor_agent_id': agent['id'],
            'caller_agent_id': caller['id'] if caller else None, 'status': status,
            'description': '<script>untrusted description</script>', 'phase': status,
            'input': {'password': 'private-task-input'}, 'result': 'private-result', **values},
            {'model': {'api_key': 'private-checkpoint-key'}})

    def test_authorized_edges_do_not_infer_relations_from_history(self):
        main, child, alone = self.agent('Main'), self.agent('Child'), self.agent('Alone')
        grant = self.service.tasks.grant({'caller_agent_id': main['id'], 'target_agent_id': child['id'], 'permissions': ['status']})
        self.task(child, 'running', main)
        self.task(alone, 'succeeded', main)  # Historical calls are NOT current authorizations.
        value = snapshot(self.service)
        self.assertEqual(len(value['nodes']), 3)
        self.assertEqual(len(value['edges']), 1)
        edge = value['edges'][0]
        self.assertEqual((edge['source'], edge['target'], edge['active_tasks']), (main['id'], child['id'], 1))
        self.assertFalse(edge['can_submit'])
        self.service.tasks.grant({'enabled': False}, grant['id'])
        self.assertFalse(snapshot(self.service)['edges'][0]['enabled'])
        self.service.store.delete('agent_grants', grant['id'])
        self.assertEqual(snapshot(self.service)['edges'], [])

    def test_enablement_is_independent_of_in_flight_activity(self):
        running=self.agent('disabled but running', enabled=False)
        idle=self.agent('idle')
        unconfigured=self.agent('unconfigured', api_key='')
        waiting=self.agent('waiting')
        queued=self.agent('queued')
        disabled=self.agent('disabled', enabled=False)
        draining=self.agent('cancelled but external executor active', enabled=False)
        self.task(running,'running');self.task(waiting,'waiting');self.task(queued,'queued')
        self.task(draining,'cancelled',executor_active=True)
        data={n['id']:n for n in snapshot(self.service)['nodes']}
        for agent, status in [(running,'running'),(idle,'idle'),(unconfigured,'unconfigured'),(waiting,'waiting'),(queued,'queued'),(disabled,'disabled'),(draining,'draining')]:
            self.assertEqual(data[agent['id']]['status'],status)
        self.assertTrue(data[running['id']]['active'])
        self.assertFalse(data[running['id']]['enabled'])
        self.assertFalse(data[running['id']]['accepting'])
        self.assertFalse(data[queued['id']]['active'])
        self.assertTrue(data[idle['id']]['accepting'])

    def test_counts_cover_all_tasks_but_details_are_bounded_and_private(self):
        agent=self.agent('Agent')
        self.task(agent,'running')
        for _ in range(101):self.task(agent,'succeeded')
        before=self.service.tasks.repo.list()
        code,value=handle(self.service,'GET',['api','agent-topology'],{}, {})
        self.assertEqual(code,200)
        node=value['nodes'][0]
        self.assertEqual(node['counts']['running'],1)
        self.assertEqual(node['counts']['succeeded'],101)
        self.assertEqual(len(node['recent_tasks']),5)
        raw=json.dumps(value)
        for secret in ['private-model-key','private-task-input','private-result','private-checkpoint-key','secret_ref','base_url']:
            self.assertNotIn(secret,raw)
        self.assertEqual(before,self.service.tasks.repo.list())
        self.assertEqual(self.service.overview()['agent_topology']['summary']['agents'],1)

    def test_empty_graph_and_cycles_and_schedule_projection(self):
        self.assertEqual(snapshot(self.service)['nodes'],[])
        a,b=self.agent('A'),self.agent('B')
        for source,target in [(a,b),(b,a)]:
            self.service.tasks.grant({'caller_agent_id':source['id'],'target_agent_id':target['id'],'permissions':['submit']})
        self.service.agents.save({'heartbeat':{'enabled':True,'interval_seconds':60,'description':'Read only fixture','input':{}}},a['id'])
        graph=snapshot(self.service)
        self.assertEqual(len(graph['edges']),2)
        node=next(n for n in graph['nodes'] if n['id']==a['id'])
        self.assertEqual(len(node['schedules']),1)
        self.assertEqual(node['schedules'][0]['status'],'active')

    def test_http_page_assets_and_topology_endpoint(self):
        import threading
        from urllib.request import build_opener, ProxyHandler
        from app import make_server
        server=make_server(self.service)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            opener=build_opener(ProxyHandler({}))
            base=f'http://127.0.0.1:{server.server_port}'
            for path,content_type in [('/web/topology.js','javascript'),('/web/topology.css','text/css'),('/api/agent-topology','application/json')]:
                with opener.open(base+path) as response:
                    self.assertEqual(response.status,200)
                    self.assertIn(content_type,response.headers['Content-Type'])
                    self.assertTrue(response.read())
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
