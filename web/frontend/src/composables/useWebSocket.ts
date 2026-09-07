import { ref, onUnmounted } from 'vue'

const WS_STATUS_PORT = 8081
const WS_LOGS_PORT = 8082

export interface WorkflowState {
  status: string
  pending: string[]
  pending_data: Record<string, unknown>
  confirmed: string[]
  current_stage: string
  thread_id: string
  job_id?: string
  agents?: Record<string, { status: string }>
}

export interface WorkflowLog {
  type: string
  level?: string
  source?: string
  message?: string
  data?: unknown
  timestamp?: string
}

export function useWebSocket(jobId: string) {
  const statusWs = ref<WebSocket | null>(null)
  const logsWs = ref<WebSocket | null>(null)
  const statusConnected = ref(false)
  const logsConnected = ref(false)

  const statusCallbacks: ((state: WorkflowState) => void)[] = []
  const logCallbacks: ((log: WorkflowLog) => void)[] = []

  function connectStatusWs() {
    if (!jobId) return
    const ws = new WebSocket(`ws://localhost:${WS_STATUS_PORT}/ws/status/${jobId}`)

    ws.onopen = () => {
      statusConnected.value = true
      console.log('[WebSocket] Status connected')
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'state_update') {
          statusCallbacks.forEach(cb => cb(msg.data as WorkflowState))
        }
      } catch (e) {
        console.error('[WebSocket] Parse error:', e)
      }
    }

    ws.onerror = () => {
      console.error('[WebSocket] Status error')
    }

    ws.onclose = () => {
      statusConnected.value = false
      console.log('[WebSocket] Status disconnected')
    }

    statusWs.value = ws
  }

  function connectLogsWs() {
    if (!jobId) return
    const ws = new WebSocket(`ws://localhost:${WS_LOGS_PORT}/ws/logs/${jobId}`)

    ws.onopen = () => {
      logsConnected.value = true
      console.log('[WebSocket] Logs connected')
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'workflow_log') {
          logCallbacks.forEach(cb => cb(msg as WorkflowLog))
        }
      } catch (e) {
        // 非 JSON 格式，忽略
      }
    }

    ws.onerror = () => {
      console.error('[WebSocket] Logs error')
    }

    ws.onclose = () => {
      logsConnected.value = false
      console.log('[WebSocket] Logs disconnected')
    }

    logsWs.value = ws
  }

  function connect() {
    connectStatusWs()
    connectLogsWs()
  }

  function disconnect() {
    statusWs.value?.close()
    logsWs.value?.close()
    statusWs.value = null
    logsWs.value = null
  }

  function onStatusUpdate(callback: (state: WorkflowState) => void) {
    statusCallbacks.push(callback)
    return () => {
      const idx = statusCallbacks.indexOf(callback)
      if (idx > -1) statusCallbacks.splice(idx, 1)
    }
  }

  function onLog(callback: (log: WorkflowLog) => void) {
    logCallbacks.push(callback)
    return () => {
      const idx = logCallbacks.indexOf(callback)
      if (idx > -1) logCallbacks.splice(idx, 1)
    }
  }

  onUnmounted(() => {
    disconnect()
  })

  return {
    statusConnected,
    logsConnected,
    connect,
    disconnect,
    onStatusUpdate,
    onLog
  }
}
