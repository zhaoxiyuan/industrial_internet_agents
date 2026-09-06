<template>
  <div>
    <div class="content-header">
      <div class="content-title">🤖 智能场景</div>
      <button class="btn btn-primary" @click="showAgentModal()">+ 新建智能场景</button>
    </div>
    <div class="content-body">
      <div v-if="agents.length === 0" class="empty-state">
        <div class="empty-state-icon">🤖</div>
        <div class="empty-state-text">暂无智能场景，点击上方按钮创建</div>
      </div>
      <div v-else class="agent-list">
        <div
          v-for="agent in agents"
          :key="agent.name"
          class="agent-card"
          @click="goToEdgePlatform(agent.name)"
        >
          <div class="agent-icon">🤖</div>
          <div class="agent-info">
            <div class="agent-name">{{ agent.name }}</div>
          </div>
          <div class="agent-actions">
            <button class="btn btn-danger btn-sm" @click="deleteAgent(agent.name)">删除</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Agent 弹窗 -->
    <div v-if="agentModalVisible" class="modal-overlay" @click.self="closeAgentModal">
      <div class="modal">
        <div class="modal-header">
          <span>🤖</span>
          <h2>{{ editingAgent ? '编辑智能场景' : '新建智能场景' }}</h2>
          <button class="modal-close" @click="closeAgentModal">×</button>
        </div>
        <div class="modal-body">
          <div class="form-group">
            <label class="form-label">场景名称 *</label>
            <input type="text" class="form-input" v-model="agentForm.name" placeholder="例如：主调度场景" />
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary" @click="closeAgentModal">取消</button>
          <button class="btn btn-primary" @click="saveAgent">保存</button>
        </div>
      </div>
    </div>

    <!-- Toast -->
    <div :class="['toast', { error: toastError }]" ref="toast">{{ toastMessage }}</div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

interface Agent {
  name: string
}

const agents = ref<Agent[]>([])
const agentModalVisible = ref(false)
const editingAgent = ref<Agent | null>(null)
const agentForm = ref({
  name: ''
})
const toastMessage = ref('')
const toastError = ref(false)
const toast = ref<HTMLElement | null>(null)

function showAgentModal(agent: Agent | null = null) {
  editingAgent.value = agent
  agentForm.value = {
    name: agent ? agent.name : ''
  }
  agentModalVisible.value = true
}

function closeAgentModal() {
  agentModalVisible.value = false
  editingAgent.value = null
}

function goToEdgePlatform(sceneName: string) {
  router.push({ path: '/edge-platform', query: { scene: sceneName } })
}

async function loadAgents() {
  try {
    const res = await fetch('/api/agents')
    const data = await res.json()
    if (data.status === 'ok') agents.value = data.agents || []
  } catch (e) { console.error('加载 Agent 失败:', e) }
}

async function saveAgent() {
  const { name } = agentForm.value
  if (!name.trim()) { showToast('请输入场景名称', true); return }

  const action = editingAgent.value ? 'update' : 'create'
  try {
    const res = await fetch('/api/agents', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, name })
    })
    const data = await res.json()
    if (data.status === 'ok') {
      showToast(editingAgent.value ? '场景已更新' : '场景已创建')
      closeAgentModal()
      loadAgents()
    } else {
      showToast(data.message || '操作失败', true)
    }
  } catch (e) { showToast('保存失败', true) }
}

async function deleteAgent(agentName: string) {
  if (!confirm(`确定要删除智能场景 "${agentName}" 吗？`)) return
  try {
    const res = await fetch('/api/agents', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'delete', name: agentName })
    })
    const data = await res.json()
    if (data.status === 'ok') { showToast('场景已删除'); loadAgents() }
    else showToast(data.message || '删除失败', true)
  } catch (e) { showToast('删除失败', true) }
}

function showToast(message: string, isError = false) {
  toastMessage.value = message
  toastError.value = isError
  if (toast.value) {
    toast.value.style.display = 'block'
    setTimeout(() => { if (toast.value) toast.value.style.display = 'none' }, 3000)
  }
}

onMounted(() => { loadAgents() })
</script>

<style scoped>
.content-header {
  background: white;
  padding: 15px 25px;
  border-bottom: 1px solid #e0e0e0;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.content-title { font-size: 18px; font-weight: bold; color: #333; }

.content-body { flex: 1; padding: 25px; overflow-y: auto; }

.btn {
  padding: 10px 20px;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  transition: all 0.2s;
}
.btn-primary { background: #667eea; color: white; }
.btn-primary:hover { background: #5568d3; }
.btn-secondary { background: #f5f5f5; color: #333; }
.btn-secondary:hover { background: #e0e0e0; }
.btn-danger { background: #ff4757; color: white; }
.btn-danger:hover { background: #ff3344; }
.btn-sm { padding: 6px 12px; font-size: 12px; }

.empty-state { text-align: center; padding: 60px 20px; color: #999; }
.empty-state-icon { font-size: 64px; margin-bottom: 20px; opacity: 0.5; }
.empty-state-text { font-size: 16px; margin-bottom: 20px; }

.agent-list { display: flex; flex-direction: column; gap: 15px; }

.agent-card {
  background: white;
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  border-left: 4px solid #667eea;
  display: flex;
  align-items: center;
  gap: 20px;
}
.agent-card.main-agent { border-left-color: #764ba2; background: linear-gradient(135deg, #f8f7ff, #fff); }

.agent-icon {
  width: 56px;
  height: 56px;
  background: linear-gradient(135deg, #667eea, #764ba2);
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  color: white;
  flex-shrink: 0;
}
.agent-card.main-agent .agent-icon { background: linear-gradient(135deg, #764ba2, #667eea); }

.agent-info { flex: 1; }
.agent-name { font-size: 18px; font-weight: bold; color: #333; margin-bottom: 4px; }
.agent-type {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: bold;
  margin-left: 8px;
}
.agent-type.main { background: #764ba2; color: white; }
.agent-type.normal { background: #667eea; color: white; }
.agent-model { font-size: 13px; color: #666; margin-bottom: 6px; }
.agent-skills { display: flex; flex-wrap: wrap; gap: 5px; }
.skill-badge { background: #f0f0f0; color: #666; padding: 2px 8px; border-radius: 4px; font-size: 11px; }
.agent-actions { display: flex; gap: 8px; flex-shrink: 0; }

.modal-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.5);
  z-index: 1000;
  display: flex;
  justify-content: center;
  align-items: center;
}
.modal { background: white; border-radius: 16px; width: 90%; max-width: 700px; max-height: 85vh; overflow: hidden; }
.modal-header {
  background: linear-gradient(135deg, #667eea, #764ba2);
  color: white;
  padding: 20px 25px;
  display: flex;
  align-items: center;
  gap: 15px;
}
.modal-header h2 { font-size: 20px; flex: 1; margin: 0; }
.modal-close {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: rgba(255,255,255,0.2);
  border: none;
  color: white;
  font-size: 20px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}
.modal-close:hover { background: rgba(255,255,255,0.3); }
.modal-body { padding: 25px; max-height: 65vh; overflow-y: auto; }
.modal-footer { padding: 15px 25px; border-top: 1px solid #eee; display: flex; justify-content: flex-end; gap: 10px; }

.form-group { margin-bottom: 18px; }
.form-label { display: block; font-size: 13px; font-weight: 600; color: #555; margin-bottom: 6px; }
.form-input, .form-textarea, .form-select {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #ddd;
  border-radius: 8px;
  font-size: 14px;
  transition: border-color 0.2s;
}
.form-input:focus, .form-textarea:focus, .form-select:focus { outline: none; border-color: #667eea; }
.form-textarea { min-height: 120px; resize: vertical; }
.form-hint { font-size: 11px; color: #999; margin-top: 4px; }

.checkbox-group-inline { display: flex; flex-wrap: wrap; gap: 8px; }
.checkbox-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: #f5f5f5;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  transition: all 0.2s;
}
.checkbox-item:hover { background: #e8e8ff; }
.checkbox-item.selected { background: #667eea; color: white; }

.toast {
  position: fixed;
  top: 20px;
  right: 20px;
  padding: 12px 20px;
  background: #4CAF50;
  color: white;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.2);
  z-index: 2000;
  display: none;
  animation: slideIn 0.3s ease;
}
.toast.error { background: #ff4757; }
@keyframes slideIn {
  from { transform: translateX(100%); opacity: 0; }
  to { transform: translateX(0); opacity: 1; }
}
</style>
