"""Signed attachment/detail routes; no platform admin secrets in URLs or pages."""
import base64
import html
import json
import time
from urllib.parse import parse_qs
from .common import Problem


PUBLIC_PATHS = {'/risk-card', '/risk-upload', '/risk-upload/submit', '/risk-material'}


def handle(handler, service, method, parsed):
    cards = service.feishu.cards
    query = parse_qs(parsed.query)
    token = query.get('token', [''])[0]
    value = cards.verify(token)
    scope, business = cards.scope(value['connection'])
    if parsed.path == '/risk-upload/submit' and method == 'POST':
        body, _ = handler.body()
        try:
            data = base64.b64decode(body['data'], validate=True)
        except (ValueError, KeyError):
            raise Problem('UPLOAD_INVALID', '无效材料编码')
        result = cards.upload(token, body['name'], body['media_type'], data)
        return handler.send_json(200, {'material_id': result['id'], 'name': result['name'], 'status': 'uploaded_not_submitted'})
    if method != 'GET':
        raise Problem('METHOD_NOT_ALLOWED', '请求方法不支持', 405)
    if parsed.path == '/risk-upload':
        if value['purpose'] != 'upload_link' or not value.get('actor'):
            raise Problem('UPLOAD_FORBIDDEN', '没有上传权限', 403)
        # Seven-day entry link issues a short-lived, event/actor/version-bound token.
        short = cards.sign({**value, 'purpose': 'upload', 'expires': int(time.time())+1800})
        script_token = json.dumps(short)
        page = '''<!doctype html><meta charset="utf-8"><title>整改材料</title>
<h1>上传整改材料</h1><p>上传后回到卡片提交，才能进入复核。支持文本、PDF、PNG、JPEG，最多 10 MB。</p>
<input type="file" id="file"><button id="upload">上传</button><pre id="result"></pre>
<script>const token=TOKEN;document.querySelector('#upload').onclick=async()=>{
const f=document.querySelector('#file').files[0];if(!f||f.size>10485760)return;
const bytes=new Uint8Array(await f.arrayBuffer());let s='';for(const x of bytes)s+=String.fromCharCode(x);
const r=await fetch('/risk-upload/submit?token='+encodeURIComponent(token),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:f.name,media_type:f.type||'text/plain',data:btoa(s)})});
document.querySelector('#result').textContent=JSON.stringify(await r.json(),null,2);};</script>'''.replace('TOKEN', script_token)
        return handler.send_bytes(200, page.encode(), 'text/html; charset=utf-8')
    with business.db() as db:
        row = business.get(db, 'disposition', value['report_id'])
        if parsed.path == '/risk-material':
            if value['purpose'] != 'download':
                raise Problem('DOWNLOAD_FORBIDDEN', '下载链接无效', 403)
            material = business.get(db, 'upload', value['material_id'])
            if material['report_id'] != row['id']:
                raise Problem('DOWNLOAD_FORBIDDEN', '材料不属于事件', 403)
            path = (business.root / material['path']).resolve()
            if not path.is_relative_to(business.root):
                raise Problem('PATH_INVALID', '材料路径不合法', 403)
            return handler.send_bytes(200, path.read_bytes(), material['media_type'])
        if value['purpose'] != 'view':
            raise Problem('VIEW_FORBIDDEN', '详情链接无效', 403)
        report = business.get(db, 'report', row['id'])
        safe = {'report': report['payload'], 'status': row['status'], 'opinion': row['opinion'], 'human_review': row['human_review']}
        content = '<!doctype html><meta charset="utf-8"><title>风险事件</title><h1>风险事件与处置</h1><pre>'+html.escape(json.dumps(safe, ensure_ascii=False, indent=2))+'</pre>'
        from urllib.parse import quote
        for material in row['materials']:
            link = cards.sign({'connection': value['connection'], 'report_id': row['id'], 'material_id': material['id'], 'purpose': 'download', 'expires': int(time.time())+1800})
            content += '<p><a href="/risk-material?token='+quote(link)+'">'+html.escape(material['name'])+'</a></p>'
        return handler.send_bytes(200, content.encode(), 'text/html; charset=utf-8')
