/* 协作与直连页面：仅使用平台短请求 API，不在浏览器承担调度职责。
 * 动态内容按文本转义；每次刷新保留正在编辑的表单，不暴露凭据与检查点。
 */

/** 刷新任务列表与授权目录，Agent 运行概况由后端任务事实生成。 */
function renderCollaboration() {
  for (const id of ['grant-caller','grant-target']) options(id,state.agents);
  const grants=byId('grants-list');grants.replaceChildren();
  for (const g of state.agent_grants||[]) {
    const row=document.createElement('div');row.className='item';
    const name=id=>state.agents.find(a=>a.id===id)?.name||id;
    row.innerHTML=`<b>${escapeHtml(name(g.caller_agent_id))} → ${escapeHtml(name(g.target_agent_id))}</b><p>${escapeHtml(g.permissions.join(', '))} · ${g.enabled?'启用':'停用'}</p>`;
    row.append(button(g.enabled?'停用':'启用',async()=>api('agent-grants/'+g.id,'PATCH',{enabled:!g.enabled})),
      button('解除授权',async()=>api('agent-grants/'+g.id,'DELETE'),true));grants.append(row);
  }
  const overview=document.createElement('p');
  overview.textContent=(state.agent_statuses||[]).map(a=>`${nameOfAgent(a.agent_id)}：运行 ${a.running}，等待 ${a.waiting}，${a.accepting?'可接受任务':'未就绪或忙碌'}`).join('；');
  grants.append(overview);
  renderTaskTable();renderHttpDefinitions();
}

/** 获取显示名称，任务仍使用稳定 ID 关联定义，改名不改变历史关系。 */
function nameOfAgent(id) {return state.agents.find(a=>a.id===id)?.name||(state.deleted_agents?.find(a=>a.id===id)?.name?state.deleted_agents.find(a=>a.id===id).name+'（已删除）':id)||'用户';}

/** 大表展示最近一百个任务；详情单独获取，避免在轮询中重复下载所有事件。 */
function renderTaskTable() {
  const rows=(state.tasks||[]).filter(t=>!byId('task-filter').value||t.status===byId('task-filter').value).slice().reverse();
  const container=byId('tasks-table');container.replaceChildren();
  const table=document.createElement('table');table.style.width='100%';
  table.innerHTML='<thead><tr><th>任务 ID / 父任务</th><th>安排者 → 执行者</th><th>描述 / 阶段</th><th>状态</th><th>当前上下文占用</th><th>成果文件</th><th>操作</th></tr></thead><tbody></tbody>';
  const body=table.querySelector('tbody');
  for (const task of rows) {
    const row=document.createElement('tr');
    const origin=task.schedule_id?'心跳计划 '+task.schedule_id:(task.parent_task_id||'总任务');
    const owner=task.schedule_id?'平台调度（'+(task.schedule_owner_agent_id?nameOfAgent(task.schedule_owner_agent_id):'管理员')+'）':nameOfAgent(task.caller_agent_id);
    const context=Number.isFinite(task.context_tokens)?`${task.context_estimated?'≈':''}${(task.context_tokens/1000).toFixed(1)}K`:'—';
    row.innerHTML=`<td>${escapeHtml(task.id)}<br><small>${escapeHtml(origin)}</small></td><td>${escapeHtml(owner)} → ${escapeHtml(nameOfAgent(task.executor_agent_id))}</td><td>${escapeHtml(task.description)}<br><small>${escapeHtml(task.phase)}</small></td><td>${escapeHtml(task.status)}</td><td title="最近模型请求及后续新增消息的上下文 token 数；≈表示估算">${context}</td><td>${escapeHtml(task.artifacts.map(a=>a.path).join('\n'))}</td><td></td>`;
    const actions=row.lastElementChild;
    actions.append(button('详情',async()=>{const result=await api('tasks/'+task.id);byId('task-detail').textContent=pretty(result);}));
    if (!['succeeded','failed','cancelled','interrupted'].includes(task.status)) actions.append(button('请求取消',async()=>{await api('tasks/'+task.id+'/cancel','POST',{});notice('取消请求已提交；已发生的外部动作不会撤销。');},true));
    for (let index=0;index<task.artifacts.length;index++) {
      const link=document.createElement('a');link.textContent='下载成果';link.href='/api/tasks/'+task.id+'/artifacts/'+index;link.target='_blank';actions.append(link);
    }
    body.append(row);
  }
  container.append(table);
}

/** HTTP 定义与 MCP 注册独立；修订后原绑定需要重新确认。 */
function renderHttpDefinitions() {
  const container=byId('http-list');container.replaceChildren();
  for (const definition of state.http_services||[]) {
    const row=document.createElement('div');row.className='item';
    row.innerHTML=`<b>${escapeHtml(definition.name)}</b><p>${escapeHtml(definition.method+' '+definition.endpoint)}</p><p>修订 ${definition.revision}；令牌${definition.token_configured?'已配置':'未配置'}；${definition.enabled?'启用':'停用'}</p>`;
    row.append(button('编辑',async()=>{const full=await api('tool-providers/http:'+definition.id);editHttp({...full,id:full.provider_id});}),button(definition.enabled?'停用':'启用',async()=>api('http-services/'+definition.id,'PATCH',{enabled:!definition.enabled})),button('删除',async()=>api('http-services/'+definition.id,'DELETE'),true));container.append(row);
  }
}

/** 编辑时不回填 token，留空由后端保留原有凭据。 */
function editHttp(definition) {
  // 编辑已有 HTTP 定义时切到统一接入页面，使用原注册 ID 保留绑定关系。
  byId('external-access-type').value = 'http';
  switchExternalAccess();
  const form=byId('http-form');form.reset();form.elements.id.value=definition?.id||'';
  if(definition) {
    for(const key of ['name','description','endpoint','method','timeout'])form.elements[key].value=definition[key];
    for(const key of ['input_schema','output_schema'])form.elements[key].value=pretty(definition[key]);
  }
}

// 前端只声明请求；依赖领取、等待恢复和状态转换均由服务器维护。
byId('grant-form').onsubmit=event=>{
  event.preventDefault();act(async()=>{
    const data=values(event.target);
    data.permissions=['submit','status','result','cancel','overview','schedule'].filter(p=>event.target.elements[p].checked);
    await api('agent-grants','POST',data);notice('协作授权已保存，后续命令立即重新校验。');
  },event.submitter);
};
byId('tasks-refresh').onclick=event=>act(refresh,event.target);
byId('task-filter').onchange=renderTaskTable;
byId('http-new').onclick=()=>editHttp();
byId('http-form').onsubmit=event=>{
  event.preventDefault();act(async()=>{
    const data=values(event.target),id=data.id;delete data.id;
    data.input_schema=JSON.parse(data.input_schema);data.output_schema=JSON.parse(data.output_schema);data.timeout=Number(data.timeout);
    const result=await api('http-services'+(id?'/'+id:''),id?'PATCH':'POST',data);
    event.target.elements.id.value=result.id;notice('直连能力已保存；请在 Agent 页面绑定 call 命令。');
  },event.submitter);
};

// 首次 overview 可能比第二份脚本更早返回，补一次已有数据的渲染。
if (state.agents) renderCollaboration();
