"""Docker-only CLI providers; registration never starts a container or program."""
import json, os, re, shutil, subprocess, time, sys
from pathlib import Path
from contextlib import closing
from .common import Problem, digest, text, uid, validate, check_schema
LOCAL_ENGINE = 'npipe:////./pipe/docker_engine' if os.name == 'nt' else 'unix:///var/run/docker.sock'
if sys.platform == 'darwin' and (Path.home()/'.docker/run/docker.sock').exists():
 LOCAL_ENGINE = 'unix://' + str(Path.home()/'.docker/run/docker.sock')

def docker_executable():
 executable=shutil.which('docker')
 if executable:return executable
 if sys.platform=='darwin':
  for candidate in ('/Applications/Docker.app/Contents/Resources/bin/docker','/opt/homebrew/bin/docker','/usr/local/bin/docker'):
   if Path(candidate).is_file() and os.access(candidate,os.X_OK):return candidate
 raise Problem('DOCKER_CLI_NOT_FOUND','平台进程找不到 Docker 命令；请检查 Docker 安装路径和平台启动 PATH',409)
def mount_matches(actual, expected, subpaths=False):
 from pathlib import PurePosixPath
 def normalized(value):
  value=value.replace('\\','/').rstrip('/')
  # Docker Desktop exposes Windows bind sources through either representation.
  for prefix in ('/run/desktop/mnt/host/','/host_mnt/'):
   if value.startswith(prefix):
    value=value[len(prefix):];value=value[0]+':/'+value[2:]
  return value.casefold() if re.match(r'^[a-zA-Z]:/',value) else value
 if bool(actual.get('RW')) == bool(expected.get('read_only',True)): return False
 source=normalized(expected['source']); root=normalized(actual.get('Source',''))
 target=PurePosixPath(expected['target']); dest=PurePosixPath(actual.get('Destination',''))
 if source==root and target==dest:return True
 if subpaths and target.is_relative_to(dest):
  relative=target.relative_to(dest).as_posix()
  return source==normalized(root+'/'+relative)
 return False
CLI_SCHEMA = {'type':'object','properties':{'command':{'type':'string'},'input':{'type':'object','additionalProperties':True}},'required':['command'],'additionalProperties':False}
RUNNER = r'''
import json,sys,subprocess,tempfile,time,os,signal,shutil
p=json.load(sys.stdin)
with tempfile.TemporaryFile() as out,tempfile.TemporaryFile() as err,tempfile.TemporaryFile() as inp:
 inp.write(json.dumps(p['input'],ensure_ascii=False).encode()); inp.seek(0)
 exe=shutil.which(p['argv'][0])
 if not exe: print(json.dumps({'error':'CLI_NOT_FOUND'})); sys.exit(0)
 proc=subprocess.Popen([exe,*p['argv'][1:]],cwd=p['cwd'],stdin=inp,stdout=out,stderr=err,start_new_session=True,env={**os.environ,**p['env']})
 deadline=time.monotonic()+p['timeout']; reason=None
 while proc.poll() is None:
  if time.monotonic()>deadline: reason='CLI_TIMEOUT'; break
  if max(os.fstat(out.fileno()).st_size,os.fstat(err.fileno()).st_size)>1048576: reason='CLI_OUTPUT_LIMIT'; break
  time.sleep(.03)
 if reason:
  try: os.killpg(proc.pid,signal.SIGKILL)
  except ProcessLookupError: pass
  try: proc.wait(timeout=3)
  except subprocess.TimeoutExpired:
   print(json.dumps({'error':'CLI_TERMINATION_FAILED','terminated':False}));sys.exit(0)
 out.seek(0); raw=out.read(1048577)
 if len(raw)>1048576: reason='CLI_OUTPUT_LIMIT'
 if reason: print(json.dumps({'error':reason,'terminated':proc.poll() is not None})); sys.exit(0)
 if proc.returncode: print(json.dumps({'error':'CLI_EXIT_ERROR','exit_code':proc.returncode})); sys.exit(0)
 try: value=json.loads(raw.decode('utf-8'))
 except (ValueError,UnicodeError): print(json.dumps({'error':'CLI_OUTPUT_INVALID'})); sys.exit(0)
 print(json.dumps({'data':value},ensure_ascii=False))
'''
class ExternalCli:
 def __init__(self,store):
  self.store=store
  for row in store.list('cli_services'):
   if row.get('execution_target')!='docker-local': store.put('cli_services',{**row,'enabled':False,'migration_required':True},row['id'])
 def save(self,data,id=None,preview=False):
  old=self.store.get('cli_services',id) if id else {}
  if data.get('execution_target',old.get('execution_target','docker-local'))!='docker-local' or any(k in data for k in ('docker_host','context','ssh','privileged')):
   raise Problem('CLI_REMOTE_NOT_IMPLEMENTED','仅支持本机 Docker CLI，宿主机和远程不可用',501)
  container=data.get('container',old.get('container',''))
  if not isinstance(container,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}',container): raise Problem('PARAMETER_INVALID','请指定本机容器名称或 ID')
  executable=data.get('executable',old.get('executable',''))
  directory=data.get('working_directory',old.get('working_directory','/app'))
  if not executable or executable.startswith('-') or any(c in executable for c in ('\0','\n')) or not directory.startswith('/') or '..' in directory.split('/'): raise Problem('CLI_PATH_INVALID','程序为容器内路径或 PATH 名称；目录为容器内绝对路径')
  commands=data.get('commands',old.get('commands',{}))
  if not isinstance(commands,dict) or not commands or len(commands)>100: raise Problem('PARAMETER_INVALID','需要子命令到固定 argv 的 JSON 映射')
  for name,argv in commands.items():
   if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',name) or not isinstance(argv,list) or len(argv)>32 or any(not isinstance(v,str) or '\0' in v for v in argv): raise Problem('PARAMETER_INVALID','子命令映射无效')
  fixed=data.get('arguments',old.get('arguments',[]))
  if not isinstance(fixed,list) or any(not isinstance(v,str) or '\0' in v for v in fixed): raise Problem('PARAMETER_INVALID','固定参数需为字符串数组')
  timeout=int(data.get('timeout',old.get('timeout',30)))
  if not 1<=timeout<=120: raise Problem('PARAMETER_INVALID','超时须为 1–120 秒')
  mounts=data.get('mounts',old.get('mounts',[]))
  if not isinstance(mounts,list) or any(not isinstance(m,dict) or set(m)-{'source','target','read_only'} or not m.get('source') or not m.get('target','').startswith('/') for m in mounts): raise Problem('PARAMETER_INVALID','挂载声明需 source、target、read_only')
  runner=data.get('runner_python',old.get('runner_python','python3'))
  if not isinstance(runner,str) or not runner or runner.startswith('-') or '\0' in runner: raise Problem('PARAMETER_INVALID','容器需 Python3 runner')
  row=dict(name=text(data.get('name',old.get('name')),'CLI 名称',80),description=text(data.get('description',old.get('description','Docker CLI')),'用途',2000),container=container,executable=executable,working_directory=directory,arguments=fixed,commands=commands,mounts=mounts,timeout=timeout,runner_python=runner,execution_target='docker-local',enabled=bool(data.get('enabled',old.get('enabled',True))),migration_required=False,revision=old.get('revision',0)+1,source_url=data.get('source_url',old.get('source_url','')))
  schema={**CLI_SCHEMA,'properties':{**CLI_SCHEMA['properties'],'command':{'type':'string','enum':list(commands)}}}
  command_schemas=data.get('command_schemas',old.get('command_schemas',{}))
  if command_schemas:
   if not isinstance(command_schemas,dict) or set(command_schemas)!=set(commands): raise Problem('PARAMETER_INVALID','command_schemas须覆盖全部固定命令')
   for spec in command_schemas.values(): check_schema(spec)
   row['command_schemas']=command_schemas
   schema['oneOf']=[{'properties':{'command':{'const':name},'input':spec},'required':['input']} for name,spec in command_schemas.items()]
  row['tools']=[dict(name='execute',description=row['description'],input_schema=schema,output_schema=True)]
  if preview: return row
  return self.store.put('cli_services',row,id or uid())
 def docker(self):
  import docker
  return docker.DockerClient(base_url=LOCAL_ENGINE,timeout=5)
 def containers(self):
  try:
   with closing(self.docker()) as client: return [dict(id=c.id,name=c.name,status=c.status,mounts=c.attrs.get('Mounts',[])) for c in client.containers.list()]
  except Exception: raise Problem('DOCKER_ENGINE_UNAVAILABLE','无法连接本机 Docker Engine；请检查 Docker Desktop 和本机 socket，未尝试启动或构建容器',409)
 def call(self,id,name,arguments,revision,run_id):
  row=self.store.get('cli_services',id)
  if row.get('execution_target')!='docker-local' or row.get('migration_required'): raise Problem('CLI_MIGRATION_REQUIRED','旧宿主机 CLI 须迁移到本机 Docker',409)
  if not row['enabled'] or row['revision']!=revision: raise Problem('BINDING_STALE','CLI 已停用或修订',409)
  if name!='execute': raise Problem('TOOL_NOT_FOUND','CLI 只有 execute 一个工具')
  validate(arguments,row['tools'][0]['input_schema'])
  return self.run(row,arguments['command'],arguments.get('input',{}),run_id)
 def run(self,row,command,input,run_id):
  from docker.errors import NotFound
  try:
   with closing(self.docker()) as client:
    container=client.containers.get(row['container'])
    if container.status!='running': raise Problem('CLI_CONTAINER_STOPPED','容器未运行',409)
    actual=container.attrs.get('Mounts',[])
    if not actual: raise Problem('CLI_MOUNT_REQUIRED','CLI 容器必须具有挂载',409)
    for m in row['mounts']:
     if not any(mount_matches(a,m,row.get('mount_subpaths',False)) for a in actual): raise Problem('CLI_MOUNT_CHANGED','实际挂载与登记不一致',409)
    if container.attrs.get('Platform','linux')!='linux': raise Problem('CLI_PLATFORM_UNSUPPORTED','当前 runner 仅支持 Linux 容器',501)
    container_id=container.id
  except Problem: raise
  except NotFound: raise Problem('DOCKER_CONTAINER_NOT_FOUND','登记的 Docker 容器不存在；请核对运行时配置和部署',409)
  except Exception: raise Problem('DOCKER_ENGINE_UNAVAILABLE','无法连接或检查本机 Docker Engine；请检查 Docker Desktop 和本机 socket',409)
  docker_path=docker_executable()
  payload=dict(argv=[row['executable'],*row['arguments'],*row['commands'][command]],cwd=row['working_directory'],input=input,timeout=row['timeout'],env={'PLATFORM_TASK_ID':run_id,'PLATFORM_REQUEST_ID':digest([run_id,row['name'],command,input]),'PYTHONUTF8':'1'})
  raw=json.dumps(payload,ensure_ascii=False).encode()
  if len(raw)>1048576: raise Problem('CLI_INPUT_LIMIT','输入超过 1 MiB')
  hold_id=uid()
  hold={'run_id':run_id,'state':'executing','until':time.time()+row['timeout']+8}
  self.store.put('execution_holds',hold,hold_id)
  try:
   p=subprocess.run([docker_path,'--host',LOCAL_ENGINE,'exec','-i',container_id,row['runner_python'],'-c',RUNNER],input=raw,capture_output=True,timeout=row['timeout']+8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
   if p.returncode:
    self.store.put('execution_holds',{**hold,'state':'uncertain'},hold_id)
    raise Problem('CLI_RESULT_UNKNOWN','Docker 连接失败；动作结果需核对',502)
   result=json.loads(p.stdout)
  except subprocess.TimeoutExpired:
   self.store.put('execution_holds',{**hold,'state':'uncertain'},hold_id)
   raise Problem('CLI_RESULT_UNKNOWN','未确认容器执行结束；需核对结果',504)
  except (ValueError,OSError):
   self.store.put('execution_holds',{**hold,'state':'uncertain'},hold_id)
   raise Problem('CLI_RESULT_UNKNOWN','无有效容器回执；结果需核对',502)
  self.store.put('execution_holds',{**hold,'state':'uncertain' if result.get('error')=='CLI_TERMINATION_FAILED' else 'exited'},hold_id)
  if result.get('error'): raise Problem(result['error'],'容器 CLI 失败；超时动作需核对回执',504 if result['error']=='CLI_TIMEOUT' else 502)
  return result['data']
 def discover(self,id):
  row=self.store.get('cli_services',id)
  if 'help' not in row.get('commands',{}): return {'tools':row['tools'],'commands':row['commands'],'note':'未配置 help；目录来自固定命令声明'}
  return {'tools':row['tools'],'help':self.run(row,'help',{},'administrator-discovery')}
