/* 能力绑定控件：类别与来源分开选择，授权工具使用普通复选框。
 * 注册名称只用于显示；提交仍使用稳定来源 ID 与原始工具名称，后端负责授权校验。 */

// 按来源保留尚未提交的勾选；自动刷新不会清空选择，也不会把一个来源的权限带给另一个。
const bindingToolDrafts = new Map();
const bindingProviderCache = new Map();
async function bindingProviderDetails(source,revision) {
  const key=source+':'+revision;
  if(!bindingProviderCache.has(key))bindingProviderCache.set(key,api('tool-providers/'+source).then(value=>value.kind==='http'?{...value,tools:[{name:'call',description:value.description,input_schema:value.input_schema}]}:value).catch(error=>{bindingProviderCache.delete(key);throw error;}));
  return bindingProviderCache.get(key);
}


/** 类别切换后重建来源目录；外部注册名称来自平台 overview，表格仅保留待开发入口。 */
function renderBindingSources() {
  const category = byId('binding-category').value;
  const select = byId('binding-source');
  const previous = select.value;
  const rows = category === 'feishu' ? [{id:'builtin', name:'飞书'}] : category === 'external' ? [
    ...state.mcps.map(provider => ({id:provider.id, name:'MCP · ' + provider.name, available:provider.healthy})),
    ...(state.cli_services || []).map(provider => ({id:'cli:' + provider.id, name:'CLI · ' + provider.name, available:provider.enabled})),
    ...(state.http_services || []).map(provider => ({id:'http:' + provider.id, name:'HTTP · ' + provider.name, available:provider.enabled}))
  ] : category === 'skill' ? (state.tool_providers || []).filter(p => p.kind === 'skill').map(p => ({id:p.id, name:p.name, available:p.enabled})) : [];
  select.replaceChildren();
  for (const row of rows) {
    const option = document.createElement('option'); option.value = row.id;
    option.textContent = row.name + (row.available === false ? '（未就绪）' : '');
    option.disabled = row.available === false; select.append(option);
  }
  const chosen = rows.find(row => row.id === previous && row.available !== false) || rows.find(row => row.available !== false);
  select.value = chosen?.id || '';
  byId('binding-source-label').hidden = !['external', 'skill'].includes(category);
  renderToolChoices();
}

/** 根据实际来源生成工具复选框；无工具时禁止提交，避免形成无意义或错误的授权。 */
function renderToolChoices() {
  const category = byId('binding-category').value;
  const source = byId('binding-source').value;
  byId('binding-connection-label').hidden = category !== 'feishu';
  byId('binding-targets-label').hidden = category !== 'feishu';
  byId('binding-job-label').hidden = category !== 'skill';
  const skill = (state.skills || []).find(s => 'skill:' + s.id === source);
  const workflowScope = skill?.metadata?.metadata?.['context-scope'] === 'workflow';
  const sourceScope = workflowScope || skill?.metadata?.metadata?.['context-scope'] === 'source';
  const scopeInput = byId('binding-job-label').querySelector('input');
  const scopeName = sourceScope ? 'source_id' : 'job_id';
  if (scopeInput.name !== scopeName) scopeInput.value = '';
  scopeInput.name = scopeName;
  byId('binding-scope-name').textContent = workflowScope ? '可信工作流采集源 ID（作业由 Main 绑定）' : sourceScope ? '可信采集源 ID（独立于作业票）' : '可信作业 ID（业务 Skill）';
  let tools = [];
  if (category === 'feishu') {
    tools = (state.functions.find(item => item.id === 'feishu.messaging.v1')?.tools || []).map(name => ({
      name, description:(state.cli_commands || []).find(item => item.name === name)?.description || ''
    }));
  } else if (category === 'skill') {
    const provider = (state.tool_providers || []).find(p => p.id === source);
    if (provider?.enabled) tools = provider.tools;
  } else if (category === 'external' && source.startsWith('cli:')) {
    const provider = (state.cli_services || []).find(item => 'cli:' + item.id === source);
    if (provider?.enabled) tools = provider.tools;
  } else if (category === 'external' && source.startsWith('http:')) {
    const provider = (state.http_services || []).find(item => 'http:' + item.id === source);
    if (provider?.enabled) tools = [{name:'call', description:provider.description}];
  } else if (category === 'external') {
    const provider = state.mcps.find(item => item.id === source);
    if (provider?.healthy) tools = provider.tools.filter(tool => tool.usable);
  }
  if(builtinCatalog && ['feishu','collaboration'].includes(category))tools=tools.map(tool=>({...tool,...builtinCatalog.find(full=>full.name===tool.name)}));
  const providerSummary=(state.tool_providers||[]).find(p=>p.id===source);
  const detailKey=source+':'+providerSummary?.revision;
  const loaded=bindingProviderCache.get(detailKey);
  if(loaded && !(loaded instanceof Promise))tools=loaded.tools;
  const selected = bindingToolDrafts.get(source) || new Set();
  // 协作权限由“谁可以调用谁”的有方向授权决定，不复制成另一套能力绑定。
  const collaboration = category === 'collaboration';
  const grants = (state.agent_grants || []).filter(grant => grant.enabled && grant.caller_agent_id === byId('binding-agent').value);
  const requiredPermissions = {submit_task:['submit'],list_tasks:['status'],get_task_status:['status'],get_task_result:['result'],wait_tasks:['status','result'],cancel_task:['cancel'],agent_status:['overview']};
  Object.assign(requiredPermissions,{set_heartbeat_schedule:['schedule'],get_heartbeat_schedule:['schedule'],control_heartbeat_schedule:['schedule']});
  if (collaboration) tools = (state.cli_commands || []).filter(tool => Object.hasOwn(requiredPermissions, tool.name));
  // 被服务器移除的工具不能继续保留在草稿或提交数据中。
  const available = new Set(tools.map(tool => tool.name));
  for (const name of selected) if (!available.has(name)) selected.delete(name);
  bindingToolDrafts.set(source, selected);
  const container = byId('binding-tools'); container.replaceChildren();
  for (const tool of tools) {
    const label = document.createElement('label'); label.className = 'tool-checkbox';
    const input = document.createElement('input'); input.type = 'checkbox'; input.value = tool.name;
    input.checked = collaboration ? grants.some(grant => requiredPermissions[tool.name].every(permission => grant.permissions.includes(permission))) : selected.has(tool.name);
    input.disabled = collaboration;
    input.onchange = () => {if (input.checked) selected.add(tool.name); else selected.delete(tool.name);};
    const text = document.createElement('span');
    const name = document.createElement('strong'); name.textContent = tool.name; text.append(name);
    if (tool.description) {const description = document.createElement('small'); description.textContent = tool.description; text.append(description);}
    label.append(input, text); container.append(label);
    const schema=tool.input_schema||tool.inputSchema;
    if(schema){const more=document.createElement('details'),title=document.createElement('summary'),pre=document.createElement('pre');title.textContent='参数定义';pre.textContent=pretty(schema);more.append(title,pre);container.append(more);}
  }
  if(['feishu','collaboration'].includes(category) && !builtinCatalog)container.append(button('查看平台工具说明与参数',async()=>{builtinCatalog=(await api('cli/catalog')).commands;renderToolChoices();}));
  if(providerSummary && tools.length && !(loaded && !(loaded instanceof Promise))) {
    container.append(button('查看工具说明与参数',async()=>{
      const detail=await bindingProviderDetails(source,providerSummary.revision);
      bindingProviderCache.set(detailKey,detail);
      if(byId('binding-source').value===source)renderToolChoices();
    }));
  }
  if (collaboration) {
    const configure = document.createElement('button'); configure.type = 'button'; configure.textContent = '配置本 Agent 的协作授权';
    configure.onclick = () => {byId('grant-caller').value = byId('binding-agent').value; document.querySelector('nav button[data-tab="collaboration"]').click();};
    container.append(configure);
  }
  byId('binding-save').hidden = collaboration;
  byId('binding-save').disabled = collaboration || !tools.length;
  byId('binding-tools-hint').textContent = collaboration
    ? '协作任务表 CLI 已实现。勾选表示当前 Agent 已具备相应权限；请在协作授权中指定子 Agent 和操作权限。实际调用还会校验具体对象与任务范围。'
    : category === 'spreadsheet'
    ? 'Excel/CSV 文件编辑待开发；协作任务表与子 Agent 调度已经实现，不依赖此功能。'
    : tools.length ? '点击工具行即可勾选或取消；保存后授权生效。'
    : '暂无可授权工具；请先注册并发现外部 MCP 工具，或启用 HTTP 直连定义。';
}
