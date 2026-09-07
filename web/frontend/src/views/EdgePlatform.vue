<template>
  <div class="edge-platform-container">
    <div class="content-header">
      <div class="content-title">{{ pageTitle }}</div>
    </div>
    <div class="content-body">
      <!-- 默认显示 iframe -->
      <div v-if="!selectedAgent" class="platform-iframe-wrapper">
        <iframe
          ref="iframeRef"
          :src="platformUrl"
          class="platform-iframe"
          @load="onIframeLoad"
          @error="onIframeError"
        ></iframe>
        <div v-if="loading" class="loading-overlay">
          <div class="loading-spinner"></div>
          <div class="loading-text">正在加载边缘多智能体平台...</div>
        </div>
        <div v-if="error" class="error-overlay">
          <div class="error-icon">⚠️</div>
          <div class="error-text">{{ error }}</div>
          <div class="error-hint">请确保边缘智能平台服务已启动（python web/server.py）</div>
        </div>
      </div>

      <!-- 显示 System Prompt -->
      <div v-else class="prompt-viewer">
        <div class="prompt-header">
          <div class="prompt-info">
            <span class="prompt-icon">{{ agentIcon }}</span>
            <span class="prompt-name">{{ agentName }}</span>
          </div>
          <button class="btn btn-secondary" @click="selectedAgent = null">返回</button>
        </div>
        <div class="prompt-content">
          <div v-if="loadingPrompt" class="loading-overlay">
            <div class="loading-spinner"></div>
            <div class="loading-text">正在加载系统提示词...</div>
          </div>
          <pre v-else class="prompt-text">{{ promptContent || '暂无系统提示词' }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const platformUrl = 'http://localhost:8080/index.html'
const iframeRef = ref<HTMLIFrameElement | null>(null)
const loading = ref(true)
const error = ref('')
const selectedAgent = ref<string | null>(null)
const promptContent = ref('')
const loadingPrompt = ref(false)

const agentMap: Record<string, { name: string; icon: string }> = {
  MAIN: { name: '主调度 Agent', icon: '🎛️' },
  P1: { name: 'P1 作业票 Agent', icon: '📋' },
  P2: { name: 'P2 任务获取 Agent', icon: '📥' },
  P3: { name: 'P3 上下文 Agent', icon: '🧠' },
  P4: { name: 'P4 数据关联 Agent', icon: '📹' },
  P5: { name: 'P5 条件核验 Agent', icon: '✅' },
  P6: { name: 'P6 过程监测 Agent', icon: '👁️' },
  P7: { name: 'P7 风险研判 Agent', icon: '⚠️' },
  P8: { name: 'P8 人机处置 Agent', icon: '🔔' },
  P9: { name: 'P9 闭环跟踪 Agent', icon: '🔄' },
  P10: { name: 'P10 归档复盘 Agent', icon: '📦' },
}

const pageTitle = computed(() => {
  if (selectedAgent.value) {
    return agentMap[selectedAgent.value]?.name || selectedAgent.value
  }
  return '广东石化作业场景'
})

const agentIcon = computed(() => agentMap[selectedAgent.value || '']?.icon || '🤖')
const agentName = computed(() => agentMap[selectedAgent.value || '']?.name || '')

watch(() => route.query.agent, (agent) => {
  if (agent) {
    selectedAgent.value = agent as string
    loadPrompt(agent as string)
  } else {
    selectedAgent.value = null
    promptContent.value = ''
  }
}, { immediate: true })

async function loadPrompt(stage: string) {
  loadingPrompt.value = true
  try {
    const res = await fetch(`/api/system_prompt?stage=${stage}`)
    const data = await res.json()
    promptContent.value = data.content || ''
  } catch (e) {
    promptContent.value = '加载失败'
  }
  loadingPrompt.value = false
}

function onIframeLoad() {
  loading.value = false
  error.value = ''
}

function onIframeError() {
  loading.value = false
  error.value = '无法连接到边缘智能平台'
}
</script>

<style scoped>
.edge-platform-container {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.content-header {
  background: white;
  padding: 15px 25px;
  border-bottom: 1px solid #e0e0e0;
  flex-shrink: 0;
}

.content-title {
  font-size: 18px;
  font-weight: bold;
  color: #333;
}

.content-body {
  flex: 1;
  padding: 0;
  overflow: hidden;
  position: relative;
}

.platform-iframe-wrapper {
  position: relative;
  width: 100%;
  height: 100%;
}

.platform-iframe {
  width: 100%;
  height: 100%;
  border: none;
  background: white;
}

.prompt-viewer {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #f5f5f5;
}

.prompt-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 24px;
  background: white;
  border-bottom: 1px solid #e0e0e0;
}

.prompt-info {
  display: flex;
  align-items: center;
  gap: 10px;
}

.prompt-icon {
  font-size: 24px;
}

.prompt-name {
  font-size: 16px;
  font-weight: bold;
  color: #333;
}

.prompt-content {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
}

.prompt-text {
  background: white;
  padding: 24px;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
  box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}

.loading-overlay,
.error-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.95);
  z-index: 10;
}

.loading-spinner {
  width: 48px;
  height: 48px;
  border: 4px solid #e0e0e0;
  border-top-color: #667eea;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin-bottom: 16px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.loading-text {
  color: #666;
  font-size: 14px;
}

.error-icon {
  font-size: 48px;
  margin-bottom: 16px;
}

.error-text {
  color: #ff4757;
  font-size: 16px;
  font-weight: bold;
  margin-bottom: 8px;
}

.error-hint {
  color: #999;
  font-size: 13px;
}
</style>
