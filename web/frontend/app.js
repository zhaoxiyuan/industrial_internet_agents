// ========== 全局状态 ==========
const state = {
    skills: [],
    agents: [],
    currentTab: 'agents',
    editingSkill: null,
    editingAgent: null
};

// ========== 初始化 ==========
document.addEventListener('DOMContentLoaded', function() {
    initMenu();
    loadSkills();
    loadAgents();
});

// ========== 菜单切换 ==========
function initMenu() {
    document.querySelectorAll('.sidebar-item').forEach(item => {
        item.addEventListener('click', function() {
            const page = this.dataset.page;
            switchPage(page);
        });
    });
}

function switchPage(page) {
    // 更新侧边栏菜单状态
    document.querySelectorAll('.sidebar-item').forEach(item => {
        item.classList.toggle('active', item.dataset.page === page);
    });

    // 更新页面显示
    document.querySelectorAll('.page-content').forEach(p => {
        p.classList.toggle('active', p.id === 'page-' + page);
    });

    state.currentTab = page;
}

// ========== 数据加载 ==========
function loadSkills() {
    fetch('/api/skills')
        .then(r => r.json())
        .then(data => {
            if (data.status === 'ok') {
                state.skills = data.skills || [];
                renderSkills();
                updateBadge('skill-count', state.skills.length);
            }
        })
        .catch(err => {
            console.error('加载 Skill 失败:', err);
            showToast('加载 Skill 失败', true);
        });
}

function loadAgents() {
    fetch('/api/agents')
        .then(r => r.json())
        .then(data => {
            if (data.status === 'ok') {
                state.agents = data.agents || [];
                renderAgents();
                updateBadge('agent-count', state.agents.length);
            }
        })
        .catch(err => {
            console.error('加载 Agent 失败:', err);
            showToast('加载 Agent 失败', true);
        });
}

function updateBadge(id, count) {
    const el = document.getElementById(id);
    if (el) el.textContent = count;
}

// ========== Skill 渲染 ==========
function renderSkills() {
    const container = document.getElementById('skill-grid');
    const searchTerm = document.getElementById('skill-search')?.value?.toLowerCase() || '';

    const filtered = state.skills.filter(skill =>
        skill.name.toLowerCase().includes(searchTerm) ||
        (skill.description || '').toLowerCase().includes(searchTerm)
    );

    if (filtered.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">🛠️</div>
                <div class="empty-state-text">${state.skills.length === 0 ? 'Skill 广场为空，点击上方按钮创建' : '未找到匹配的 Skill'}</div>
            </div>
        `;
        return;
    }

    container.innerHTML = filtered.map(skill => `
        <div class="skill-card">
            <div class="skill-card-header">
                <div class="skill-icon">🛠️</div>
                <div>
                    <div class="skill-name">${escapeHtml(skill.name)}</div>
                </div>
            </div>
            <div class="skill-desc">${escapeHtml(skill.description || '暂无描述')}</div>
            <div class="skill-tools">
                ${(skill.tools || []).map(t => `<span class="tool-tag">${escapeHtml(t)}</span>`).join('')}
            </div>
            <div class="skill-actions">
                <button class="btn btn-secondary btn-sm" onclick="viewSkill('${escapeHtml(skill.name)}')">查看</button>
                <button class="btn btn-secondary btn-sm" onclick="editSkill('${escapeHtml(skill.name)}')">编辑</button>
                <button class="btn btn-danger btn-sm" onclick="deleteSkill('${escapeHtml(skill.name)}')">删除</button>
            </div>
        </div>
    `).join('');
}

function filterSkills() {
    renderSkills();
}

// ========== Agent 渲染 ==========
function renderAgents() {
    const container = document.getElementById('agent-list');

    if (state.agents.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">🤖</div>
                <div class="empty-state-text">暂无 Agent，点击上方按钮创建</div>
            </div>
        `;
        return;
    }

    container.innerHTML = state.agents.map(agent => {
        const isMain = agent.type === 'main';
        const skillBadges = (agent.skills || []).map(skillName => {
            const skill = state.skills.find(s => s.name === skillName);
            return `<span class="skill-badge">${escapeHtml(skill ? skill.name : skillName)}</span>`;
        }).join('');

        return `
            <div class="agent-card ${isMain ? 'main-agent' : ''}">
                <div class="agent-icon">${isMain ? '🎛️' : '🤖'}</div>
                <div class="agent-info">
                    <div class="agent-name">
                        ${escapeHtml(agent.name)}
                        <span class="agent-type ${isMain ? 'main' : 'normal'}">${isMain ? '主 Agent' : '普通 Agent'}</span>
                    </div>
                    <div class="agent-model">模型: ${escapeHtml(agent.model || '默认')}</div>
                    <div class="agent-skills">${skillBadges || '<span style="color:#999;font-size:12px;">未绑定 Skill</span>'}</div>
                </div>
                <div class="agent-actions">
                    <button class="btn btn-secondary btn-sm" onclick="editAgent('${escapeHtml(agent.name)}')">编辑</button>
                    ${!isMain ? `<button class="btn btn-danger btn-sm" onclick="deleteAgent('${escapeHtml(agent.name)}')">删除</button>` : '<span style="color:#999;font-size:11px;">主 Agent 不可删除</span>'}
                </div>
            </div>
        `;
    }).join('');
}

// ========== Skill 弹窗 ==========
function showSkillModal(skill = null) {
    state.editingSkill = skill;
    document.getElementById('skill-modal-icon').textContent = skill ? '🛠️' : '🛠️';
    document.getElementById('skill-modal-title').textContent = skill ? '编辑 Skill' : '新建 Skill';

    document.getElementById('skill-name').value = skill ? skill.name : '';
    document.getElementById('skill-description').value = skill ? skill.description || '' : '';
    document.getElementById('skill-tools').value = skill ? (skill.tools || []).join(', ') : '';
    document.getElementById('skill-prompt-injection').value = skill ? skill.prompt_injection || '' : '';

    document.getElementById('skill-modal').classList.add('active');
}

function closeSkillModal() {
    document.getElementById('skill-modal').classList.remove('active');
    state.editingSkill = null;
}

function editSkill(skillName) {
    const skill = state.skills.find(s => s.name === skillName);
    if (skill) {
        showSkillModal(skill);
    }
}

function saveSkill() {
    const name = document.getElementById('skill-name').value.trim();
    const description = document.getElementById('skill-description').value.trim();
    const toolsStr = document.getElementById('skill-tools').value.trim();
    const prompt_injection = document.getElementById('skill-prompt-injection').value.trim();

    if (!name) {
        showToast('请输入 Skill 名称', true);
        return;
    }

    const tools = toolsStr ? toolsStr.split(',').map(t => t.trim()).filter(t => t) : [];

    const data = {
        name,
        description,
        tools,
        prompt_injection
    };

    const action = state.editingSkill ? 'update' : 'create';

    fetch('/api/skills', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, ...data })
    })
    .then(r => r.json())
    .then(res => {
        if (res.status === 'ok') {
            showToast(state.editingSkill ? 'Skill 已更新' : 'Skill 已创建');
            closeSkillModal();
            loadSkills();
            loadAgents(); // 刷新 Agent 的 Skill 选择器
        } else {
            showToast(res.message || '操作失败', true);
        }
    })
    .catch(err => {
        console.error('保存 Skill 失败:', err);
        showToast('保存失败', true);
    });
}

function deleteSkill(skillName) {
    if (!confirm(`确定要删除 Skill "${skillName}" 吗？`)) return;

    fetch('/api/skills', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'delete', name: skillName })
    })
    .then(r => r.json())
    .then(res => {
        if (res.status === 'ok') {
            showToast('Skill 已删除');
            loadSkills();
            loadAgents();
        } else {
            showToast(res.message || '删除失败', true);
        }
    })
    .catch(err => {
        console.error('删除 Skill 失败:', err);
        showToast('删除失败', true);
    });
}

function viewSkill(skillName) {
    const skill = state.skills.find(s => s.name === skillName);
    if (!skill) return;

    document.getElementById('view-skill-name').textContent = skill.name;
    document.getElementById('view-skill-desc').textContent = skill.description || '暂无描述';
    document.getElementById('view-skill-tools').innerHTML = (skill.tools || []).map(t => `<span class="tool-tag">${escapeHtml(t)}</span>`).join('') || '<span style="color:#999">暂无</span>';
    document.getElementById('view-skill-content').innerHTML = renderMarkdown(skill.prompt_injection || '暂无内容');

    document.getElementById('view-skill-modal').classList.add('active');
}

function renderMarkdown(text) {
    if (!text) return '';
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        .replace(/^## (.+)$/gm, '<h2>$1</h2>')
        .replace(/^# (.+)$/gm, '<h1>$1</h1>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/`(.+?)`/g, '<code>$1</code>')
        .replace(/^- (.+)$/gm, '<li>$1</li>')
        .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
        .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
        .replace(/\n\n/g, '</p><p>')
        .replace(/\n/g, '<br>');
}

function closeViewSkillModal() {
    document.getElementById('view-skill-modal').classList.remove('active');
}

function uploadSkillZip(input) {
    const file = input.files[0];
    if (!file) return;

    if (!file.name.endsWith('.zip')) {
        showToast('请选择 ZIP 格式的文件', true);
        input.value = '';
        return;
    }

    const formData = new FormData();
    formData.append('file', file);

    showToast('正在上传...');

    fetch('/api/skills/upload', {
        method: 'POST',
        body: formData
    })
    .then(r => r.json())
    .then(res => {
        if (res.status === 'ok') {
            showToast(res.message || '上传成功');
            loadSkills();
            loadAgents();
        } else {
            showToast(res.message || '上传失败', true);
        }
    })
    .catch(err => {
        console.error('上传失败:', err);
        showToast('上传失败', true);
    })
    .finally(() => {
        input.value = '';
    });
}

// ========== Agent 弹窗 ==========
function showAgentModal(agent = null) {
    state.editingAgent = agent;
    document.getElementById('agent-modal-icon').textContent = agent ? '🤖' : '🤖';
    document.getElementById('agent-modal-title').textContent = agent ? '编辑 Agent' : '新建 Agent';

    document.getElementById('agent-name').value = agent ? agent.name : '';
    document.getElementById('agent-type').value = agent ? agent.type : 'normal';
    document.getElementById('agent-model').value = agent ? agent.model || '' : '';
    document.getElementById('agent-system-prompt').value = agent ? agent.system_prompt || '' : '';
    document.getElementById('agent-prompt-injection').value = agent ? agent.prompt_injection || '' : '';

    // 渲染 Skill 选择器
    renderSkillSelector(agent ? agent.skills : []);

    document.getElementById('agent-modal').classList.add('active');
}

function closeAgentModal() {
    document.getElementById('agent-modal').classList.remove('active');
    state.editingAgent = null;
}

function onAgentTypeChange() {
    // 可以在这里添加主 Agent 特殊逻辑
}

function renderSkillSelector(selectedSkills = []) {
    const container = document.getElementById('skill-selector');

    if (state.skills.length === 0) {
        container.innerHTML = '<div class="empty-state-text" style="padding: 10px; font-size: 12px;">暂无 Skill，请先创建</div>';
        return;
    }

    container.innerHTML = state.skills.map(skill => {
        const isSelected = selectedSkills.includes(skill.name);
        return `
            <div class="checkbox-item ${isSelected ? 'selected' : ''}" data-skill-name="${escapeHtml(skill.name)}">
                ${escapeHtml(skill.name)}
            </div>
        `;
    }).join('');

    // 绑定点击事件
    container.querySelectorAll('.checkbox-item').forEach(item => {
        item.onclick = () => toggleSkillSelection(item, item.dataset.skillName);
    });
}

function toggleSkillSelection(el, skillName) {
    el.classList.toggle('selected');
}

function getSelectedSkills() {
    const items = document.querySelectorAll('#skill-selector .checkbox-item.selected');
    return Array.from(items).map(item => item.dataset.skillName);
}

function editAgent(agentName) {
    const agent = state.agents.find(a => a.name === agentName);
    if (agent) {
        showAgentModal(agent);
    }
}

function saveAgent() {
    const name = document.getElementById('agent-name').value.trim();
    const type = document.getElementById('agent-type').value;
    const model = document.getElementById('agent-model').value.trim();
    const system_prompt = document.getElementById('agent-system-prompt').value.trim();
    const prompt_injection = document.getElementById('agent-prompt-injection').value.trim();
    const skills = getSelectedSkills();

    if (!name) {
        showToast('请输入 Agent 名称', true);
        return;
    }

    const data = {
        name,
        type,
        model,
        system_prompt,
        prompt_injection,
        skills
    };

    const action = state.editingAgent ? 'update' : 'create';

    fetch('/api/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, ...data })
    })
    .then(r => r.json())
    .then(res => {
        if (res.status === 'ok') {
            showToast(state.editingAgent ? 'Agent 已更新' : 'Agent 已创建');
            closeAgentModal();
            loadAgents();
        } else {
            showToast(res.message || '操作失败', true);
        }
    })
    .catch(err => {
        console.error('保存 Agent 失败:', err);
        showToast('保存失败', true);
    });
}

function deleteAgent(agentName) {
    if (!confirm(`确定要删除 Agent "${agentName}" 吗？`)) return;

    fetch('/api/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'delete', name: agentName })
    })
    .then(r => r.json())
    .then(res => {
        if (res.status === 'ok') {
            showToast('Agent 已删除');
            loadAgents();
        } else {
            showToast(res.message || '删除失败', true);
        }
    })
    .catch(err => {
        console.error('删除 Agent 失败:', err);
        showToast('删除失败', true);
    });
}

// ========== 工具函数 ==========
function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function showToast(message, isError = false) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = 'toast' + (isError ? ' error' : '');
    toast.style.display = 'block';
    setTimeout(() => {
        toast.style.display = 'none';
    }, 3000);
}
