/* Type-specific provider registration, unified catalog preview and binding. */
function switchExternalAccess() {
 const type=byId('external-access-type').value;
 for(const [id,key] of [['mcp-form','mcp'],['external-cli-form','cli'],['http-form','http']]) byId(id).hidden=type!==key;
}
function editExternalCli(provider) {
 byId('external-access-type').value='cli';switchExternalAccess();
 const form=byId('external-cli-form');form.reset();form.elements.id.value=provider?.id||'';
 if(provider) for(const key of ['name','description','container','executable','working_directory','runner_python','timeout','source_url']) if(provider[key]!==undefined) form.elements[key].value=provider[key];
 for(const key of ['arguments','commands','mounts']) form.elements[key].value=pretty(provider?.[key]??(key==='commands'?{status:['status']}:[]));
}
function renderExternalCli() {
 const box=byId('external-cli-list');box.replaceChildren();
 for(const p of state.cli_services||[]) {
  const row=document.createElement('div');row.className='item';
  const info=document.createElement('p');info.textContent=`${p.name} · ${p.container||'待迁移'} · ${p.executable} · 修订 ${p.revision} · ${p.migration_required?'旧定义不可执行':p.enabled?'启用':'停用'}`;row.append(info);
  row.append(button('编辑',async()=>editExternalCli(p)),button('删除',async()=>api('cli-services/'+p.id,'DELETE'),true));box.append(row);
 }
 renderProviderCatalog();
}
function renderProviderCatalog() {
 const box=byId('provider-catalog');box.replaceChildren();
 for(const p of state.tool_providers||[]) {
  const row=document.createElement('div');row.className='item';
  const info=document.createElement('p');info.textContent=`${p.kind} · ${p.name} · 修订 ${p.revision} · ${p.tools?.length||1} 项工具`;row.append(info);
  row.append(button('发现 / 查看目录',async()=>{
   const op=await api('tool-providers/'+p.id+'/discover','POST',{});accepted(op);
   let current=op;
   for(let n=0;n<80&&['queued','running'].includes(current.status);n++){await new Promise(r=>setTimeout(r,500));current=await api('operations/'+op.id);}
   if(current.status!=='succeeded')throw Error(current.error?.message||'发现仍在运行，请查看操作记录');
   const pre=document.createElement('pre');pre.textContent=pretty(current.result);row.append(pre);
   row.append(button('确认该目录修订',async()=>{await api('tool-providers/'+p.id+'/confirm','POST',{candidate_id:current.result.candidate_id});notice('目录已确认，请重新确认 Agent 绑定');}));
  }),button('导出无密钥定义',async()=>{byId('provider-import').value=pretty(await api('tool-providers/'+p.id+'/export'));}));box.append(row);
 }
}
byId('external-access-type').onchange=switchExternalAccess;
byId('external-cli-new').onclick=()=>editExternalCli();
byId('external-cli-form').onsubmit=event=>{event.preventDefault();act(async()=>{
 const data=values(event.target),id=data.id;delete data.id;
 data.kind='cli';data.execution_target='docker-local';data.timeout=Number(data.timeout);
 for(const key of ['commands','arguments','mounts'])data[key]=JSON.parse(data[key]);
 const saved=await api('tool-providers'+(id?'/cli:'+id:''),id?'PATCH':'POST',data);
 event.target.elements.id.value=saved.provider_id;notice('Docker CLI 已登记；保存未执行命令，请绑定 execute 工具。');
},event.submitter);};
byId('docker-containers').onclick=()=>act(async()=>{byId('docker-container-info').textContent=pretty(await api('docker/containers'));});
let previewedDefinition=null;
byId('provider-import').oninput=()=>{previewedDefinition=null;byId('provider-save-import').disabled=true;};
byId('provider-preview').onclick=()=>act(async()=>{
 const result=await api('tool-providers/import','POST',JSON.parse(byId('provider-import').value));previewedDefinition=result.definition;
 byId('provider-preview-result').textContent=pretty(result);byId('provider-save-import').disabled=false;
});
byId('provider-save-import').onclick=()=>act(async()=>{if(!previewedDefinition)throw Error('请先预览');await api('tool-providers','POST',previewedDefinition);notice('定义已保存，请发现目录并授权');byId('provider-save-import').disabled=true;});
switchExternalAccess();

byId('openapi-preview').onclick=()=>act(async()=>{
 const result=await api('tool-providers/openapi','POST',{document:JSON.parse(byId('openapi-import').value)});
 const box=byId('openapi-candidates');box.replaceChildren();
 for(const definition of result.definitions)box.append(button('选择 '+definition.name,async()=>{byId('provider-import').value=pretty(definition);previewedDefinition=null;byId('provider-save-import').disabled=true;}));
});

async function discoverProvider(id,row){
 const op=await api('tool-providers/'+id+'/discover','POST',{});accepted(op);let current=op;
 for(let n=0;n<80&&['queued','running'].includes(current.status);n++){await new Promise(r=>setTimeout(r,500));current=await api('operations/'+op.id);}
 if(current.status!=='succeeded')throw Error(current.error?.message||'发现仍在运行，请查看操作记录');
 const pre=document.createElement('pre');pre.textContent=pretty(current.result);row.append(pre);
 row.append(button('确认该目录修订',async()=>{await api('tool-providers/'+id+'/confirm','POST',{candidate_id:current.result.candidate_id});notice('目录已确认，请重新确认 Agent 绑定');}));
}
