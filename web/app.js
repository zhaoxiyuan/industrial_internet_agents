// ========== 全局状态 ==========
const state = {
    currentTab: 'config',
    selectedMenu: 'model-config',
    workflowState: {
        status: 'idle',
        pending: [],
        confirmed: [],
        currentStage: '',
        threadId: null
    },
    selectedWorkflowNode: null,
    ws: null,           // 状态 WebSocket 连接
    logsWs: null        // 日志 WebSocket 连接
};

// ========== 阶段信息 ==========
const STAGE_INFO = {
    MAIN: {
        name: '主调度中心', icon: '🎛️', color: '#673AB7', humanConfirm: null,
        tools: ['start_workflow', 'get_status', 'confirm_stage', 'list_pending'],
        activity: '接收作业申请，启动 P1-P10 完整工作流；实时查询状态；协调人工确认',
        inputs: '作业申请、用户指令',
        outputs: '工作流状态、待确认项列表',
        intelligence: '作为主调度 Agent，负责协调 P1-P10 完整作业流程，管理作业生命周期：启动→执行→监控→闭环→归档。'
    },
    P1: {
        name: '作业预约、JSA与作业票', icon: '📋', color: '#4CAF50', humanConfirm: '作业票审批',
        tools: ['permit_submit', 'jsa_analyze', 'permit_generate_draft'],
        activity: '获取作业申请，识别作业类型、区域、设备、人员和时间；分析JSA；辅助形成作业票',
        inputs: '作业申请、历史JSA、模板、人员信息',
        outputs: '结构化任务、JSA结果、作业票草稿',
        intelligence: '调用作业类型识别、票证结构化、JSA完整性审查和危害—措施匹配Skill；对缺失字段、风险措施不足和人员资质冲突进行提示；仅生成草稿，不自动审批。'
    },
    P2: {
        name: '作业任务获取', icon: '📌', color: '#2196F3', humanConfirm: '是否纳入智能监测',
        tools: ['task_list', 'task_get', 'task_instance_create', 'task_subscribe'],
        activity: '从作业票系统获取已批准或待执行任务，建立唯一任务实例',
        inputs: '作业票、审批状态、计划',
        outputs: '任务实例、初始状态',
        intelligence: '按时间、区域和状态轮询或订阅待执行任务，建立Task ID、场景上下文容器和初始权限上下文；重复任务必须幂等处理。'
    },
    P3: {
        name: '作业上下文理解', icon: '🧠', color: '#9C27B0', humanConfirm: '上下文缺失确认',
        tools: ['context_build', 'context_validate', 'context_history'],
        activity: '聚合作业类型、区域、设备、介质、风险、措施、人员、时间和关联作业',
        inputs: '作业票、JSA、场景数据',
        outputs: '标准作业上下文包',
        intelligence: '形成"作业对象—区域—设备—介质—人员—资质—风险—措施—时间—关联作业—数据源"的标准上下文包，并记录数据来源和有效时间。'
    },
    P4: {
        name: '摄像与数据关联', icon: '📹', color: '#FF9800', humanConfirm: '资源绑定确认',
        tools: ['binding_match', 'binding_status', 'binding_confirm', 'binding_request_manual'],
        activity: '匹配固定/移动摄像、传感器、定位和报警数据',
        inputs: '作业区域、资源台账',
        outputs: '数据源绑定关系、监测清单',
        intelligence: '根据作业区域、摄像头覆盖关系、移动设备绑定、传感器点位和人员定位能力形成监测资源清单；无法自动匹配时发起人工补充。'
    },
    P5: {
        name: '作业前条件核验', icon: '✅', color: '#F44336', humanConfirm: '允许开工或整改',
        tools: ['verify_checklist', 'verify_execute', 'verify_recommendation'],
        activity: '核对隔离、警戒、消防、气体检测、人员资质和PPE',
        inputs: '措施清单、视频、检测数据',
        outputs: '核验结果、缺失项、开工建议',
        intelligence: '按作业类型生成检查清单，调用人员资质、现场视频、气体检测和规则执行工具逐项核验；输出"符合、待确认、不符合、不适用"四态结果和证据。'
    },
    P6: {
        name: '作业过程动态监测(A5)', icon: '📡', color: '#E91E63', humanConfirm: null,
        tools: [],
        activity: '基于 A5 实时CV检测、传感器监测和定位追踪，识别 PPE 缺失、传感器告警和监护人离岗事件',
        inputs: '作业票、CV模型、传感器、UWB定位',
        outputs: '候选风险事件、证据片段',
        intelligence: 'A5 场景 A-E 随机触发：正常作业/头盔缺失/气体上升/监护人离岗/多人违规。80%阈值判定违规，处理后触发 A6 研判。'
    },
    P7: {
        name: '风险研判与分级', icon: '⚠️', color: '#FF5722', humanConfirm: '高风险判断确认',
        tools: ['risk_analyze', 'risk_grade', 'risk_cases', 'risk_list'],
        activity: '融合上下文、模型结果、规则和历史事件，去重并判级',
        inputs: '候选事件、上下文、规则、知识',
        outputs: '风险事件、等级、依据和建议',
        intelligence: '通过多源证据融合、规则执行、相似案例查询和风险等级计算，形成风险事件；输出事实、证据、规则依据、置信度和处置建议。'
    },
    P8: {
        name: '人机协同处置', icon: '🔧', color: '#795548', humanConfirm: '下发、暂停、恢复',
        tools: ['disposition_create', 'disposition_confirm', 'disposition_status', 'disposition_list'],
        activity: '按角色与权限推送责任人，形成整改、暂停、复核或升级建议',
        inputs: '风险事件、处置规则、组织关系',
        outputs: '处置任务、通知、确认记录',
        intelligence: '根据风险等级、属地责任和角色权限确定接收人；高风险事件、写入操作、暂停和恢复必须通过人工确认服务。'
    },
    P9: {
        name: '闭环跟踪与报告', icon: '🔄', color: '#607D8B', humanConfirm: '关闭事件和作业',
        tools: ['closure_status', 'closure_verify', 'closure_report', 'closure_close'],
        activity: '跟踪整改状态，复核处置结果，汇总全过程记录',
        inputs: '处置反馈、复核证据、任务日志',
        outputs: '闭环状态、作业过程报告',
        intelligence: '持续跟踪任务接收、整改、反馈和复核；关闭前执行闭环完整性检查，自动生成作业过程报告和证据索引。'
    },
    P10: {
        name: '归档与复盘', icon: '📦', color: '#9E9E9E', humanConfirm: '档案确认、规则发布',
        tools: ['archive_task', 'archive_cases', 'archive_performance', 'archive_suggestions'],
        activity: '归档票证、视频证据、风险事件、处置记录和报告，形成案例',
        inputs: '全过程数据',
        outputs: '作业档案、案例、优化建议',
        intelligence: '经确认后归档全过程记录，将误报、漏报、规则冲突和处置效果沉淀为案例及知识规则优化建议。'
    }
};

const ALL_STAGES = ['MAIN', 'P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7', 'P8', 'P9', 'P10'];

// ========== 初始化 ==========
document.addEventListener('DOMContentLoaded', function() {
    initTabs();
    initMenu();
    loadModelConfig();
    loadAllPrompts();
    renderWorkflowDiagram();
    fillMockData();
});

// ========== 标签页切换 ==========
function initTabs() {
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', function() {
            const tabId = this.dataset.tab;
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            this.classList.add('active');
            document.getElementById('tab-' + tabId).classList.add('active');
            state.currentTab = tabId;
        });
    });
}

// ========== 菜单切换 ==========
function initMenu() {
    document.querySelectorAll('.menu-item').forEach(item => {
        item.addEventListener('click', function() {
            const panel = this.dataset.panel;
            document.querySelectorAll('.menu-item').forEach(m => m.classList.remove('active'));
            this.classList.add('active');
            document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
            document.getElementById('panel-' + panel).classList.add('active');
            state.selectedMenu = panel;
        });
    });
    // 初始化时默认激活 model-config
    const defaultItem = document.querySelector('.menu-item[data-panel="model-config"]');
    if (defaultItem) defaultItem.click();
}

// ========== 配置相关 ==========

// 协议预设 URL
const PROTOCOL_PRESETS = {
    openai:     "",
    anthropic:  "https://api.anthropic.com",
};
// 厂商速查
const PROVIDER_HINTS = {
    openai: {
        "MiniMax 中国站":     "https://api.minimaxi.com/v1",
        "DeepSeek":          "https://api.deepseek.com/v1",
        "阿里百炼 DashScope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "智谱 GLM":           "https://open.bigmodel.cn/api/paas/v4",
        "月之暗面 Kimi":      "https://api.moonshot.cn/v1",
        "OpenAI 官方":       "https://api.openai.com/v1",
        "Ollama 本地":       "http://localhost:11434/v1",
    },
    anthropic: {
        "Anthropic 官方":    "https://api.anthropic.com",
    },
};

// 当前配置的原始数据（用于保存 profile 时取真实 api_key）
let currentConfigRaw = {};

function loadModelConfig() {
    Promise.all([
        fetch('/api/config').then(r => r.json()),
        fetch('/api/config/snapshots').then(r => r.json()).catch(() => []),
    ]).then(([config, snapshots]) => {
        currentConfigRaw = config;
        // 填表单
        const apiKey = config.api_key || '';
        document.getElementById('config-api-key').value = maskString(apiKey);
        document.getElementById('config-api-key').dataset.rawValue = apiKey;
        document.getElementById('config-base-url').value = config.base_url || '';
        document.getElementById('config-model').value = config.model || '';
        document.getElementById('config-protocol').value = config.protocol || 'openai';
        document.getElementById('config-temperature').value = config.temperature || '';
        document.getElementById('config-max-tokens').value = config.max_tokens || '';
        renderProviderHints(config.protocol || 'openai');
        // 渲染历史
        renderSnapshots(snapshots);
    }).catch(err => {
        console.error('[loadModelConfig] Error:', err);
    });
}

function onProtocolChange() {
    const proto = document.getElementById('config-protocol').value;
    const url = PROTOCOL_PRESETS[proto];
    if (url !== undefined) document.getElementById('config-base-url').value = url;
    renderProviderHints(proto);
}

function renderProviderHints(protocol) {
    const el = document.getElementById('provider-hints');
    if (!el) return;
    const hints = PROVIDER_HINTS[protocol] || {};
    const entries = Object.entries(hints);
    if (entries.length === 0) { el.innerHTML = ''; return; }
    let html = '<span style="font-size:12px;color:#888;margin-right:8px;">快速:</span>';
    for (const [name, url] of entries) {
        html += `<button type="button" class="btn btn-secondary" style="padding:2px 8px;font-size:11px;margin:1px;" onclick="document.getElementById('config-base-url').value='${url}';">${name}</button>`;
    }
    el.innerHTML = html;
}

function maskString(str) {
    if (!str) return '';
    if (str.length <= 4) return '****';
    return str.substring(0, 4) + '*'.repeat(Math.min(str.length - 4, 16));
}

function toggleApiKeyVisibility() {
    const input = document.getElementById('config-api-key');
    const eyeOpen = document.getElementById('eye-open');
    const eyeClosed = document.getElementById('eye-closed');
    if (input.type === 'password') {
        input.value = input.dataset.rawValue || '';
        input.type = 'text';
        eyeOpen.style.display = 'none';
        eyeClosed.style.display = 'inline';
    } else {
        input.dataset.rawValue = input.value;
        input.value = maskString(input.value);
        input.type = 'password';
        eyeOpen.style.display = 'inline';
        eyeClosed.style.display = 'none';
    }
}

function testLlmConnection() {
    const apiKeyInput = document.getElementById('config-api-key');
    const apiKey = apiKeyInput.type === 'password' ? (apiKeyInput.dataset.rawValue || '') : apiKeyInput.value;
    const data = {
        protocol: document.getElementById('config-protocol').value,
        base_url: document.getElementById('config-base-url').value.trim(),
        api_key:  apiKey.trim(),
        model:    document.getElementById('config-model').value.trim(),
    };
    if (!data.base_url || !data.api_key || !data.model) {
        showTestResult('err', 'ERR: base_url、api_key、model 均不能为空');
        return;
    }
    const btn = document.getElementById('btn-test-llm');
    btn.disabled = true; btn.textContent = '...';
    fetch('/api/test/llm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    }).then(r => r.json()).then(res => {
        if (res.ok) showTestResult('ok', `OK (${res.protocol}) 回复: ${res.reply}`);
        else showTestResult('err', `ERR: ${res.error}`);
    }).catch(e => {
        showTestResult('err', `ERR: ${e.message}`);
    }).finally(() => {
        btn.disabled = false; btn.textContent = '🔌 测试连接';
    });
}

function showTestResult(type, msg) {
    const el = document.getElementById('test-result');
    el.style.display = 'block';
    el.style.background = type === 'ok' ? '#e8f5e9' : '#ffebee';
    el.style.color = type === 'ok' ? '#2e7d32' : '#c62828';
    el.style.border = `1px solid ${type === 'ok' ? '#a5d6a7' : '#ef9a9a'}`;
    el.textContent = msg;
}

function saveModelConfig() {
    const apiKeyInput = document.getElementById('config-api-key');
    let apiKey = apiKeyInput.type === 'password' ? (apiKeyInput.dataset.rawValue || '') : apiKeyInput.value;
    // 安全检查：如果获取到的 apiKey 是 masked 值，说明可能有问题
    if (!apiKey || apiKey.startsWith('****')) {
        document.getElementById('config-status').textContent = '⚠️ 请先点击眼睛图标切换到明文模式';
        setTimeout(() => { document.getElementById('config-status').textContent = ''; }, 3000);
        return;
    }
    const data = {
        api_key:     apiKey.trim(),
        base_url:    document.getElementById('config-base-url').value.trim(),
        model:       document.getElementById('config-model').value.trim(),
        protocol:    document.getElementById('config-protocol').value,
        temperature: document.getElementById('config-temperature').value.trim(),
        max_tokens:  document.getElementById('config-max-tokens').value.trim(),
    };
    fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    }).then(r => r.json()).then(res => {
        document.getElementById('config-status').textContent = res.status === 'ok' ? '✅ 已保存' : '❌ 失败';
        setTimeout(() => { document.getElementById('config-status').textContent = ''; }, 2000);
    }).catch(() => {
        document.getElementById('config-status').textContent = '❌ 失败';
    });
}

function showSaveProfileModal() {
    document.getElementById('save-profile-modal').style.display = 'block';
    document.getElementById('profile-name-input').focus();
}

function hideSaveProfileModal() {
    document.getElementById('save-profile-modal').style.display = 'none';
    document.getElementById('profile-name-input').value = '';
}

function doSaveProfile() {
    const name = document.getElementById('profile-name-input').value.trim();
    if (!name) { alert('请输入配置名称'); return; }
    const apiKeyInput = document.getElementById('config-api-key');
    const apiKey = apiKeyInput.type === 'password' ? (apiKeyInput.dataset.rawValue || '') : apiKeyInput.value;
    const config = {
        api_key:     apiKey.trim(),
        base_url:    document.getElementById('config-base-url').value.trim(),
        model:       document.getElementById('config-model').value.trim(),
        protocol:    document.getElementById('config-protocol').value,
        temperature: document.getElementById('config-temperature').value.trim(),
        max_tokens:  document.getElementById('config-max-tokens').value.trim(),
    };
    fetch('/api/config/snapshots/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, config }),
    }).then(r => r.json()).then(res => {
        if (res.status === 'ok') {
            renderSnapshots(res.snapshots);
            hideSaveProfileModal();
            document.getElementById('config-status').textContent = '✅ 已另存';
            setTimeout(() => { document.getElementById('config-status').textContent = ''; }, 2000);
        }
    });
}

function loadProfileToForm(profile) {
    const apiKeyInput = document.getElementById('config-api-key');
    apiKeyInput.value = maskString(profile.api_key || '');
    apiKeyInput.dataset.rawValue = profile.api_key || '';
    apiKeyInput.type = 'password';
    document.getElementById('eye-open').style.display = 'inline';
    document.getElementById('eye-closed').style.display = 'none';
    document.getElementById('config-base-url').value = profile.base_url || '';
    document.getElementById('config-model').value = profile.model || '';
    document.getElementById('config-protocol').value = profile.protocol || 'openai';
    document.getElementById('config-temperature').value = profile.temperature || '';
    document.getElementById('config-max-tokens').value = profile.max_tokens || '';
    renderProviderHints(profile.protocol || 'openai');
    document.getElementById('config-status').textContent = '已加载: ' + profile.name;
    setTimeout(() => { document.getElementById('config-status').textContent = ''; }, 2000);
}

function deleteProfile(name, event) {
    event.stopPropagation();
    if (!confirm('删除配置「' + name + '」？')) return;
    fetch('/api/config/snapshots/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
    }).then(r => r.json()).then(res => {
        if (res.status === 'ok') renderSnapshots(res.snapshots);
    });
}

function renderSnapshots(snapshots) {
    const container = document.getElementById('snapshots-list');
    if (!snapshots || snapshots.length === 0) {
        container.innerHTML = '<span style="color:#999;font-size:13px;">暂无保存的配置</span>';
        return;
    }
    container.innerHTML = snapshots.map(p => `
        <div onclick="loadProfileToForm(${escapeHtml(JSON.stringify(p))})"
             style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:12px 14px;cursor:pointer;min-width:200px;max-width:260px;transition:box-shadow 0.2s;">
            <div style="font-weight:bold;font-size:14px;margin-bottom:6px;color:#333;">${escapeHtml(p.name || '')}</div>
            <div style="font-size:12px;color:#666;margin-bottom:3px;">模型: ${escapeHtml(p.model || '-')}</div>
            <div style="font-size:12px;color:#888;margin-bottom:3px;">协议: ${escapeHtml(p.protocol || 'openai')}</div>
            <div style="font-size:12px;color:#888;margin-bottom:3px;word-break:break-all;">URL: ${escapeHtml(p.base_url || '-')}</div>
            <div style="font-size:12px;color:#888;">Key: ${escapeHtml(p.api_key || '****')}</div>
            <div style="margin-top:8px;display:flex;gap:6px;">
                <button onclick="loadProfileToForm(${escapeHtml(JSON.stringify(p))})"
                        style="background:#FF9800;color:#fff;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:12px;">应用</button>
                <button onclick="deleteProfile('${escapeHtml(p.name || '')}', event)"
                        style="background:#f5f5f5;color:#999;border:1px solid #ddd;border-radius:4px;padding:3px 8px;cursor:pointer;font-size:12px;">删除</button>
            </div>
        </div>
    `).join('');
}


function loadAllPrompts() {
    // 映射 stage ID 到 API 路径
    const stageToApi = {
        'MAIN': 'MAIN',
        'P1': 'P1', 'P2': 'P2', 'P3': 'P3', 'P4': 'P4', 'P5': 'P5',
        'P6': 'P6', 'P7': 'P7', 'P8': 'P8', 'P9': 'P9', 'P10': 'P10'
    };

    ALL_STAGES.forEach(stage => {
        const apiStage = stageToApi[stage] || stage;
        fetch('/api/prompt/' + apiStage)
            .then(r => r.text())
            .then(text => {
                document.getElementById('prompt-' + stage.toLowerCase()).value = text;
                updatePreview(stage.toLowerCase());
            })
            .catch(() => {
                document.getElementById('prompt-' + stage.toLowerCase()).value = '系统提示词加载失败';
            });
    });
}

function updatePreview(id) {
    const text = document.getElementById('prompt-' + id).value;
    const preview = document.getElementById('preview-' + id);
    if (preview) {
        // 简单的 Markdown 解析
        let html = text
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
            .replace(/\n\n/g, '</p><p>')
            .replace(/\n/g, '<br>');
        preview.innerHTML = html;
    }
}

function saveAgentPrompt(stage) {
    const content = document.getElementById('prompt-' + stage.toLowerCase()).value;
    fetch('/api/prompt/' + stage, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content })
    }).then(() => {
        document.getElementById('prompt-status-' + stage.toLowerCase()).textContent = '✅ 已保存';
        setTimeout(() => {
            document.getElementById('prompt-status-' + stage.toLowerCase()).textContent = '';
        }, 2000);
    });
}

// ========== 工作流相关 ==========
function renderWorkflowDiagram() {
    const container = document.getElementById('workflow-diagram');
    const { confirmed, currentStage, pending, agents, status: mainStatus } = state.workflowState;

    function getNodeHtml(stage) {
        const info = STAGE_INFO[stage];
        // 从 agents 数据获取状态
        const agentData = agents ? agents[stage] : null;
        const agentStatus = agentData ? agentData.status : null;

        let isCompleted = agentStatus === 'completed';
        let isPending = agentStatus === 'waiting';
        let isCurrent = agentStatus === 'running';

        // 如果没有 agents 数据，使用旧逻辑
        if (!agentData) {
            isCompleted = confirmed.includes(stage);
            isCurrent = stage === currentStage;
            isPending = pending.includes(stage) || pending.some(p => p.startsWith(stage + '_'));
        }

        let nodeClass = '';
        let statusIcon = '<span class="status-dot"></span><span class="status-text">待执行</span>';
        if (isCompleted) {
            nodeClass = 'completed';
            statusIcon = '<span class="status-dot"></span><span class="status-text">执行完成</span>';
        } else if (isPending) {
            nodeClass = 'pending';
            statusIcon = '<span class="status-dot"></span><span class="status-text">待人工确认</span>';
        } else if (isCurrent) {
            nodeClass = 'current';
            statusIcon = '<span class="status-dot running"></span><span class="status-text">进行中</span>';
        }

        // MAIN 节点稍大一些
        const isMain = stage === 'MAIN';
        const cardClass = isMain ? 'workflow-s-card-main' : 'workflow-s-card';

        return `
            <div class="workflow-s-node ${isMain ? 'main-node' : ''}">
                <div class="${cardClass} ${nodeClass}"
                     onclick="selectWorkflowNode('${stage}')"
                     onmouseenter="showNodeTooltip('${stage}', event)"
                     onmouseleave="hideNodeTooltip()">
                    ${info.humanConfirm ? '<span class="s-human-tag">👤</span>' : ''}
                    <div class="s-node-icon">${info.icon}</div>
                    <div class="s-node-stage">${stage}</div>
                    <div class="s-node-name">${info.name}</div>
                    <div class="s-node-status">${statusIcon}</div>
                </div>
            </div>
        `;
    }

    // 主Agent到子Agent的调度虚线箭头
    function getDispatchArrowHtml(targetCompleted) {
        const arrowCls = targetCompleted ? 'completed' : 'pending';
        return `<div class="workflow-s-arrow ${arrowCls} dispatch-arrow">⤵️</div>`;
    }

    let html = '<div class="workflow-s-container">';

    // 主调度节点（MAIN）横跨整行
    const mainAgentStatus = mainStatus || (mainStatus === 'completed' ? 'completed' : (agents && agents['MAIN'] ? agents['MAIN'].status : 'pending'));
    const isMainCompleted = mainAgentStatus === 'completed';
    const isMainCurrent = mainAgentStatus === 'running';
    const isMainWaiting = mainAgentStatus === 'waiting';
    let mainClass = '';
    if (isMainCompleted) mainClass = 'completed';
    else if (isMainCurrent) mainClass = 'current';
    else if (isMainWaiting) mainClass = 'pending';
    html += `<div class="main-dispatch-bar ${mainClass}">
        <div class="main-dispatch-content"
             onclick="selectWorkflowNode('MAIN')"
             onmouseenter="showNodeTooltip('MAIN', event)"
             onmouseleave="hideNodeTooltip()">
            <span class="main-icon">🎛️</span>
            <span class="main-label">MAIN</span>
            <span class="main-name">主调度中心</span>
            <span class="main-status">
                <span class="status-dot"></span>
                <span class="status-text">${isMainCompleted ? '执行完成' : (isMainCurrent ? '进行中' : (isMainWaiting ? '待人工确认' : '待执行'))}</span>
            </span>
        </div>
    </div>`;

    // 调度箭头行（P1-P10对应）
    const allStages = ['P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7', 'P8', 'P9', 'P10'];
    html += '<div class="workflow-s-row dispatch-row">';
    allStages.forEach((stage) => {
        html += `<div class="dispatch-arrow-wrapper"><div class="workflow-s-arrow dispatch-arrow">⤵️</div></div>`;
    });
    html += '</div>';

    // P1-P10 节点单行排列
    html += '<div class="workflow-s-row sub-stages-row">';
    allStages.forEach((stage, i) => {
        html += getNodeHtml(stage);
        if (i < allStages.length - 1) {
            html += `<div class="workflow-s-arrow sub-arrow">· · ·</div>`;
        }
    });
    html += '</div>';

    html += '</div>';
    container.innerHTML = html;
}

function selectWorkflowNode(stage) {
    state.selectedWorkflowNode = stage;
    renderWorkflowNodeDetail(stage);
}

function renderWorkflowNodeDetail(stage) {
    const info = STAGE_INFO[stage];
    const { confirmed, pending, agents, currentStage } = state.workflowState;

    // 从 agents 数据获取状态
    const agentData = agents ? agents[stage] : null;
    const agentStatus = agentData ? agentData.status : null;

    let isCompleted = agentStatus === 'completed';
    let isPending = agentStatus === 'waiting';
    let isCurrent = agentStatus === 'running';

    // 如果没有 agents 数据，使用旧逻辑
    if (!agentData) {
        isCompleted = confirmed.includes(stage);
        isPending = pending.includes(stage) || pending.some(p => p.startsWith(stage + '_'));
        isCurrent = stage === currentStage;
    }

    let statusBadge = '';
    if (isCompleted) statusBadge = '<span class="status-badge status-completed">✅ 执行完成</span>';
    else if (isPending) statusBadge = '<span class="status-badge status-pending">⏸️ 待人工确认</span>';
    else if (isCurrent) statusBadge = '<span class="status-badge status-current">⏳ 进行中</span>';
    else statusBadge = '<span class="status-badge" style="background: #ccc;">⏹️ 待执行</span>';

    const panel = document.getElementById('detail-panel');
    if (!panel) return;
    panel.innerHTML = `
        <div class="detail-header">
            <span class="detail-icon">${info.icon}</span>
            <div>
                <div class="detail-title" style="color: ${info.color}">${stage} ${info.name}</div>
                <div class="detail-subtitle">${statusBadge}</div>
            </div>
        </div>
        ${info.humanConfirm ? `
        <div style="background: #FFF8E1; border: 2px solid #FFC107; border-radius: 8px; padding: 15px; margin: 15px 0;">
            <div style="font-weight: bold; color: #FF9800; margin-bottom: 8px;">👤 人工确认点</div>
            <div>${info.humanConfirm}</div>
            <div style="color: #666; font-size: 12px; margin-top: 5px;">需要人工介入确认后才能继续工作流</div>
        </div>
        ` : ''}
        <div class="detail-section">
            <div class="detail-section-title">📋 描述</div>
            <p style="color: #666; line-height: 1.6;">${info.activity}</p>
        </div>
        <div class="detail-section">
            <div class="detail-section-title">📥 主要输入</div>
            <p style="color: #666;">${info.inputs}</p>
        </div>
        <div class="detail-section">
            <div class="detail-section-title">📤 主要输出</div>
            <p style="color: #666;">${info.outputs}</p>
        </div>
        <div class="detail-section">
            <div class="detail-section-title">🔧 工具</div>
            <div>${info.tools.map(t => `<span class="tool-tag">${t}</span>`).join(' ')}</div>
        </div>
        <div class="detail-section">
            <div class="detail-section-title">🧠 智能化任务设计</div>
            <p style="color: #666; background: #f8f8f8; padding: 10px; border-radius: 6px; border-left: 3px solid #FF9800;">${info.intelligence}</p>
        </div>
    `;
}

// ========== 工作流执行 ==========

function startWorkflow() {
    const app = buildApplicationJson();
    addLog('🚀 启动工作流...');

    fetch('/api/workflow/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(app)
    }).then(r => r.json())
      .then(data => {
          if (data.job_id) {
              state.workflowState.threadId = data.job_id;
              state.workflowState.jobId = data.job_id;
              state.workflowState.status = 'starting';
              addLog('📋 作业单号: ' + data.job_id, 'success');
              addLog('⏳ 工作流启动中，建立 WebSocket 连接...');

              // 建立 WebSocket 连接
              connectWebSocket(data.job_id);
          }
      })
      .catch(err => {
          addLog('❌ 错误: ' + err.message, 'error');
      });
}

const WS_STATUS_PORT = 8081;  // 状态 WebSocket 端口
const WS_LOGS_PORT = 8082;     // 日志 WebSocket 端口

function connectWebSocket(jobId) {
    // 关闭之前的连接
    disconnectWebSocket();

    const statusWsUrl = `ws://localhost:${WS_STATUS_PORT}/ws/status/${jobId}`;
    addLog('🔌 连接状态 WebSocket: ' + statusWsUrl);

    try {
        state.ws = new WebSocket(statusWsUrl);

        state.ws.onopen = () => {
            addLog('✅ 状态 WebSocket 连接已建立', 'success');
        };

        state.ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);

                if (msg.type === 'heartbeat') {
                    // 心跳包，忽略
                    return;
                }

                if (msg.type === 'state_update') {
                    const data = msg.data;
                    const prevStage = state.workflowState.current_stage || '';

                    state.workflowState = data;
                    renderWorkflowDiagram();
                    updateControlPanel();

                    // 检测 P1 完成，切换到 P2 时显示完成提示
                    if (prevStage === 'P1' && data.current_stage === 'P2') {
                        hideP1StepsModal();
                        showP1CompleteModal();
                    }

                    // 更新日志
                    const currentStage = data.current_stage || '';
                    const pending = data.pending || [];

                    if (pending.length > 0) {
                        // 有待确认项，弹窗
                        if (!document.getElementById('hitl-modal').classList.contains('active')) {
                            addLog('⏸️ 等待人工确认: ' + pending.join(', '), 'warning');
                            showHitlModal(pending[0], data.pending_data[pending[0]]);
                        }
                    } else if (data.status === 'completed') {
                        // 工作流完成
                        addLog('✅ 工作流执行完成', 'success');
                    } else if (data.status === 'error') {
                        addLog('❌ 工作流执行错误', 'error');
                    } else if (currentStage) {
                        // 执行中
                        addLog('⏳ 执行中: ' + currentStage);
                    }

                    // 打印agents状态便于调试
                    if (data.agents) {
                        console.log('[state_update] agents:', JSON.stringify(data.agents));
                    }
                }
            } catch (e) {
                console.error('解析状态 WebSocket 消息失败:', e);
            }
        };

        state.ws.onerror = (error) => {
            addLog('❌ 状态 WebSocket 连接错误', 'error');
            console.error('WebSocket error:', error);
        };

        state.ws.onclose = () => {
            addLog('🔌 状态 WebSocket 连接已关闭');
            state.ws = null;
        };
    } catch (e) {
        addLog('❌ 状态 WebSocket 连接失败: ' + e.message, 'error');
    }

    // 同时连接日志 WebSocket
    connectLogsWebSocket(jobId);
}

function connectLogsWebSocket(jobId) {
    // 关闭之前的日志连接
    disconnectLogsWebSocket();

    const logsWsUrl = `ws://localhost:${WS_LOGS_PORT}/ws/logs/${jobId}`;
    addLog('📋 连接日志 WebSocket: ' + logsWsUrl);

    try {
        state.logsWs = new WebSocket(logsWsUrl);

        state.logsWs.onopen = () => {
            addLog('✅ 日志 WebSocket 连接已建立', 'success');
        };

        state.logsWs.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);

                if (msg.type === 'heartbeat') {
                    // 心跳包，忽略
                    return;
                }

                if (msg.type === 'workflow_log') {
                    // 显示结构化日志
                    displayWorkflowLog(msg);
                } else if (msg.type === 'connected') {
                    addLog('📋 ' + msg.message, 'success');
                }
            } catch (e) {
                // 非 JSON 格式，直接显示原始文本
                addLog('📋 ' + event.data);
            }
        };

        state.logsWs.onerror = (error) => {
            addLog('❌ 日志 WebSocket 连接错误', 'error');
            console.error('Logs WebSocket error:', error);
        };

        state.logsWs.onclose = () => {
            addLog('📋 日志 WebSocket 连接已关闭');
            state.logsWs = null;
        };
    } catch (e) {
        addLog('❌ 日志 WebSocket 连接失败: ' + e.message, 'error');
    }
}

function disconnectWebSocket() {
    if (state.ws) {
        state.ws.close();
        state.ws = null;
    }
}

function disconnectLogsWebSocket() {
    if (state.logsWs) {
        state.logsWs.close();
        state.logsWs = null;
    }
}

function fillMockData() {
    fetch('/data/input/mock_job_content.json')
        .then(r => r.json())
        .then(data => {
            const randomIndex = Math.floor(Math.random() * data.length);
            const item = data[randomIndex];
            document.getElementById('app-job-content').value = item.job_content || '';
            document.getElementById('app-region').value = item.region || '';
            document.getElementById('app-person-name').value = item.person_name || '';
            document.getElementById('app-person-badge').value = item.person_badge || '';
            document.getElementById('app-start').value = item.start || '';
            document.getElementById('app-end').value = item.end || '';
        })
        .catch(() => {});
}

function buildApplicationJson() {
    return {
        job_content: document.getElementById('app-job-content').value,
        region: document.getElementById('app-region').value,
        personnel: [{
            name: document.getElementById('app-person-name').value,
            badge_id: document.getElementById('app-person-badge').value
        }],
        planned_start: document.getElementById('app-start').value,
        planned_end: document.getElementById('app-end').value
    };
}

function resetWorkflow() {
    disconnectWebSocket();
    disconnectLogsWebSocket();
    state.workflowState = {
        status: 'idle',
        pending: [],
        confirmed: [],
        currentStage: '',
        threadId: null,
        jobId: null
    };
    state.selectedWorkflowNode = null;
    renderWorkflowDiagram();
    updateControlPanel();
    document.getElementById('log-container').innerHTML = '<div class="log-entry"><span class="log-time">[--:--:--]</span> 已重置</div>';
    document.getElementById('app-job-content').value = '';
    document.getElementById('app-region').value = '';
    document.getElementById('app-person-name').value = '';
    document.getElementById('app-person-badge').value = '';
    document.getElementById('app-start').value = '';
    document.getElementById('app-end').value = '';
}

function updateControlPanel() {
    const { confirmed = [], pending = [], currentStage = '', status = 'idle' } = state.workflowState;
    const panel = document.getElementById('control-panel');
    if (!panel) return;

    // confirmed 可能是 dict 或 array，转换为数组长度
    const confirmedCount = Array.isArray(confirmed) ? confirmed.length : Object.keys(confirmed || {}).length;

    let html = '';
    if (status === 'idle') {
        html = '<span style="color: #999;">就绪</span>';
    } else if (pending.length > 0) {
        html = `<span style="color: #FFC107;">⏸️ 等待人工确认: ${pending.join(', ')}</span>`;
    } else if (currentStage) {
        html = `<span style="color: #FF9800;">⏳ 执行中: ${currentStage}</span>`;
    }
    html += `<div style="margin-top: 10px;">已完成: ${confirmedCount}/${ALL_STAGES.length}</div>`;
    panel.innerHTML = html;
}

// ========== 日志 ==========
function addLog(message, type = '') {
    const container = document.getElementById('log-container');
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    const cls = type === 'success' ? 'log-success' : (type === 'error' ? 'log-error' : (type === 'warning' ? 'log-warning' : ''));
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.innerHTML = `<span class="log-time">[${time}]</span> <span class="${cls}">${message}</span>`;
    container.appendChild(entry);
    container.scrollTop = container.scrollHeight;
}

// 显示结构化工作流日志
function displayWorkflowLog(msg) {
    const container = document.getElementById('log-container');
    const time = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString('zh-CN', { hour12: false }) : new Date().toLocaleTimeString('zh-CN', { hour12: false });

    const level = msg.level || 'INFO';
    const source = msg.source || '';
    const message = msg.message || '';
    const data = msg.data;

    // 根据级别设置颜色
    let levelColor = '#4CAF50'; // INFO - 绿色
    let levelPrefix = 'ℹ️';
    if (level === 'WARNING') {
        levelColor = '#FFC107';
        levelPrefix = '⚠️';
    } else if (level === 'ERROR') {
        levelColor = '#f44336';
        levelPrefix = '❌';
    } else if (level === 'DEBUG') {
        levelColor = '#9E9E9E';
        levelPrefix = '🔍';
    }

    // 源标签颜色
    const sourceColors = {
        'P1': '#4CAF50', 'P2': '#2196F3', 'P3': '#9C27B0', 'P4': '#FF9800',
        'P5': '#F44336', 'P6': '#E91E63', 'P7': '#FF5722', 'P8': '#795548',
        'P9': '#607D8B', 'P10': '#9E9E9E', 'MAIN': '#673AB7',
        'TOOL': '#00BCD4', 'AGENT': '#E91E63', 'LLM': '#FFEB3B',
        'WORKFLOW': '#673AB7'
    };
    const sourceColor = sourceColors[source] || '#888';

    // 构建日志条目 HTML
    let entryHtml = `<span class="log-time">[${time}]</span> `;
    entryHtml += `<span style="color: ${levelColor}; font-weight: bold;">${levelPrefix} ${level}</span> `;
    entryHtml += `<span style="color: ${sourceColor}; font-weight: bold;">[${source}]</span> `;
    entryHtml += `<span>${message}</span>`;

    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.innerHTML = entryHtml;

    // 如果有附加数据，显示数据（根据来源决定展示方式）
    if (data && Object.keys(data).length > 0) {
        // LLM 日志：显示可展开的 JSON 视图
        if (source === 'LLM') {
            const jsonDiv = document.createElement('div');
            jsonDiv.className = 'llm-log-data';
            jsonDiv.innerHTML = formatJsonView(data);
            entry.appendChild(jsonDiv);
        } else {
            // 其他日志：显示完整数据（支持多行 JSON 美化）
            const dataSummary = formatDataSummary(data);
            if (dataSummary) {
                const dataDiv = document.createElement('div');
                dataDiv.style.marginLeft = '20px';
                dataDiv.style.color = '#888';
                dataDiv.style.fontSize = '11px';
                dataDiv.style.whiteSpace = 'pre-wrap';
                dataDiv.style.fontFamily = '"Consolas", monospace';
                dataDiv.textContent = dataSummary;
                entry.appendChild(dataDiv);
            }
        }
    }

    container.appendChild(entry);
    container.scrollTop = container.scrollHeight;

    // 限制日志数量，防止内存溢出（增大到2000）
    while (container.children.length > 2000) {
        container.removeChild(container.firstChild);
    }

    // P1 步骤检测与更新
    detectAndUpdateP1Steps(msg);
}

// 格式化 JSON 视图（带语法高亮和折叠）
function formatJsonView(data, indent = 0) {
    const pad = '  '.repeat(indent);
    const nextPad = '  '.repeat(indent + 1);

    if (data === null) {
        return `<span class="json-null">null</span>`;
    }
    if (data === undefined) {
        return `<span class="json-null">undefined</span>`;
    }
    if (typeof data === 'boolean') {
        return `<span class="json-boolean">${data}</span>`;
    }
    if (typeof data === 'number') {
        return `<span class="json-number">${data}</span>`;
    }
    if (typeof data === 'string') {
        // 判断是否是 JSON 字符串
        if (data.length > 200) {
            return `<span class="json-string">"${escapeHtml(data.substring(0, 200))}..."</span>`;
        }
        return `<span class="json-string">"${escapeHtml(data)}"</span>`;
    }
    if (Array.isArray(data)) {
        if (data.length === 0) {
            return `<span class="json-bracket">[]</span>`;
        }
        if (data.length <= 3 && data.every(item => typeof item !== 'object')) {
            return `<span class="json-bracket">[${data.map(v => formatJsonView(v, indent + 1)).join(', ')}]</span>`;
        }
        let html = `<span class="json-bracket">[</span><span class="json-toggle" onclick="toggleJsonBlock(this)">▶</span><span class="json-collapsed">${data.length}项</span><span class="json-expanded" style="display:none;">\n`;
        data.forEach((item, i) => {
            html += `${nextPad}${formatJsonView(item, indent + 1)}${i < data.length - 1 ? ',' : ''}\n`;
        });
        html += `${pad}</span><span class="json-bracket">]</span>`;
        return html;
    }
    if (typeof data === 'object') {
        const keys = Object.keys(data);
        if (keys.length === 0) {
            return `<span class="json-bracket">{}</span>`;
        }
        if (keys.length <= 2 && keys.every(k => typeof data[k] !== 'object')) {
            return `<span class="json-bracket">{${keys.map(k => `<span class="json-key">"${k}"</span>: ${formatJsonView(data[k], indent + 1)}`).join(', ')}}</span>`;
        }
        let html = `<span class="json-bracket">{</span><span class="json-toggle" onclick="toggleJsonBlock(this)">▶</span><span class="json-collapsed">${keys.length}字段</span><span class="json-expanded" style="display:none;">\n`;
        keys.forEach((k, i) => {
            html += `${nextPad}<span class="json-key">"${k}"</span>: ${formatJsonView(data[k], indent + 1)}${i < keys.length - 1 ? ',' : ''}\n`;
        });
        html += `${pad}</span><span class="json-bracket">}</span>`;
        return html;
    }
    return String(data);
}

// 切换 JSON 块的展开/折叠状态
function toggleJsonBlock(toggleEl) {
    const entry = toggleEl.closest('.llm-log-data');
    const collapsed = entry.querySelector('.json-collapsed');
    const expanded = entry.querySelector('.json-expanded');

    if (expanded.style.display === 'none') {
        // 展开
        collapsed.style.display = 'none';
        expanded.style.display = '';
        toggleEl.textContent = '▼';
    } else {
        // 折叠
        expanded.style.display = 'none';
        collapsed.style.display = '';
        toggleEl.textContent = '▶';
    }
}

// HTML 转义
function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// P1 步骤状态管理
const P1_STEPS = {
    permit_submit: { index: 1, name: '提交作业申请' },
    jsa_analyze: { index: 2, name: 'JSA 安全分析' },
    permit_generate_draft: { index: 3, name: '生成作业票' },
    permit_check: { index: 4, name: '作业票查询' }
};
let p1StepsCompleted = 0;
let p1ModalShown = false;

function detectAndUpdateP1Steps(msg) {
    const source = msg.source || '';
    const message = msg.message || '';

    // 只处理 P1 和 TOOL 源的日志
    if (source !== 'P1' && source !== 'TOOL' && source !== 'AGENT') return;

    // 检测工具入口和出口
    for (const [toolName, stepInfo] of Object.entries(P1_STEPS)) {
        if (message.includes(`>>> ${toolName} 工具入口`)) {
            // 工具开始执行
            if (!p1ModalShown) {
                showP1StepsModal();
                p1ModalShown = true;
            }
            updateP1Step(stepInfo.index, 'running', `执行中: ${stepInfo.name}`);
        } else if (message.includes(`<<< ${toolName} 工具出口`)) {
            // 工具执行完成
            updateP1Step(stepInfo.index, 'completed', `${stepInfo.name} 完成`);
            p1StepsCompleted++;
            updateP1Progress();

            // 如果所有步骤完成，显示 P1 完成提示弹窗，3秒后进入P2
            if (p1StepsCompleted >= 4) {
                setTimeout(() => {
                    hideP1StepsModal();
                    showP1CompleteModal();
                    p1StepsCompleted = 0;
                    p1ModalShown = false;
                }, 1500);
            }
        }
    }
}

// P1 完成提示弹窗，3秒倒计时后自动进入P2
let p1CountdownInterval = null;

function showP1CompleteModal() {
    const countdownEl = document.getElementById('p1-complete-countdown');
    let countdown = 3;
    countdownEl.textContent = countdown;

    document.getElementById('p1-complete-modal').classList.add('active');

    // 清除之前的定时器
    if (p1CountdownInterval) {
        clearInterval(p1CountdownInterval);
    }

    // 开始倒计时
    p1CountdownInterval = setInterval(() => {
        countdown--;
        if (countdown <= 0) {
            clearInterval(p1CountdownInterval);
            p1CountdownInterval = null;
            hideP1CompleteModal();
            // 自动进入P2 - 由于是异步执行，前端不需要额外操作
            addLog('⏳ P1 完成，进入 P2 作业任务获取阶段...', 'success');
        } else {
            countdownEl.textContent = countdown;
        }
    }, 1000);
}

function hideP1CompleteModal() {
    document.getElementById('p1-complete-modal').classList.remove('active');
    if (p1CountdownInterval) {
        clearInterval(p1CountdownInterval);
        p1CountdownInterval = null;
    }
}

function showP1StepsModal() {
    // 重置所有步骤状态
    for (let i = 1; i <= 4; i++) {
        const stepEl = document.getElementById(`p1-step-${i}`);
        stepEl.className = 'p1-step pending';
        stepEl.querySelector('.p1-step-status').textContent = '待执行';
    }
    document.getElementById('p1-progress-fill').style.width = '0%';
    document.getElementById('p1-progress-text').textContent = '0 / 4 步骤完成';
    document.getElementById('p1-log-content').textContent = '等待执行...';

    document.getElementById('p1-steps-modal').classList.add('active');
}

function hideP1StepsModal() {
    document.getElementById('p1-steps-modal').classList.remove('active');
}

function updateP1Step(stepIndex, status, logMessage) {
    const stepEl = document.getElementById(`p1-step-${stepIndex}`);
    stepEl.className = `p1-step ${status}`;
    stepEl.querySelector('.p1-step-status').textContent = status === 'running' ? '执行中' : '完成';

    // 更新当前日志
    document.getElementById('p1-log-content').textContent = logMessage;
}

function updateP1Progress() {
    const percent = (p1StepsCompleted / 4) * 100;
    document.getElementById('p1-progress-fill').style.width = `${percent}%`;
    document.getElementById('p1-progress-text').textContent = `${p1StepsCompleted} / 4 步骤完成`;
}

// 格式化数据显示摘要
function formatDataSummary(data) {
    if (!data || typeof data !== 'object') return '';

    const entries = Object.entries(data);
    if (entries.length === 0) return '';

    const parts = [];
    for (const [key, value] of entries) { // 显示所有字段
        if (value === null || value === undefined) continue;

        let valStr = '';
        if (typeof value === 'string') {
            // 如果是 JSON 字符串，美化格式化
            try {
                const parsed = JSON.parse(value);
                valStr = JSON.stringify(parsed, null, 2);
            } catch {
                valStr = value;
            }
        } else if (typeof value === 'object') {
            valStr = JSON.stringify(value, null, 2);
        } else {
            valStr = String(value);
        }

        if (valStr) {
            parts.push(`${key}: ${valStr}`);
        }
    }

    return parts.join('\n');
}

// ========== HITL 弹窗 ==========
function showHitlModal(stage, data) {
    // 提取基础阶段名称（如从"P1_permit_submit"提取"P1"）
    const baseStage = stage.split('_')[0];
    const info = STAGE_INFO[baseStage] || STAGE_INFO[stage];
    document.getElementById('modal-icon').textContent = info.icon;
    document.getElementById('modal-title').textContent = stage + ' - ' + info.name;
    document.getElementById('modal-subtitle').textContent = info.humanConfirm || '人工确认';

    data = data || {};
    const pendingInfo = data.pending || data;
    document.getElementById('modal-info-content').innerHTML = pendingInfo.message || info.humanConfirm || '请确认';

    const evidenceSection = document.getElementById('modal-evidence-section');
    const suggestionSection = document.getElementById('modal-suggestion-section');

    if (pendingInfo.evidence) {
        document.getElementById('modal-evidence').innerHTML = Array.isArray(pendingInfo.evidence) ? pendingInfo.evidence.join('<br>') : pendingInfo.evidence;
        evidenceSection.style.display = 'block';
    } else {
        evidenceSection.style.display = 'none';
    }

    if (pendingInfo.suggestion) {
        document.getElementById('modal-suggestion').innerHTML = pendingInfo.suggestion;
        suggestionSection.style.display = 'block';
    } else {
        suggestionSection.style.display = 'none';
    }

    document.getElementById('hitl-modal').classList.add('active');
}

function closeModal() {
    document.getElementById('hitl-modal').classList.remove('active');
}

function confirmDecision(decision) {
    console.log('confirmDecision called:', decision);
    console.log('state.workflowState:', JSON.stringify(state.workflowState));
    const jobId = state.workflowState.thread_id || state.workflowState.jobId || state.workflowState.threadId;
    console.log('jobId:', jobId);
    if (!jobId) {
        addLog('❌ 错误: 没有进行中的工作流，请先启动工作流', 'error');
        return;
    }

    const pending = state.workflowState.pending || [];
    const stage = pending[0];
    console.log('stage:', stage);
    if (!stage) {
        addLog('❌ 错误: 没有待确认的阶段', 'error');
        return;
    }

    addLog(`✅ ${stage} 确认: ${decision}`);
    addLog(`📤 发送确认请求: thread_id=${jobId}, stage=${stage}, decision=${decision}`);

    fetch('/api/workflow/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            thread_id: jobId,
            stage: stage,
            decision: decision,
            async_execute: true  // 异步执行，点击确认后立即返回
        })
    }).then(r => {
        addLog(`📥 收到响应状态: ${r.status}`);
        return r.json();
    }).then(data => {
        addLog(`📋 响应数据: ${JSON.stringify(data)}`);
        closeModal();

        if (data.status === 'executing') {
            // 异步执行中，等待 WebSocket 状态更新
            addLog(`⏳ ${stage} 已确认，异步执行中...`, 'info');
        } else if (data.pending && data.pending.length > 0) {
            // 还有更多待确认（同步模式下的直接返回）
            addLog('⏳ 等待: ' + data.pending.join(', '), 'warning');
            setTimeout(() => {
                showHitlModal(data.pending[0], data.pending_data[data.pending[0]]);
            }, 300);
        } else if (data.status === 'completed') {
            addLog('✅ 工作流完成', 'success');
        } else {
            addLog(`⏳ 等待状态更新，状态: ${data.status}`);
        }
      }).catch(err => {
        addLog(`❌ 请求失败: ${err.message}`, 'error');
      });
}

// ========== 节点悬浮提示 ==========
let tooltipEl = null;

function getStageDescription(stage) {
    const descs = {
        P1: '接收作业申请，识别作业类型、区域、设备、人员和时间；分析JSA；辅助形成作业票',
        P2: '从作业票系统获取已批准或待执行任务，建立唯一任务实例',
        P3: '聚合作业类型、区域、设备、介质、风险、措施、人员、时间和关联作业',
        P4: '匹配固定/移动摄像、传感器、定位和报警数据',
        P5: '核对隔离、警戒、消防、气体检测、人员资质和PPE',
        P6: '持续获取视频、传感器、定位和作业状态，识别违章及条件变化',
        P7: '融合上下文、模型结果、规则和历史事件，去重并判级',
        P8: '按角色与权限推送责任人，形成整改、暂停、复核或升级建议',
        P9: '跟踪整改状态，复核处置结果，汇总全过程记录',
        P10: '归档票证、视频证据、风险事件、处置记录和报告，形成案例'
    };
    return descs[stage] || '';
}

function showNodeTooltip(stage, event) {
    const info = STAGE_INFO[stage];

    if (!tooltipEl) {
        tooltipEl = document.createElement('div');
        tooltipEl.className = 'node-tooltip';
        document.body.appendChild(tooltipEl);
    }

    const toolsHtml = info.tools.map(t => `<span class="tooltip-tool-tag">${t}</span>`).join('');

    let humanHtml = '';
    if (info.humanConfirm) {
        humanHtml = `<div class="tooltip-section">
            <div class="tooltip-section-title">👤 人工控制点</div>
            <div class="tooltip-human">${info.humanConfirm}</div>
        </div>`;
    }

    tooltipEl.innerHTML = `
        <div class="tooltip-header">
            <span class="tooltip-header-icon">${info.icon}</span>
            <div class="tooltip-header-info">
                <div class="tooltip-header-stage">${stage}</div>
                <div class="tooltip-header-name">${info.name}</div>
            </div>
        </div>
        <div class="tooltip-body">
            <div class="tooltip-section">
                <div class="tooltip-section-title">🎯 主要活动</div>
                <div class="tooltip-section-content">${info.activity}</div>
            </div>
            <div class="tooltip-section">
                <div class="tooltip-section-title">📥 主要输入</div>
                <div class="tooltip-section-content">${info.inputs}</div>
            </div>
            <div class="tooltip-section">
                <div class="tooltip-section-title">📤 主要输出</div>
                <div class="tooltip-section-content">${info.outputs}</div>
            </div>
            <div class="tooltip-section">
                <div class="tooltip-section-title">🔧 工具</div>
                <div class="tooltip-tools">${toolsHtml}</div>
            </div>
            <div class="tooltip-section">
                <div class="tooltip-section-title">🧠 智能化任务设计</div>
                <div class="tooltip-section-content tooltip-intelligence">${info.intelligence}</div>
            </div>
            ${humanHtml}
        </div>
    `;

    // 定位
    const targetEl = event.target.closest('.workflow-s-card, .workflow-s-card-main, .main-dispatch-content');
    if (!targetEl) return;
    const rect = targetEl.getBoundingClientRect();
    let left = rect.right + 10;
    let top = rect.top;

    // 边界检测
    if (left + 400 > window.innerWidth) {
        left = rect.left - 410;
    }
    if (top + 400 > window.innerHeight) {
        top = window.innerHeight - 410;
    }

    tooltipEl.style.left = left + 'px';
    tooltipEl.style.top = top + 'px';
    tooltipEl.classList.add('visible');
}

function hideNodeTooltip() {
    if (tooltipEl) {
        tooltipEl.classList.remove('visible');
    }
}
