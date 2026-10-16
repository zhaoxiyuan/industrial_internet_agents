"""Authorized read-only Skill projection consumed by official SkillsMiddleware."""
import shutil
from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.tools import StructuredTool
from .common import Problem, digest
class SkillBackend(FilesystemBackend):
 def __init__(self,service,agent):
  self.service,self.agent_id=service,agent['id']
  active=[service.store.get('skills',sid) for sid in agent['skill_ids'] if service.store.get('skills',sid)['enabled']]
  self.by_name={s['name']:s['id'] for s in active}
  root=service.data/'skill_views'/digest([agent['id'],[(s['id'],s['digest']) for s in active]])
  for s in active:
   for relative in s['files']:
    source=service.skills.resource(s['id'],relative)
    target=root/'skills'/s['name']/relative
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists(): shutil.copyfile(source,target)
  root.mkdir(parents=True,exist_ok=True)
  super().__init__(root_dir=root,virtual_mode=True)
 def authorize(self,path):
  if '..' in path.split('/') or '\\' in path: raise Problem('PATH_INVALID','Skill 路径越界')
  parts=path.strip('/').split('/')
  if len(parts)<2 or parts[0]!='skills' or parts[1] not in self.by_name: raise Problem('TOOL_FORBIDDEN','只允许已绑定 Skill 资源',403)
  sid=self.by_name[parts[1]]; agent=self.service.store.get('agents',self.agent_id)
  if sid not in agent['skill_ids'] or not self.service.store.get('skills',sid)['enabled']: raise Problem('BINDING_DISABLED','Skill 已解绑或停用',409)
 def read(self,file_path,offset=0,limit=100):
  self.authorize(file_path)
  return super().read(file_path,offset,limit)
 def download_files(self,paths):
  for path in paths: self.authorize(path)
  return super().download_files(paths)
 def tool(self):
  def read_file(file_path:str,offset:int=0,limit:int=1000):
   return self.read(file_path,offset,min(limit,2000))
  return StructuredTool.from_function(read_file,name='read_file',description='只读已绑定 Skill 的正文和参考文件；不能读取平台配置或执行脚本。')
