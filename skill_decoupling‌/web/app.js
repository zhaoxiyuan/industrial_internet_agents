/* 单页管理界面：请求与后台任务分离，任何耗时操作先返回 operationId。
 * 所有动态文本使用 escapeHtml/textContent，不执行 Skill 或远端 MCP 返回的 HTML。
 */
let state = {}, refreshing = false, builtinCatalog = null;
let heartbeatDraft = '';
let runtimeSource=null, liveReply=null, messageLoadVersion=0, displayedSession='', messageSignature='';
// 回复目标同时记录连接与事件，切换连接时清空，防止旧请求返回后串到新账号。
let replyConnection = "", eventLoadVersion = 0;
/** 按稳定页面 ID 获取控件，所有 ID 均由本项目 HTML 定义。 */
const byId = id => document.getElementById(id);
/** 转义动态字符串用于受控模板；不能把远端名称直接拼入 HTML。 */
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
/** 格式化配置和审计结果，只用于显示，不作为代码执行。 */
const pretty = value => JSON.stringify(value, null, 2);

/** 显示一次动作结果；不遮挡页面，也不禁用其他模块。 */
function notice(message, error = false) {
  byId('notice').textContent = message;
  byId('notice').className = error ? 'error' : 'success';
}

/** 管理 API 都是短请求；普通请求20秒，较大的概览请求60秒超时只终止客户端等待，不表示后台操作已取消。 */
async function api(path, method = 'GET', body) {
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), path === 'overview' ? 60000 : 20000);
  try {
    const reply = await fetch('/api/' + path, {method, signal:controller.signal,
      headers:{'Content-Type':'application/json'}, body:body === undefined ? undefined : JSON.stringify(body)});
    const data = await reply.json();
    if (!reply.ok) throw Error((data.error?.code || reply.status) + '：' + (data.error?.message || '操作失败'));
    return data;
  } finally { clearTimeout(timeout); }
}

/** 从表单读取字符串；JSON、数组和布尔类型由具体提交函数转换。 */
function values(form) { return Object.fromEntries(new FormData(form).entries()); }

/** 统一表单按钮状态与错误展示。await 不阻塞浏览器事件循环。 */
async function act(work, button) {
  if (button) button.disabled = true;
  try { const result = await work(); await refresh(); return result; }
  catch (error) { notice(error.name === 'AbortError' ? '请求超时，请查看后台操作记录。' : error.message, true); }
  finally { if (button) button.disabled = false; }
}

/** 小文件以 base64 上传，不读取与当前表单无关的本机文件。 */
async function encodeFile(file) {
  if (file.size > 20 * 1024 * 1024) throw Error('文件不能超过 20MB');
  return await new Promise((resolve, reject) => { const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',')[1]); reader.onerror = reject; reader.readAsDataURL(file); });
}

/** 更新下拉框时保留用户当前选择；后台轮询不能重置正在填写的表单。 */
function options(id, rows, empty) {
  const select = byId(id), chosen = [...select.selectedOptions].map(x => x.value);
  select.innerHTML = (empty === undefined ? '' : `<option value="">${escapeHtml(empty)}</option>`) + rows.map(row =>
    `<option value="${escapeHtml(row.id)}">${escapeHtml(row.name)}</option>`).join('');
  for (const option of select.options) option.selected = chosen.includes(option.value);
  if (!select.multiple && !chosen.some(value => [...select.options].some(o => o.value === value))) select.selectedIndex = 0;
}

/** 用事件绑定替代拼接 onclick；远端名称只作为文本显示。 */
function button(label, work, danger = false) {
  const element = document.createElement('button'); element.type = 'button'; element.textContent = label;
  element.className = danger ? 'danger' : 'secondary';
  element.onclick = () => act(work, element); return element;
}

/** 把受理状态显示给用户，完成结果在操作记录页持续更新。 */
function accepted(operation) { notice(operation.task_id ? '任务已受理：'+operation.task_id+'，在“协作任务”查看进度。' : '操作已受理：'+operation.kind+'，在“操作记录”查看结果。'); }

/** 刷新静态对象不触发网络探测；重入保护避免定时器堆积请求。 */
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try { state = await api('overview'); byId('health').textContent = state.scheduler?.error ? '调度需检查' : '平台在线'; render(); await loadMessages(); }
  finally { refreshing = false; }
}

/** 各模块列表只反映本模块生命周期；Skill 列表不出现服务启停按钮。 */
function render() {
  const container = byId('skills-list'); container.replaceChildren();
  for (const skill of state.skills) {
    const row = document.createElement('div'); row.className = 'item';
    row.innerHTML = `<b>${escapeHtml(skill.name)}</b> <span class="badge">${skill.enabled?'可加载':'已停用'}</span><p>${escapeHtml(skill.description)}</p><small>${escapeHtml(skill.digest)}</small><br>`;
    row.append(button('查看内容', async () => {const detail=await api('skills/'+skill.id);byId('skill-preview').textContent = detail.body + '\n\n资源：\n' + detail.files.join('\n');}),
      button(skill.enabled?'停用 Skill':'启用 Skill', async () => {await api('skills/'+skill.id,'PATCH',{enabled:!skill.enabled});notice('Skill 状态已更新，服务状态独立保留。');}),
      button('删除', async () => {if (confirm('删除 Skill 注册？已绑定时会拒绝删除。')) await api('skills/'+skill.id,'DELETE');}, true));
    container.append(row);
  }
  options('connection-edit', state.connections, '新建连接');
  for (const id of ['send-connection','events-connection','binding-connection']) options(id, state.connections);
  renderConnections(); renderMcps();
  options('agent-edit', state.agents, '新建 Agent');
  options('inbound-agent',state.agents,'仅接收，不自动执行');
  for (const id of ['binding-agent','chat-agent']) options(id, state.agents);
  options('agent-skills', state.skills);
  renderBindingSources(); renderAgents();
  renderSchedules();
  renderSessionOptions();
  renderSessionControls();
  renderBuiltinCatalog();
  renderOperations();
  if (typeof renderExternalCli === 'function') renderExternalCli();
  if (typeof renderCollaboration === 'function') renderCollaboration();
  if (typeof renderTopology === 'function') renderTopology();
}

/** 连接卡片展示 env 路径、密钥配置状态和回调入口，不展示敏感值。 */
function renderConnections() {
  const list = byId('connections-list'); list.replaceChildren();
  for (const c of state.connections) {
    const row = document.createElement('div'); row.className='item';
    row.innerHTML=`<b>${escapeHtml(c.name)}</b> · ${c.enabled?'启用':'停用'}<p>App Secret：${c.app_secret_configured?'已配置':'未配置'}；Token：${c.verification_token_configured?'已配置':'未配置'}；Encrypt Key：${c.encrypt_key_configured?'已配置':'未配置'}</p><p class="muted">${escapeHtml(c.env_file)}<br>回调：${escapeHtml(location.origin+c.webhook_path)}</p>`;
    row.append(button('测试连通', async()=>accepted(await api('connections/'+c.id+'/test','POST',{}))),
      button('编辑', async()=>{byId('connection-edit').value=c.id; editConnection();}),
      button('查询群目录', async()=>accepted(await api('connections/'+c.id+'/lookup','POST',{entity_type:'group'}))));
    const closeBox=document.createElement('details');
    closeBox.innerHTML='<summary>人工决定关闭作业</summary><p>关闭会停止本作业调度并释放当前票；未解决风险保留记录，不视为已恢复。</p><label>作业票号<input class="close-job-id" required></label><label>决定人<select class="close-job-actor"></select></label><label>关闭理由<input class="close-job-reason" required></label>';
    const people=closeBox.querySelector('.close-job-actor');
    for(const [id,user] of Object.entries(c.users||{})){const option=document.createElement('option');option.value=id;option.textContent=user.name||id;people.append(option);}
    closeBox.append(button('确认人工关闭',async()=>{
      const job_id=closeBox.querySelector('.close-job-id').value.trim(),actor=people.value,reason=closeBox.querySelector('.close-job-reason').value.trim();
      if(!job_id||!actor||!reason)throw new Error('请填写票号、决定人和关闭理由');
      accepted(await api('connections/'+c.id+'/confirm-job-closed','POST',{job_id,actor,reason,mode:'human_override'}));
    }));
    row.append(closeBox);
    list.append(row);
  }
}

/** MCP 卡片同时展示协议就绪和工具 Schema；修改发现修订后需重新确认绑定。 */
function renderMcps() {
  const list=byId('mcp-list');list.replaceChildren();
  for(const m of state.mcps){
    const row=document.createElement('div');row.className='item';
    row.innerHTML=`<b>${escapeHtml(m.name)}</b> · ${'HTTP/HTTPS 接入'} · ${escapeHtml(m.status)}<p>${escapeHtml(m.endpoint)}</p><p class="muted">工具修订 ${m.revision}；数据目录 ${escapeHtml(m.storage_dir||'平台数据库保存调用记录')}<br>${escapeHtml(m.last_error?.message||'')}</p>`;
    row.append(button('发现 / 预览目录',async()=>discoverProvider('mcp:'+m.id,row)));
    row.append(button('编辑定义', async () => editMcp(m)), button('删除定义',async()=>{if(confirm('删除注册？服务数据将保留。'))await api('mcps/'+m.id,'DELETE');},true));
    for(const t of m.tools){
      const detail=document.createElement('details'), title=document.createElement('summary');
      title.textContent=t.name+' · '+(t.usable?'可绑定':'Schema 暂不支持');detail.append(title);
      const body=document.createElement('div');detail.append(body);
      detail.ontoggle=async()=>{if(!detail.open||body.childElementCount)return;try{const provider=await bindingProviderDetails('mcp:'+m.id,m.revision);const tool=provider.tools.find(x=>x.name===t.name);const description=document.createElement('p');description.textContent=tool.description;const pre=document.createElement('pre');pre.textContent=pretty(tool.inputSchema);body.append(description,pre);}catch(error){body.textContent=error.message;}};
      row.append(detail);
    }
    list.append(row);
  }
}

/** 编辑定义保留服务身份；服务部署和进程生命周期由独立模块负责。
 * Token 不回显，留空表示保留；保存修改后需要重新发现目录和确认绑定。
 */
function editMcp(provider) {
  byId('external-access-type').value = 'mcp';
  if (typeof switchExternalAccess === 'function') switchExternalAccess();
  const form = byId('mcp-form');
  form.reset();
  form.elements.id.value = provider?.id || '';
  if (provider) {
    for (const key of ['name', 'description', 'endpoint', 'source_url', 'protocol']) {
      form.elements[key].value = provider[key] || '';
    }
  }
}

/** 能力来源切换后显示对应工具；只保留当前用户仍可选择的选项。 */
// 工具来源与复选框渲染由 binding-controls.js 维护，业务提交仍走同一绑定接口。

/** Agent 卡片展示 Skill 数量和模型状态；测试模型不执行业务工具。 */
function renderAgents(){
  const list=byId('agents-list');list.replaceChildren();
  for(const a of state.agents){const row=document.createElement('div');row.className='item';row.innerHTML=`<b>${escapeHtml(a.name)}</b> · ${escapeHtml(a.model||'未配置模型')}<p>Skill ${a.skill_ids.length} 个；密钥${a.key_configured?'已配置':'未配置'}；执行目标：platform-local</p>`;
    const contract=document.createElement('details');const heading=document.createElement('summary');heading.textContent='调用接口说明';contract.append(heading,agentInterfaceDescription(a));row.append(contract);
    row.append(button('编辑',async()=>{byId('agent-edit').value=a.id;editAgent();}),button('测试模型',async()=>accepted(await api('agents/'+a.id+'/test','POST',{}))),button('删除 Agent',()=>deleteAgent(a),true));list.append(row);}
  const bindings=byId('bindings-list');bindings.replaceChildren();
  for(const b of state.bindings){const row=document.createElement('div');row.className='item';row.innerHTML=`<b>${escapeHtml(state.agents.find(a=>a.id===b.agent_id)?.name)}</b><p>${escapeHtml(b.tools.join(', '))} · ${b.enabled?'启用':'停用'}</p>`;
    row.append(button(b.enabled?'暂停绑定':'启用 / 确认新修订',async()=>{await api('bindings/'+b.id,'PATCH',{enabled:!b.enabled});notice('绑定已更新，MCP 与连接没有被启停。');}),
      button('解除绑定',async()=>{await api('bindings/'+b.id,'DELETE');notice('已解除绑定，服务与数据保留。');},true));bindings.append(row);}
}

/** Show the concrete impact before deleting; the server rechecks all blockers atomically. */
async function deleteAgent(agent) {
  const plan=await api('agents/'+agent.id+'/deletion');
  if(!plan.can_delete)throw Error('不能删除 '+agent.name+'：'+plan.blockers.map(b=>b.message).join(' '));
  if(!confirm(`删除 Agent「${agent.name}」？\nID：${agent.id}\n\n将删除 ${plan.bindings} 条工具绑定、${plan.grants} 条协作授权及专属模型密钥。\n保留 ${plan.retained_tasks} 条历史任务、${plan.retained_sessions} 个会话、成果、Skill 与外部服务。\nAgent 定义删除后不可在界面恢复。`))return;
  await api('agents/'+agent.id,'DELETE');
  if(byId('agent-edit').value===agent.id){byId('agent-edit').value='';editAgent();}
  notice('已删除 Agent「'+agent.name+'」及关联绑定/授权；历史记录与业务数据保留。');
}

/** 后台操作和工具回执以完整 JSON 可展开查看；失败状态不统一包装成服务崩溃。 */
function renderOperations(){
  for(const [containerId,rows,endpoint] of [['operations-list',state.operations,'operations'],['runs-list',state.tool_runs,'tool-runs']]) {
    const container=byId(containerId);container.replaceChildren();
    for(const row of [...rows].reverse()) {
      const detail=document.createElement('details'),title=document.createElement('summary'),pre=document.createElement('pre');
      detail.className='item';title.textContent=(row.kind||row.tool)+' · '+(row.status||'')+' · '+row.created;detail.append(title,pre);
      detail.ontoggle=async()=>{if(!detail.open||pre.textContent)return;pre.textContent='正在读取详情…';try{const full=await api(endpoint+'/'+row.id);pre.textContent=pretty(endpoint==='tool-runs'?full.result:full);}catch(error){pre.textContent=error.message;}};
      container.append(detail);
    }
    if(!container.childElementCount)container.textContent=containerId==='runs-list'?'暂无工具调用':'暂无操作';
  }
}

/** 增加一条目录填写行；保留未知元数据，编辑名称和岗位时不会丢失既有扩展字段。 */
function addDirectoryRow(kind, id = '', metadata = {}) {
  const row = document.createElement('div');
  row.className = 'directory-entry';
  row.directoryMetadata = {...metadata};
  const group = kind === 'groups';
  const fields = [['id', group ? 'chat_id' : 'open_id', id],
    ['name', group ? '群名称' : '用户名称', metadata.name || ''],
    [group ? 'description' : 'role', group ? '用途说明（可选）' : '岗位（可选）', metadata[group ? 'description' : 'role'] || '']];
  for (const [field, title, value] of fields) {
    const label = document.createElement('label'), input = document.createElement('input');
    label.textContent = title;
    input.dataset.field = field;
    input.value = value;
    input.placeholder = field === 'id' ? (group ? 'oc_...' : 'ou_...') : title;
    label.append(input);
    row.append(label);
  }
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'secondary';
  remove.textContent = '移除此项';
  remove.onclick = () => row.remove();
  row.append(remove);
  byId(kind + '-editor').append(row);
}

/** 用配置映射还原填写行；空目录保留为空，用户通过“添加”建立新条目。 */
function fillDirectory(kind, mapping) {
  byId(kind + '-editor').replaceChildren();
  for (const [id, metadata] of Object.entries(mapping || {})) addDirectoryRow(kind, id, metadata);
}

/** 将填写行转换为原有 ID→元数据映射，拒绝缺项、重复和群/用户 ID 填反。 */
function readDirectory(kind) {
  const mapping = Object.create(null), group = kind === 'groups';
  for (const row of byId(kind + '-editor').children) {
    const fields = Object.fromEntries([...row.querySelectorAll('input[data-field]')].map(input => [input.dataset.field, input.value.trim()]));
    if (!Object.values(fields).some(Boolean)) continue;
    if (!fields.id || !fields.name) throw Error((group ? '群' : '用户') + '目录需要填写 ID 和名称。');
    if (!fields.id.startsWith(group ? 'oc_' : 'ou_')) throw Error(group ? '群 chat_id 必须以 oc_ 开头；用户 open_id 请填写在用户目录。' : '用户 open_id 必须以 ou_ 开头。');
    if (Object.hasOwn(mapping, fields.id)) throw Error('目录中出现重复 ID：' + fields.id);
    const {id, ...metadata} = fields;
    mapping[id] = {...row.directoryMetadata, ...metadata};
  }
  return mapping;
}

/** 编辑连接时填充非敏感值；密码字段始终清空表示保留原值。 */
function editConnection(){
  const c=state.connections.find(c=>c.id===byId('connection-edit').value),form=byId('connection-form');form.reset();
  form.elements.id.value=c?.id||'';
  if(c)for(const key of ['name','app_id','api_base'])form.elements[key].value=c[key];
  fillDirectory('groups', c?.groups);
  fillDirectory('users', c?.users);
  form.elements.enabled.checked=c?.enabled??true;
  form.elements.inbound_agent_id.value=c?.inbound_agent_id||'';
  byId('env-info').textContent=c?`${c.env_file}；密钥留空保留，保存后立即生效。`:'新连接保存后生成独立 .env。';
}

/** 编辑 Agent 不回显密钥，也不自动改变原能力绑定。 */
function editAgent(){
  const a=state.agents.find(a=>a.id===byId('agent-edit').value),form=byId('agent-form');form.reset();form.elements.id.value=a?.id||'';
  if(a)for(const key of ['name','model','base_url','system_prompt','max_tokens','description','concurrency','timeout','contract_version'])form.elements[key].value=a[key] ?? form.elements[key].value;
  for(const option of byId('agent-skills').options)option.selected=a?.skill_ids.includes(option.value)||false;
  form.elements.enabled.checked=a?.enabled ?? true;
  form.elements.input_schema.value=pretty(a?.input_schema||{type:'object',properties:{},additionalProperties:true});
  form.elements.output_schema.value=a?.output_schema?pretty(a.output_schema):'';
  fillHeartbeat(a?.heartbeat);
  byId('agent-info').textContent=a?'模型密钥'+(a.key_configured?'已配置，留空保留':'尚未配置'):'';
}

function readHeartbeat() {
  return {enabled:byId('heartbeat-enabled').checked, interval_seconds:Number(byId('heartbeat-interval').value),
    description:byId('heartbeat-description').value, input:JSON.parse(byId('heartbeat-input').value),
    overlap_scope:byId('heartbeat-scope').value, timeout_seconds:byId('heartbeat-timeout').value ? Number(byId('heartbeat-timeout').value) : null, first_run:byId('heartbeat-first').value, overlap_policy:byId('heartbeat-policy').value, overlap_key:byId('heartbeat-overlap').value};
}

function fillHeartbeat(value = {}) {
  byId('heartbeat-enabled').checked=value.enabled ?? false;
  byId('heartbeat-interval').value=value.interval_seconds ?? 60;
  byId('heartbeat-description').value=value.description ?? '';
  byId('heartbeat-input').value=pretty(value.input ?? {});
  byId('heartbeat-scope').value=value.overlap_scope ?? 'self';
  byId('heartbeat-timeout').value=value.timeout_seconds ?? '';
  byId('heartbeat-first').value=value.first_run ?? 'after_interval';
  byId('heartbeat-policy').value=value.overlap_policy ?? 'skip';
  byId('heartbeat-overlap').value=value.overlap_key ?? '';
  heartbeatDraft=JSON.stringify(readHeartbeat());
}

function renderSchedules() {
  const list=byId('schedules-list');list.replaceChildren();
  const stamp=value=>value==null?'—':new Date(value*1000).toLocaleString();
  for(const plan of state.schedules || []) {
    const row=document.createElement('div');row.className='item';row.dataset.scheduleId=plan.id;
    const title=document.createElement('b');title.textContent=`${state.agents.find(a=>a.id===plan.agent_id)?.name || plan.agent_id} · ${plan.status}`;row.append(title);
    const info=document.createElement('p');
    info.textContent=`计划 ${plan.id} · 来源 ${plan.source} · 所有者 ${plan.owner_agent_id || '管理员'} · H=${plan.interval_seconds}秒 · 下次触发 ${stamp(plan.next_run_at)} · 最近任务 ${plan.last_task_id || '—'} · 跳过 ${plan.skipped_count} · 离线错过 ${plan.missed_count} · 最近耗时 ${plan.last_duration_seconds == null ? '—' : plan.last_duration_seconds.toFixed(2)+'秒'}`;
    row.append(info);
    const error=document.createElement('p');error.className='muted';error.textContent=plan.last_error?.message || plan.last_execution_error?.message || '无错误';row.append(error);
    const backlog=document.createElement('p');backlog.className='muted';backlog.textContent=(plan.throughput_warning?'执行耗时已超过周期，处理速度可能不足。':'')+'业务积压：未知。'+plan.backlog_note;row.append(backlog);
    row.append(button('最近执行',async()=>{byId('schedule-detail').textContent=pretty(await api('schedules/'+plan.id));}));
    const control=async action=>{
      const updated=await api('schedules/'+plan.id+'/'+action,'POST',{});
      if(plan.source==='agent-default' && byId('agent-edit').value===plan.agent_id)
        fillHeartbeat({...updated,enabled:updated.status==='active'});
      notice('计划已'+({pause:'暂停',resume:'恢复',stop:'停止'})[action]+'；在途任务保持原状态。');
    };
    if(plan.status==='active')row.append(button('暂停',()=>control('pause')));
    else row.append(button('恢复',()=>control('resume')));
    if(plan.status!=='stopped')row.append(button('停止',()=>control('stop'),true));
    if(plan.last_error?.code==='EXECUTION_EXIT_UNCONFIRMED')row.append(button('已核验执行器退出',async()=>{
      if(!confirm('请先通过容器或执行环境核验该任务进程已经退出。确认解除占用？'))return;
      await api('schedules/'+plan.id+'/acknowledge-exit','POST',{task_id:plan.blocking_task_id,confirmed:true});
    },true));
    list.append(row);
  }
  if(!list.childElementCount)list.textContent='暂无心跳计划；已有Agent默认关闭。';
}

/** 自动绑定当前回复目标，隐藏事件引用，仅显示可核对的会话和发送人信息。 */
function setReplyTarget(connection, event, latest = false) {
  const form = byId('reply-form');
  replyConnection = connection;
  form.elements.event_id.value = event?.event_id || '';
  byId('reply-submit').disabled = !event;
  byId('reply-target').textContent = event
    ? `${latest ? '回复最新消息' : '回复所选消息'}：chat_id ${event.conversation_id || '未知'}；open_id ${event.sender_id || '未知'}`
    : '暂无可回复消息。读取消息后自动选择最新一条。';
}

/** 加载最近消息并默认选择最新一条，不 ACK；过期网络响应不能覆盖新连接的目标。 */
async function loadEvents() {
  const id = byId('events-connection').value, version = ++eventLoadVersion;
  setReplyTarget(id, null);
  const list = byId('events-list');
  list.replaceChildren();
  if (!id) return;
  const response = await api('connections/' + id + '/events');
  if (version !== eventLoadVersion || byId('events-connection').value !== id) return;
  const events = [...response.events].sort((a, b) => b.seq - a.seq);
  for (const event of events) {
    const row = document.createElement('div');
    row.className = 'item';
    row.innerHTML = `<b>${event === events[0] ? '最新消息' : '历史消息'}</b> · ${escapeHtml(event.status)}
      <p class="message-identifiers">chat_id：<code>${escapeHtml(event.conversation_id || '未知')}</code><br>
      open_id：<code>${escapeHtml(event.sender_id || '未知')}</code></p>
      <pre>${escapeHtml(event.content?.text ?? pretty(event.content))}</pre>`;
    row.append(button('回复这条消息', async () => setReplyTarget(id, event, event === events[0])),
      button('确认已处理', async () => accepted(await api('connections/' + id + '/ack', 'POST', {event_id:event.event_id, status:'processed'}))),
      button('明确忽略', async () => accepted(await api('connections/' + id + '/ack', 'POST', {event_id:event.event_id, status:'ignored'}))));
    list.append(row);
  }
  setReplyTarget(id, events[0], true);
  if (!events.length) list.textContent = '暂无已验证入站消息。请先配置 Webhook 与事件订阅。';
}

// 会话归属保持不变，界面只能选择当前 Agent 的会话。
function agentSessions(rows) {
  return (rows || []).filter(session => session.agent_id === byId('chat-agent').value);
}
function renderSessionOptions() {
  options('session-select', agentSessions(state.sessions), '选择或新建对话');
}

function renderBuiltinCatalog() {
  const box=byId('builtin-tools');box.replaceChildren();
  if(!builtinCatalog){box.append(button('查看平台工具说明与参数',async()=>{builtinCatalog=(await api('cli/catalog')).commands;renderBuiltinCatalog();}));return;}
  for(const tool of builtinCatalog){const row=document.createElement('details'),title=document.createElement('summary');title.textContent=tool.name;row.append(title);const description=document.createElement('p');description.textContent=tool.description;const pre=document.createElement('pre');pre.textContent=pretty(tool.inputSchema||tool.parameters);row.append(description,pre);box.append(row);}
}

function renderSessionControls() {
  const session=agentSessions(state.sessions).find(s=>s.id===byId('session-select').value);
  if(byId('session-delete-confirmation').dataset.sessionId!==session?.id)byId('session-delete-confirmation').hidden=true;
  const input=byId('session-name');
  if(input.dataset.sessionId!==(session?.id||'')) {
    input.value=session?.name||'';input.dataset.sessionId=session?.id||'';
  }
  byId('session-rename').disabled=!session;byId('session-delete').disabled=!session;
  const current=state.tasks?.find(t=>t.session_id===session?.id&&['queued','running','waiting'].includes(t.status));
  const status={queued:'等待执行',running:'正在回复',waiting:'等待协作结果'};
  const latest=state.tasks?.filter(t=>t.session_id===session?.id).at(-1);
  byId('chat-status').textContent=current?status[current.status]:latest?.error?.message||'准备就绪';
  byId('chat-send').disabled=!!current || (!session && !byId('chat-agent').value);
  byId('session-delete').disabled=!session||!!current;
}

function messageBubble(role,content,label) {
  const row=document.createElement(role==='tool'?'details':'div');row.className='bubble '+role;
  const title=document.createElement(role==='tool'?'summary':'div');title.className='message-role';title.textContent=label;row.append(title);
  const body=document.createElement('div');body.className='message-content';row.append(body);
  if(role==='assistant')ChatContent.render(body,content);
  else if(role==='tool'){const pre=document.createElement('pre');pre.textContent=ChatContent.hideThink(content);body.append(pre);}
  else body.textContent=content;
  return row;
}

function paintLiveReply() {
  if(!liveReply||liveReply.sessionId!==byId('session-select').value)return;
  const box=byId('chat-messages'),following=box.scrollHeight-box.scrollTop-box.clientHeight<90;
  let bubble=byId('live-reply');
  if(!bubble){bubble=messageBubble('assistant','','Agent · 正在回复');bubble.id='live-reply';box.append(bubble);}
  const content=ChatContent.hideThink(liveReply.text);
  ChatContent.render(bubble.querySelector('.message-content'),content||'正在组织回复…');
  if(following)box.scrollTop=box.scrollHeight;
}

/** Request identity prevents stale messages from appearing in another conversation. */
async function loadMessages() {
  const id=byId('session-select').value,box=byId('chat-messages'),version=++messageLoadVersion;
  if(!id){displayedSession='';messageSignature='';box.innerHTML='<div class="chat-empty"><span>✦</span><h3>开始一段新对话</h3><p>选择 Agent，输入需求，即可开始。</p></div>';return;}
  if(!agentSessions(state.sessions).some(s=>s.id===id)){byId('session-select').value='';return loadMessages();}
  const messages=await api('sessions/'+id+'/messages');
  if(version!==messageLoadVersion||id!==byId('session-select').value)return;
  const signature=JSON.stringify(messages),changed=id!==displayedSession;
  if(changed||signature!==messageSignature) {
    const following=changed||box.scrollHeight-box.scrollTop-box.clientHeight<90,scroll=box.scrollTop;
    box.replaceChildren();
    const session=state.sessions.find(s=>s.id===id),agentName=state.agents.find(a=>a.id===session?.agent_id)?.name||'Agent';
    for(const message of messages) {
      if(!['user','assistant','tool'].includes(message.role))continue;
      let content=typeof message.content==='string'?message.content:Array.isArray(message.content)?message.content.filter(c=>c.type==='text').map(c=>c.text).join('\n'):'';
      if(message.role==='assistant') {
        content=ChatContent.hideThink(content);
        if(!content && !message.tool_calls?.length)continue;
        if(!content)content='正在调用工具…';
      }
      box.append(messageBubble(message.role,content,message.role==='user'?'你':message.role==='tool'?'工具回执（点击展开）':agentName));
    }
    if(!box.childElementCount)box.innerHTML='<div class="chat-empty"><span>✦</span><h3>对话已准备好</h3><p>发送第一条消息。</p></div>';
    displayedSession=id;messageSignature=signature;box.scrollTop=following?box.scrollHeight:scroll;
  }
  const active=state.tasks?.find(t=>t.session_id===id&&['queued','running','waiting'].includes(t.status));
  if(active && (!liveReply||liveReply.taskId!==active.id))watchRuntime(active.id,id);
  if(!active && liveReply?.sessionId===id){stopRuntime();byId('live-reply')?.remove();}
  else paintLiveReply();
}

/** 初始化只安装一次事件监听；每组处理器对应一个页面职责。
 * 事件处理函数只组装业务参数，权限、凭据注入与持久化由后端处理。
 */
function setup() {
  // 导航切换只改变当前视图，不丢弃其他页面正在填写的配置。
  document.querySelectorAll('nav button').forEach(button => {
    button.onclick = () => {
      document.querySelectorAll('.tab, nav button').forEach(element => element.classList.remove('active'));
      button.classList.add('active');
      byId('tab-' + button.dataset.tab).classList.add('active');
    };
  });

  // Skill 导入：所选 ZIP 优先；不根据 Skill 内容触发服务部署。
  byId('skill-form').onsubmit = event => {
    event.preventDefault();
    act(async () => {
      const data = values(event.target), file = byId('skill-zip').files[0];
      if (file) data.archive = await encodeFile(file);
      await api('skills', 'POST', data);
      notice('Skill 已导入。没有启动任何服务。');
    }, event.submitter);
  };

  // 连接编辑：新建清空隐藏 ID；已有连接的密钥不回显，留空交由后端保留。
  byId('group-add').onclick = () => addDirectoryRow('groups');
  byId('user-add').onclick = () => addDirectoryRow('users');
  byId('connection-edit').onchange = editConnection;
  byId('connection-new').onclick = () => {
    byId('connection-edit').value = '';
    editConnection();
  };
  byId('connection-form').onsubmit = event => {
    event.preventDefault();
    act(async () => {
      const data = values(event.target), id = data.id;
      delete data.id;
      data.groups = readDirectory('groups');
      data.users = readDirectory('users');
      data.enabled = event.target.elements.enabled.checked;
      const saved = await api('connections' + (id ? '/' + id : ''), id ? 'PATCH' : 'POST', data);
      await refresh();
      byId('connection-edit').value = saved.id;
      editConnection();
      notice('配置已原子写入 ' + saved.env_file + '，运行时立即生效。');
    }, event.submitter);
  };

  // 发送与回复均提交后台操作；前端只显示“已受理”，最终成功依赖服务端回执。
  byId('send-form').onsubmit = event => {
    event.preventDefault();
    act(async () => {
      const data = values(event.target);
      if (!confirm('确认向指定飞书目标发送这一条消息？')) return;
      accepted(await api('connections/' + data.connection + '/send', 'POST', {
        target: {type: data.type, [data.field]: data.target}, text: data.text
      }));
    }, event.submitter);
  };
  byId('events-refresh').onclick = event => act(loadEvents, event.target);
  byId('events-connection').onchange = () => act(loadEvents);
  byId('reply-form').onsubmit = event => {
    event.preventDefault();
    act(async () => {
      const id = byId('events-connection').value, data = values(event.target);
      if (!data.event_id || replyConnection !== id) throw Error('请先读取当前连接的消息。');
      accepted(await api('connections/' + id + '/reply', 'POST', data));
    }, event.submitter);
  };

  // MCP 保存只登记 HTTP/HTTPS 接入定义，不启动或上传服务代码。
  byId('mcp-new').onclick = () => editMcp();
  byId('mcp-form').onsubmit = event => {
    event.preventDefault();
    act(async () => {
      const data = values(event.target), id = data.id;
      delete data.id;
      const saved = await api('mcps' + (id ? '/' + id : ''), id ? 'PATCH' : 'POST', data);
      event.target.elements.id.value = saved.id;
      notice('MCP 定义已保存，请检查 HTTP/HTTPS 连接并发现工具。');
    }, event.submitter);
  };

  // Agent 可先创建后配置模型，Skill 选择与能力授权是两个独立表单。
  byId('agent-edit').onchange = editAgent;
  byId('agent-new').onclick = () => {
    byId('agent-edit').value = '';
    editAgent();
  };
  byId('agent-form').onsubmit = event => {
    event.preventDefault();
    act(async () => {
      const data = values(event.target), id = data.id;
      delete data.id;
      data.max_tokens = Number(data.max_tokens);
      for(const key of ['concurrency','timeout','contract_version']) data[key]=Number(data[key]);
      data.enabled=event.target.elements.enabled.checked;
      data.input_schema=JSON.parse(data.input_schema);
      data.output_schema=data.output_schema.trim()?JSON.parse(data.output_schema):null;
      data.skill_ids = [...byId('agent-skills').selectedOptions].map(option => option.value);
      const heartbeat=readHeartbeat();
      if(JSON.stringify(heartbeat)!==heartbeatDraft || (!id && heartbeat.enabled)) data.heartbeat=heartbeat;
      const saved = await api('agents' + (id ? '/' + id : ''), id ? 'PATCH' : 'POST', data);
      await refresh();
      byId('agent-edit').value = saved.id;
      editAgent();
      notice('Agent 已保存；能力通过右侧独立绑定。');
    }, event.submitter);
  };
  byId('binding-source').onchange = renderToolChoices;
  byId('binding-category').onchange = renderBindingSources;
  byId('binding-agent').onchange = renderToolChoices;
  byId('binding-form').onsubmit = event => {
    event.preventDefault();
    act(async () => {
      const data = values(event.target);
      if (byId('binding-category').value === 'spreadsheet') throw new Error('表格工具待开发，暂不可绑定');
      if (byId('binding-category').value === 'collaboration') throw new Error('协作工具通过有方向的 Agent 协作授权配置');
      data.tools = [...byId('binding-tools').querySelectorAll('input:checked')].map(input => input.value);
      if (!data.tools.length) throw new Error('请至少勾选一个授权工具');
      data.allowed_targets = JSON.parse(data.allowed_targets);
      await api('bindings', 'POST', data);
      notice('能力绑定成功，没有隐式启动或停止服务。');
    }, event.submitter);
  };

  // 会话 ID 在创建成功后才写入下拉框；每次发送由服务端限制同会话并发。
  byId('session-new').onclick = event => act(async () => {
    const session = await api('sessions', 'POST', {agent_id: byId('chat-agent').value,name:'新会话'});
    await refresh();
    byId('session-select').value = session.id;
    renderSessionControls();
    await loadMessages();
  }, event.target);
  byId('chat-agent').onchange = () => {stopRuntime();byId('session-select').value='';renderSessionOptions();renderSessionControls();act(loadMessages);};
  byId('session-select').onchange = () => {stopRuntime();renderSessionControls();act(loadMessages);};
  byId('session-name-form').onsubmit=event=>{
    event.preventDefault();act(async()=>{
      await api('sessions/'+byId('session-select').value,'PATCH',{name:byId('session-name').value});notice('对话名称已保存。');
    },event.submitter);
  };
  byId('session-delete').onclick=()=>{
    const session=state.sessions.find(s=>s.id===byId('session-select').value);if(!session)return;
    byId('session-delete-text').textContent='永久删除「'+session.name+'」及其消息？删除后无法恢复。';
    byId('session-delete-confirmation').dataset.sessionId=session.id;byId('session-delete-confirmation').hidden=false;
  };
  byId('session-delete-cancel').onclick=()=>{byId('session-delete-confirmation').hidden=true;};
  byId('session-delete-confirm').onclick=event=>act(async()=>{
    const id=byId('session-select').value,session=state.sessions.find(s=>s.id===id);
    if(!session||byId('session-delete-confirmation').dataset.sessionId!==id)return;
    await api('sessions/'+id,'DELETE');stopRuntime();byId('session-select').value='';notice('对话已永久删除。');
  },event.target);
  byId('chat-input').onkeydown=event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();if(!byId('chat-send').disabled)byId('chat-form').requestSubmit();}};
  byId('chat-form').onsubmit = event => {
    event.preventDefault();
    act(async () => {
      let id=byId('session-select').value;
      if(!id){const session=await api('sessions','POST',{agent_id:byId('chat-agent').value,name:event.target.elements.content.value.trim().slice(0,30)||'新会话'});await refresh();id=session.id;byId('session-select').value=id;renderSessionControls();}
      const task=await api('sessions/' + id + '/messages', 'POST', values(event.target));
      accepted(task);watchRuntime(task.id,id);
      event.target.elements.content.value = '';
    }, event.submitter);
  };
}

// 仅有已受理的操作仍在执行时轮询；普通表单编辑不会不断触发后台请求。
setup();
refresh().catch(error => notice(error.message, true));
setInterval(() => {
  if (!document.hidden && (state.operations?.some(operation => ['queued', 'running'].includes(operation.status)) || state.tasks?.some(task=>['queued','running','waiting'].includes(task.status)))) {
    refresh().catch(error => notice(error.message, true));
  }
}, 1500);

function stopRuntime(){runtimeSource?.close();runtimeSource=null;liveReply=null;byId('live-reply')?.remove();}
function watchRuntime(taskId,sessionId=byId('session-select').value) {
  stopRuntime();liveReply={taskId,sessionId,text:''};
  const source=new EventSource('/api/tasks/'+taskId+'/stream');runtimeSource=source;
  let scheduled=false;
  source.onmessage=e=>{
    if(runtimeSource!==source||!liveReply)return;
    const event=JSON.parse(e.data);
    if(event.type==='text_delta')liveReply.text+=event.text;
    else if(event.type==='tool_start')liveReply.text='';
    else if(event.type==='error')byId('chat-status').textContent=event.error?.message||'执行失败';
    if(!scheduled){scheduled=true;requestAnimationFrame(()=>{scheduled=false;if(runtimeSource===source)paintLiveReply();});}
  };
  source.addEventListener('finished',()=>{if(runtimeSource===source){stopRuntime();messageSignature='';refresh().catch(e=>notice(e.message,true));}});
}
