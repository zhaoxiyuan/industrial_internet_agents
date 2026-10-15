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
    historyJobs: [],
    historyViewJobId: null,
    liveWorkflowState: null,
    // 工单页面占用（租约）：readOnly 表示本页只能看，不能操作
    lease: { jobId: null, holding: false, readOnly: false, heartbeatTimer: null, occupant: null },
    permitTypes: [],
    permitType: '',
    realApplication: null,
    inputSource: 'manual',
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
const ACTIVE_WORKFLOW_STORAGE_KEY = 'industrialInternetActiveWorkflow';
const HOLDER_STORAGE_KEY = 'industrialInternetHolderId';

// 「本页正在操作哪张工单」必须按标签页隔离：用 localStorage 会让新标签页继承
// 另一个页面的工单并自动跟随。sessionStorage 按标签页独立，且刷新（F5）不丢，
// 因此既能保持页面之间各干各的，也不影响刷新后恢复当前作业。
const activeWorkflowStore = window.sessionStorage;

function getHolderId() {
    // 每个标签页一个稳定标识：刷新不变，不同标签页不同。
    let holderId = null;
    try { holderId = sessionStorage.getItem(HOLDER_STORAGE_KEY); } catch (e) { holderId = null; }
    if (!holderId) {
        holderId = 'page-' + Math.random().toString(36).slice(2, 8) + '-' + Date.now().toString(36);
        try { sessionStorage.setItem(HOLDER_STORAGE_KEY, holderId); } catch (e) { /* 忽略 */ }
    }
    return holderId;
}

function holderLabel() {
    const holderId = getHolderId();
    return '页面#' + holderId.slice(-6);
}

// ========== 工单页面占用（租约） ==========

function renderLeaseBanner() {
    const banner = document.getElementById('lease-banner');
    if (!banner) return;
    const text = document.getElementById('lease-banner-text');
    if (state.lease.readOnly) {
        const who = (state.lease.occupant && state.lease.occupant.holder_label)
            ? state.lease.occupant.holder_label : '另一个页面';
        text.textContent = '🔒 运行失败：作业单占用中（该工单正由 ' + who + ' 操作），本页只读';
        banner.classList.add('active');
    } else {
        banner.classList.remove('active');
    }
}

function setLeaseReadOnly(occupant, message) {
    state.lease.readOnly = true;
    state.lease.holding = false;
    state.lease.occupant = occupant || null;
    stopLeaseHeartbeat();
    if (message) addLog('🔒 ' + message, 'warning');
    renderLeaseBanner();
}

function clearLeaseReadOnly() {
    state.lease.readOnly = false;
    state.lease.occupant = null;
    renderLeaseBanner();
}

function stopLeaseHeartbeat() {
    if (state.lease.heartbeatTimer) {
        clearInterval(state.lease.heartbeatTimer);
        state.lease.heartbeatTimer = null;
    }
}

function startLeaseHeartbeat(jobId) {
    stopLeaseHeartbeat();
    state.lease.heartbeatTimer = setInterval(() => {
        if (state.lease.jobId !== jobId || state.lease.readOnly) return;
        fetch('/api/workflow/heartbeat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ job_id: jobId, holder_id: getHolderId() })
        }).then(r => r.json())
          .then(data => {
              if (!data) return;
              if (data.status === 'ok') return;
              // 租约丢失（被其他页面接管，或心跳超时）：本页转为只读。
              setLeaseReadOnly(data.occupant, data.error || '本页已失去该工单的操作权');
          })
          .catch(() => { /* 网络抖动交给下一次心跳 */ });
    }, 10000);
}

function claimJobLease(jobId) {
    if (!jobId) return Promise.resolve({ ok: false });
    return fetch('/api/workflow/claim', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            job_id: jobId,
            holder_id: getHolderId(),
            holder_label: holderLabel()
        })
    }).then(r => r.json())
      .then(data => {
          if (!data) return { ok: false };
          if (data.status === 'ok') {
              state.lease.jobId = jobId;
              state.lease.holding = true;
              state.lease.readOnly = false;
              state.lease.occupant = null;
              clearLeaseReadOnly();
              startLeaseHeartbeat(jobId);
              return { ok: true };
          }
          if (data.status === 'occupied') {
              // 记下当前只读工单，避免只读状态误拦其他工单。
              state.lease.jobId = jobId;
              setLeaseReadOnly(data.occupant, data.error || '作业单占用中');
              return { ok: false, occupied: true, data: data };
          }
          addLog('❌ 取得工单操作权失败: ' + (data.error || data.status), 'error');
          return { ok: false, data: data };
      })
      .catch(err => {
          addLog('❌ 取得工单操作权请求失败: ' + err.message, 'error');
          return { ok: false };
      });
}

function releaseJobLease(jobId, useBeacon) {
    const target = jobId || state.lease.jobId;
    if (!target || !state.lease.holding) return;
    state.lease.holding = false;
    state.lease.jobId = null;
    stopLeaseHeartbeat();
    const body = JSON.stringify({ job_id: target, holder_id: getHolderId() });
    try {
        fetch('/api/workflow/release', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body,
            keepalive: useBeacon === true
        }).catch(() => { /* 尽力而为，超时机制兜底 */ });
    } catch (e) { /* 忽略 */ }
}

// 只读时拦截操作类按钮：给出明确原因，而不是让请求发出去再失败。
// jobId 可选：只读状态绑定在具体工单上，操作其他工单时不应被误拦。
function blockIfReadOnly(jobId) {
    if (!state.lease.readOnly) return false;
    const lockedJob = state.lease.jobId;
    if (jobId && lockedJob && String(lockedJob) !== String(jobId)) return false;
    const who = (state.lease.occupant && state.lease.occupant.holder_label) || '另一个页面';
    addLog('🔒 运行失败：作业单占用中（该工单正由 ' + who + ' 操作）', 'warning');
    addLog('ℹ️ 请等待对方页面释放操作权，或等待其租约超时后再试。', 'warning');
    renderLeaseBanner();
    return true;
}

function rememberActiveWorkflow(workflowState) {
    const jobId = workflowState && (workflowState.thread_id || workflowState.jobId || workflowState.threadId);
    try {
        if (jobId && workflowState.status !== 'completed' && workflowState.status !== 'idle') {
            activeWorkflowStore.setItem(ACTIVE_WORKFLOW_STORAGE_KEY, JSON.stringify({
                jobId: jobId,
                status: workflowState.status,
                currentStage: workflowState.current_stage || workflowState.currentStage || ''
            }));
        } else if (workflowState && (workflowState.status === 'completed' || workflowState.status === 'idle')) {
            activeWorkflowStore.removeItem(ACTIVE_WORKFLOW_STORAGE_KEY);
        }
    } catch (e) {
        console.warn('保存当前作业失败:', e);
    }
}

function restoreActiveWorkflow() {
    let saved;
    try {
        saved = JSON.parse(activeWorkflowStore.getItem(ACTIVE_WORKFLOW_STORAGE_KEY) || 'null');
    } catch (e) {
        activeWorkflowStore.removeItem(ACTIVE_WORKFLOW_STORAGE_KEY);
        return;
    }
    if (!saved || !/^\d{17}$/.test(saved.jobId || '')) {
        // 兼容更新前未写入浏览器缓存、或后端重启后的情况。
        // 带上 holder_id：服务端会跳过正被其他页面操作的工单，保证新页面保持独立。
        fetch('/api/workflow/latest-incomplete?holder_id=' + encodeURIComponent(getHolderId()))
            .then(r => r.json())
            .then(data => {
                if (!data || !data.job_id || data.status === 'none') return;
                state.workflowState = data;
                state.lease.jobId = data.job_id;
                renderWorkflowDiagram();
                updateControlPanel();
                return claimJobLease(data.job_id).then(() => {
                    if (state.lease.readOnly) return;
                    rememberActiveWorkflow(data);
                    addLog('🔄 已找回最近未完成作业: ' + data.job_id);
                    connectWebSocket(data.job_id);
                    reconcilePendingConfirmation(data, '已找回的作业');
                    startResumeStatePolling(data.job_id);
                });
            })
            .catch(err => console.warn('查找未完成作业失败:', err));
        return;
    }

    // 先同步恢复最小状态，避免页面刚打开、状态接口尚未返回时点击“启动”误建新作业。
    state.workflowState = {
        status: saved.status || 'unknown',
        current_stage: saved.currentStage || '',
        threadId: saved.jobId,
        jobId: saved.jobId,
        pending: [],
        confirmed: []
    };

    fetch('/api/workflow/state?thread_id=' + encodeURIComponent(saved.jobId))
        .then(r => r.json())
        .then(data => {
            if (!data || data.status === 'idle' || data.status === 'unknown') {
                activeWorkflowStore.removeItem(ACTIVE_WORKFLOW_STORAGE_KEY);
                return;
            }
            state.workflowState = data;
            state.lease.jobId = saved.jobId;
            rememberActiveWorkflow(data);
            renderWorkflowDiagram();
            updateControlPanel();
            // 无论是否取得操作权都继续展示；能否操作由租约决定。
            return claimJobLease(saved.jobId).then(() => {
                if (data.status === 'completed') return;
                addLog('🔄 已恢复未完成作业: ' + saved.jobId);
                connectWebSocket(saved.jobId);
                reconcilePendingConfirmation(data, '已恢复的作业');
                startResumeStatePolling(saved.jobId);
            });
        })
        .catch(err => console.warn('恢复当前作业失败:', err));
}

// ========== 初始化 ==========
document.addEventListener('DOMContentLoaded', function() {
    initTabs();
    initMenu();
    loadModelConfig();
    loadAllPrompts();
    renderWorkflowDiagram();
    loadPermitTypes();
    selectInputSource('manual');
    loadHistoryJobs();
    restoreActiveWorkflow();
});

// 关闭/刷新页面时尽力交还工单占用；失败也没关系，服务端靠心跳超时兜底。
window.addEventListener('beforeunload', function() {
    releaseJobLease(null, true);
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

// ========== 历史作业 ==========

function switchWorkEntry(mode) {
    const isHistory = mode === 'history';
    if (!isHistory && state.historyViewJobId) exitHistoryView();
    document.getElementById('work-entry-new-tab').classList.toggle('active', !isHistory);
    document.getElementById('work-entry-history-tab').classList.toggle('active', isHistory);
    document.getElementById('work-entry-new-panel').classList.toggle('active', !isHistory);
    document.getElementById('work-entry-history-panel').classList.toggle('active', isHistory);
    if (isHistory) loadHistoryJobs();
}

function historyStatusText(job) {
    if (job.status === 'completed') return '已完成';
    if (job.status === 'waiting') return '等待 ' + (job.current_stage || '') + ' 确认';
    if (job.status === 'error' || job.status === 'failed') return (job.current_stage || '工作流') + ' 执行失败';
    if (job.status === 'running' || job.status === 'executing' || job.status === 'starting') return (job.current_stage || '工作流') + ' 执行中';
    return job.status || '未知';
}

function loadHistoryJobs() {
    const container = document.getElementById('history-job-list');
    if (!container) return;
    container.innerHTML = '<div class="history-empty">正在加载...</div>';
    fetch('/api/workflow/history?limit=50')
        .then(r => r.json())
        .then(data => {
            state.historyJobs = data.jobs || [];
            renderHistoryJobs();
        })
        .catch(err => {
            container.innerHTML = '<div class="history-empty">加载失败：' + escapeHtml(err.message) + '</div>';
        });
}

function renderHistoryJobs() {
    const container = document.getElementById('history-job-list');
    if (!container) return;
    if (state.historyJobs.length === 0) {
        container.innerHTML = '<div class="history-empty">暂无历史作业</div>';
        return;
    }
    container.innerHTML = state.historyJobs.map(job => {
        const selected = state.historyViewJobId === job.job_id ? ' selected' : '';
        const content = job.job_content || job.job_type || '未填写作业内容';
        const meta = [job.region, job.applicant].filter(Boolean).join(' · ');
        return `<div class="history-job-card${selected}" data-job-id="${job.job_id}">
            <div class="history-job-head">
                <span class="history-job-id">${job.job_id}</span>
                <span class="history-job-status">${escapeHtml(historyStatusText(job))}</span>
            </div>
            <div class="history-job-content">${escapeHtml(content)}</div>
            <div class="history-job-meta">${escapeHtml(meta || '无区域信息')}</div>
            <div class="history-job-actions">
                <button class="btn btn-secondary" onclick="viewHistoryJob('${job.job_id}')">查看流程</button>
                <button class="btn btn-secondary" onclick="viewHistoryPermit('${job.job_id}')">查看作业单</button>
                ${job.can_continue ? `<button class="btn btn-primary" onclick="continueHistoryJob('${job.job_id}')">继续</button>` : ''}
                <button class="btn btn-reject" onclick="deleteHistoryJob('${job.job_id}')">删除</button>
            </div>
        </div>`;
    }).join('');
}

function deleteHistoryJob(jobId) {
    // 破坏性操作：先确认。服务端只把工单目录移到 data/jobs_trash，不物理删除。
    if (!confirm('确定删除作业 ' + jobId + ' 吗？\n\n删除后该作业会从列表移除，如需找回请联系管理员（服务端 data/jobs_trash）。')) return;
    fetch('/api/workflow/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: jobId })
    }).then(r => r.json())
      .then(data => {
          if (data.status === 'ok') {
              addLog('🗑️ 已删除作业: ' + jobId, 'success');
              if (state.historyViewJobId === jobId) exitHistoryView();
              loadHistoryJobs();
          } else {
              addLog('❌ 删除失败: ' + (data.error || '未知错误'), 'error');
              alert('删除失败：' + (data.error || '未知错误'));
          }
      })
      .catch(err => addLog('❌ 删除失败: ' + err.message, 'error'));
}

function workflowStateFromDetail(detail) {
    return {
        status: detail.status || 'unknown',
        pending: detail.pending || [],
        pending_data: detail.pending_data || {},
        confirmed: detail.confirmed || [],
        current_stage: detail.current_stage || '',
        thread_id: detail.job_id,
        jobId: detail.job_id,
        threadId: detail.job_id,
        agents: detail.agents || {}
    };
}

function renderHistoryLogs(detail) {
    const container = document.getElementById('log-container');
    const logs = detail.logs || [];
    if (logs.length === 0) {
        container.innerHTML = '<div class="log-entry"><span class="log-time">[--:--:--]</span> 该作业暂无日志</div>';
        return;
    }
    container.innerHTML = '';
    logs.forEach(item => {
        const time = item.timestamp ? new Date(item.timestamp).toLocaleTimeString('zh-CN', { hour12: false }) : '--:--:--';
        const message = item.message || item.action || JSON.stringify(item);
        const entry = document.createElement('div');
        entry.className = 'log-entry';
        entry.innerHTML = `<span class="log-time">[${escapeHtml(time)}]</span> ${escapeHtml(message)}`;
        container.appendChild(entry);
    });
    container.scrollTop = container.scrollHeight;
}

function viewHistoryJob(jobId) {
    fetch('/api/workflow/job-detail?job_id=' + encodeURIComponent(jobId))
        .then(r => r.json())
        .then(detail => {
            if (detail.error) throw new Error(detail.error || '读取作业失败');
            if (!state.historyViewJobId) state.liveWorkflowState = state.workflowState;
            state.historyViewJobId = jobId;
            state.workflowState = workflowStateFromDetail(detail);
            document.getElementById('history-view-text').textContent = '正在查看历史作业：' + jobId + '（只读）';
            document.getElementById('history-view-banner').classList.add('active');
            renderHistoryJobs();
            renderWorkflowDiagram();
            updateControlPanel();
            renderHistoryLogs(detail);
        })
        .catch(err => addLog('❌ 历史作业读取失败: ' + err.message, 'error'));
}

function viewHistoryPermit(jobId) {
    const modal = document.getElementById('history-permit-modal');
    const content = document.getElementById('history-permit-content');
    document.getElementById('history-permit-title').textContent = '历史作业单：' + jobId;
    state.historyPermitDetail = null;
    for (const id of ['history-show-jsa', 'history-show-permit', 'history-jsa-download']) document.getElementById(id).style.display = 'none';
    content.textContent = '正在加载作业单内容...';
    modal.classList.add('active');
    fetch('/api/workflow/job-detail?job_id=' + encodeURIComponent(jobId))
        .then(r => r.json())
        .then(detail => {
            if (detail.error) throw new Error(detail.error || '读取作业单失败');
            if (!modal.classList.contains('active')) return;
            state.historyPermitDetail = detail;
            renderHistoryDocument(false);
        })
        .catch(err => {
            content.innerHTML = '<div class="permit-missing">作业单读取失败：' + escapeHtml(err.message) + '</div>';
        });
}

function closeHistoryPermit() {
    document.getElementById('history-permit-modal').classList.remove('active');
}

function exitHistoryView() {
    if (!state.historyViewJobId) return;
    state.historyViewJobId = null;
    if (state.liveWorkflowState) state.workflowState = state.liveWorkflowState;
    state.liveWorkflowState = null;
    document.getElementById('history-view-banner').classList.remove('active');
    renderHistoryJobs();
    renderWorkflowDiagram();
    updateControlPanel();
    document.getElementById('log-container').innerHTML = '<div class="log-entry"><span class="log-time">[--:--:--]</span> 已返回当前作业</div>';
}

function continueHistoryJob(jobId) {
    fetch('/api/workflow/job-detail?job_id=' + encodeURIComponent(jobId))
        .then(r => r.json())
        .then(detail => {
            if (detail.error) throw new Error(detail.error || '读取作业失败');
            state.historyViewJobId = null;
            state.liveWorkflowState = null;
            state.workflowState = workflowStateFromDetail(detail);
            state.lease.jobId = jobId;
            document.getElementById('history-view-banner').classList.remove('active');
            rememberActiveWorkflow(state.workflowState);
            renderWorkflowDiagram();
            updateControlPanel();
            // 先取得该工单的操作权：被其他页面占用时保持只读，不抢别人的工单。
            claimJobLease(jobId).then(() => {
                if (detail.status === 'error' || detail.status === 'failed') {
                    resumeWorkflow(jobId);
                } else {
                    connectWebSocket(jobId);
                    reconcilePendingConfirmation(state.workflowState, '历史作业');
                    startResumeStatePolling(jobId);
                }
                switchWorkEntry('new');
            });
        })
        .catch(err => addLog('❌ 继续历史作业失败: ' + err.message, 'error'));
}

// ========== 工作流执行 ==========

function applyApplicationToForm(application) {
    application = application || {};
    const firstPerson = (application.personnel || [])[0] || {};
    document.getElementById('app-job-content').value = application.job_content || '';
    document.getElementById('app-region').value = application.region || application.work_location || '';
    document.getElementById('app-person-name').value = firstPerson.name || application.person_name || '';
    document.getElementById('app-person-badge').value = firstPerson.badge_id || application.person_badge || '';
    document.getElementById('app-start').value = application.planned_start || application.start || '';
    document.getElementById('app-end').value = application.planned_end || application.end || '';
}

function selectInputSource(source) {
    state.inputSource = source === 'docx' ? 'docx' : 'manual';
    document.getElementById('source-manual-option').classList.toggle('active', state.inputSource === 'manual');
    document.getElementById('source-docx-option').classList.toggle('active', state.inputSource === 'docx');
    document.getElementById('source-manual-panel').classList.toggle('active', state.inputSource === 'manual');
    document.getElementById('source-docx-panel').classList.toggle('active', state.inputSource === 'docx');
    // 手动填报的字段由所选作业类型决定；旧表单只在 DOCX 模式下用于核对识别结果。
    document.getElementById('docx-application-fields').style.display = state.inputSource === 'docx' ? '' : 'none';
    if (state.inputSource === 'docx') applyApplicationToForm(state.realApplication || {});
}

// ========== 手动填报：作业类型与字段 ==========

function currentPermitType() {
    return (state.permitTypes || []).find(item => item.key === state.permitType) || null;
}

function loadPermitTypes() {
    fetch('/api/workflow/permit-types')
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'ok') throw new Error(data.error || '加载失败');
            state.permitTypes = data.types || [];
            const select = document.getElementById('permit-type-select');
            select.innerHTML = '<option value="">请选择作业类型</option>' +
                state.permitTypes.map(t => `<option value="${escapeHtml(t.key)}">${escapeHtml(t.name)}</option>`).join('');
            renderPermitFields();
        })
        .catch(err => {
            document.getElementById('permit-type-select').innerHTML = '<option value="">作业类型加载失败</option>';
            addLog('❌ 加载作业类型失败: ' + err.message, 'error');
        });
}

function onPermitTypeChange() {
    state.permitType = document.getElementById('permit-type-select').value;
    renderPermitFields();
}

function permitFieldInputId(key) {
    return 'permit-field-' + key;
}

// 系统自动填的字段（如作业申请时间）：本地时间「YYYY-MM-DD HH:MM」。
function nowText() {
    const d = new Date();
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function renderPermitFields() {
    const container = document.getElementById('permit-type-fields');
    const hint = document.getElementById('permit-mock-hint');
    const type = currentPermitType();
    if (!type) {
        container.innerHTML = '<div class="docx-status">请选择作业类型，随后填写该类型需要的基本信息。</div>';
        if (hint) hint.textContent = '';
        return;
    }
    const mockSets = PERMIT_MOCK_DATA[type.key] || [];
    if (hint) {
        hint.textContent = mockSets.length
            ? `共 ${mockSets.length} 套示例数据，每点一次切换一套`
            : '该类型暂无示例数据';
    }
    let html = `<div class="section-title">📋 ${escapeHtml(type.name)} · 基本信息</div>`;
    for (const field of type.fields) {
        const inputId = permitFieldInputId(field.key);
        const required = field.required ? ' <span style="color: #C62828;">*</span>' : '';
        html += `<div class="form-group"><label class="form-label">${escapeHtml(field.label)}${required}</label>`;
        if (field.type === 'textarea') {
            html += `<textarea class="form-input" id="${inputId}" rows="3" style="height: 60px !important; min-height: 60px !important;"></textarea>`;
        } else if (field.type === 'select') {
            html += `<select class="form-input" id="${inputId}"><option value=""></option>` +
                field.options.map(o => `<option value="${escapeHtml(o)}">${escapeHtml(o)}</option>`).join('') + '</select>';
        } else if (field.type === 'multiselect') {
            // 复选框组：原生 select multiple 要按住 Ctrl/Cmd 才能多选，单击会清掉已选项。
            html += `<div class="check-group" id="${inputId}">` +
                field.options.map(o => `<label class="check-item"><input type="checkbox" value="${escapeHtml(o)}"><span>${escapeHtml(o)}</span></label>`).join('') +
                '</div>';
        } else if (field.type === 'bool') {
            html += `<select class="form-input" id="${inputId}"><option value="是">是</option><option value="否">否</option></select>`;
        } else if (field.type === 'time_range') {
            html += `<div class="form-row"><input type="text" class="form-input" id="${inputId}-start" placeholder="开始时间，如 2026-10-09 09:00">` +
                `<input type="text" class="form-input" id="${inputId}-end" placeholder="结束时间，如 2026-10-09 17:00" style="margin-left: 8px;"></div>`;
        } else {
            // readonly + auto=now 的字段（作业申请时间）由系统填，表单上只读展示。
            const preset = field.auto === 'now' ? nowText() : '';
            const locked = field.readonly ? ' disabled style="background:#F4F4F5;color:#52525B;"' : '';
            html += `<input type="text" class="form-input" id="${inputId}" value="${escapeHtml(preset)}"${locked}>`;
        }
        html += '</div>';
    }
    container.innerHTML = html;
}

function readPermitFieldValue(field) {
    if (field.type === 'time_range') {
        const start = (document.getElementById(permitFieldInputId(field.key) + '-start') || {}).value || '';
        const end = (document.getElementById(permitFieldInputId(field.key) + '-end') || {}).value || '';
        return { start: start.trim(), end: end.trim() };
    }
    const element = document.getElementById(permitFieldInputId(field.key));
    if (!element) return '';
    if (field.type === 'multiselect') {
        return Array.from(element.querySelectorAll('input[type="checkbox"]:checked'))
            .map(box => box.value).filter(Boolean);
    }
    return (element.value || '').trim();
}

function collectPermitFieldValues() {
    const type = currentPermitType();
    const values = {};
    if (!type) return values;
    for (const field of type.fields) {
        values[field.key] = readPermitFieldValue(field);
    }
    return values;
}

function resetPermitFields() {
    state.permitType = '';
    const select = document.getElementById('permit-type-select');
    if (select) select.value = '';
    renderPermitFields();
}

// 每种作业类型两套示例数据，供「填入 Mock 数据」按钮循环填入。
// 直接内嵌在前端：不新增 /data/ 请求，避免牵动 console /jobmon 的脚本适配契约。
// key 必须与 permit_templates.json 解析后的字段一致；bool 字段用「是」「否」。
const PERMIT_MOCK_DATA = {
    hot_work: [
        {
            apply_unit: '炼油三部', area_unit: '焦化车间', applicant: '李强', work_people: '王明',
            work_place: '焦化 I 装置 F-0102 空气预热器区',
            work_content: '更换 F-0102 空气预热器入口膨胀节，配合切割与焊接作业。',
            work_time: { start: '2026-10-12 08:30', end: '2026-10-12 17:00' },
            hot_place: 'F-0102 空气预热器入口法兰处', hot_person: '王明', hot_cert: '动火-2026-1087',
            hot_types: ['焊接', '气割', '打磨'], plan_required: '是'
        },
        {
            apply_unit: '炼油二部', area_unit: '常减压车间', applicant: '张伟', work_people: '陈刚',
            work_place: '常减压 II 装置 加热炉 F-0201 二层平台',
            work_content: '加热炉 F-0201 火嘴法兰紧固，吹扫管线补焊。',
            work_time: { start: '2026-10-15 09:00', end: '2026-10-15 16:30' },
            hot_place: 'F-0201 加热炉二层火嘴平台', hot_person: '陈刚', hot_cert: '动火-2026-1132',
            hot_types: ['焊接', '明火'], plan_required: '否'
        }
    ],
    confined_space: [
        {
            apply_unit: '炼油三部', area_unit: '焦化车间', applicant: '李强', work_people: '王明',
            work_place: '焦化 I 装置 F-0102 空气预热器内部',
            work_content: '进入 F-0102 空气预热器内部清理积灰、检查换热管束。',
            work_time: { start: '2026-10-13 08:00', end: '2026-10-13 18:00' },
            space_name: '空气预热器 F-0102 壳体内', attach_plan: '是'
        },
        {
            apply_unit: '储运部', area_unit: '罐区车间', applicant: '赵磊', work_people: '孙浩',
            work_place: '中间罐区 T-1203 污油罐',
            work_content: '进入 T-1203 罐内清理罐底油泥，检查内壁防腐层。',
            work_time: { start: '2026-10-16 08:00', end: '2026-10-16 17:30' },
            space_name: '污油罐 T-1203 罐内', attach_plan: '是'
        }
    ],
    line_open: [
        {
            apply_unit: '炼油三部', area_unit: '焦化车间', applicant: '李强', work_people: '王明',
            work_place: '焦化 I 装置 原料油泵 P-0101 出口管线',
            work_content: 'P-0101 出口管线加装盲板，隔离原料油系统。',
            work_time: { start: '2026-10-12 08:00', end: '2026-10-12 12:00' },
            work_category: '堵盲板', pipe_name: '原料油泵 P-0101 出口管线 DN200', medium: '原料油'
        },
        {
            apply_unit: '炼油二部', area_unit: '常减压车间', applicant: '张伟', work_people: '陈刚',
            work_place: '常减压 II 装置 柴油汽提塔 C-0203 侧线',
            work_content: '柴油汽提塔 C-0203 侧线抽盲板，恢复管线联通。',
            work_time: { start: '2026-10-17 09:00', end: '2026-10-17 15:00' },
            work_category: '抽盲板', pipe_name: '柴油汽提塔 C-0203 侧线 DN150', medium: '柴油'
        }
    ],
    high_work: [
        {
            apply_unit: '炼油三部', area_unit: '焦化车间', applicant: '李强', work_people: '王明',
            work_place: '焦化 I 装置 焦炭塔 T-0101 顶部平台',
            work_content: '焦炭塔 T-0101 顶部平台护栏除锈刷漆。',
            work_time: { start: '2026-10-14 08:30', end: '2026-10-14 17:00' },
            high_level: 'Ⅱ级', plan_required: '否'
        },
        {
            apply_unit: '设备管理部', area_unit: '检修车间', applicant: '周波', work_people: '吴涛',
            work_place: '常减压 II 装置 常压塔 C-0201 顶部人孔区',
            work_content: '常压塔 C-0201 顶部更换液位计，搭设临边防护。',
            work_time: { start: '2026-10-18 08:00', end: '2026-10-18 16:00' },
            high_level: 'Ⅲ级', plan_required: '是'
        }
    ],
    lifting: [
        {
            apply_unit: '炼油三部', area_unit: '焦化车间', applicant: '李强',
            work_place: '焦化 I 装置 检修场地',
            work_content: '吊装 F-0102 空气预热器换热管束并落位。',
            work_time: { start: '2026-10-12 13:00', end: '2026-10-12 17:00' },
            crane_name: '25t 汽车起重机 辽A·H8231', lifting_item: '空气预热器换热管束',
            lifting_weight: '18', slinger: '王明', lifting_level: '三级'
        },
        {
            apply_unit: '炼油二部', area_unit: '常减压车间', applicant: '张伟',
            work_place: '常减压 II 装置 加热炉北侧检修区',
            work_content: '吊装加热炉炉管组件并落位固定。',
            work_time: { start: '2026-10-19 09:00', end: '2026-10-19 15:00' },
            crane_name: '50t 汽车起重机 辽A·H9056', lifting_item: '加热炉炉管组件',
            lifting_weight: '42', slinger: '陈刚', lifting_level: '二级'
        }
    ],
    temp_power: [
        {
            apply_unit: '炼油三部', area_unit: '焦化车间',
            work_place: '焦化 I 装置 F-0102 检修作业区',
            work_content: 'F-0102 检修用电焊机、角磨机临时电源敷设。',
            work_time: { start: '2026-10-12 08:00', end: '2026-10-12 18:00' },
            permit_no: 'LDY-2026-0417', voltage: '380'
        },
        {
            apply_unit: '炼油二部', area_unit: '常减压车间',
            work_place: '常减压 II 装置 加热炉检修区',
            work_content: '加热炉检修用电焊机与行灯临时电源敷设。',
            work_time: { start: '2026-10-15 08:00', end: '2026-10-15 17:00' },
            permit_no: 'LDY-2026-0453', voltage: '220'
        }
    ],
    earth_work: [
        {
            apply_unit: '工程管理部', area_unit: '焦化车间', applicant: '郑凯', work_people: '刘洋',
            work_place: '焦化 I 装置 循环水埋地管线管段处',
            work_content: '开挖循环水埋地管线，更换腐蚀管段。',
            work_time: { start: '2026-10-20 08:00', end: '2026-10-20 17:30' },
            earth_scope: '沿管线走向开挖沟槽，长 12 米、宽 1.2 米、深 1.5 米，放坡系数 1:0.5，附开挖简图。',
            plan_required: '是'
        },
        {
            apply_unit: '工程管理部', area_unit: '储运车间', applicant: '郑凯', work_people: '刘洋',
            work_place: '中间罐区 T-1203 罐区北侧',
            work_content: '开挖罐区消防水管线漏点，配合补焊处理。',
            work_time: { start: '2026-10-21 08:30', end: '2026-10-21 16:30' },
            earth_scope: '机械开挖配合人工清底，长 8 米、宽 1.0 米、深 1.2 米，附开挖简图。',
            plan_required: '否'
        }
    ],
    road_close: [
        {
            apply_unit: '工程管理部', area_unit: '焦化车间', applicant: '郑凯', work_people: '刘洋',
            work_time: { start: '2026-10-20 08:00', end: '2026-10-20 18:00' },
            road_reason: '焦化 I 装置东侧消防通道埋地管线抢修，需占道施工。',
            related_units: '消防保卫部、生产调度中心、焦化车间', plan_required: '是'
        },
        {
            apply_unit: '工程管理部', area_unit: '储运车间', applicant: '郑凯', work_people: '刘洋',
            work_time: { start: '2026-10-22 09:00', end: '2026-10-22 15:00' },
            road_reason: '中间罐区北侧管廊防腐施工搭设脚手架，需占道作业。',
            related_units: '消防保卫部、储运车间', plan_required: '否'
        }
    ],
    radiation: [
        {
            work_place: '焦化 I 装置 F-0102 空气预热器入口管线焊缝，影响范围半径 30 米',
            work_content: '对 F-0102 入口膨胀节焊缝进行射线探伤，检测 6 道焊口。',
            work_time: { start: '2026-10-15 20:00', end: '2026-10-16 02:00' }
        },
        {
            work_place: '常减压 II 装置 C-0201 塔顶管线焊缝，影响范围半径 25 米',
            work_content: '对常压塔 C-0201 塔顶管线环焊缝进行射线探伤，检测 4 道焊口。',
            work_time: { start: '2026-10-19 21:00', end: '2026-10-20 01:00' }
        }
    ],
    non_routine: [
        {
            apply_unit: '炼油三部', area_unit: '焦化车间', applicant: '李强', work_people: '王明',
            work_place: '焦化 I 装置 F-0102 空气预热器及附属管线',
            work_content: '对 F-0102 空气预热器及附属管线进行水压试验，试验压力 1.6 MPa。',
            work_time: { start: '2026-10-13 08:00', end: '2026-10-13 17:00' },
            nr_scope: '设备及管线整体水压试验，含壳体、进出口管段与膨胀节，附试压流程简图。',
            plan_required: '是'
        },
        {
            apply_unit: '炼油二部', area_unit: '常减压车间', applicant: '张伟', work_people: '陈刚',
            work_place: '常减压 II 装置 柴油汽提塔 C-0203 及侧线',
            work_content: '对柴油汽提塔 C-0203 及其侧线进行气密性试验，试验压力 0.6 MPa。',
            work_time: { start: '2026-10-17 08:30', end: '2026-10-17 16:30' },
            nr_scope: '塔体及侧线气密试验，含塔顶、侧线抽出与返塔管线，附试压流程简图。',
            plan_required: '否'
        }
    ]
};

// 记录每种类型当前填到第几套，实现「每点一次切换一套」。
const permitMockCursor = {};

function fillPermitMock() {
    const type = currentPermitType();
    if (!type) {
        addLog('❌ 请先选择作业类型，再填入 Mock 数据', 'error');
        return;
    }
    const sets = PERMIT_MOCK_DATA[type.key] || [];
    if (!sets.length) {
        addLog(`❌ ${type.name} 暂无 Mock 数据`, 'error');
        return;
    }
    const index = (permitMockCursor[type.key] || 0) % sets.length;
    const values = sets[index];
    permitMockCursor[type.key] = index + 1;

    for (const field of type.fields) {
        if (field.readonly) continue;   // 系统自动填的字段（申请时间），Mock 不覆盖
        const value = values[field.key];
        if (value === undefined || value === null) continue;
        const inputId = permitFieldInputId(field.key);
        if (field.type === 'time_range') {
            const start = document.getElementById(inputId + '-start');
            const end = document.getElementById(inputId + '-end');
            if (start) start.value = value.start || '';
            if (end) end.value = value.end || '';
        } else if (field.type === 'multiselect') {
            const element = document.getElementById(inputId);
            if (element) {
                const chosen = Array.isArray(value) ? value : [value];
                element.querySelectorAll('input[type="checkbox"]').forEach(box => {
                    box.checked = chosen.includes(box.value);
                });
            }
        } else {
            const element = document.getElementById(inputId);
            if (element) element.value = String(value);
        }
    }
    addLog(`🧪 已填入 ${type.name} Mock 数据（第 ${index + 1}/${sets.length} 套）`);
}

function setDocxStatus(message, type = '') {
    const status = document.getElementById('permit-docx-status');
    status.className = 'docx-status' + (type ? ' ' + type : '');
    status.textContent = message;
}

function handlePermitDocx(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.docx')) {
        setDocxStatus('仅支持 .docx 作业许可文件。', 'error');
        return;
    }
    if (file.size > 10 * 1024 * 1024) {
        setDocxStatus('文件超过 10MB，无法上传。', 'error');
        return;
    }
    setDocxStatus('正在解析 ' + file.name + '...');
    const reader = new FileReader();
    reader.onload = () => {
        const contentBase64 = String(reader.result || '').split(',')[1] || '';
        fetch('/api/workflow/parse-docx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename: file.name, content_base64: contentBase64 })
        }).then(r => r.json()).then(data => {
            if (data.status === 'error') throw new Error(data.error || '解析失败');
            state.realApplication = data.application || {};
            applyApplicationToForm(state.realApplication);
            const missing = data.missing_fields || [];
            if (data.is_blank) {
                setDocxStatus('已识别为“' + (data.document.permit_title || '作业许可') + '”空白模板。请补充：' + (missing.join('、') || '关键作业信息') + '。', 'warning');
            } else if (missing.length) {
                setDocxStatus('解析完成，但启动前还需补充：' + missing.join('、') + '。', 'warning');
            } else {
                setDocxStatus('解析完成：' + (data.document.permit_title || file.name) + '。请核对下方识别结果。', 'success');
            }
        }).catch(err => {
            state.realApplication = null;
            applyApplicationToForm({});
            setDocxStatus('解析失败：' + err.message, 'error');
        });
    };
    reader.onerror = () => setDocxStatus('读取文件失败，请重新选择。', 'error');
    reader.readAsDataURL(file);
}

function validateNewApplication(application) {
    if (state.inputSource === 'manual') {
        const type = currentPermitType();
        if (!type) return ['作业类型'];
        const missing = [];
        for (const field of type.fields) {
            if (!field.required) continue;
            const value = readPermitFieldValue(field);
            const empty = field.type === 'time_range'
                ? !(value.start && value.end)
                : (Array.isArray(value) ? value.length === 0 : value === '');
            if (empty) missing.push(field.label);
        }
        return missing;
    }
    if (state.inputSource !== 'docx') return [];
    if (!state.realApplication) return ['请先上传并解析真实 DOCX 作业许可'];
    const missing = [];
    if (!application.job_content) missing.push('作业内容');
    if (!application.region) missing.push('作业区域/地点');
    if (!(application.personnel || []).some(person => person && person.name)) missing.push('作业人员');
    if (!application.planned_start) missing.push('开始时间');
    if (!application.planned_end) missing.push('结束时间');
    return missing;
}

function startWorkflow() {
    // 检查是否有未完成的作业
    const existingJobId = state.workflowState.thread_id ||
                          state.workflowState.jobId ||
                          state.workflowState.threadId;
    if (existingJobId &&
        state.workflowState.status &&
        state.workflowState.status !== 'completed' &&
        state.workflowState.status !== 'idle') {

        // 三选一：旧作业（继续执行）/ 新作业（另起一单）/ 取消（不启动）。
        const jobId = existingJobId;
        const currentStage = state.workflowState.current_stage ||
                             state.workflowState.currentStage || '未知';
        const status = state.workflowState.status || '未知';

        askStartChoice({ jobId, currentStage, status }).then(choice => {
            if (choice === 'old') {
                if (blockIfReadOnly(jobId)) return;
                // 先取得该工单的操作权；被其他页面占用时保持只读并提示接管。
                claimJobLease(jobId).then(res => {
                    if (!res.ok) return;
                    // 失败状态才触发断点恢复；running/waiting 只重新连接现有作业。
                    if (status === 'error' || status === 'failed') {
                        resumeWorkflow(jobId);
                    } else {
                        addLog('🔄 重新连接作业: ' + jobId);
                        connectWebSocket(jobId);
                        startResumeStatePolling(jobId);
                    }
                });
                return;
            }
            if (choice === 'new') {
                createNewWorkflow();
                return;
            }
            // 取消：不创建任何作业，旧作业保持原状
            addLog('⏹️ 已取消启动：未创建新作业，旧作业保持原状', 'warning');
        });
        return;
    }

    createNewWorkflow();
}

// 启动时发现未完成作业时的三选一弹窗；返回 'old' | 'new' | 'cancel'。
let startChoiceResolver = null;

function askStartChoice(info) {
    return new Promise(resolve => {
        const modal = document.getElementById('start-choice-modal');
        const summary = document.getElementById('start-choice-summary');
        if (!modal || !summary) { resolve('cancel'); return; }
        summary.textContent =
            `作业单号：${info.jobId}　当前阶段：${info.currentStage}　状态：${info.status}`;
        startChoiceResolver = resolve;
        modal.classList.add('active');
    });
}

function resolveStartChoice(choice) {
    const modal = document.getElementById('start-choice-modal');
    if (modal) modal.classList.remove('active');
    const resolve = startChoiceResolver;
    startChoiceResolver = null;
    if (resolve) resolve(choice);
}

// 创建新作业并启动（原先内联在 startWorkflow 里的那一段）。
function createNewWorkflow() {
    resumeStatePollGeneration++;
    releaseJobLease();
    const app = buildApplicationJson();
    const missing = validateNewApplication(app);
    if (missing.length) {
        const message = missing.length === 1 && missing[0].startsWith('请先') ? missing[0] : '真实作业许可缺少：' + missing.join('、') + '。请在识别结果中补充后再启动。';
        addLog('⚠️ ' + message, 'warning');
        alert(message);
        return;
    }
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
              rememberActiveWorkflow(state.workflowState);
              addLog('📋 作业单号: ' + data.job_id, 'success');
              // 新工单必然空闲，取得操作权后再建立操作通道。
              claimJobLease(data.job_id).then(() => {
                  addLog('⏳ 工作流启动中，建立 WebSocket 连接...');
                  connectWebSocket(data.job_id);
              });
          }
      })
      .catch(err => {
          addLog('❌ 错误: ' + err.message, 'error');
      });
}

function resumeWorkflow(jobId) {
    if (blockIfReadOnly(jobId)) return;
    addLog('🔄 继续执行作业: ' + jobId);

    // 先确保本页持有操作权，再请求恢复执行；被占用时保持只读。
    claimJobLease(jobId).then(res => {
        if (!res.ok) return;
        fetch('/api/workflow/resume', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                job_id: jobId,
                force: false,
                holder_id: getHolderId()
            })
        }).then(r => r.json())
          .then(data => {
              if (data.status === 'resuming') {
                  addLog('✅ 作业恢复执行中...', 'success');
                  addLog('📋 作业单号: ' + data.job_id);
                  addLog('📍 从阶段: ' + data.stage);

                  // 建立 WebSocket 连接
                  connectWebSocket(data.job_id);
                  startResumeStatePolling(data.job_id);
              } else if (data.status === 'occupied' || data.status === 'lease_required') {
                  setLeaseReadOnly(data.occupant, data.error);
              } else if (data.status === 'error') {
                  addLog('❌ 恢复失败: ' + data.error, 'error');
                  addLog('当前作业已保留。请处理失败原因后再次恢复；如需新作业，请明确点击“重置”。', 'warning');
              }
          })
          .catch(err => {
              addLog('❌ 错误: ' + err.message, 'error');
          });
    });
}

let resumeStatePollGeneration = 0;

function reconcilePendingConfirmation(workflowState, sourceLabel, suppressStage) {
    const pending = workflowState.pending || [];
    const stage = pending[0] || '';
    const hitlModal = document.getElementById('hitl-modal');
    const modalStage = hitlModal.classList.contains('active') ? (hitlModal.dataset.stage || '') : '';

    // 本次审批已被并发保护拦下时，不能用服务端状态把同一个阶段再弹回来：
    // 该阶段要么已被处理、要么已由另一个后台任务接管，重新弹出等于窗口关不掉。
    if (suppressStage && stage === suppressStage) {
        if (hitlModal.classList.contains('active')) closeModal();
        return true;
    }

    // 另一个页面可能已经处理当前审批。只要服务端不再等待弹窗对应阶段，
    // 当前窗口就是过期窗口，必须先关闭，避免用户提交错误的下一阶段状态。
    if (modalStage && modalStage !== stage) {
        closeModal();
        if (stage) {
            addLog(`ℹ️ ${modalStage} 已由其他页面处理，当前等待 ${stage} 确认`, 'warning');
        } else if (workflowState.status === 'completed') {
            addLog(`ℹ️ ${modalStage} 已由其他页面处理，工作流已完成`, 'success');
        } else if (workflowState.status === 'error' || workflowState.status === 'failed') {
            addLog(`ℹ️ ${modalStage} 审批状态已变化，当前工作流执行失败`, 'warning');
        } else {
            addLog(`ℹ️ ${modalStage} 已由其他页面处理，工作流正在继续`, 'info');
        }
    }

    if (!stage) return false;

    const pendingData = workflowState.pending_data || {};
    const review = (pendingData[stage] || {}).pending || {};
    const reviewChanged = review.approval_id && review.approval_id !== hitlModal.dataset.approvalId;
    if (!hitlModal.classList.contains('active') || hitlModal.dataset.stage !== stage || reviewChanged) {
        showHitlModal(stage, pendingData[stage]);
        addLog('⏸️ ' + sourceLabel + '已到达人工确认: ' + stage, 'warning');
    }
    return true;
}

function startResumeStatePolling(jobId) {
    const generation = ++resumeStatePollGeneration;
    let attempts = 0;
    let observedRunning = false;

    function poll() {
        if (generation !== resumeStatePollGeneration) return;
        attempts++;
        fetch('/api/workflow/state?thread_id=' + encodeURIComponent(jobId))
            .then(r => r.json())
            .then(data => {
                if (generation !== resumeStatePollGeneration || !data) return;

                if (state.historyViewJobId) {
                    state.liveWorkflowState = data;
                    rememberActiveWorkflow(data);
                    // 与 WebSocket 通道保持一致：只读查看历史作业时不刷新当前作业视图，
                    // 但已打开的审批窗口必须按最新状态同步，否则会残留过期窗口。
                    if (document.getElementById('hitl-modal').classList.contains('active')) {
                        reconcilePendingConfirmation(data, '恢复执行');
                    }
                    if (data.status !== 'completed' && attempts < 1200) setTimeout(poll, 500);
                    return;
                }
                state.workflowState = data;
                rememberActiveWorkflow(data);
                renderWorkflowDiagram();
                updateControlPanel();

                if (reconcilePendingConfirmation(data, '恢复执行')) return;

                if (data.status === 'running' || data.status === 'executing' || data.status === 'starting') {
                    observedRunning = true;
                }
                if (data.status === 'completed') return;
                // 恢复线程启动前可能短暂读到旧 error，先等待它切换为 running。
                if ((data.status === 'error' || data.status === 'failed') &&
                    (observedRunning || attempts >= 20)) return;
                if (attempts < 1200) {
                    setTimeout(poll, 500);
                }
            })
            .catch(() => {
                if (generation === resumeStatePollGeneration && attempts < 1200) {
                    setTimeout(poll, 500);
                }
            });
    }

    poll();
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

                    if (state.historyViewJobId) {
                        state.liveWorkflowState = data;
                        rememberActiveWorkflow(data);
                        // 只读查看历史作业时不刷新当前作业视图，但**已经打开的**审批窗口
                        // 仍必须按当前作业的最新状态同步：服务端可能已由另一个页面处理
                        // 或整个工作流已完成，不同步的话窗口会一直残留在屏幕上。
                        // 这里只在窗口已经打开时才同步，避免在只读浏览时突然弹出审批窗。
                        if (document.getElementById('hitl-modal').classList.contains('active')) {
                            reconcilePendingConfirmation(data, '工作流');
                        }
                        return;
                    }
                    state.workflowState = data;
                    rememberActiveWorkflow(data);
                    renderWorkflowDiagram();
                    updateControlPanel();

                    // 更新日志
                    const currentStage = data.current_stage || '';

                    if (reconcilePendingConfirmation(data, '工作流')) {
                        // 人工审批窗口已与服务端最新状态同步。
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
                    if (!state.historyViewJobId) displayWorkflowLog(msg);
                } else if (msg.type === 'connected') {
                    if (!state.historyViewJobId) addLog('📋 ' + msg.message, 'success');
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

function buildApplicationJson() {
    if (state.inputSource === 'manual') {
        const permitFields = collectPermitFieldValues();
        const workTime = permitFields.work_time || {};
        const workPeople = permitFields.work_people || '';
        return {
            input_source: 'manual',
            permit_type: state.permitType,
            permit_fields: permitFields,
            // 同时给出下游沿用的申请字段，便于历史列表与后续阶段展示。
            job_content: permitFields.work_content || '',
            region: permitFields.area_unit || permitFields.work_place || '',
            personnel: workPeople ? [{ name: workPeople }] : [],
            planned_start: workTime.start || '',
            planned_end: workTime.end || ''
        };
    }
    const sourceApplication = state.realApplication;
    const application = sourceApplication ? JSON.parse(JSON.stringify(sourceApplication)) : {};
    delete application.person_name;
    delete application.person_badge;
    delete application.start;
    delete application.end;
    return Object.assign(application, {
        input_source: state.inputSource,
        job_content: document.getElementById('app-job-content').value,
        region: document.getElementById('app-region').value,
        personnel: [{
            ...(application.personnel && application.personnel[0] ? application.personnel[0] : {}),
            name: document.getElementById('app-person-name').value,
            badge_id: document.getElementById('app-person-badge').value
        }, ...((application.personnel || []).slice(1))],
        planned_start: document.getElementById('app-start').value,
        planned_end: document.getElementById('app-end').value
    });
}

function resetWorkflow() {
    resumeStatePollGeneration++;
    disconnectWebSocket();
    disconnectLogsWebSocket();
    // 重置后本页不再操作该工单，主动交还占用，让其他页面可以立即接手。
    releaseJobLease();
    state.lease.readOnly = false;
    state.lease.occupant = null;
    renderLeaseBanner();
    state.workflowState = {
        status: 'idle',
        pending: [],
        confirmed: [],
        currentStage: '',
        threadId: null,
        jobId: null
    };
    state.selectedWorkflowNode = null;
    state.historyViewJobId = null;
    state.liveWorkflowState = null;
    state.realApplication = null;
    resetPermitFields();
    document.getElementById('history-view-banner').classList.remove('active');
    rememberActiveWorkflow(state.workflowState);
    renderWorkflowDiagram();
    updateControlPanel();
    document.getElementById('log-container').innerHTML = '<div class="log-entry"><span class="log-time">[--:--:--]</span> 已重置</div>';
    document.getElementById('app-job-content').value = '';
    document.getElementById('app-region').value = '';
    document.getElementById('app-person-name').value = '';
    document.getElementById('app-person-badge').value = '';
    document.getElementById('app-start').value = '';
    document.getElementById('app-end').value = '';
    const fileInput = document.getElementById('permit-docx-file');
    if (fileInput) fileInput.value = '';
    setDocxStatus('请选择真实的 .docx 作业许可文件。解析后请核对并补充下方字段。');
    selectInputSource('manual');
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
    } else if (status === 'completed') {
        html = '<span style="color: #4CAF50;">✅ 工作流已完成</span>';
    } else if (status === 'error' || status === 'failed') {
        html = `<span style="color: #f44336;">❌ ${currentStage || '工作流'} 执行失败</span>`;
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

function firstValue(...values) {
    return values.find(value => value !== undefined && value !== null && value !== '') || '';
}

function displayValue(value, fallback = '待补充') {
    if (Array.isArray(value)) return value.length ? value.map(item => typeof item === 'object' ? firstValue(item.name, item.description, JSON.stringify(item)) : item).join('、') : fallback;
    if (value && typeof value === 'object') return JSON.stringify(value, null, 2);
    return value === undefined || value === null || value === '' ? fallback : String(value);
}

function permitField(label, value, cls = '') {
    return `<div class="permit-field ${cls}"><div class="permit-label">${escapeHtml(label)}</div><div class="permit-value">${escapeHtml(displayValue(value))}</div></div>`;
}

function normalizeHazards(jsa, permitContent) {
    const raw = firstValue(jsa && jsa.hazards, jsa && jsa.steps, permitContent && permitContent.hazards, []);
    if (!Array.isArray(raw)) return [];
    return raw.map((hazard, index) => {
        if (typeof hazard === 'string') return { description: hazard, severity: '', measures: [] };
        return {
            step: hazard.step || "",
            consequence: hazard.consequence || "",
            description: firstValue(hazard.description, hazard.name, hazard.hazard, '风险 ' + (index + 1)),
            severity: firstValue(hazard.severity, hazard.level, ''),
            measures: firstValue(hazard.measures, hazard.controls, hazard.control ? [hazard.control] : [])
        };
    });
}

function displayMissingField(value) {
    if (value && typeof value === 'object') {
        value = firstValue(value.message, value.field, value.code, '未知校验问题');
    }
    const text = String(value || '').trim();
    const personnelMatch = text.match(/^personnel_(.+)_qualifications$/);
    if (personnelMatch) return `作业人员“${personnelMatch[1]}”的资质证明`;
    const labels = {
        job_content: '作业内容',
        region: '作业区域',
        planned_start: '计划开始时间',
        planned_end: '计划结束时间',
        personnel: '作业人员',
        equipment: '作业设备',
        job_level: '作业等级',
        job_type: '作业类型',
        work_unit: '作业单位',
        applicant_unit: '申请单位',
        territorial_unit: '属地单位',
        work_location: '作业地点',
        medium: '作业介质',
        related_permits: '关联许可证',
        attachments: '相关附件',
        gas_detection: '气体检测记录'
    };
    if (labels[text]) return labels[text];
    if (/^[A-Za-z0-9_.-]+$/.test(text)) return '其他必填信息';
    return text.replaceAll('_', ' ');
}

// 手动填报生成的许可证票面：按模板行渲染表格，colspan 还原合并单元格。
function renderPermitTable(title, rows) {
    let html = `<div class="permit-template-title">${escapeHtml(title)}</div>`;
    html += '<table class="permit-template-table">';
    for (const row of rows) {
        html += '<tr>';
        for (const cell of row) {
            const span = Number(cell.colspan) > 1 ? ` colspan="${Number(cell.colspan)}"` : '';
            html += `<td${span}>${escapeHtml(cell.text || '')}</td>`;
        }
        html += '</tr>';
    }
    html += '</table>';
    return html;
}

// 下载当前工单的许可证：服务端按模板即时生成 docx。
function downloadPermitDocx() {
    const confirmState = state.historyViewJobId
        ? (state.liveWorkflowState || state.workflowState)
        : state.workflowState;
    const jobId = confirmState.thread_id || confirmState.jobId || confirmState.threadId;
    if (!jobId) {
        addLog('❌ 没有可下载的工单', 'error');
        return;
    }
    const link = document.createElement('a');
    // 必须用「相对当前页面」的路径，不能写绝对路径：
    // 本前端会被 console 以 iframe 嵌到 /jobmon/index.html 下，而 console 只改写 fetch
    // 调用里的 /api 前缀，不会改写 <a href>；写绝对路径会落到 console 自己的 /api/*
    // （反代到 apiserver）而不是作业监测服务。相对路径在直连 8080 与 /jobmon 嵌入下都能解析正确。
    link.href = 'api/workflow/permit-docx?job_id=' + encodeURIComponent(jobId);
    document.body.appendChild(link);
    link.click();
    link.remove();
}

function jsaDownloadUrl(detail) {
    const jsa = (detail.permit || {}).jsa_result || (detail.p1_result || {}).jsa_result || {};
    return jsa.source === 'ai' && jsa.report_path && detail.job_id
        ? 'api/workflow/permit-docx?artifact=jsa&job_id=' + encodeURIComponent(detail.job_id) : '';
}

function renderJsaReport(detail) {
    const p1 = detail.p1_result || {};
    const jsa = (detail.permit || {}).jsa_result || p1.jsa_result || {};
    let html = permitField('工单编号', detail.job_id);
    if (jsa.source !== 'ai' || !Array.isArray(jsa.steps) || !jsa.steps.length) {
        return html + `<div class="permit-warning">JSA 分析未完成：${escapeHtml(jsa.error || '该工单没有新版 JSA 报告，请重新开展分析。')}</div>`;
    }
    html += `<div class="permit-template-title">${escapeHtml(jsa.job_name || '作业')} JSA 分析报告</div>`;
    let chapter = 0;
    const section = (title, body) => `<div class="permit-section"><div class="permit-section-title">${++chapter}、${escapeHtml(title)}</div>${body}</div>`;
    const text = value => `<div class="permit-value">${escapeHtml(displayValue(value))}</div>`;
    const list = items => '<ul>' + (items || []).map(item => `<li>${escapeHtml(item)}</li>`).join('') + '</ul>';
    const app = detail.application || {};
    const fields = app.permit_fields || {};
    html += section('作业基本信息', text((p1.jsa_info_lines || []).length ? p1.jsa_info_lines :
        [jsa.job_name, fields.work_content || app.job_content, fields.work_place || app.region, jsa.type_name].filter(Boolean)));
    html += section('作业风险概述', text(jsa.risk_summary));
    html += section('作业前安全确认要求', list(jsa.pre_checks));
    const rows = jsa.steps.map(step => '<tr>' + ['step', 'hazard', 'consequence', 'level', 'control'].map(key =>
        `<td>${escapeHtml(displayValue(step[key]))}</td>`).join('') + '</tr>').join('');
    html += section('JSA 风险分析', '<table class="permit-table"><thead><tr><th>作业步骤</th><th>主要危险</th><th>可能后果</th><th>风险等级</th><th>控制措施</th></tr></thead><tbody>' + rows + '</tbody></table>');
    for (const special of jsa.special_sections || []) html += section(special.title, list(special.items));
    html += section('应急处置要求', text(jsa.emergency));
    html += section('作业前最终确认清单', list(jsa.final_checklist));
    html += '<div class="permit-value">由系统生成，须作业单位确认。</div>';
    return html;
}

function renderP1Review(detail) {
    const p1 = detail.p1_result || {};
    const pending = p1.pending_confirmation || {};
    const isJsa = pending.type === 'jsa_approval';
    const modal = document.getElementById('hitl-modal');
    modal.dataset.approvalId = pending.approval_id || '';
    document.getElementById('modal-title').textContent = isJsa ? 'P1 - JSA 报告审核' : 'P1 - 作业票审批';
    document.getElementById('modal-subtitle').textContent = isJsa ? '批准 JSA 后，系统才会判定安全措施并生成作业票' : '请核对作业票并审批';
    const download = document.getElementById('jsa-download-button');
    const url = jsaDownloadUrl(detail);
    download.style.display = isJsa && url ? '' : 'none';
    if (isJsa && url) download.href = url;
    else download.removeAttribute('href');
    if (!isJsa) return renderP1Approval(detail);
    document.getElementById('permit-download-button').style.display = 'none';
    const jsa = p1.jsa_result || {};
    const approve = document.getElementById('hitl-approve-button');
    approve.disabled = jsa.source !== 'ai' || !(jsa.steps || []).length;
    approve.title = approve.disabled ? 'JSA 未成功生成，不能批准并出票' : '';
    return renderJsaReport(detail);
}

function renderHistoryDocument(showJsa) {
    const detail = state.historyPermitDetail;
    if (!detail) return;
    const p1 = detail.p1_result || {};
    const content = (detail.permit || {}).permit_content || p1.permit_content || {};
    const hasPermit = Object.keys(content).length > 0;
    showJsa = showJsa || !hasPermit;
    document.getElementById('history-permit-title').textContent = (showJsa ? 'JSA 报告：' : '历史作业单：') + detail.job_id;
    document.getElementById('history-permit-content').innerHTML = showJsa ? renderJsaReport(detail) : renderP1Approval(detail, false);
    document.getElementById('history-show-jsa').style.display = showJsa ? 'none' : '';
    document.getElementById('history-show-permit').style.display = showJsa && hasPermit ? '' : 'none';
    const link = document.getElementById('history-jsa-download');
    const url = jsaDownloadUrl(detail);
    link.style.display = showJsa && url ? '' : 'none';
    if (showJsa && url) link.href = url;
    else link.removeAttribute('href');
}

function renderP1Approval(detail, manageApprovalButton = true) {
    const application = detail.application || {};
    const permit = detail.permit || {};
    const p1 = detail.p1_result || {};
    const content = permit.permit_content || p1.permit_content || {};
    const jsa = permit.jsa_result || p1.jsa_result || {};
    const personnel = firstValue(content.personnel, application.personnel, []);
    const personnelText = Array.isArray(personnel) ? personnel.map(person => {
        if (typeof person === 'string') return person;
        const role = firstValue(person.role, person.position, '');
        const qualification = displayValue(person.qualifications || person.qualification, '');
        return [person.name, role, person.badge_id, qualification].filter(Boolean).join(' / ');
    }).join('\n') : displayValue(personnel);
    const jobContent = firstValue(content.job_content, application.job_content);
    const jobType = firstValue(content.job_type, content.permit_type, application.job_type, application.permit_type,
        jobContent && jobContent.includes('：') ? jobContent.split('：')[0] : '');
    const hazards = normalizeHazards(jsa, content);
    const standaloneMeasures = firstValue(content.measures, jsa.measures, []);
    const hazardRows = hazards.map(hazard => `<tr><td>${escapeHtml(displayValue(hazard.step))}</td><td>${escapeHtml(displayValue(hazard.description))}</td><td>${escapeHtml(displayValue(hazard.consequence))}</td><td>${escapeHtml(displayValue(hazard.severity, '未分级'))}</td><td>${escapeHtml(displayValue(hazard.measures, '待补充'))}</td></tr>`).join('');
    const gasRecords = firstValue(content.gas_detection, application.gas_detection, permit.gas_detection, []);
    const gasRows = Array.isArray(gasRecords) ? gasRecords.map(record => `<tr><td>${escapeHtml(displayValue(record.time || record.detected_at))}</td><td>${escapeHtml(displayValue(record.location))}</td><td>${escapeHtml(displayValue(firstValue(record.oxygen, record.oxygen_percent)))}</td><td>${escapeHtml(displayValue(firstValue(record.lel, record.lel_percent)))}</td><td>${escapeHtml(displayValue(firstValue(record.toxic_gas, record.toxic)))}</td><td>${escapeHtml(displayValue(firstValue(record.result, record.qualified)))}</td></tr>`).join('') : '';
    const missing = [...new Set([...(p1.missing_fields || []), ...(content.missing_fields || []), ...(permit.missing_fields || [])])];
    const validationIssues = firstValue(content.validation_issues, permit.validation_issues, p1.validation_issues, []);
    const issueList = Array.isArray(validationIssues) ? validationIssues : [];
    const critical = [...new Set([...(p1.critical_missing_fields || []), ...(content.critical_missing_fields || []), ...issueList.filter(item => item && (item.severity === 'critical' || item.level === '严重')).map(item => item.field || item.message)])].filter(Boolean);
    const relatedPermits = firstValue(content.related_permits, application.related_permits, application.related_work_permits, []);
    const attachments = firstValue(content.attachments, application.attachments, []);

    let html = '';
    const templateRows = Array.isArray(content.permit_rows) ? content.permit_rows : [];
    if (templateRows.length) {
        // 手动填报生成的许可证：票面表格本身就是审批对象，直接按模板行渲染。
        html += renderPermitTable(content.permit_title || '作业许可证', templateRows);
    } else {
    html += '<div class="permit-approval-grid">';
    html += permitField('作业编号', firstValue(permit.permit_draft_id, p1.permit_draft_id, detail.job_id));
    html += permitField('作业类型', jobType);
    html += permitField('作业等级', firstValue(content.job_level, application.job_level));
    html += permitField('作业区域', firstValue(content.region, application.region));
    html += permitField('作业单位', firstValue(content.work_unit, application.work_unit, application.applicant_unit), 'wide');
    html += permitField('属地单位', firstValue(content.territorial_unit, application.territorial_unit), 'wide');
    html += permitField('作业地点/部位', firstValue(content.work_location, content.location, application.work_location, application.region), 'wide');
    html += permitField('设备/介质', [displayValue(firstValue(content.equipment, application.equipment), ''), displayValue(firstValue(content.medium, application.medium), '')].filter(Boolean).join(' / '), 'wide');
    html += permitField('作业内容', jobContent, 'full');
    html += permitField('作业人员/职责/资质', personnelText, 'full');
    html += permitField('计划时间', displayValue(firstValue(content.planned_start, application.planned_start)) + ' 至 ' + displayValue(firstValue(content.planned_end, application.planned_end)), 'wide');
    html += permitField('关联许可证', relatedPermits, 'wide');
    html += permitField('附件', attachments, 'full');
    html += '</div>';
    }

    html += '<div class="permit-section"><div class="permit-section-title">安全措施判定</div>';
    const measureDecisions = content.measure_decisions || {};
    const decidedItems = Array.isArray(measureDecisions.items) ? measureDecisions.items : [];
    const measureCatalog = Array.isArray(content.safety_measures_catalog) ? content.safety_measures_catalog : [];
    if (measureDecisions.source === 'ai') {
        const needCount = decidedItems.filter(item => item.needed === true).length;
        html += `<div class="permit-value">AI 已逐条判定 ${decidedItems.length} 条安全措施：<b>需要 ${needCount} 条（√）</b>、不涉及 ${decidedItems.length - needCount} 条（×）。请复核措施是否到位，并在「确认人」处签字。</div>`;
        if (measureCatalog.length) {
            const measureRows = measureCatalog.map(item => {
                const ai = item.ai_needed;
                const aiMark = ai === true ? '√' : (ai === false ? '×' : '—');
                const humanMark = item.selected === true ? '√' : (item.selected === false ? '×' : '—');
                return `<tr><td>${escapeHtml(displayValue(item.sequence))}</td><td>${escapeHtml(displayValue(item.description))}</td><td>${aiMark}</td><td>${humanMark}</td></tr>`;
            }).join('');
            html += `<table class="permit-table"><thead><tr><th>序号</th><th>安全措施</th><th>AI 判定</th><th>上传票原勾选</th></tr></thead><tbody>${measureRows}</tbody></table>`;
        }
    } else if (measureDecisions.source === 'unavailable') {
        html += `<div class="permit-warning">⚠️ AI 未完成安全措施判定，请人工逐条勾选后再批准。${measureDecisions.error ? '（原因：' + escapeHtml(displayValue(measureDecisions.error)) + '）' : ''}</div>`;
    } else if (measureDecisions.source === 'no-measures') {
        html += '<div class="permit-value">该类型的许可证没有可判定的安全措施清单（如临时用电的检查项是另一套表单），请按票面要求人工确认。</div>';
    } else {
        html += '<div class="permit-value">本次没有生成安全措施判定结果。</div>';
    }
    html += '</div>';

    html += '<div class="permit-section"><div class="permit-section-title">风险与削减措施</div>';
    if (hazardRows) html += `<table class="permit-table"><thead><tr><th>作业步骤</th><th>风险</th><th>可能后果</th><th>等级</th><th>对应措施</th></tr></thead><tbody>${hazardRows}</tbody></table>`;
    else html += `<div class="permit-value">${escapeHtml(displayValue(standaloneMeasures, '尚未生成 JSA 风险—措施对应关系'))}</div>`;
    html += '</div>';

    html += '<div class="permit-section"><div class="permit-section-title">气体检测</div>';
    html += gasRows ? `<table class="permit-table"><thead><tr><th>时间</th><th>位置</th><th>O₂</th><th>LEL</th><th>有毒气体</th><th>结果</th></tr></thead><tbody>${gasRows}</tbody></table>` : '<div class="permit-value">未提供气体检测记录；如本作业要求检测，需在开工前补充并复核时效。</div>';
    html += '</div>';

    if (missing.length || issueList.length) {
        const issues = issueList.map(item => displayMissingField(item)).filter(Boolean);
        const visibleMissing = [...new Set([...missing.map(displayMissingField), ...issues])];
        html += `<div class="permit-missing"><strong>待补充/校验问题：</strong>${escapeHtml(visibleMissing.join('、'))}</div>`;
    } else {
        html += '<div class="permit-ok">当前未发现已记录的缺失字段。现场条件仍需由审批人核对。</div>';
    }

    if (manageApprovalButton) {
        const approveButton = document.getElementById('hitl-approve-button');
        approveButton.disabled = critical.length > 0;
        approveButton.title = critical.length ? '存在关键缺失项：' + critical.map(displayMissingField).join('、') : '';
        // 只有手动填报的工单带模板票面，才有可下载的许可证。
        const downloadButton = document.getElementById('permit-download-button');
        if (downloadButton) downloadButton.style.display = templateRows.length ? '' : 'none';
    }
    return html;
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
    const infoContent = document.getElementById('modal-info-content');
    infoContent.textContent = pendingInfo.message || info.humanConfirm || '请确认';
    const modalCard = document.getElementById('hitl-modal-card');
    const approveButton = document.getElementById('hitl-approve-button');
    modalCard.classList.toggle('p1-approval-modal', baseStage === 'P1');
    approveButton.disabled = baseStage === 'P1';
    approveButton.title = '';
    document.getElementById('jsa-download-button').style.display = 'none';
    // 许可证下载只在 P1 的模板票面上出现，默认隐藏。
    document.getElementById('permit-download-button').style.display = 'none';

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

    const hitlModal = document.getElementById('hitl-modal');
    hitlModal.dataset.stage = stage;
    hitlModal.dataset.approvalId = pendingInfo.approval_id || '';
    const reviewRequest = String(Date.now()) + Math.random();
    hitlModal.dataset.reviewRequest = reviewRequest;
    hitlModal.classList.add('active');

    if (baseStage === 'P1') {
        // 与 confirmDecision 一致：只读查看历史作业时，审批窗口仍属于当前作业。
        const liveState = state.historyViewJobId
            ? (state.liveWorkflowState || state.workflowState)
            : state.workflowState;
        const jobId = liveState.thread_id || liveState.jobId || liveState.threadId;
        infoContent.textContent = '正在加载审批内容...';
        fetch('/api/workflow/job-detail?job_id=' + encodeURIComponent(jobId))
            .then(r => r.json())
            .then(detail => {
                if (hitlModal.dataset.reviewRequest !== reviewRequest || hitlModal.dataset.stage !== stage || !hitlModal.classList.contains('active')) return;
                if (detail.error) throw new Error(detail.error || '读取作业票失败');
                infoContent.innerHTML = renderP1Review(detail);
            })
            .catch(err => {
                if (hitlModal.dataset.reviewRequest !== reviewRequest || !hitlModal.classList.contains('active')) return;
                infoContent.innerHTML = '<div class="permit-missing">审批内容加载失败：' + escapeHtml(err.message) + '</div>';
                approveButton.disabled = true;
            });
    }
}

function closeModal() {
    const hitlModal = document.getElementById('hitl-modal');
    hitlModal.classList.remove('active');
    delete hitlModal.dataset.stage;
    delete hitlModal.dataset.approvalId;
    delete hitlModal.dataset.reviewRequest;
}

function resyncWorkflowState(jobId, sourceLabel, suppressStage) {
    // 审批请求的结果不可用（已被其他页面处理、工单已有后台任务、服务端报错或
    // 请求本身失败）时，必须向服务端重新取一次真实状态再决定窗口去留。
    // 不能沿用本地旧状态，否则会把已经过期的审批窗口重新弹出来。
    // suppressStage：本次刚失败的那个阶段不再弹回，避免窗口关不掉。
    if (!jobId) return;
    fetch('/api/workflow/state?thread_id=' + encodeURIComponent(jobId))
        .then(r => r.json())
        .then(data => {
            if (!data) return;
            if (state.historyViewJobId) {
                state.liveWorkflowState = data;
            } else {
                state.workflowState = data;
                renderWorkflowDiagram();
                updateControlPanel();
            }
            rememberActiveWorkflow(data);
            reconcilePendingConfirmation(data, sourceLabel || '状态同步', suppressStage);
        })
        .catch(err => {
            console.warn('重新获取工作流状态失败:', err);
        });
}

function confirmDecision(decision) {
    console.log('confirmDecision called:', decision);
    console.log('state.workflowState:', JSON.stringify(state.workflowState));
    // 只读查看历史作业时，state.workflowState 是历史作业的数据，但屏幕上的审批
    // 窗口属于当前作业；必须用当前作业的状态来取 jobId 和阶段，否则审批会发到
    // 另一张工单上，被服务端判为“该阶段未被等待”而无法生效。
    const confirmState = state.historyViewJobId
        ? (state.liveWorkflowState || state.workflowState)
        : state.workflowState;
    const jobId = confirmState.thread_id || confirmState.jobId || confirmState.threadId;
    console.log('jobId:', jobId);
    if (!jobId) {
        addLog('❌ 错误: 没有进行中的工作流，请先启动工作流', 'error');
        return;
    }

    // 本页只读（工单被另一个页面占用）时直接拒绝，并说明原因。
    if (blockIfReadOnly(jobId)) return;

    const pending = confirmState.pending || [];
    const stage = pending[0] || '';
    const hitlModal = document.getElementById('hitl-modal');
    const modalStage = hitlModal.dataset.stage || '';
    console.log('stage:', stage);
    if (!stage || (modalStage && modalStage !== stage)) {
        closeModal();
        addLog(`ℹ️ ${modalStage || '当前'}审批已失效，可能已由其他页面处理`, 'warning');
        // 本地状态可能已经过期，改成向服务端取一次真实状态再决定窗口去留：
        // 服务端仍在等待本阶段就重新弹出，已经继续/完成/失败就保持关闭。
        resyncWorkflowState(jobId, '审批失效后状态同步');
        return;
    }

    const approvalId = hitlModal.dataset.approvalId || '';
    addLog(`${decision === 'approve' ? '✅ 批准' : '⛔ 否决'} ${stage}`);
    addLog(`📤 发送确认请求: thread_id=${jobId}, stage=${stage}, decision=${decision}`);

    // 请求发出前先关闭当前阶段窗口。后端可能在 HTTP 响应返回前就通过
    // WebSocket 推送下一阶段 waiting，不能让旧窗口挡住或随后关闭新窗口。
    closeModal();

    fetch('/api/workflow/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            thread_id: jobId,
            stage: stage,
            decision: decision,
            approval_id: approvalId,
            holder_id: getHolderId(),
            async_execute: true  // 异步执行，点击确认后立即返回
        })
    }).then(r => {
        addLog(`📥 收到响应状态: ${r.status}`);
        return r.json();
    }).then(data => {
        addLog(`📋 响应数据: ${JSON.stringify(data)}`);

        if (data.status === 'occupied' || data.status === 'lease_required') {
            // 工单被另一个页面占用（服务端兜底，正常情况下已被 blockIfReadOnly 拦下）。
            setLeaseReadOnly(data.occupant, data.error);
            return;
        }
        if (data.rejected) {
            addLog(`⛔ ${stage} 已否决，工作流停止；点击“启动”可从 ${stage} 重新执行`, 'warning');
        } else if (data.duplicate === true || data.status === 'already_processed' || data.status === 'already_executing') {
            // 并发保护生效的结果，不是失败：该阶段已被本页或另一个页面处理过，
            // 或者该工单已经有后台执行任务在跑。向操作员说明原因，并关闭窗口；
            // 关闭后不再把这个阶段弹回来（它已经不处于可审批状态），
            // 后续状态由 WebSocket 推送和轮询继续同步。
            const why = data.status === 'already_executing'
                ? '该工单已有后台执行任务在执行'
                : '该阶段已由其他页面或上一次请求处理';
            addLog(`ℹ️ ${stage} 本次审批未生效：${why}，本页审批窗口已关闭`, 'warning');
            if (data.message) addLog(`ℹ️ ${data.message}`);
            closeModal();
            resyncWorkflowState(jobId, '审批未生效', stage === 'P1' ? '' : stage);
        } else if (data.status === 'error') {
            addLog(`❌ ${stage} 审批失败：${data.error || '服务端返回错误'}`, 'error');
            closeModal();
            resyncWorkflowState(jobId, '审批失败');
        } else if (data.status === 'executing') {
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
        // 请求结果未知，向服务端重新取状态，而不是用本地旧状态把窗口重新弹出来。
        resyncWorkflowState(jobId, '请求失败后状态同步');
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
