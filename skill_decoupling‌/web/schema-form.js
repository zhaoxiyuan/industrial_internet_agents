/* Agent 调用契约的只读说明；不渲染任务输入表单、不拼接用户 HTML。 */

/** 创建安全的 DOM 节点；字段名称和服务端说明全部作为纯文本。 */
function schemaElement(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}

/** 接口说明是管理文档，不含密钥；每个 Agent 使用同一入口和自己的稳定 ID。 */
function agentInterfaceDescription(agent) {
  const details = schemaElement('div');
  details.append(schemaElement('p', 'Agent ID：' + agent.id), schemaElement('p', '职责：' + (agent.description || '尚未填写')));
  details.append(schemaElement('p', '提交任务：POST ' + location.origin + '/api/tasks；返回 HTTP 202 和任务 ID。'));
  details.append(schemaElement('p', 'agent_id 固定为本 Agent ID；description 为任务描述；input 由下方契约规定。'));
  details.append(schemaElement('p', '查询状态及结果：GET ' + location.origin + '/api/tasks/{任务ID}；取消：POST /api/tasks/{任务ID}/cancel。'));
  details.append(schemaElement('p', 'Agent 间调用：获得协作授权后使用 CLI submit_task，参数为 agent_id、description、input；通过 get_task_status / get_task_result 查询。'));
  details.append(schemaElement('p', '执行位置：本平台本地 Runtime；提交接口异步返回，完成情况以任务状态为准。'));
  const input = schemaElement('details'); input.append(schemaElement('summary', '输入契约（只读）'), schemaElement('pre', JSON.stringify(agent.input_schema ?? {type:'object',properties:{},additionalProperties:true}, null, 2))); details.append(input);
  const output = schemaElement('details'); output.append(schemaElement('summary', '输出契约（只读）'), schemaElement('pre', agent.output_schema ? JSON.stringify(agent.output_schema, null, 2) : '未限制结构；允许文本答复。')); details.append(output);
  return details;
}
