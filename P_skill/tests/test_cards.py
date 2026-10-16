import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from industrial_skills import disposition, p7, main
from platform_core.service import Service
from test_pipeline import pipeline, monitor, assess
import pytest


class MockFeishu:
    """Protocol fake only; actual platform transport, auth and receipts remain exercised."""
    def __init__(self):
        self.calls=[];self.cards={};owner=self
        def validate_card(card):
            assert card['schema']=='2.0' and card['config']['update_multi'] is True
            names=set()
            def walk(value):
                if isinstance(value,dict):
                    assert value.get('tag')!='textarea' and 'form_action_type' not in value
                    if value.get('tag') in ('input','form','button') and 'name' in value:
                        assert value['name'] not in names
                        names.add(value['name'])
                    if value.get('tag')=='button' and value.get('action_type')=='form_submit':
                        assert isinstance(value.get('value'),dict) and 'behaviors' not in value
                    if value.get('tag')=='input':assert 'min_length' not in value
                    for child in value.values():walk(child)
                elif isinstance(value,list):
                    for child in value:walk(child)
            walk(card)
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):self.handle_api()
            def do_PUT(self):self.handle_api()
            def handle_api(self):
                value=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.calls.append((self.command,self.path,value))
                if '/auth/' in self.path: result={'code':0,'tenant_access_token':'mock-only','expire':3600}
                elif self.path.endswith('/cards'):
                    card=json.loads(value['data']);validate_card(card)
                    cid='card-'+str(len(owner.cards)+1);owner.cards[cid]={'sequence':0,'card':card};result={'code':0,'data':{'card_id':cid}}
                elif self.command=='PUT':
                    cid=self.path.split('/')[-1];assert value['sequence']>owner.cards[cid]['sequence']
                    assert value['uuid'];card=json.loads(value['card']['data']);validate_card(card)
                    owner.cards[cid]={'sequence':value['sequence'],'card':card};result={'code':0,'data':{}}
                else:
                    assert value['msg_type']=='interactive' and json.loads(value['content'])['type']=='card'
                    result={'code':0,'data':{'message_id':'msg-'+str(len(owner.calls))}}
                raw=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
    def close(self):self.server.shutdown();self.server.server_close()


def ready(pipeline):
    store,context=pipeline
    for _ in range(3):monitor(context);assess(context)
    with store.db() as db:events=store.rows(db,'event')
    for event in events:p7.report_closed_event({'event_id':event['id'],'summary':'test closed event'},context('p7'))
    claim=disposition.claim_risk_report({},context('p8p9'))
    return disposition.initialize_disposition({'claim':claim['claim']},context('p8p9'))


def test_card_protocol_callbacks_and_human_review(pipeline,tmp_path):
    business,context=pipeline;row=ready(pipeline)
    mock=MockFeishu();service=Service(tmp_path/'platform')
    try:
        agent=service.agents.save({'name':'p8p9'})
        connection=service.feishu.save({'name':'TEST','app_id':'mock','app_secret':'mock','verification_token':'verified-token',
                         'api_base':mock.url,'groups':{'oc_test':{'name':'test'}},'users':{}})
        cards=service.feishu.cards
        cards.configure(connection['id'],{'storage':str(business.root),'job_id':business.job_id,'target':{'type':'group','id':'oc_test'},
                'agents':[agent['id']],'public_base':'http://localhost','simulation':True})
        first=cards.sync(connection['id'],agent['id'],row['id'])
        assert first['status']=='simulated'
        cards.sync(connection['id'],agent['id'],row['id'])
        assert len([c for c in mock.calls if '/messages?' in c[1]])==1
        def callback(action,actor,fields=None,decision=None,version=None,flat=False):
            with business.db() as db:current=business.get(db,'disposition',row['id'])
            value={'action':action,'decision':decision,'scope':cards.sign({'connection':connection['id'],'report_id':row['id'],'version':version or current['version'],'purpose':'view','expires':time.time()+300})}
            from platform_core.common import digest
            value['upload_token']=cards.sign({'connection':connection['id'],'report_id':row['id'],'version':current['version'],
                   'actor':actor,'purpose':'upload_batch','batch_id':digest([connection['id'],row['id'],current['version'],actor]),'expires':time.time()+1800})
            payload={'header':{'event_type':'card.action.trigger','event_id':str(time.time_ns()),'token':'verified-token'},
                     'event':{'operator':{'open_id':actor},'action':{'value':json.dumps(json.dumps(value)),'form_value':fields or {}}}}
            if flat:
                payload={'event_type':'card.action.trigger','event_id':str(time.time_ns()),'token':'verified-token',
                         'operator_open_id':actor,'value':value,**(fields or {})}
            return service.feishu.inbound(connection['id'],payload,{},json.dumps(payload).encode())
        assert callback('acknowledge_disposition','forbidden')['toast']['type']=='error'
        accepted=callback('acknowledge_disposition','ou_worker',flat=True)
        assert accepted['toast']['type']=='success'
        assert set(accepted)=={'toast','card'} and accepted['card']['type']=='raw'
        assert accepted['card']['data']['schema']=='2.0'
        assert callback('acknowledge_disposition','ou_worker',version=1)['toast']['type']=='error'
        cards.sync(connection['id'],agent['id'],row['id'])
        with business.db() as db:current=business.get(db,'disposition',row['id'])
        token=cards.sign({'connection':connection['id'],'report_id':row['id'],'version':current['version'],'purpose':'upload','actor':'ou_worker','expires':time.time()+1800})
        material=cards.upload(token,'inspection.txt','text/plain','安全帽已佩戴，护目镜已佩戴，现场复核照片另待提供。'.encode())
        assert callback('submit_rectification_materials','ou_worker')['toast']['type']=='success'
        snap=disposition.read_review_snapshot({'report_id':row['id']},context('p8p9'))
        opinion={'snapshot_id':snap['snapshot_id'],'decision':'unknown','reason':'只有说明文字，缺现场佐证。','limitations':['not visual evidence']}
        with pytest.raises(ValueError,match='READ_MATERIALS_FIRST'):disposition.save_review_opinion(opinion,context('p8p9'))
        disposition.read_material({'snapshot_id':snap['snapshot_id'],'material_id':material['id']},context('p8p9'))
        disposition.save_review_opinion(opinion,context('p8p9'))
        cards.sync(connection['id'],agent['id'],row['id'])
        assert callback('record_closure_review','ou_reviewer',{'approved_comment':'已完成现场人工核验，整改材料与实际情况一致。'},'approved')['toast']['type']=='success'
        cards.sync(connection['id'],agent['id'],row['id'])
        assert len([c for c in mock.calls if '/messages?' in c[1]])==1
        assert len([c for c in mock.calls if c[0]=='PUT'])==3
        assert disposition.read_disposition({'report_id':row['id']},context('p8p9'))['status']=='closed'
        assert not disposition.verify_job_closable({},context('p8p9'))['closable']
        with pytest.raises(Exception):cards.verify(token+'tamper')
    finally:service.close();mock.close()
