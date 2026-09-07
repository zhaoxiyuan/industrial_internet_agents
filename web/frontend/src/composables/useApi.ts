// API 调用封装 - 保持原有端点不变

// 日志相关
export async function fetchLogCounts() {
  const res = await fetch('/api/logs')
  return res.json()
}

export async function fetchLogFile(filename: string) {
  const res = await fetch(`/api/logs/${encodeURIComponent(filename)}`)
  return res.json()
}

export async function clearLogs() {
  const res = await fetch('/api/logs/clear', { method: 'POST' })
  return res.json()
}

// 场景相关
export async function startScenario(scenario: string) {
  const res = await fetch('/api/scenario/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scenario })
  })
  return res.json()
}

export async function stopScenario() {
  const res = await fetch('/api/scenario/stop', { method: 'POST' })
  return res.json()
}

export async function getScenarioStatus() {
  const res = await fetch('/api/scenario/status')
  return res.json()
}

// Agent 相关
export async function startAgent(intervalSec: number, batchSize: number) {
  const res = await fetch('/api/agent/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ interval_sec: intervalSec, batch_size: batchSize })
  })
  return res.json()
}

export async function stopAgent() {
  const res = await fetch('/api/agent/stop', { method: 'POST' })
  return res.json()
}

export async function getAgentStatus() {
  const res = await fetch('/api/agent/status')
  return res.json()
}

export async function getAgentEvents() {
  const res = await fetch('/api/agent/events')
  return res.json()
}

// 规则相关
export async function loadRules() {
  const res = await fetch('/api/rules')
  return res.json()
}

export async function saveRules(rules: string) {
  const res = await fetch('/api/rules', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rules })
  })
  return res.json()
}

export async function resetRules() {
  const res = await fetch('/api/rules/reset', { method: 'POST' })
  return res.json()
}

// 系统提示词相关
export async function loadSystemPrompt() {
  const res = await fetch('/api/system_prompt')
  return res.json()
}

export async function saveSystemPrompt(systemPrompt: string) {
  const res = await fetch('/api/system_prompt', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ system_prompt: systemPrompt })
  })
  return res.json()
}

export async function resetSystemPrompt() {
  const res = await fetch('/api/system_prompt/reset', { method: 'POST' })
  return res.json()
}

// 配置相关
export async function loadConfig() {
  const res = await fetch('/api/config')
  return res.json()
}

export async function saveConfig(config: ConfigData) {
  const res = await fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config)
  })
  return res.json()
}

export async function testLlm(config: ConfigData) {
  const res = await fetch('/api/test/llm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config)
  })
  return res.json()
}

export async function loadConfigSnapshots() {
  const res = await fetch('/api/config/snapshots')
  return res.json()
}

export async function saveConfigSnapshot(name: string, config: ConfigData) {
  const res = await fetch('/api/config/snapshots/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, config })
  })
  return res.json()
}

export async function deleteConfigSnapshot(name: string) {
  const res = await fetch('/api/config/snapshots/delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  })
  return res.json()
}

// 提示词相关
export async function loadPrompt(stage: string) {
  const res = await fetch(`/api/prompt/${stage}`)
  return res.text()
}

export async function savePrompt(stage: string, content: string) {
  const res = await fetch(`/api/prompt/${stage}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content })
  })
  return res.json()
}

// 工作流相关
export async function startWorkflow(app: Record<string, unknown>) {
  const res = await fetch('/api/workflow/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(app)
  })
  return res.json()
}

export async function confirmWorkflow(threadId: string, stage: string, decision: string) {
  const res = await fetch('/api/workflow/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ thread_id: threadId, stage, decision, async_execute: true })
  })
  return res.json()
}

// A6 相关
export async function loadAssessments(params: {
  limit?: number
  start?: string
  end?: string
  risk_level?: string
}) {
  const query = new URLSearchParams()
  if (params.limit) query.set('limit', String(params.limit))
  if (params.start) query.set('start', params.start)
  if (params.end) query.set('end', params.end)
  if (params.risk_level) query.set('risk_level', params.risk_level)

  const res = await fetch(`/api/a6/assessments?${query}`)
  return res.json()
}

export async function getAssessmentDetail(a6EventId: string) {
  const res = await fetch(`/api/a6/assessments/${a6EventId}`)
  return res.json()
}

export async function clearA6Logs() {
  const res = await fetch('/api/a6/clear_logs', { method: 'POST' })
  return res.json()
}

// 类型定义
export interface ConfigData {
  api_key?: string
  base_url?: string
  model?: string
  protocol?: string
  temperature?: string
  max_tokens?: string
}
