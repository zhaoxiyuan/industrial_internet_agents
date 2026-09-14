# 文档索引参考

## 文档目录结构

```
docs/
├── architecture.md              # 技术架构文档
├── index.md                     # 执行链路和交互流程
├── system_prompt.md             # System Prompt 说明文档
├── websocket.md                 # WebSocket 协议文档
├── SDD.md                       # 软件设计说明书
├── task.md                      # P1-P10 阶段定义
├── LangSmith-Integration-SDD.md # LangSmith 观测平台集成说明
└── agents/                      # Agent 专项文档
    ├── MAIN_AGENT.md
    ├── P1_PERMIT_AGENT.md
    ├── P6_MONITOR_AGENT.md
    ├── P7_RISK_AGENT.md
    └── P8_DISPOSITION_AGENT.md
```

---

## 关键文档说明

### architecture.md
系统整体技术架构，包括：
- 技术栈（LangChain 1.x, LangGraph, Gradio 6.x, MiniMax API）
- Agent 实现模式
- 工具定义模式
- Gradio ChatInterface 适配
- 目录结构说明

### index.md
执行链路和交互流程，包括：
- User → Gradio → create_agent → LLM → ToolNode → 知识库
- 各阶段的输入输出关系

### system_prompt.md
System Prompt 管理机制，包括：
- prompt 加载方式
- 各 Agent 的 prompt 特点
- prompt 优化建议

### websocket.md
WebSocket 通信协议，包括：
- 端口分配（8081 状态、8082 日志）
- 消息格式
- 连接管理

### SDD.md
软件设计说明书，包括：
- 功能需求
- 非功能需求
- 接口设计
- 数据结构

### task.md
P1-P10 各阶段详细定义，包括：
- 阶段输入
- 阶段输出
- 关键逻辑
- 验收标准

---

## 子系统文档

### A5 实时监控
```
A5/
├── agent/
│   ├── a5_agent.py              # A5 监控 Agent
│   ├── work_permit_rules.py     # 作业许可规则
│   └── system_prompt.py
├── scenario_data/               # 模拟场景 A-E
└── stream_players/              # 数据采集器
```

### A6 风险评估
```
A6/
├── agent/
│   ├── a6_agent.py              # A6 风险评估 Agent
│   ├── tools.py                 # A5DataTools + OutputTools
│   └── prompt_manager.py
└── logs/assessments/            # 评估输出
```

### A7 长期记忆
```
A7/
├── storage/
│   └── p8_long_term.py          # P8 长期记忆（双层 JSON）
├── notify/                      # 飞书通知
└── schema/                      # 数据 Schema
```

---

## 文档更新规则

根据 `CLAUDE.md`：

**代码变更后，必须检查并同步更新 `docs/` 目录下的相关文档：**
- `docs/architecture.md` - 系统架构和技术说明
- `docs/agents/*.md` - 各 Agent 的说明文档
- `docs/index.md` - 执行链路和交互流程

**规则：文档说明必须与代码逻辑保持一致。代码变更后：**
1. 检查是否影响架构、接口、执行流程
2. 如有影响，立即更新相关文档
3. 不要留下"文档待更新"之类的 TODO
