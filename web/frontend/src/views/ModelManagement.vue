<template>
  <div class="model-management-container">
    <div class="content-header">
      <div class="content-title">⚙️ 模型管理</div>
    </div>
    <div class="content-body">
      <!-- 当前配置卡片 -->
      <div class="config-card">
        <div class="config-grid">
          <div class="form-group">
            <label class="form-label">协议</label>
            <select class="form-input" v-model="config.protocol" @change="onProtocolChange">
              <option value="openai">OpenAI / OpenAI 兼容</option>
              <option value="anthropic">Anthropic Claude</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">模型名</label>
            <input type="text" class="form-input" v-model="config.model" placeholder="MiniMax-M3 / gpt-4o ...">
          </div>
          <div class="form-group full-width">
            <label class="form-label">Base URL</label>
            <input type="text" class="form-input" v-model="config.base_url" placeholder="https://api.minimaxi.com/v1">
            <div class="provider-hints">{{ providerHints }}</div>
          </div>
          <div class="form-group full-width">
            <label class="form-label">API Key</label>
            <div class="api-key-wrapper">
              <input :type="showApiKey ? 'text' : 'password'" class="form-input" v-model="config.api_key" placeholder="sk-..." style="padding-right: 40px;">
              <span class="eye-icon" @click="showApiKey = !showApiKey">
                <svg v-if="showApiKey" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
              </span>
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">Temperature</label>
            <input type="number" class="form-input" v-model="config.temperature" placeholder="0.2" step="0.1" min="0" max="2">
          </div>
          <div class="form-group">
            <label class="form-label">Max Tokens</label>
            <input type="number" class="form-input" v-model="config.max_tokens" placeholder="2048" step="1" min="1">
          </div>
        </div>
        <div class="btn-group">
          <button class="btn btn-primary" @click="testConnection" :disabled="testing">
            {{ testing ? '测试中...' : '🔌 测试连接' }}
          </button>
          <button class="btn btn-secondary" @click="saveConfig">💾 保存到 .env</button>
          <button class="btn btn-secondary" @click="showSaveProfileModal = true">💾 另存为</button>
          <span v-if="statusMessage" :class="['status-message', statusMessage.type]">{{ statusMessage.text }}</span>
        </div>
        <div v-if="testResult" :class="['test-result', testResult.ok ? 'success' : 'error']">
          {{ testResult.message }}
        </div>
      </div>

      <!-- 另存弹窗 -->
      <div v-if="showSaveProfileModal" class="save-profile-modal">
        <input type="text" class="form-input" v-model="profileName" placeholder="给这个配置起个名字，如：MiniMax-生产">
        <button class="btn btn-primary" @click="doSaveProfile">确认保存</button>
        <button class="btn btn-secondary" @click="showSaveProfileModal = false; profileName = ''">取消</button>
      </div>

      <!-- 配置历史 -->
      <div class="section-title">📁 配置历史</div>
      <div class="snapshots-list">
        <div v-for="snapshot in snapshots" :key="snapshot.name" class="snapshot-item" @click="loadSnapshot(snapshot)">
          <span class="snapshot-name">{{ snapshot.name }}</span>
          <span class="snapshot-time">{{ snapshot.time }}</span>
        </div>
        <div v-if="snapshots.length === 0" class="empty-hint">暂无配置历史</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'

interface Config {
  protocol: string
  model: string
  base_url: string
  api_key: string
  temperature: string
  max_tokens: string
}

interface Snapshot {
  name: string
  time: string
  config: Config
}

const config = ref<Config>({
  protocol: 'openai',
  model: '',
  base_url: '',
  api_key: '',
  temperature: '',
  max_tokens: ''
})

const snapshots = ref<Snapshot[]>([])
const testing = ref(false)
const testResult = ref<{ ok: boolean; message: string } | null>(null)
const statusMessage = ref<{ type: string; text: string } | null>(null)
const showApiKey = ref(false)
const showSaveProfileModal = ref(false)
const profileName = ref('')

const providerHints = computed(() => {
  if (config.value.protocol === 'openai') {
    return 'OpenAI 兼容接口，Base URL 通常为 https://api.openai.com/v1 或第三方兼容地址'
  } else {
    return 'Anthropic Claude 接口，Base URL 通常为 https://api.anthropic.com/v1'
  }
})

async function loadConfig() {
  try {
    const res = await fetch('/api/config')
    const data = await res.json()
    config.value = {
      protocol: data.protocol || 'openai',
      model: data.model || '',
      base_url: data.base_url || '',
      api_key: data.api_key || '',
      temperature: data.temperature || '',
      max_tokens: data.max_tokens || ''
    }
  } catch (e) {
    console.error('加载配置失败:', e)
  }
}

async function loadSnapshots() {
  try {
    const res = await fetch('/api/config/snapshots')
    const data = await res.json()
    if (data.snapshots) {
      snapshots.value = data.snapshots
    }
  } catch (e) {
    console.error('加载配置历史失败:', e)
  }
}

async function saveConfig() {
  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config.value)
    })
    if (res.ok) {
      showStatus('success', '配置已保存到 .env')
      loadSnapshots()
    }
  } catch (e) {
    showStatus('error', '保存失败')
  }
}

async function testConnection() {
  testing.value = true
  testResult.value = null
  try {
    const res = await fetch('/api/test/llm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config.value)
    })
    const data = await res.json()
    testResult.value = { ok: data.ok, message: data.message || data.error || '测试完成' }
  } catch (e) {
    testResult.value = { ok: false, message: '连接测试失败' }
  } finally {
    testing.value = false
  }
}

async function doSaveProfile() {
  if (!profileName.value.trim()) {
    showStatus('error', '请输入配置名称')
    return
  }
  try {
    const res = await fetch('/api/config/snapshots/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: profileName.value,
        config: config.value
      })
    })
    if (res.ok) {
      showStatus('success', '配置已保存')
      showSaveProfileModal.value = false
      profileName.value = ''
      loadSnapshots()
    }
  } catch (e) {
    showStatus('error', '保存失败')
  }
}

function loadSnapshot(snapshot: Snapshot) {
  config.value = { ...snapshot.config }
  showStatus('success', `已加载配置: ${snapshot.name}`)
}

function onProtocolChange() {
  // 协议切换时可以做一些提示
}

function showStatus(type: string, text: string) {
  statusMessage.value = { type, text }
  setTimeout(() => { statusMessage.value = null }, 3000)
}

onMounted(() => {
  loadConfig()
  loadSnapshots()
})
</script>

<style scoped>
.model-management-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 20px;
  overflow-y: auto;
}

.content-header {
  background: white;
  padding: 15px 25px;
  border-bottom: 1px solid #e0e0e0;
  margin: -20px -20px 20px -20px;
  flex-shrink: 0;
}

.content-title {
  font-size: 18px;
  font-weight: bold;
  color: #333;
}

.content-body {
  flex: 1;
}

.config-card {
  background: white;
  border: 1px solid #e0e0e0;
  border-radius: 10px;
  padding: 20px;
  margin-bottom: 20px;
}

.config-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 15px;
}

.form-group {
  display: flex;
  flex-direction: column;
}

.form-group.full-width {
  grid-column: 1 / -1;
}

.form-label {
  font-size: 13px;
  color: #666;
  margin-bottom: 5px;
}

.form-input {
  padding: 10px 12px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 14px;
  transition: border-color 0.2s;
}

.form-input:focus {
  outline: none;
  border-color: #667eea;
}

.api-key-wrapper {
  position: relative;
}

.eye-icon {
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  cursor: pointer;
  color: #888;
}

.provider-hints {
  margin-top: 4px;
  font-size: 12px;
  color: #999;
}

.btn-group {
  display: flex;
  gap: 10px;
  margin-top: 20px;
  align-items: center;
}

.btn {
  padding: 10px 20px;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
  transition: all 0.2s;
}

.btn-primary {
  background: #667eea;
  color: white;
}

.btn-primary:hover {
  background: #5568d3;
}

.btn-primary:disabled {
  background: #ccc;
  cursor: not-allowed;
}

.btn-secondary {
  background: #f5f5f5;
  color: #333;
}

.btn-secondary:hover {
  background: #e0e0e0;
}

.status-message {
  margin-left: 12px;
  font-size: 13px;
}

.status-message.success {
  color: #4CAF50;
}

.status-message.error {
  color: #f44336;
}

.test-result {
  margin-top: 15px;
  padding: 10px 15px;
  border-radius: 6px;
  font-size: 13px;
  white-space: pre-wrap;
}

.test-result.success {
  background: #e8f5e9;
  color: #2e7d32;
  border: 1px solid #4caf50;
}

.test-result.error {
  background: #ffebee;
  color: #c62828;
  border: 1px solid #f44336;
}

.save-profile-modal {
  background: #fff8e1;
  border: 1px solid #ffcc02;
  border-radius: 8px;
  padding: 15px;
  margin-bottom: 20px;
  display: flex;
  gap: 10px;
  align-items: center;
}

.save-profile-modal .form-input {
  flex: 1;
}

.section-title {
  font-size: 14px;
  font-weight: bold;
  color: #333;
  margin-bottom: 10px;
}

.snapshots-list {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.snapshot-item {
  background: #f5f5f5;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  padding: 10px 15px;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.snapshot-item:hover {
  background: #e0e0e0;
  border-color: #667eea;
}

.snapshot-name {
  font-size: 14px;
  font-weight: 500;
  color: #333;
}

.snapshot-time {
  font-size: 12px;
  color: #999;
}

.empty-hint {
  color: #999;
  font-size: 13px;
}
</style>
