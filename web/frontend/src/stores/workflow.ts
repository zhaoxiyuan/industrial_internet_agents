import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { WorkflowState, WorkflowLog } from '@/composables/useWebSocket'

export const useWorkflowStore = defineStore('workflow', () => {
  const jobId = ref<string | null>(null)
  const status = ref<string>('idle')
  const pending = ref<string[]>([])
  const pendingData = ref<Record<string, unknown>>({})
  const confirmed = ref<string[]>([])
  const currentStage = ref<string>('')
  const threadId = ref<string | null>(null)
  const agents = ref<Record<string, { status: string }>>({})
  const logs = ref<WorkflowLog[]>([])

  const isRunning = computed(() => status.value === 'running' || status.value === 'starting')
  const isPending = computed(() => pending.value.length > 0)

  function updateState(state: WorkflowState) {
    status.value = state.status
    pending.value = state.pending || []
    pendingData.value = state.pending_data || {}
    confirmed.value = state.confirmed || []
    currentStage.value = state.current_stage || ''
    threadId.value = state.thread_id || null
    if (state.agents) {
      agents.value = state.agents
    }
  }

  function setJobId(id: string) {
    jobId.value = id
  }

  function addLog(log: WorkflowLog) {
    logs.value.push(log)
    if (logs.value.length > 2000) {
      logs.value = logs.value.slice(-2000)
    }
  }

  function reset() {
    jobId.value = null
    status.value = 'idle'
    pending.value = []
    pendingData.value = {}
    confirmed.value = []
    currentStage.value = ''
    threadId.value = null
    agents.value = {}
    logs.value = []
  }

  return {
    jobId,
    status,
    pending,
    pendingData,
    confirmed,
    currentStage,
    threadId,
    agents,
    logs,
    isRunning,
    isPending,
    updateState,
    setJobId,
    addLog,
    reset
  }
})
