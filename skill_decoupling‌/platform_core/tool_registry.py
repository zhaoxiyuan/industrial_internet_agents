"""Unified provider management over existing stable storage and bindings."""
from .common import Problem, digest
class ToolProviders:
 def __init__(self,service): self.service=service
 def list(self):
  s=self.service
  return [dict(s.registry.public(p),kind='mcp',provider_id=p['id'],id='mcp:'+p['id']) for p in s.store.list('mcps')]+[dict(p,kind='cli',provider_id=p['id'],id='cli:'+p['id']) for p in s.store.list('cli_services')]+[dict(s.direct.public(p),kind='http',provider_id=p['id'],id='http:'+p['id']) for p in s.store.list('http_services')]+self.skill_providers()
 def skill_providers(self):
  result=[]
  for skill in self.service.store.list('skills'):
   if not skill.get('tools'):continue
   runtime=self.service.store.get('skill_runtimes',skill['id'],required=False)
   revision=self.service.skill_tools.revision(skill) if runtime else skill['digest']
   result.append(dict(kind='skill',id='skill:'+skill['id'],provider_id=skill['id'],name=skill['name'],description=skill['description'],tools=skill['tools'],revision=revision,enabled=skill['enabled'] and bool(runtime and runtime['enabled'])))
  return result
 def get(self,id):
  return next((p for p in self.list() if p['id']==id),None) or self.missing()
 def missing(self): raise Problem('NOT_FOUND','工具提供者不存在',404)
 def save(self,data,id=None):
  kind=data.get('kind') or (id.split(':')[0] if id else None)
  operation={'mcp':self.service.registry,'cli':self.service.external_cli,'http':self.service.direct}.get(kind)
  if operation is None: raise Problem('PARAMETER_INVALID','接入类型必须为 mcp、cli 或 http')
  value=operation.save({k:v for k,v in data.items() if k not in ('kind','id','provider_id')},id.split(':',1)[1] if id else None)
  return self.get(kind+':'+value['id'])
 def export(self,id):
  value=self.get(id)
  return {k:v for k,v in value.items() if k not in ('secret_ref','token','api_key','tools','created','updated','provider_id','id','token_configured','revision','healthy','status','last_error','migration_required')}
 def preview(self,data):
  if data.get('kind') not in ('mcp','cli','http'): raise Problem('PARAMETER_INVALID','导入定义需声明 kind')
  if any(k in data for k in ('token','api_key','secret_ref')): raise Problem('PARAMETER_INVALID','导入文件不能含密钥，请在表单单独配置')
  kind=data['kind']
  operation={'mcp':self.service.registry,'cli':self.service.external_cli,'http':self.service.direct}[kind]
  validated=operation.save(data,preview=True)
  return {'definition':{**validated,'kind':kind},'note':'仅预览，未保存、授权或执行'}
 def discover(self,id):
  p=self.get(id); s=self.service
  if p['kind']=='mcp':
   advertised=s.registry.client(p).list_tools()
   # Reuse Registry validation without committing a live provider revision.
   from .common import check_schema
   tools=[]
   for t in advertised:
    check_schema(t['inputSchema'])
    tools.append({**t,'usable':True})
  elif p['kind']=='skill':
   tools=[{'name':t['name'],'description':t['description'],'inputSchema':t['input_schema'],'outputSchema':t['output_schema'],'usable':True} for t in p['tools']]
  elif p['kind']=='cli':
   result=s.external_cli.discover(p['provider_id']); tools=result['tools']
  else: tools=[{'name':'call','description':p['description'],'inputSchema':p['input_schema'],'outputSchema':p['output_schema'],'usable':True}]
  candidate=s.store.put('tool_candidates',{'provider_id':id,'base_revision':p['revision'],'tools':tools})
  return {'candidate_id':candidate['id'],'tools':tools,'base_revision':p['revision'],'note':'待管理员确认；未增加 Agent 权限'}
 def confirm(self,id,data):
  p=self.get(id); s=self.service; c=s.store.get('tool_candidates',data.get('candidate_id'))
  if c['provider_id']!=id or c['base_revision']!=p['revision']: raise Problem('CATALOG_CONFLICT','提供者已修订，请重新发现',409)
  if p['kind']=='mcp':
   row=s.store.get('mcps',p['provider_id'])
   s.store.put('mcps',{**row,'tools':c['tools'],'healthy':True,'status':'connected','revision':row['revision']+1},row['id'])
  s.store.delete('tool_candidates',c['id'])
  return self.get(id)

 def openapi(self,data):
  from .common import check_schema,url
  document=data.get('document',{})
  if not str(document.get('openapi','')).startswith('3.'):
   raise Problem('OPENAPI_UNSUPPORTED','仅支持 OpenAPI 3.x JSON 文档')
  base=data.get('base_url') or next((s.get('url') for s in document.get('servers',[]) if s.get('url')),'')
  url(base); operations=[]
  for path,item in document.get('paths',{}).items():
   if '{' in path or '$ref' in item: raise Problem('OPENAPI_UNSUPPORTED','暂不支持路径参数和引用；请先展开文档')
   for method,spec in item.items():
    if method not in ('get','post'): continue
    if spec.get('callbacks') or spec.get('security') not in (None,[]):
     raise Problem('OPENAPI_UNSUPPORTED','认证须在平台表单配置；回调不支持')
    params=item.get('parameters',[])+spec.get('parameters',[])
    props={};required=[]
    for param in params:
     if param.get('in')!='query' or method!='get' or '$ref' in param: raise Problem('OPENAPI_UNSUPPORTED','仅 GET 标量查询参数和 POST JSON body 受支持')
     schema=param.get('schema',{})
     if schema.get('type') not in ('string','number','integer','boolean'): raise Problem('OPENAPI_UNSUPPORTED','查询参数必须是标量')
     props[param['name']]=schema
     if param.get('required'):required.append(param['name'])
    input_schema={'type':'object','properties':props,'required':required,'additionalProperties':False}
    if method=='post':
     body=spec.get('requestBody',{})
     content=body.get('content',{})
     if content and set(content)!={'application/json'}: raise Problem('OPENAPI_UNSUPPORTED','仅支持 JSON 请求')
     input_schema=content.get('application/json',{}).get('schema',input_schema)
    response=spec.get('responses',{}).get('200') or spec.get('responses',{}).get('201')
    if not response:raise Problem('OPENAPI_UNSUPPORTED','需声明 200 或 201 JSON 响应')
    output=response.get('content',{}).get('application/json',{}).get('schema')
    if output is None:raise Problem('OPENAPI_UNSUPPORTED','需声明 JSON 响应 Schema')
    check_schema(input_schema);check_schema(output)
    operations.append(dict(kind='http',name=spec.get('operationId') or method+' '+path,description=spec.get('description') or spec.get('summary') or path,endpoint=base.rstrip('/')+'/'+path.lstrip('/'),method=method.upper(),input_schema=input_schema,output_schema=output))
  return {'definitions':operations,'note':'候选定义，尚未保存或授权；复杂契约需手工登记'}
