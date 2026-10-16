"""Artifact persistence, explicit transfer and authorization regression tests."""
import json
import unittest
from pathlib import Path
import test_collaboration as collaboration
from platform_core.common import Problem

class ArtifactTests(unittest.TestCase):
    setUp=collaboration.CollaborationTests.setUp
    tearDown=collaboration.CollaborationTests.tearDown
    agent=collaboration.CollaborationTests.agent
    grant=collaboration.CollaborationTests.grant
    def running(self, agent, parent=None, refs=None):
        task=self.service.tasks.submit(agent['id'],'artifact test',parent_id=parent,artifact_ids=refs)
        return self.service.tasks.repo.update(task,status='running')

    def test_explicit_transfer_durable_and_scope(self):
        p6,p7,other=self.agent('P6'),self.agent('P7'),self.agent('other')
        self.grant(p6,p7)
        task=self.running(p6)
        args=dict(provider='industrial-records',record_ref='p6-source',relative_path='scope/P6/file.json',content_type='application/json',sha256='a'*64,version=1)
        ref=self.service.artifacts.call(p6['id'],'register_artifact',args,task['id'])
        self.assertEqual(ref['artifact_id'],self.service.artifacts.call(p6['id'],'register_artifact',args,task['id'])['artifact_id'])
        own=self.service.tasks.repo.get(task['id'])['artifacts']
        self.assertEqual(len(own),1)
        self.assertEqual(json.loads((self.service.root/own[0]['path']).read_text(encoding='utf-8'))['record_ref'],'p6-source')
        p7=self.service.agents.save({'input_schema':{'type':'object','properties':{'source_ref':{'type':'string'}},'required':['source_ref'],'additionalProperties':False}},p7['id'])
        child=self.service.tasks.submit(p7['id'],'bound child',parent_id=task['id'],artifact_inputs={'source_ref':ref['artifact_id']})
        child=self.service.tasks.repo.update(child,status='running')
        self.assertEqual(child['input'],{'source_ref':'p6-source'})
        self.assertEqual(child['input_artifacts'][0]['record_ref'],'p6-source')
        self.assertEqual(self.service.artifacts.call(p7['id'],'get_artifact',{'artifact_id':ref['artifact_id']},child['id'])['record_ref'],'p6-source')
        unrelated=self.running(other)
        with self.assertRaises(Problem):self.service.artifacts.call(other['id'],'get_artifact',{'artifact_id':ref['artifact_id']},unrelated['id'])
        grant=self.service.store.list('agent_grants')[0]
        self.service.tasks.grant({'enabled':False},grant['id'])
        with self.assertRaises(Problem):self.service.artifacts.call(p7['id'],'get_artifact',{'artifact_id':ref['artifact_id']},child['id'])

    def test_conflicts_and_paths(self):
        agent=self.agent('P6');task=self.running(agent)
        args=dict(provider='records',record_ref='ref',relative_path='P6/file.json',content_type='application/json',sha256='b'*64,version=1)
        self.service.artifacts.call(agent['id'],'register_artifact',args,task['id'])
        with self.assertRaises(Problem):self.service.artifacts.call(agent['id'],'register_artifact',dict(args,sha256='c'*64),task['id'])
        for path in ('../file.json','/root/file.json','C:/file.json','a\\file.json'):
            with self.assertRaises(Problem):self.service.artifacts.call(agent['id'],'register_artifact',dict(args,relative_path=path),task['id'])

if __name__=='__main__':unittest.main()
