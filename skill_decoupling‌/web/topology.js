/* Grant topology is a read-only projection; only explicit detail actions change definitions.
 * No external graph library/CDN. Names, descriptions and tool strings stay escaped text.
 */
const topologyUI = {selected:null, zoom:1, width:800, height:480, fit:true, signature:''};
const topologyLabels = {running:'运行中', waiting:'等待协作', queued:'排队中', draining:'执行收尾中', idle:'空闲', disabled:'已停用', unconfigured:'待配置'};
const topologyPermissions = {submit:'提交子任务', status:'查询 / 等待', result:'读取结果', overview:'运行概况', cancel:'请求取消', schedule:'管理心跳'};
const topologyTaskLabels = {...topologyLabels, succeeded:'已完成', failed:'失败', cancelled:'已取消', interrupted:'已中断'};

/** Condense strongly connected components before ranking: reciprocal grants cannot loop layout. */
function topologyLayout(nodes, edges) {
  const adj=new Map(nodes.map(n=>[n.id,[]])), indices=new Map(), low=new Map(), stack=[], onStack=new Set(), groups=[];
  edges.forEach(e=>adj.get(e.source)?.push(e.target));
  let index=0;
  function visit(id) {
    indices.set(id,index);low.set(id,index++);stack.push(id);onStack.add(id);
    for(const next of adj.get(id)) {
      if(!indices.has(next)){visit(next);low.set(id,Math.min(low.get(id),low.get(next)));}
      else if(onStack.has(next))low.set(id,Math.min(low.get(id),indices.get(next)));
    }
    if(low.get(id)===indices.get(id)) {
      const group=[];let next;
      do{next=stack.pop();onStack.delete(next);group.push(next);}while(next!==id);
      groups.push(group);
    }
  }
  nodes.forEach(n=>{if(!indices.has(n.id))visit(n.id);});
  const owner=new Map();groups.forEach((g,i)=>g.forEach(id=>owner.set(id,i)));
  const next=groups.map(()=>new Set()), degree=groups.map(()=>0), rank=groups.map(()=>0);
  edges.forEach(e=>{const a=owner.get(e.source),b=owner.get(e.target);if(a!==b&&!next[a].has(b)){next[a].add(b);degree[b]++;}});
  const queue=degree.flatMap((d,i)=>d===0?[i]:[]);
  for(let i=0;i<queue.length;i++)for(const b of next[queue[i]]){rank[b]=Math.max(rank[b],rank[queue[i]]+1);if(--degree[b]===0)queue.push(b);}
  const columns=[];
  nodes.forEach(n=>{const r=rank[owner.get(n.id)];(columns[r]??=[]).push(n);});
  const height=Math.max(460,...columns.map(c=>c.length*140+90)), positions=new Map();
  columns.forEach((column,i)=>{
    column.sort((a,b)=>(adj.get(b.id).length-adj.get(a.id).length)||a.name.localeCompare(b.name,undefined,{numeric:true})||a.id.localeCompare(b.id));
    column.forEach((n,j)=>positions.set(n.id,{x:40+i*330,y:(height-column.length*140)/2+j*140+12}));
  });
  return {positions,width:Math.max(650,columns.length*330+50),height};
}

function topologyNavigate(tab){document.querySelector(`nav button[data-tab="${tab}"]`).click();}
function topologyTime(value){return value?new Date(value).toLocaleString():'暂无任务';}
function topologySelect(id){topologyUI.selected=id;topologyUI.signature='';renderTopology();}

function topologyScale() {
  const viewport=byId('topology-viewport');
  if(topologyUI.fit&&viewport.clientWidth)topologyUI.zoom=Math.min(1,Math.max(.35,(viewport.clientWidth-18)/topologyUI.width));
  byId('topology-canvas').style.transform=`scale(${topologyUI.zoom})`;
  Object.assign(byId('topology-spacer').style,{width:topologyUI.width*topologyUI.zoom+'px',height:topologyUI.height*topologyUI.zoom+'px'});
  byId('topology-fit').textContent=`适应画布 · ${Math.round(topologyUI.zoom*100)}%`;
}

function renderTopology() {
  const graph=state.agent_topology;if(!graph)return;
  const summary=graph.summary;
  byId('topology-stats').innerHTML=[['Agent',summary.agents],['已启用',summary.enabled],['已激活',summary.active],['待执行任务',summary.queued],['有效授权',summary.enabled_grants]].map(([name,value])=>`<div><strong>${value}</strong><span>${name}</span></div>`).join('');
  byId('topology-updated').textContent=`更新于 ${topologyTime(graph.generated_at)} · 此页每 4 秒刷新 · “已激活”指运行、等待协作或执行收尾；已启用不等于正在运行。`;
  if(!graph.nodes.some(n=>n.id===topologyUI.selected))topologyUI.selected=null;
  const signature=JSON.stringify([graph.nodes,graph.edges,topologyUI.selected,byId('topology-search').value,byId('topology-disabled').checked,byId('topology-neighbors').checked]);
  if(signature===topologyUI.signature){topologyScale();return;}
  topologyUI.signature=signature;
  let edges=graph.edges.filter(e=>byId('topology-disabled').checked||e.enabled);
  const query=byId('topology-search').value.trim().toLowerCase();
  const connected=new Set([topologyUI.selected]);
  edges.filter(e=>e.source===topologyUI.selected||e.target===topologyUI.selected).forEach(e=>{connected.add(e.source);connected.add(e.target);});
  const nodes=graph.nodes.filter(n=>(!query||[n.name,n.description,n.id].some(s=>s.toLowerCase().includes(query)))&&(!byId('topology-neighbors').checked||!topologyUI.selected||connected.has(n.id)));
  const visible=new Set(nodes.map(n=>n.id));edges=edges.filter(e=>visible.has(e.source)&&visible.has(e.target));
  const layout=topologyLayout(nodes,edges);Object.assign(topologyUI,{width:layout.width,height:layout.height});
  const canvas=byId('topology-canvas'),focused=document.activeElement?.dataset.agentId;
  canvas.replaceChildren();Object.assign(canvas.style,{width:layout.width+'px',height:layout.height+'px'});
  const empty=byId('topology-empty');empty.hidden=nodes.length>0;
  empty.textContent=graph.nodes.length?'没有匹配的 Agent，请调整搜索或筛选。':'暂无 Agent，请先在“Agent 与绑定”中创建。';
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('width',layout.width);svg.setAttribute('height',layout.height);svg.setAttribute('aria-hidden','true');
  svg.innerHTML='<defs><marker id="topology-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/></marker></defs>';
  for(const e of edges){
    const a=layout.positions.get(e.source),b=layout.positions.get(e.target),forward=b.x>a.x;
    const x1=a.x+238,y1=a.y+52,x2=forward?b.x:b.x+238,y2=b.y+52;
    const path=document.createElementNS(svg.namespaceURI,'path');
    const dx=forward?(x2-x1)*.52:64+Math.abs(y2-y1)*.1;
    path.setAttribute('d',`M ${x1} ${y1} C ${x1+dx} ${y1}, ${forward?x2-dx:x2+dx} ${y2}, ${x2} ${y2}`);
    path.setAttribute('class',`topology-edge ${e.enabled?(e.can_submit?'dispatch':'observe'):'disabled'} ${e.active_tasks?'flowing':''} ${topologyUI.selected&&(e.source===topologyUI.selected||e.target===topologyUI.selected)?'selected':''}`);
    path.setAttribute('marker-end','url(#topology-arrow)');
    const title=document.createElementNS(svg.namespaceURI,'title');title.textContent=`${nameOfAgent(e.source)} → ${nameOfAgent(e.target)}：${e.permissions.map(p=>topologyPermissions[p]).join('、')}；在途 ${e.active_tasks}`;path.append(title);svg.append(path);
  }
  canvas.append(svg);
  for(const node of nodes){
    const p=layout.positions.get(node.id),element=document.createElement('button');element.type='button';
    element.className=`topology-node ${node.status} ${node.id===topologyUI.selected?'selected':''}`;
    element.dataset.agentId=node.id;element.style.left=p.x+'px';element.style.top=p.y+'px';
    element.setAttribute('aria-pressed',String(node.id===topologyUI.selected));
    element.setAttribute('aria-label',`${node.name}，${topologyLabels[node.status]}，${node.enabled?'已启用':'已停用'}，查看详情`);
    const shortName=node.name.replace('P-skill validation ',''),monogram=shortName.match(/^p\d+/i)?.[0]||shortName.slice(0,2);
    element.innerHTML=`<span class="topology-node-top"><span class="topology-monogram">${escapeHtml(monogram.toUpperCase())}</span><span class="topology-status"><i class="topology-dot ${node.status}"></i>${topologyLabels[node.status]}</span></span><strong title="${escapeHtml(node.name)}">${escapeHtml(node.name)}</strong><span class="topology-node-bottom">${node.skills.length} Skills · ${node.counts.running} 运行 / ${node.counts.waiting} 等待<span>${node.enabled?'已启用':'已停用'}</span></span>`;
    element.onclick=()=>topologySelect(node.id);canvas.append(element);
  }
  if(focused)[...canvas.querySelectorAll('button')].find(b=>b.dataset.agentId===focused)?.focus({preventScroll:true});
  topologyScale();renderTopologyDetail(graph);
}

function renderTopologyDetail(graph) {
  const node=graph.nodes.find(n=>n.id===topologyUI.selected),panel=byId('topology-detail');
  if(!node){panel.innerHTML='<div class="topology-placeholder"><span>◎</span><h3>选择一个 Agent</h3><p>查看实时状态、绑定能力<br>及授权协作关系。</p></div>';return;}
  const scroll=panel.scrollTop;
  panel.innerHTML=`<div class="topology-detail-head"><span class="topology-status"><i class="topology-dot ${node.status}"></i>${topologyLabels[node.status]}</span><h3>${escapeHtml(node.name)}</h3><p>${escapeHtml(node.description||'尚未填写职责说明')}</p><code>${escapeHtml(node.id)}</code></div><div class="topology-detail-actions"></div><dl class="topology-facts"><dt>配置</dt><dd>${node.enabled?'已启用':'已停用'} · ${node.configured?'模型已配置':'模型配置不完整'}</dd><dt>模型</dt><dd>${escapeHtml(node.model||'未配置')}</dd><dt>执行</dt><dd>${escapeHtml(node.execution_target)}</dd><dt>容量</dt><dd>${node.counts.running} / ${node.concurrency} 运行 · 排队 ${node.counts.queued}</dd><dt>任务受理</dt><dd>${node.accepting?'可受理':'暂不可受理'}</dd><dt>心跳计划</dt><dd>${node.schedules.filter(s=>s.status==='active').length} 个启用 / 共 ${node.schedules.length} 个</dd></dl><p class="muted">配置状态不代表模型或外部服务已通过健康检测。停用仅阻止新任务，不取消在途执行。</p><h4>心跳管理</h4><div class="topology-heartbeats"></div><h4>绑定 Skills</h4><div class="topology-chips">${node.skills.map(s=>`<span>${escapeHtml(s.name)}${s.enabled?'':' · 停用'}</span>`).join('')||'<span>未绑定 Skill</span>'}</div><h4>工具绑定</h4><div class="topology-bindings">${node.bindings.map(b=>`<details><summary>${escapeHtml(b.source)} · ${b.enabled?'启用':'停用'}</summary><p>${escapeHtml((b.tools||[]).join('、'))}</p></details>`).join('')||'<p class="muted">暂无工具绑定</p>'}</div><h4>协作授权</h4><div class="topology-relations"></div><h4>近期任务 <small>最近更新 5 条</small></h4><div class="topology-recent"></div>`;
  const actions=panel.querySelector('.topology-detail-actions');
  actions.append(button('编辑配置',async()=>{topologyNavigate('agents');byId('agent-edit').value=node.id;editAgent();}),
    button('管理授权',async()=>{topologyNavigate('collaboration');byId('grant-caller').value=node.id;const other=graph.nodes.find(n=>n.id!==node.id);if(other)byId('grant-target').value=other.id;byId('grant-form').scrollIntoView({block:'start',behavior:'smooth'});}),
    button(node.enabled?'停用 Agent':'启用 Agent',async()=>{await api('agents/'+node.id,'PATCH',{enabled:!node.enabled});notice(node.enabled?'已停用：不再受理新任务，在途任务继续显示实际状态。':'Agent 已启用。');},node.enabled),
    button('删除 Agent',()=>deleteAgent(node),true));
  renderTopologyHeartbeats(panel.querySelector('.topology-heartbeats'),node);
  const relations=panel.querySelector('.topology-relations');
  for(const edge of graph.edges.filter(e=>e.source===node.id||e.target===node.id)){
    const outgoing=edge.source===node.id,other=outgoing?edge.target:edge.source;
    const row=document.createElement('div');row.className='topology-relation';
    const select=document.createElement('button');select.className='topology-link';select.textContent=(outgoing?'→ 调用 ':'← 被调用于 ')+nameOfAgent(other);select.onclick=()=>topologySelect(other);
    const info=document.createElement('p');info.textContent=`${edge.enabled?'已授权':'授权停用'} · ${edge.permissions.map(p=>topologyPermissions[p]||p).join('、')} · 在途 ${edge.active_tasks} / 排队 ${edge.queued_tasks}`;
    row.append(select,info);relations.append(row);
  }
  if(!relations.childElementCount)relations.textContent='暂无协作授权，独立 Agent 仍可接受用户任务。';
  const recent=panel.querySelector('.topology-recent');
  for(const task of node.recent_tasks){const row=document.createElement('div');row.className='topology-task';
    row.innerHTML=`<span class="topology-task-status ${escapeHtml(task.status)}">${escapeHtml(topologyTaskLabels[task.status]||task.status)}</span><p>${escapeHtml(task.description)}</p><small>${escapeHtml(topologyTime(task.updated))}</small>`;
    row.append(button('查看任务',async()=>{const detail=await api('tasks/'+task.id);topologyNavigate('collaboration');byId('task-detail').textContent=pretty(detail);byId('task-detail').scrollIntoView({block:'center'});}));recent.append(row);}
  if(!recent.childElementCount)recent.textContent='尚无任务记录。';
  panel.scrollTop=scroll;
}

byId('topology-search').oninput=renderTopology;
byId('topology-disabled').onchange=renderTopology;
byId('topology-neighbors').onchange=renderTopology;
byId('topology-refresh').onclick=e=>act(refresh,e.target);
byId('topology-fit').onclick=()=>{topologyUI.fit=true;topologyScale();};
for(const [id,delta] of [['topology-minus',-.15],['topology-plus',.15]])byId(id).onclick=()=>{topologyUI.fit=false;topologyUI.zoom=Math.max(.35,Math.min(1.75,topologyUI.zoom+delta));topologyScale();};
new ResizeObserver(topologyScale).observe(byId('topology-viewport'));
let topologyPolling=false;
setInterval(async()=>{
  if(document.hidden||!byId('tab-topology').classList.contains('active')||refreshing||topologyPolling)return;
  topologyPolling=true;
  try{const graph=await api('agent-topology');if(!state.agent_topology||graph.generated_at>=state.agent_topology.generated_at){state.agent_topology=graph;renderTopology();}}catch(error){byId('topology-updated').textContent='刷新失败，保留上次快照：'+error.message;}
  finally{topologyPolling=false;}
},4000);
if(state.agent_topology)renderTopology();

function renderTopologyHeartbeats(container,node) {
  container.append(button('添加心跳',async()=>openTopologyHeartbeat(node)));
  const note=document.createElement('p');note.className='muted';note.textContent='每次唤醒使用独立对话。删除仅停止未来触发，保留任务历史及在途执行。';container.append(note);
  for(const plan of node.schedules) {
    const card=document.createElement('div');card.className='topology-heartbeat';
    const status={active:'运行中',paused:'已暂停',stopped:'已停止'}[plan.status]||plan.status;
    card.innerHTML=`<b>${escapeHtml(status)} · 每 ${escapeHtml(plan.interval_seconds)} 秒</b><p>${escapeHtml(plan.description||'未填写任务描述')}</p><small>防重入：${plan.overlap_scope==='self'?'仅当前 Agent':'整条任务链'} · 单轮超时：${plan.timeout_seconds?escapeHtml(plan.timeout_seconds)+' 秒':'继承 Agent'}<br>下次触发：${plan.next_run_at?escapeHtml(new Date(plan.next_run_at*1000).toLocaleString()):'—'}<br>作业票：${escapeHtml(plan.business_scope?.job_id||'未绑定业务票')}</small>`;
    card.append(button('配置',async()=>openTopologyHeartbeat(node,await api('schedules/'+plan.id))));
    card.append(button(plan.status==='active'?'暂停':'恢复',async()=>{await api('schedules/'+plan.id+'/'+(plan.status==='active'?'pause':'resume'),'POST',{});}));
    card.append(button('删除',async()=>{
      if(!confirm('删除该心跳计划？未来不再触发；任务历史保留，在途任务继续执行。'))return;
      await api('schedules/'+plan.id,'DELETE',{version:plan.version});notice('心跳计划已删除。');
    },true));container.append(card);
  }
  if(!node.schedules.length){const empty=document.createElement('p');empty.textContent='暂无心跳计划';container.append(empty);}
}

function openTopologyHeartbeat(node,plan=null) {
  document.getElementById('topology-heartbeat-dialog')?.remove();
  const dialog=document.createElement('dialog');dialog.id='topology-heartbeat-dialog';dialog.className='heartbeat-dialog';
  dialog.innerHTML=`<form><h3>${plan?'配置':'添加'}心跳 · ${escapeHtml(node.name)}</h3>
    <p class="muted">每次独立对话；前一轮仍占用时跳过本次唤醒，不积压补发。业务 Agent 需先绑定作业票，已有计划不会自动切换到新票。</p>
    <label class="check"><input name="enabled" type="checkbox">启用计划</label>
    <label>心跳间隔（秒，1–86400）<input name="interval_seconds" type="number" min="1" max="86400" required></label>
    <label>单轮超时（秒，留空继承 Agent 上限）<input name="timeout_seconds" type="number" min="1"></label>
    <label>防重入范围<select name="overlap_scope"><option value="self">仅当前 Agent（Main 推荐）</option><option value="chain">整条任务链（包含子任务）</option></select></label>
    <label>首次触发<select name="first_run"><option value="after_interval">等待一个周期</option><option value="immediate">启用后立即触发</option></select></label>
    <label>共享防重入键（任务链模式可选）<input name="overlap_key" maxlength="128"></label>
    <label>任务描述<textarea name="description" rows="5" maxlength="10000" required></textarea></label>
    <label>输入参数（JSON 对象）<textarea name="input" rows="4" required></textarea></label>
    <p class="heartbeat-error" role="alert"></p><div class="heartbeat-dialog-actions"><button type="button" class="secondary" data-cancel>取消</button><button type="submit">保存配置</button></div></form>`;
  const form=dialog.querySelector('form'),fields=form.elements;
  const defaults={interval_seconds:60,timeout_seconds:'',overlap_scope:'self',first_run:'after_interval',overlap_key:'',description:'',input:{}};
  for(const key of Object.keys(defaults))fields.namedItem(key).value=key==='input'?pretty(plan?.input??{}):(plan?.[key]??defaults[key]);
  fields.namedItem('enabled').checked=plan?.status==='active';
  dialog.querySelector('[data-cancel]').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();
  form.onsubmit=async event=>{
    event.preventDefault();const submit=form.querySelector('[type=submit]'),error=dialog.querySelector('[role=alert]');submit.disabled=true;error.textContent='';
    try {
      const value=values(form),input=JSON.parse(value.input);
      if(!input||Array.isArray(input)||typeof input!=='object')throw Error('输入参数必须是 JSON 对象');
      const data={agent_id:node.id,request_id:crypto.randomUUID(),enabled:fields.namedItem('enabled').checked,
        interval_seconds:Number(value.interval_seconds),timeout_seconds:value.timeout_seconds?Number(value.timeout_seconds):null,
        overlap_scope:value.overlap_scope,first_run:value.first_run,overlap_key:value.overlap_key,overlap_policy:'skip',description:value.description,input};
      if(plan)data.version=plan.version;
      await api(plan?'schedules/'+plan.id:'schedules',plan?'PATCH':'POST',data);
      dialog.close();notice('心跳配置已保存。');await refresh();
    }catch(e){error.textContent=e.message;}finally{submit.disabled=false;}
  };
  document.body.append(dialog);dialog.showModal();
}
