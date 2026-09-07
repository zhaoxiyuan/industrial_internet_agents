<template>
  <div class="skill-square-container">
    <div class="content-header">
      <div class="content-title">🛠️ Skill 广场</div>
      <div style="display: flex; gap: 10px;">
        <button class="btn btn-secondary" @click="triggerUpload">📦 上传压缩包</button>
        <button class="btn btn-primary" @click="showSkillModal()">+ 新建 Skill</button>
        <input type="file" ref="fileInput" accept=".zip" style="display: none;" @change="uploadSkillZip" />
      </div>
    </div>
    <div class="content-body">
      <div class="search-bar">
        <input type="text" class="search-input" v-model="skillSearch" placeholder="搜索 Skill 名称或描述..." />
      </div>
      <div v-if="paginatedSkills.length === 0" class="empty-state">
        <div class="empty-state-icon">🛠️</div>
        <div class="empty-state-text">
          {{ skills.length === 0 ? 'Skill 广场为空，点击上方按钮创建' : '未找到匹配的 Skill' }}
        </div>
      </div>
      <div v-else class="skill-grid">
        <div v-for="skill in paginatedSkills" :key="skill.name" class="skill-card">
          <div class="skill-card-header">
            <div class="skill-icon">🛠️</div>
            <div>
              <div class="skill-name">{{ skill.name }}</div>
            </div>
          </div>
          <div class="skill-desc">{{ skill.description || '暂无描述' }}</div>
          <div class="skill-tools">
            <span v-for="tool in skill.tools || []" :key="tool" class="tool-tag">{{ tool }}</span>
          </div>
          <div class="skill-actions">
            <button class="btn btn-secondary btn-sm" @click="viewSkill(skill.name)">查看</button>
            <button class="btn btn-secondary btn-sm" @click="editSkill(skill.name)">编辑</button>
            <button class="btn btn-danger btn-sm" @click="deleteSkill(skill.name)">删除</button>
          </div>
        </div>
      </div>
      <!-- 分页 -->
      <div v-if="totalPages > 1" class="pagination">
        <button class="btn btn-secondary btn-sm" :disabled="currentPage === 1" @click="currentPage--">上一页</button>
        <span class="page-info">{{ currentPage }} / {{ totalPages }}</span>
        <button class="btn btn-secondary btn-sm" :disabled="currentPage === totalPages" @click="currentPage++">下一页</button>
      </div>
    </div>

    <!-- Skill 弹窗 -->
    <div v-if="skillModalVisible" class="modal-overlay" @click.self="closeSkillModal">
      <div class="modal">
        <div class="modal-header">
          <span>🛠️</span>
          <h2>{{ editingSkill ? '编辑 Skill' : '新建 Skill' }}</h2>
          <button class="modal-close" @click="closeSkillModal">×</button>
        </div>
        <div class="modal-body">
          <div class="form-group">
            <label class="form-label">Skill 名称 * (英文，用连词符连接)</label>
            <input type="text" class="form-input" v-model="skillForm.name" placeholder="例如：project-knowledge-query" />
          </div>
          <div class="form-group">
            <label class="form-label">描述</label>
            <textarea class="form-textarea" v-model="skillForm.description" rows="2" placeholder="描述这个 Skill 的功能和使用场景"></textarea>
          </div>
          <div class="form-group">
            <label class="form-label">绑定工具</label>
            <input type="text" class="form-input" v-model="skillForm.toolsStr" placeholder="多个工具用逗号分隔，如: get_project_docs" />
            <div class="form-hint">输入已实现的工具函数名，多个用逗号分隔</div>
          </div>
          <div class="form-group">
            <label class="form-label">Skill 内容</label>
            <textarea class="form-textarea" v-model="skillForm.prompt_injection" rows="10" placeholder="Skill 的完整内容，支持 Markdown 格式"></textarea>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary" @click="closeSkillModal">取消</button>
          <button class="btn btn-primary" @click="saveSkill">保存</button>
        </div>
      </div>
    </div>

    <!-- Skill 查看弹窗 -->
    <div v-if="viewSkillModalVisible" class="modal-overlay" @click.self="closeViewSkillModal">
      <div class="modal" style="max-width: 800px;">
        <div class="modal-header">
          <span>🛠️</span>
          <h2>{{ viewingSkill?.name }}</h2>
          <button class="modal-close" @click="closeViewSkillModal">×</button>
        </div>
        <div class="modal-body">
          <div class="form-group">
            <label class="form-label">描述</label>
            <div class="view-field">{{ viewingSkill?.description || '暂无描述' }}</div>
          </div>
          <div class="form-group">
            <label class="form-label">绑定工具</label>
            <div class="skill-tools">
              <template v-if="viewingSkill?.tools?.length">
                <span v-for="tool in viewingSkill.tools" :key="tool" class="tool-tag">{{ tool }}</span>
              </template>
              <span v-else>暂无</span>
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">Skill 内容</label>
            <div class="md-preview" v-html="renderMarkdown(viewingSkill?.prompt_injection || '暂无内容')"></div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-primary" @click="closeViewSkillModal">关闭</button>
        </div>
      </div>
    </div>

    <div :class="['toast', { error: toastError }]" ref="toast">{{ toastMessage }}</div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'

interface Skill {
  name: string
  description?: string
  tools?: string[]
  prompt_injection?: string
}

const skills = ref<Skill[]>([])
const skillSearch = ref('')
const skillModalVisible = ref(false)
const viewSkillModalVisible = ref(false)
const editingSkill = ref<Skill | null>(null)
const viewingSkill = ref<Skill | null>(null)
const skillForm = ref({ name: '', description: '', toolsStr: '', prompt_injection: '' })
const toastMessage = ref('')
const toastError = ref(false)
const toast = ref<HTMLElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const currentPage = ref(1)
const pageSize = ref(10)

const filteredSkills = computed(() => {
  const term = skillSearch.value.toLowerCase()
  if (!term) return skills.value
  return skills.value.filter(s =>
    s.name.toLowerCase().includes(term) || (s.description || '').toLowerCase().includes(term)
  )
})

const totalPages = computed(() => Math.ceil(filteredSkills.value.length / pageSize.value))

const paginatedSkills = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredSkills.value.slice(start, start + pageSize.value)
})

watch(skillSearch, () => { currentPage.value = 1 })

function showSkillModal(skill: Skill | null = null) {
  editingSkill.value = skill
  if (skill) {
    skillForm.value = { name: skill.name, description: skill.description || '', toolsStr: (skill.tools || []).join(', '), prompt_injection: skill.prompt_injection || '' }
  } else {
    skillForm.value = { name: '', description: '', toolsStr: '', prompt_injection: '' }
  }
  skillModalVisible.value = true
}

function closeSkillModal() { skillModalVisible.value = false; editingSkill.value = null }

function editSkill(skillName: string) {
  const skill = skills.value.find(s => s.name === skillName)
  if (skill) showSkillModal(skill)
}

function viewSkill(skillName: string) {
  const skill = skills.value.find(s => s.name === skillName)
  if (skill) { viewingSkill.value = skill; viewSkillModalVisible.value = true }
}

function closeViewSkillModal() { viewSkillModalVisible.value = false; viewingSkill.value = null }

async function loadSkills() {
  try {
    const res = await fetch('/api/skills')
    const data = await res.json()
    if (data.status === 'ok') skills.value = data.skills || []
  } catch (e) { console.error('加载 Skill 失败:', e) }
}

async function saveSkill() {
  const { name, description, toolsStr, prompt_injection } = skillForm.value
  if (!name.trim()) { showToast('请输入 Skill 名称', true); return }
  const tools = toolsStr ? toolsStr.split(',').map(t => t.trim()).filter(t => t) : []
  const action = editingSkill.value ? 'update' : 'create'
  try {
    const res = await fetch('/api/skills', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, name, description, tools, prompt_injection })
    })
    const data = await res.json()
    if (data.status === 'ok') { showToast(editingSkill.value ? 'Skill 已更新' : 'Skill 已创建'); closeSkillModal(); loadSkills() }
    else showToast(data.message || '操作失败', true)
  } catch (e) { showToast('保存失败', true) }
}

async function deleteSkill(skillName: string) {
  if (!confirm(`确定要删除 Skill "${skillName}" 吗？`)) return
  try {
    const res = await fetch('/api/skills', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'delete', name: skillName })
    })
    const data = await res.json()
    if (data.status === 'ok') { showToast('Skill 已删除'); loadSkills() }
    else showToast(data.message || '删除失败', true)
  } catch (e) { showToast('删除失败', true) }
}

function triggerUpload() { fileInput.value?.click() }

async function uploadSkillZip(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  if (!file.name.endsWith('.zip')) { showToast('请选择 ZIP 格式的文件', true); input.value = ''; return }
  const formData = new FormData()
  formData.append('file', file)
  showToast('正在上传...')
  try {
    const res = await fetch('/api/skills/upload', { method: 'POST', body: formData })
    const data = await res.json()
    if (data.status === 'ok') { showToast(data.message || '上传成功'); loadSkills() }
    else showToast(data.message || '上传失败', true)
  } catch (e) { showToast('上传失败', true) }
  finally { input.value = '' }
}

function renderMarkdown(text: string): string {
  if (!text) return ''
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>').replace(/^## (.+)$/gm, '<h2>$1</h2>').replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`(.+?)`/g, '<code>$1</code>').replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
    .replace(/\n\n/g, '</p><p>').replace(/\n/g, '<br>')
}

function showToast(message: string, isError = false) {
  toastMessage.value = message
  toastError.value = isError
  if (toast.value) { toast.value.style.display = 'block'; setTimeout(() => { if (toast.value) toast.value.style.display = 'none' }, 3000) }
}

onMounted(() => loadSkills())
</script>

<style scoped>
.skill-square-container { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.content-header { background: white; padding: 15px 25px; border-bottom: 1px solid #e0e0e0; display: flex; align-items: center; justify-content: space-between; flex-shrink: 0; }
.content-title { font-size: 18px; font-weight: bold; color: #333; }
.content-body { flex: 1; padding: 25px; overflow-y: auto; min-height: 0; }

.btn { padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; transition: all 0.2s; }
.btn-primary { background: #667eea; color: white; }
.btn-primary:hover { background: #5568d3; }
.btn-secondary { background: #f5f5f5; color: #333; }
.btn-secondary:hover { background: #e0e0e0; }
.btn-danger { background: #ff4757; color: white; }
.btn-danger:hover { background: #ff3344; }
.btn-sm { padding: 6px 12px; font-size: 12px; }

.search-bar { display: flex; gap: 10px; margin-bottom: 20px; }
.search-input { flex: 1; padding: 10px 15px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; }
.search-input:focus { outline: none; border-color: #667eea; }

.empty-state { text-align: center; padding: 60px 20px; color: #999; }
.empty-state-icon { font-size: 64px; margin-bottom: 20px; opacity: 0.5; }
.empty-state-text { font-size: 16px; margin-bottom: 20px; }

.skill-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 20px; }
.skill-card {
  background: white;
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  border: 1px solid #eee;
  transition: all 0.2s;
}
.skill-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.12); }
.skill-card-header { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.skill-icon { width: 48px; height: 48px; background: linear-gradient(135deg, #667eea, #764ba2); border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 24px; color: white; }
.skill-name { font-size: 16px; font-weight: bold; color: #333; }
.skill-desc { font-size: 13px; color: #666; margin-bottom: 12px; line-height: 1.5; }
.skill-tools { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 12px; }
.tool-tag { background: #e8e8ff; color: #667eea; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-family: monospace; }
.skill-actions { display: flex; gap: 8px; padding-top: 12px; border-top: 1px solid #eee; }

.modal-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 1000; display: flex; justify-content: center; align-items: center; }
.modal { background: white; border-radius: 16px; width: 90%; max-width: 700px; max-height: 85vh; overflow: hidden; }
.modal-header { background: linear-gradient(135deg, #667eea, #764ba2); color: white; padding: 20px 25px; display: flex; align-items: center; gap: 15px; }
.modal-header h2 { font-size: 20px; flex: 1; margin: 0; }
.modal-close { width: 32px; height: 32px; border-radius: 50%; background: rgba(255,255,255,0.2); border: none; color: white; font-size: 20px; cursor: pointer; display: flex; align-items: center; justify-content: center; }
.modal-close:hover { background: rgba(255,255,255,0.3); }
.modal-body { padding: 25px; max-height: 65vh; overflow-y: auto; }
.modal-footer { padding: 15px 25px; border-top: 1px solid #eee; display: flex; justify-content: flex-end; gap: 10px; }

.form-group { margin-bottom: 18px; }
.form-label { display: block; font-size: 13px; font-weight: 600; color: #555; margin-bottom: 6px; }
.form-input, .form-textarea { width: 100%; padding: 10px 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; }
.form-input:focus, .form-textarea:focus { outline: none; border-color: #667eea; }
.form-textarea { min-height: 120px; resize: vertical; }
.form-hint { font-size: 11px; color: #999; margin-top: 4px; }

.view-field { color: #666; padding: 10px; background: #f5f5f5; border-radius: 6px; }
.md-preview h1 { font-size: 20px; margin: 0 0 15px; color: #333; }
.md-preview h2 { font-size: 18px; margin: 15px 0 10px; color: #333; }
.md-preview h3 { font-size: 16px; margin: 12px 0 8px; color: #333; }
.md-preview p { margin: 10px 0; line-height: 1.6; }
.md-preview code { background: #f0f0f0; padding: 2px 6px; border-radius: 3px; font-family: monospace; }
.md-preview pre { background: #1e1e1e; color: #d4d4d4; padding: 15px; border-radius: 6px; overflow-x: auto; }

.toast { position: fixed; top: 20px; right: 20px; padding: 12px 20px; background: #4CAF50; color: white; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.2); z-index: 2000; display: none; animation: slideIn 0.3s ease; }
.toast.error { background: #ff4757; }
@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }

.pagination { display: flex; align-items: center; justify-content: center; gap: 15px; margin-top: 25px; padding-top: 20px; border-top: 1px solid #eee; }
.page-info { font-size: 14px; color: #666; }
.pagination .btn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
