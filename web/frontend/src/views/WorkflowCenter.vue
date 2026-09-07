<template>
  <div>
    <div class="content-header">
      <div class="content-title">📊 工作流中心</div>
    </div>
    <div class="content-body">
      <!-- Tab 切换 -->
      <div class="workflow-tabs">
        <div
          v-for="(tab, index) in tabs"
          :key="index"
          :class="['workflow-tab', { active: currentTab === index }]"
          @click="currentTab = index"
        >
          {{ tab }}
        </div>
      </div>

      <!-- 工作流 -->
      <div v-show="currentTab === 0" class="tab-panel">
        <div class="card">
          <h3>工作流控制</h3>
          <div class="workflow-controls">
            <button class="btn btn-primary" @click="startWorkflow" :disabled="workflowStore.isRunning">▶ 启动工作流</button>
            <button class="btn btn-danger" @click="resetWorkflow" :disabled="!workflowStore.jobId">🔄 重置</button>
            <span :class="['status', workflowStore.isRunning ? 'status-run' : 'status-stop']">{{ getWorkflowStatusText() }}</span>
          </div>
        </div>
        <div class="card">
          <h3>工作流状态</h3>
          <div class="workflow-status">
            <div class="status-item">
              <span class="label">状态:</span>
              <span class="value">{{ workflowStore.status }}</span>
            </div>
            <div class="status-item">
              <span class="label">当前阶段:</span>
              <span class="value">{{ workflowStore.currentStage || '-' }}</span>
            </div>
            <div class="status-item">
              <span class="label">待确认:</span>
              <span class="value">{{ workflowStore.pending.join(', ') || '-' }}</span>
            </div>
            <div class="status-item">
              <span class="label">已完成:</span>
              <span class="value">{{ workflowStore.confirmed.join(', ') || '-' }}</span>
            </div>
          </div>
        </div>
        <div class="card">
          <h3>Agent 状态 (P1-P10)</h3>
          <div class="agents-grid">
            <div
              v-for="stage in allStages"
              :key="stage"
              :class="['agent-item', getAgentStatusClass(stage)]"
            >
              <span class="agent-name">{{ stage }}</span>
              <span class="agent-status">{{ getAgentStatusText(stage) }}</span>
            </div>
          </div>
        </div>
        <div class="card">
          <h3>工作流日志</h3>
          <div class="log-container" ref="logContainer">
            <div v-for="(log, index) in workflowStore.logs" :key="index" class="log-entry">
              <span class="log-time">[{{ formatLogTime(log.timestamp) }}]</span>
              <span :class="['log-level', log.level?.toLowerCase()]">{{ log.level }}</span>
              <span v-if="log.source" class="log-source">[{{ log.source }}]</span>
              <span class="log-message">{{ log.message }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- A5 作业监测 -->
      <div v-show="currentTab === 1" class="tab-panel">
        <div class="card">
          <h3>A5 作业过程监测</h3>
          <div class="controls">
            <select v-model="selectedScenario">
              <option value="B">场景 B - 工人摘头盔</option>
              <option value="A">场景 A - 正常作业</option>
              <option value="C">场景 C - 可燃气体上升</option>
              <option value="D">场景 D - 监护人离岗</option>
              <option value="E">场景 E - 多重违规</option>
            </select>
            <button class="btn btn-primary" @click="startScenario" :disabled="scenarioRunning">▶ 开始播放</button>
            <button class="btn btn-danger" @click="stopScenario" :disabled="!scenarioRunning">■ 停止</button>
          </div>
          <div class="progress">
            <div class="progress-bar" :style="{ width: progressPercent + '%' }"></div>
          </div>
          <div class="grid-2">
            <div class="left-col">
              <div class="data-card">
                <h4>PPE 检测</h4>
                <div v-for="(info, pid) in workers" :key="pid" class="data-row">
                  <span>{{ pid }}({{ info.name }})</span>
                  <span v-if="!cvSummary[pid]" class="warn">无数据</span>
                  <template v-else>
                    <span>头盔:<span :class="cvSummary[pid].helmet_ok ? 'ok' : 'no'">{{ cvSummary[pid].helmet_ok ? 'OK' : 'NO' }}</span></span>
                  </template>
                </div>
              </div>
              <div class="data-card">
                <h4>传感器</h4>
                <div v-for="(s, sid) in sensorSummary" :key="sid" class="data-row">
                  <span>{{ sid }}</span>
                  <span :class="s.status === 'alarm' ? 'no' : s.status === 'warning' ? 'warn' : 'ok'">{{ s.status }}</span>
                </div>
              </div>
            </div>
            <div class="right-col">
              <div class="data-card">
                <h4>报警事件 <span class="event-count">{{ agentEvents.length ? `共 ${agentEvents.length} 个事件` : '' }}</span></h4>
                <div class="event-list">
                  <div v-for="(ev, i) in agentEvents" :key="i" :class="['event-item', { closed: ev.status === 'closed' }]">
                    <div class="ev-head">
                      <span class="ev-type">{{ ev.type || '未知' }}</span>
                      <span class="ev-time">{{ ev.wall_time || ev._source_file }}</span>
                    </div>
                    <div class="ev-person">{{ getWorkerName(ev.person?.id) }}</div>
                    <div v-if="ev.explanation" class="ev-exp">{{ ev.explanation }}</div>
                  </div>
                  <div v-if="agentEvents.length === 0" class="empty">暂无事件</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- A6 风险研判 -->
      <div v-show="currentTab === 2" class="tab-panel">
        <div class="card">
          <h3>A6 风险研判看板</h3>
          <div class="controls">
            <select v-model="riskLevel">
              <option value="">全部风险等级</option>
              <option value="high">高风险</option>
              <option value="medium">中风险</option>
              <option value="low">低风险</option>
            </select>
            <button class="btn btn-primary" @click="loadAssessments">🔍 刷新</button>
          </div>
          <div class="stats-summary">
            <span>总数: {{ assessments.length }}</span>
            <span class="high">高风险: {{ highCount }}</span>
            <span class="medium">中风险: {{ mediumCount }}</span>
            <span class="low">低风险: {{ lowCount }}</span>
          </div>
          <div class="assessment-list">
            <div v-for="(item, index) in assessments" :key="index" :class="['assessment-item', item.risk_level]">
              <div class="assessment-header">
                <span :class="['risk-badge', item.risk_level]">{{ getRiskLabel(item.risk_level) }}</span>
                <span class="assessment-time">{{ formatTime(item.wall_time || item.created_at) }}</span>
              </div>
              <div class="assessment-content">{{ item.description || item.summary || '无描述' }}</div>
            </div>
            <div v-if="assessments.length === 0" class="empty">暂无研判数据</div>
          </div>
        </div>
      </div>

      <!-- 配置中心 -->
      <div v-show="currentTab === 3" class="tab-panel">
        <div class="card">
          <h3>LLM 模型配置</h3>
          <div class="form-grid">
            <div class="form-row">
              <label>协议</label>
              <select v-model="config.protocol">
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
              </select>
            </div>
            <div class="form-row">
              <label>API URL</label>
              <input type="text" v-model="config.base_url" />
            </div>
            <div class="form-row">
              <label>API Key</label>
              <input :type="showApiKey ? 'text' : 'password'" v-model="config.api_key" />
              <button class="btn-icon" @click="showApiKey = !showApiKey">{{ showApiKey ? '🙈' : '👁' }}</button>
            </div>
            <div class="form-row">
              <label>模型</label>
              <input type="text" v-model="config.model" />
            </div>
          </div>
          <div class="form-actions">
            <button class="btn btn-primary" @click="testLlm">🔌 测试连接</button>
            <button class="btn btn-success" @click="saveConfig">💾 保存</button>
          </div>
          <div v-if="testResult" :class="['test-result', testResult.ok ? 'ok' : 'error']">{{ testResult.ok ? '✅ ' + testResult.message : '❌ ' + testResult.message }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useWorkflowStore } from '@/stores/workflow'
import {
  fetchLogCounts,
  fetchLogFile,
  startScenario as apiStartScenario,
  stopScenario as apiStopScenario,
  getScenarioStatus,
  getAgentEvents,
  loadAssessments as apiLoadAssessments,
  loadConfig,
  saveConfig as apiSaveConfig,
  testLlm as apiTestLlm,
  startWorkflow as apiStartWorkflow
} from '@/composables/useApi'
import { useWebSocket } from '@/composables/useWebSocket'

const tabs = ['工作流', 'A5 作业监测', 'A6 风险研判', '配置中心']
const currentTab = ref(0)

// P1-P10 所有阶段
const allStages = ['P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7', 'P8', 'P9', 'P10']

const workflowStore = useWorkflowStore()
const { onStatusUpdate, onLog, connect, disconnect } = useWebSocket('')

// A5 状态
const selectedScenario = ref('B')
const scenarioRunning = ref(false)
const currentSecond = ref(0)
const cvCount = ref(0)
const logCounts = ref<any>(null)
const latestSnapshot = ref<any>(null)
const agentEvents = ref<any[]>([])

const workers: Record<string, { name: string; role: string }> = {
  P7: { name: '张师傅', role: '焊工' },
  P8: { name: '王师傅', role: '焊工' },
  P11: { name: '赵师傅', role: '监护人' }
}

// A6 状态
const assessments = ref<any[]>([])
const riskLevel = ref('')

// 配置状态
const config = ref({ protocol: 'openai', base_url: '', api_key: '', model: '' })
const showApiKey = ref(false)
const testResult = ref<{ ok: boolean; message: string } | null>(null)

// 定时器
let pollTimer: ReturnType<typeof setInterval> | null = null

// 计算属性
const progressPercent = computed(() => Math.min(100, (currentSecond.value / 30) * 100))

const cvSummary = computed(() => {
  const snap = latestSnapshot.value?.snapshots?.[0]
  if (!snap?.cv_summary) return {}
  const result: any = {}
  for (const [pid, info] of Object.entries(snap.cv_summary) as [string, any][]) {
    result[pid] = {
      helmet_ok: info.helmet_missing_ratio < 0.8,
      goggles_ok: info.goggles_missing_ratio < 0.8,
      suit_ok: info.suit_missing_ratio < 0.8
    }
  }
  return result
})

const sensorSummary = computed(() => latestSnapshot.value?.snapshots?.[0]?.sensor_summary)

const highCount = computed(() => assessments.value.filter(a => a.risk_level === 'high').length)
const mediumCount = computed(() => assessments.value.filter(a => a.risk_level === 'medium').length)
const lowCount = computed(() => assessments.value.filter(a => a.risk_level === 'low').length)

// 方法
function getWorkerName(wid?: string) { return workers[wid || '']?.name || wid || '?' }
function getRiskLabel(level?: string) { return { high: '高风险', medium: '中风险', low: '低风险' }[level || ''] || '未知' }
function formatTime(isoString?: string) { if (!isoString) return '-'; try { return new Date(isoString).toLocaleString('zh-CN') } catch { return isoString } }

function getWorkflowStatusText() {
  if (workflowStore.isRunning) return '运行中'
  if (workflowStore.isPending) return '等待确认'
  if (workflowStore.status === 'completed') return '已完成'
  if (workflowStore.status === 'error') return '错误'
  return '就绪'
}

function getStageStatus(stage: string): string {
  return workflowStore.agents[stage]?.status || 'idle'
}

function getAgentStatusText(stage: string): string {
  const status = getStageStatus(stage)
  const statusMap: Record<string, string> = {
    idle: '等待',
    running: '运行中',
    waiting: '待确认',
    completed: '完成',
    error: '错误'
  }
  return statusMap[status] || status
}

function getAgentStatusClass(stage: string): string {
  const status = getStageStatus(stage)
  return `agent-${status || 'idle'}`
}

function formatLogTime(timestamp?: string) {
  if (!timestamp) return '--:--:--'
  try { return new Date(timestamp).toLocaleTimeString('zh-CN', { hour12: false }) } catch { return '--:--:--' }
}

async function startWorkflow() {
  try {
    const result = await apiStartWorkflow({ note: '测试工作流' }) as { job_id?: string; error?: string }
    if (result.error) { alert(result.error); return }
    const jobId = result.job_id
    if (jobId) {
      workflowStore.setJobId(jobId)
      // 重新连接 WebSocket
      disconnect()
      onStatusUpdate((state) => { workflowStore.updateState(state) })
      onLog((log) => { workflowStore.addLog(log) })
      connect()
    }
  } catch (e) { alert('启动失败: ' + e) }
}

function resetWorkflow() { workflowStore.reset() }

async function startScenario() {
  try {
    await apiStartScenario(selectedScenario.value)
    scenarioRunning.value = true
    startPoll()
  } catch (e) { alert('启动失败: ' + e) }
}

async function stopScenario() {
  try { await apiStopScenario(); scenarioRunning.value = false; stopPoll() } catch (e) { alert('停止失败: ' + e) }
}

async function loadA5Data() {
  try {
    logCounts.value = await fetchLogCounts()
    const eventsData = await getAgentEvents() as { events: any[] }
    agentEvents.value = eventsData.events || []
    const status = await getScenarioStatus() as { current_second: number; cv_count?: number }
    currentSecond.value = status.current_second || 0
    cvCount.value = status.cv_count || 0
    if (logCounts.value?.snapshots?.length) {
      const latestFile = logCounts.value.snapshots[logCounts.value.snapshots.length - 1]
      latestSnapshot.value = await fetchLogFile(latestFile)
    }
  } catch (e) { console.error('[A5] Error:', e) }
}

async function loadAssessments() {
  try {
    const params: any = {}
    if (riskLevel.value) params.risk_level = riskLevel.value
    const result = await apiLoadAssessments(params) as { assessments?: any[] }
    assessments.value = result.assessments || []
  } catch (e) { console.error('[A6] Error:', e) }
}

async function loadConfigData() {
  try {
    const cfg = await loadConfig() as any
    config.value = { protocol: cfg.protocol || 'openai', base_url: cfg.base_url || '', api_key: cfg.api_key || '', model: cfg.model || '' }
  } catch (e) { console.error('[Config] Error:', e) }
}

async function testLlm() {
  try {
    testResult.value = null
    const result = await apiTestLlm(config.value) as { ok: boolean; reply?: string; error?: string }
    testResult.value = { ok: result.ok, message: result.ok ? (result.reply || '连接成功') : (result.error || '连接失败') }
  } catch (e) { testResult.value = { ok: false, message: String(e) } }
}

async function saveConfig() {
  try { await apiSaveConfig(config.value); alert('配置已保存') } catch (e) { alert('保存失败: ' + e) }
}

function startPoll() {
  stopPoll()
  pollTimer = setInterval(loadA5Data, 1000)
}

function stopPoll() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

onMounted(() => {
  loadA5Data()
  loadAssessments()
  loadConfigData()
  startPoll()
})

onUnmounted(() => { stopPoll() })
</script>

<style scoped>
.content-header { background: white; padding: 15px 25px; border-bottom: 1px solid #e0e0e0; }
.content-title { font-size: 18px; font-weight: bold; color: #333; }
.content-body { flex: 1; padding: 25px; overflow-y: auto; }

.workflow-tabs { display: flex; gap: 4px; margin-bottom: 20px; border-bottom: 2px solid #e0e0e0; }
.workflow-tab { padding: 10px 20px; cursor: pointer; font-size: 14px; color: #666; border-bottom: 2px solid transparent; margin-bottom: -2px; transition: all 0.2s; }
.workflow-tab:hover { color: #667eea; }
.workflow-tab.active { color: #667eea; border-bottom-color: #667eea; font-weight: bold; }

.tab-panel { animation: fadeIn 0.2s; }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

.card { background: white; border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.card h3 { font-size: 14px; color: #333; margin: 0 0 15px; padding-bottom: 10px; border-bottom: 1px solid #eee; }
.card h4 { font-size: 13px; color: #666; margin: 0 0 10px; }

.btn { padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; transition: all 0.2s; }
.btn-primary { background: #667eea; color: white; }
.btn-primary:hover { background: #5568d3; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-danger { background: #ff4757; color: white; }
.btn-danger:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-success { background: #4CAF50; color: white; }

.controls { display: flex; gap: 10px; margin-bottom: 15px; align-items: center; }
select, input { padding: 8px 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; }
select { background: white; }
input:focus, select:focus { outline: none; border-color: #667eea; }

.progress { width: 100%; height: 8px; background: #e0e0e0; border-radius: 4px; margin-bottom: 15px; overflow: hidden; }
.progress-bar { height: 100%; background: #667eea; transition: width 0.3s; border-radius: 4px; }

.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.data-card { background: #f9f9f9; border-radius: 8px; padding: 15px; margin-bottom: 10px; }
.data-row { display: flex; justify-content: space-between; padding: 5px 0; font-size: 13px; border-bottom: 1px solid #eee; }
.data-row:last-child { border-bottom: none; }
.ok { color: #4CAF50; }
.no { color: #ff4757; font-weight: bold; }
.warn { color: #fbbf24; }

.event-list { max-height: 300px; overflow-y: auto; }
.event-item { background: #fff5f5; border-left: 3px solid #ff4757; padding: 10px; margin-bottom: 8px; border-radius: 4px; }
.event-item.closed { background: #f0fff0; border-left-color: #4CAF50; }
.ev-head { display: flex; justify-content: space-between; margin-bottom: 4px; font-size: 12px; }
.ev-type { color: #ff4757; font-weight: bold; }
.ev-time { color: #999; }
.ev-person { color: #666; font-size: 13px; }
.ev-exp { color: #999; font-size: 12px; margin-top: 4px; }
.event-count { color: #999; font-weight: normal; font-size: 12px; margin-left: 8px; }

.assessment-list { max-height: 400px; overflow-y: auto; }
.assessment-item { background: #f9f9f9; border-left: 3px solid #999; padding: 12px; margin-bottom: 8px; border-radius: 4px; }
.assessment-item.high { border-left-color: #ff4757; }
.assessment-item.medium { border-left-color: #fbbf24; }
.assessment-item.low { border-left-color: #4CAF50; }
.assessment-header { display: flex; justify-content: space-between; margin-bottom: 8px; }
.risk-badge { padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }
.risk-badge.high { background: #ff4757; color: white; }
.risk-badge.medium { background: #fbbf24; color: #333; }
.risk-badge.low { background: #4CAF50; color: white; }
.assessment-time { color: #999; font-size: 12px; }
.assessment-content { color: #333; font-size: 13px; line-height: 1.5; }
.stats-summary { display: flex; gap: 20px; margin-bottom: 15px; font-size: 13px; color: #666; }
.stats-summary .high { color: #ff4757; }
.stats-summary .medium { color: #fbbf24; }
.stats-summary .low { color: #4CAF50; }

.workflow-controls { display: flex; gap: 10px; align-items: center; margin-bottom: 15px; }
.status { font-size: 13px; }
.status-run { color: #4CAF50; }
.status-stop { color: #999; }

.workflow-status { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.status-item { background: #f9f9f9; padding: 10px; border-radius: 6px; display: flex; gap: 8px; }
.status-item .label { color: #999; }
.status-item .value { color: #333; font-weight: 500; }

.agents-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }
.agent-item { background: #f9f9f9; padding: 12px 8px; border-radius: 8px; text-align: center; border: 2px solid transparent; }
.agent-item .agent-name { display: block; font-size: 14px; font-weight: bold; margin-bottom: 4px; }
.agent-item .agent-status { font-size: 11px; }
.agent-idle { border-color: #e0e0e0; }
.agent-idle .agent-name { color: #999; }
.agent-idle .agent-status { color: #999; }
.agent-running { border-color: #667eea; background: #f5f5ff; }
.agent-running .agent-name { color: #667eea; }
.agent-running .agent-status { color: #667eea; }
.agent-waiting { border-color: #fbbf24; background: #fffff5; }
.agent-waiting .agent-name { color: #fbbf24; }
.agent-waiting .agent-status { color: #fbbf24; }
.agent-completed { border-color: #4CAF50; background: #f5fff5; }
.agent-completed .agent-name { color: #4CAF50; }
.agent-completed .agent-status { color: #4CAF50; }
.agent-error { border-color: #ff4757; background: #fff5f5; }
.agent-error .agent-name { color: #ff4757; }
.agent-error .agent-status { color: #ff4757; }

.log-container { background: #1e1e1e; border-radius: 8px; padding: 10px; max-height: 200px; overflow-y: auto; font-family: monospace; font-size: 12px; }
.log-entry { padding: 4px 0; color: #d4d4d4; border-bottom: 1px solid #333; }
.log-entry:last-child { border-bottom: none; }
.log-time { color: #666; }
.log-level { font-weight: bold; margin: 0 6px; }
.log-level.info { color: #4CAF50; }
.log-level.warning { color: #fbbf24; }
.log-level.error { color: #ff4757; }
.log-source { color: #60a5fa; margin-right: 6px; }
.log-message { color: #e0e0e0; }

.form-grid { display: grid; gap: 12px; margin-bottom: 15px; }
.form-row { display: flex; align-items: center; gap: 10px; }
.form-row label { width: 80px; color: #666; font-size: 13px; }
.form-row input, .form-row select { flex: 1; }
.btn-icon { background: #e0e0e0; border: none; padding: 8px 12px; border-radius: 6px; cursor: pointer; }
.form-actions { display: flex; gap: 10px; }
.test-result { margin-top: 12px; padding: 10px; border-radius: 6px; font-size: 13px; }
.test-result.ok { background: #f0fff0; color: #4CAF50; }
.test-result.error { background: #fff0f0; color: #ff4757; }

.empty { color: #999; font-size: 13px; text-align: center; padding: 20px; }
</style>
