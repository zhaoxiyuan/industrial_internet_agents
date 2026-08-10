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
        name: '作业过程动态监测', icon: '📡', color: '#E91E63', humanConfirm: null,
        tools: ['monitor_start', 'monitor_stop', 'monitor_status', 'monitor_events'],
        activity: '持续获取视频、传感器、定位和作业状态，识别违章及条件变化',
        inputs: '视频流、传感器、定位、规则',
        outputs: '候选风险事件、证据片段',
        intelligence: '根据作业类型和上下文动态选择CV/VL模型、提示词和采样策略；对连续帧结果进行时序聚合，避免单帧误报。'
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
}

// ========== 配置相关 ==========
function loadModelConfig() {
    fetch('/api/config')
        .then(r => {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(data => {
            console.log('[loadModelConfig] Received data:', {api_key: data.api_key ? '***' : '', base_url: data.base_url, model: data.model});
            // API Key 用 * 号展示
            const apiKey = data.api_key || '';
            document.getElementById('config-api-key').value = maskString(apiKey);
            document.getElementById('config-api-key').dataset.rawValue = apiKey;
            document.getElementById('config-base-url').value = data.base_url || '';
            document.getElementById('config-model').value = data.model || '';
            console.log('[loadModelConfig] Fields populated, apiKey masked:', maskString(apiKey));
        })
        .catch(err => {
            console.error('[loadModelConfig] Error:', err);
        });
}

function maskString(str) {
    if (!str) return '';
    if (str.length <= 4) return '****';
    return str.substring(0, 4) + '*'.repeat(Math.min(str.length - 4, 20));
}

function toggleApiKeyVisibility() {
    const input = document.getElementById('config-api-key');
    const icon = document.getElementById('toggle-api-key');
    if (input.type === 'password') {
        // 切换到显示模式：先更新 raw-value 为当前值，再显示
        input.dataset.rawValue = input.value;
        input.type = 'text';
        icon.textContent = '🙈';
    } else {
        // 切换到隐藏模式：先把 raw-value 更新为当前显示的值，再隐藏
        input.dataset.rawValue = input.value;
        input.value = maskString(input.value);
        input.type = 'password';
        icon.textContent = '👁️';
    }
}

function saveModelConfig() {
    // 保存时使用原始值（如果当前是显示状态，需要从 input.value 获取；如果隐藏状态从 dataset.rawValue 获取）
    const apiKeyInput = document.getElementById('config-api-key');
    const apiKey = apiKeyInput.type === 'password' ? apiKeyInput.dataset.rawValue : apiKeyInput.value;

    const data = {
        api_key: apiKey,
        base_url: document.getElementById('config-base-url').value,
        model: document.getElementById('config-model').value
    };
    fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    }).then(() => {
        document.getElementById('config-status').textContent = '✅ 已保存';
        setTimeout(() => {
            document.getElementById('config-status').textContent = '';
        }, 2000);
    });
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
                    state.workflowState = data;
                    renderWorkflowDiagram();
                    updateControlPanel();

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
    const { confirmed, pending, currentStage, status } = state.workflowState;
    const panel = document.getElementById('control-panel');

    if (status === 'idle') {
        panel.innerHTML = '<span style="color: #999;">就绪</span>';
    } else if (pending.length > 0) {
        panel.innerHTML = `<span style="color: #FFC107;">⏸️ 等待人工确认: ${pending.join(', ')}</span>`;
    } else if (currentStage) {
        panel.innerHTML = `<span style="color: #FF9800;">⏳ 执行中: ${currentStage}</span>`;
    }
    panel.innerHTML += `<div style="margin-top: 10px;">已完成: ${confirmed.length}/${ALL_STAGES.length}</div>`;
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

    // 如果有附加数据，显示数据摘要
    if (data && Object.keys(data).length > 0) {
        const dataSummary = formatDataSummary(data);
        if (dataSummary) {
            const dataDiv = document.createElement('div');
            dataDiv.style.marginLeft = '20px';
            dataDiv.style.color = '#888';
            dataDiv.style.fontSize = '11px';
            dataDiv.textContent = dataSummary;
            entry.appendChild(dataDiv);
        }
    }

    container.appendChild(entry);
    container.scrollTop = container.scrollHeight;

    // 限制日志数量，防止内存溢出
    while (container.children.length > 500) {
        container.removeChild(container.firstChild);
    }
}

// 格式化数据显示摘要
function formatDataSummary(data) {
    if (!data || typeof data !== 'object') return '';

    const entries = Object.entries(data);
    if (entries.length === 0) return '';

    const parts = [];
    for (const [key, value] of entries.slice(0, 5)) { // 最多显示5个字段
        if (value === null || value === undefined) continue;

        let valStr = '';
        if (typeof value === 'string') {
            valStr = value.length > 50 ? value.substring(0, 50) + '...' : value;
        } else if (typeof value === 'object') {
            valStr = JSON.stringify(value).length > 50 ? JSON.stringify(value).substring(0, 50) + '...' : JSON.stringify(value);
        } else {
            valStr = String(value);
        }

        if (valStr) {
            parts.push(`${key}: ${valStr}`);
        }
    }

    return parts.join(' | ');
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
    const rect = event.target.closest('.workflow-s-card, .workflow-s-card-main').getBoundingClientRect();
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
