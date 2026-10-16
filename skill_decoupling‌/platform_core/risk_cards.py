"""Platform-owned risk cards. Fixed business scope, CardKit receipts and verified actions."""
import base64
import hashlib
import hmac
import json
import secrets
import time
import threading
from pathlib import Path
from urllib.parse import quote

from .common import Problem, digest, dumps
from .http_client import request
from .feishu_cards_compat.cards import build_job_card


class RiskCards:
    def __init__(self, feishu):
        self.feishu = feishu
        self.store = feishu.store
        self.key = self.store.secret('risk-card-signing') or secrets.token_hex(32)
        self.store.secret('risk-card-signing', self.key)
        self.stop_event = threading.Event()
        self.worker = None

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        def flush():
            while not self.stop_event.wait(.5):
                for scope in self.store.list('risk_card_scopes'):
                    try:
                        _, business = self.scope(scope['connection_id'])
                        with business.db() as db:
                            intents = [r['id'] for r in business.rows(db, 'card_intent') if r['status'] == 'pending' and r.get('not_before',0)<=time.time()]
                        for report_id in intents:
                            self.sync(scope['connection_id'], scope['agents'][0], report_id)
                            if self.stop_event.wait(.2):
                                return
                    except Exception:
                        # Unknown receipts stay unknown. A later administration query can reconcile them.
                        continue
        self.worker = threading.Thread(target=flush, name='platform-card-flush', daemon=True)
        self.worker.start()

    def close(self):
        self.stop_event.set()
        if self.worker:
            self.worker.join(timeout=2)

    def configure(self, connection, value):
        """Admin-only mapping, separate from model arguments and Skill snapshots."""
        from industrial_skills.store import Store
        self.feishu.require(connection)
        root = Path(value['storage']).resolve()
        if root.is_relative_to(self.store.data):
            raise Problem('STORAGE_INVALID', '业务目录不能包含平台私有数据')
        if value.get('source_id'):
            from industrial_skills.acquisition import source_store
            config=source_store(root,value['source_id']).config()
        else:
            business = Store(root, value['job_id'])
            config = business.config()
        if not value.get('agents') or not isinstance(value['agents'], list):
            raise Problem('CARD_SCOPE_INVALID', '至少需要一个授权Agent')
        if value.get('simulation') and not config.get('test_mode') and not value.get('source_id'):
            raise Problem('SIMULATION_FORBIDDEN', '模拟回执只用于测试作业')
        target = self.feishu.resolve(connection, value['target'])
        for actor in value['agents']:
            self.store.get('agents', actor)
        return self.store.put('risk_card_scopes', {**value, 'storage': str(root), 'target': {'type': target['type'], 'id': target['id']}, 'connection_id': connection}, connection)

    def scope(self, connection, actor=None):
        from industrial_skills.store import Store
        scope = self.store.get('risk_card_scopes', connection)
        if actor is not None and actor not in scope['agents']:
            raise Problem('CARD_SCOPE_FORBIDDEN', '当前 Agent 无此卡片作业权限', 403)
        if scope.get('source_id'):
            from industrial_skills.acquisition import source_store
            source=source_store(scope['storage'],scope['source_id'])
            with source.db() as db:active=source.get(db,'active_job','current',False)
            if not active or not active.get('job_id'):raise Problem('WORK_PERMIT_REQUIRED','请先提供并绑定作业票',409)
            scope={**scope,'job_id':active['job_id']}
        return scope, Store(scope['storage'], scope['job_id'])

    def sign(self, value):
        raw = base64.urlsafe_b64encode(dumps(value).encode()).decode().rstrip('=')
        return raw + '.' + hmac.new(self.key.encode(), raw.encode(), hashlib.sha256).hexdigest()

    def verify(self, token):
        try:
            raw, signature = token.split('.')
            expected = hmac.new(self.key.encode(), raw.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise ValueError()
            value = json.loads(base64.urlsafe_b64decode(raw + '='*((-len(raw)) % 4)))
            if value['expires'] < time.time():
                raise ValueError()
            return value
        except (ValueError, KeyError, TypeError):
            raise Problem('CARD_TOKEN_INVALID', '链接签名无效或过期', 403)

    def render(self, connection, row, report):
        scope, _ = self.scope(connection)
        event = row.get('event_snapshot') or report['payload']['event']
        base = scope['public_base'].rstrip('/')
        signed = self.sign({'connection': connection, 'report_id': row['id'], 'version': row['version'],
                            'actor': row.get('accepted_by'), 'purpose': 'view', 'expires': int(time.time())+7*86400})
        entry = base + '/risk-card?token=' + quote(signed)
        batch_id = digest([connection, row['id'], row['version'], row.get('accepted_by')])
        upload_token = self.sign({'connection':connection, 'report_id':row['id'], 'version':row['version'],
                     'actor':row.get('accepted_by'), 'batch_id':batch_id, 'purpose':'upload_batch', 'expires':int(time.time())+7*86400})
        # The validated form-submit renderer lives in materials_in_audit; the legacy
        # waiting_human_review renderer contains duplicate `comment` input names.
        state = {'job_id': row['id'], 'job_status': 'materials_in_audit' if row['status'] == 'waiting_human_review' else row['status'], 'display_risk_level': row['risk_level'],
                 'accepted_by': {'open_id': row.get('accepted_by'), 'name': row.get('accepted_by')} if row.get('accepted_by') else None,
                 'events': [{'risk_event_id': event['id'], 'event_type': event['risk_item'], 'risk_level': row['risk_level'],
                             'risk_basis': event['basis'], 'suggestions': event['advice']}],
                 'materials': {'submissions': [], 'latest_review_text': row.get('review_text') or '\n'.join(m.get('name', '') for m in row['materials'])},
                 'review': {'p9_opinion': {'comment': (row.get('opinion') or {}).get('reason', '')},
                            'last_comment': (row.get('human_review') or {}).get('comment', '')}}
        card = build_job_card(state, row['version'], entry, actor_open_id=row.get('accepted_by'),
                 upload_url_factory=lambda *args: base+'/risk-upload?token='+quote(self.sign({'connection': connection,
                       'report_id': row['id'], 'version': row['version'], 'actor': row.get('accepted_by'),
                       'target': 'materials_submission', 'batch_id':batch_id, 'purpose': 'upload_link', 'expires': int(time.time())+7*86400})))
        labels={'open':'风险持续','uncertain':'观测中断，尚未确认结束','closing':'恢复确认中','closed':'风险现象已结束'}
        card['body']['elements'].insert(0, {'tag':'markdown','content':'**风险状态：** '+labels.get(event['state'],event['state'])+'\n**处置状态：** '+row['status']})
        def visit(value):
            if isinstance(value, dict):
                if value.get('tag') == 'button' and 'value' in value:
                    raw = value['value']
                    body = json.loads(raw) if isinstance(raw, str) else raw
                    body['scope'] = signed
                    if body.get('action') == 'submit_rectification_materials':
                        body['upload_token'] = upload_token
                    value['value'] = dumps(body) if isinstance(raw, str) else body
                if value.get('tag') == 'markdown' and '30 分钟' in value.get('content', ''):
                    value['content'] = '**请按现场处置时限接取任务。**'
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(card)
        return card

    def sync(self, connection, actor, report_id, allowed_targets=None):
        """Exactly one initial message per occurrence; uncertain sends require reconciliation."""
        scope, business = self.scope(connection, actor)
        with business.db() as db:
            if business.get(db,'job','state')['status']=='closed':raise Problem('JOB_ALREADY_CLOSED','人工已关闭作业，停止后续卡片同步',409)
        target = self.feishu.resolve(connection, scope['target'])
        if allowed_targets and target['id'] not in allowed_targets:
            raise Problem('TARGET_FORBIDDEN', '卡片目标未授权', 403)
        with self.feishu.lock:
            with business.db() as db:
                row = business.get(db, 'disposition', report_id)
                report = business.get(db, 'report', report_id)
            card = self.render(connection, row, report)
            key = digest([connection, scope['job_id'], report_id])
            previous = self.store.get('risk_cards', key, required=False) or {}
            if previous.get('status') == 'unknown':
                return {k: v for k, v in previous.items() if k != 'card'}
            if previous.get('business_version') == row['version'] and previous.get('status') in ('delivered', 'simulated'):
                return {k: v for k, v in previous.items() if k != 'card'}
            sequence = previous.get('sequence', 0)+1
            pending = {**previous, 'connection_id': connection, 'report_id': report_id, 'business_version': row['version'],
                       'sequence': sequence, 'status': 'unknown', 'card': card}
            self.store.put('risk_cards', pending, key)
            config = self.feishu.require(connection)
            # Simulation still executes identical CardKit HTTP protocol at an explicit local mock.
            if scope.get('simulation'):
                from urllib.parse import urlsplit
                if urlsplit(config['api_base']).hostname not in ('127.0.0.1', 'localhost', '::1'):
                    raise Problem('SIMULATION_ENDPOINT_INVALID', '模拟只能访问本地测试端点')
            headers = {'Authorization': 'Bearer '+self.feishu.token(connection)}
            endpoint = config['api_base'].rstrip('/')
            def api(path, payload, method='POST'):
                result = request(endpoint+path, payload, headers, method=method)
                if result.get('code') != 0:
                    raise Problem('CARD_API_FAILED', '飞书卡片接口拒绝请求', 502)
                return result.get('data', {})
            if not pending.get('card_id'):
                created = api('/open-apis/cardkit/v1/cards', {'type': 'card_json', 'data': dumps(card)})
                if not created.get('card_id'):
                    raise Problem('CARD_RECEIPT_MISSING', '缺少 card_id', 502)
                pending['card_id'] = created['card_id']
                self.store.put('risk_cards', pending, key)
            if not pending.get('message_id'):
                sent = api('/open-apis/im/v1/messages?receive_id_type='+target['receive_id_type'],
                          {'receive_id': target['id'], 'msg_type': 'interactive', 'uuid': key[:40],
                           'content': dumps({'type': 'card', 'data': {'card_id': pending['card_id']}})})
                if not sent.get('message_id'):
                    raise Problem('CARD_RECEIPT_MISSING', '缺少 message_id', 502)
                pending['message_id'] = sent['message_id']
            else:
                api('/open-apis/cardkit/v1/cards/'+quote(pending['card_id'], safe=''),
                    {'card': {'type': 'card_json', 'data': dumps(card)}, 'sequence': sequence,
                     'uuid': digest([key, sequence])[:40]}, 'PUT')
            pending['status'] = 'simulated' if scope.get('simulation') else 'delivered'
            pending['evidence'] = 'mock_http_receipt' if scope.get('simulation') else 'feishu_api_receipt'
            receipt = self.store.put('risk_cards', pending, key)
            with business.db() as db:
                current = business.get(db, 'disposition', report_id)
                # Delivery bookkeeping must not invalidate an otherwise current callback version.
                current['last_synced_event_version'] = row.get('event_version')
                current['delivery'] = {k: pending[k] for k in ('status', 'card_id', 'message_id', 'sequence', 'evidence')}
                db.execute('UPDATE objects SET data=? WHERE job_id=? AND kind=? AND id=?',
                           (dumps(current), business.job_id, 'disposition', report_id))
                intent = business.get(db, 'card_intent', report_id)
                if intent['disposition_version'] == row['version']:
                    business.put(db, 'card_intent', report_id, {**intent, 'status': pending['status']})
            return {k: v for k, v in receipt.items() if k != 'card'}

    def callback(self, connection, payload):
        """Called only AFTER Feishu.inbound verification. No network/model in callback."""
        from industrial_skills.disposition import apply_human_action
        try:
            event = payload.get('event')
            if event is None:
                # Compatibility with the previously verified flattened gateway envelope.
                raw_value = payload.get('value') or {k:payload[k] for k in ('action','scope','decision','new_level','upload_token') if k in payload}
                event = {'operator':{'open_id':payload.get('operator_open_id')},
                         'action':{'value':raw_value,'form_value':{k:payload[k] for k in ('reason','comment','review_text','approved_comment','rejection_reason','new_level') if k in payload}}}
            action = event['action']
            value = action['value']
            for _ in range(2):
                if isinstance(value, str):
                    value = json.loads(value)
            scope_token = self.verify(value['scope'])
            if scope_token['connection'] != connection or scope_token['purpose'] != 'view':
                raise ValueError('CARD_SCOPE_INVALID')
            scope, business = self.scope(connection)
            actor = event['operator']['open_id']
            fields = dict(action.get('form_value') or {})
            fields['approved'] = value.get('decision') == 'approved'
            comment_field = 'approved_comment' if fields['approved'] else 'rejection_reason'
            if comment_field not in fields and 'comment' in fields:
                fields[comment_field] = fields['comment']
            if 'level' in value:
                fields['level'] = value['level']
            level = value.get('new_level', fields.get('new_level'))
            if level is not None:
                fields['level'] = int(level)
            if value['action'] == 'submit_rectification_materials':
                batch = self.verify(value['upload_token']) if value.get('upload_token') else None
                if batch and (batch['purpose'] != 'upload_batch' or batch['connection'] != connection or batch['report_id'] != scope_token['report_id'] or batch['version'] != scope_token['version'] or batch['actor'] != actor):
                    raise ValueError('UPLOAD_BATCH_INVALID')
                batch_id = batch['batch_id'] if batch else digest([connection,scope_token['report_id'],scope_token['version'],actor])
                text_material = None
                if fields.get('review_text', '').strip():
                    text_material = self.upload(self.sign({**scope_token, 'actor': actor, 'batch_id':batch_id, 'purpose': 'upload', 'expires': int(time.time())+1800}),
                                                '卡片处置说明.txt', 'text/plain', fields['review_text'].encode())
                with business.db() as db:
                    fields['material_ids'] = [m['id'] for m in business.rows(db, 'upload') if batch and m.get('batch_id') == batch_id and m.get('expires',0)>time.time() and m['actor'] == actor and m['report_id'] == scope_token['report_id'] and m['version_at_issue'] == scope_token['version'] and not m.get('used')]
                    if text_material and text_material['id'] not in fields['material_ids']:
                        fields['material_ids'].append(text_material['id'])
            if 'reason' not in fields:
                fields['reason'] = fields.get('relinquish_reason', '')
            callback_id = payload.get('header',{}).get('event_id') or payload.get('event_id')
            if not callback_id:
                raise ValueError('CALLBACK_ID_REQUIRED')
            apply_human_action(business, callback_id, actor, scope_token['report_id'], scope_token['version'], value['action'], fields, participant_directory={'connection_id':connection,'user_ids':list(self.feishu.require(connection).get('users',{}))})
            with business.db() as db:
                current = business.get(db,'disposition',scope_token['report_id'])
                report = business.get(db,'report',scope_token['report_id'])
            # Durable intent is consumed by a later P8P9 task or platform flush; never a callback send.
            return {'toast': {'type': 'success', 'content': '已记录，卡片将更新'}, 'card':{'type':'raw','data':self.render(connection,current,report)}}
        except (ValueError, KeyError, TypeError, Problem):
            return {'toast': {'type': 'error', 'content': '操作未生效：权限、版本或材料不满足要求，请刷新卡片'}}

    def upload(self, token, name, media_type, data):
        """Signed actor/event/version link; no arbitrary filesystem paths."""
        from industrial_skills.store import digest as business_digest
        value = self.verify(token)
        if value['purpose'] != 'upload' or len(data) > 10*1024*1024 or media_type not in ('text/plain', 'application/pdf', 'image/png', 'image/jpeg'):
            raise Problem('UPLOAD_INVALID', '材料类型、大小或上传令牌不合法')
        _, business = self.scope(value['connection'])
        with business.db() as db:
            row = business.get(db, 'disposition', value['report_id'])
            if row['version'] != value['version'] or row['accepted_by'] != value['actor'] or row['status'] not in ('acknowledged', 'rectifying'):
                raise Problem('UPLOAD_STALE', '上传链接已失效', 409)
            key = business_digest([value['report_id'], value['version'], value['actor'], hashlib.sha256(data).hexdigest()])
            previous = business.get(db, 'upload', key, False)
            if previous:
                return previous
            relative = 'materials/'+key
            path = business.root / relative
            path.parent.mkdir(exist_ok=True)
            if not path.exists():
                path.write_bytes(data)
            return business.put(db, 'upload', key, {'report_id': row['id'], 'actor': value['actor'], 'version_at_issue': value['version'],
                  'batch_id':value.get('batch_id') or digest([value['connection'],row['id'],value['version'],value['actor']]), 'expires':value['expires'],
                  'name': str(name)[:150], 'media_type': media_type, 'path': relative, 'sha256': hashlib.sha256(data).hexdigest(), 'used': False})
