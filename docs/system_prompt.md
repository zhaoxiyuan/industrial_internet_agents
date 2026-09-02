# System Prompt 加载与保存

## 1. 目录结构

```
agents/
├── system_prompt/                    # 提示词文件目录
│   ├── MAIN_AGENT_SYSTEM_PROMPT.md   # 主调度中心
│   ├── P1_PERMIT_SYSTEM_PROMPT.md    # P1 作业预约
│   ├── P2_TASK_SYSTEM_PROMPT.md      # P2 作业任务
│   ├── P3_CONTEXT_SYSTEM_PROMPT.md   # P3 上下文理解
│   ├── P4_BINDING_SYSTEM_PROMPT.md   # P4 监测资源绑定
│   ├── P5_VERIFY_SYSTEM_PROMPT.md    # P5 条件核验
│   ├── P6_MONITOR_SYSTEM_PROMPT.md   # P6 动态监测
│   ├── P7_RISK_SYSTEM_PROMPT.md      # P7 风险研判
│   ├── P8_DISPOSITION_SYSTEM_PROMPT.md # P8 人机协同
│   ├── P9_CLOSURE_SYSTEM_PROMPT.md   # P9 闭环跟踪
│   └── P10_ARCHIVE_SYSTEM_PROMPT.md  # P10 归档复盘
└── utils/
    └── system_prompt.py              # 提示词加载/保存模块
```

## 2. 核心模块

**文件**: `agents/utils/system_prompt.py`

```python
# 阶段 → 文件名映射
STAGE_TO_FILE = {
    "MAIN": "MAIN_AGENT_SYSTEM_PROMPT.md",
    "P1": "P1_PERMIT_SYSTEM_PROMPT.md",
    # ... P2-P10
}

def load_system_prompt(stage: str) -> str:
    """从文件加载提示词"""
    filepath = os.path.join(SYSTEM_PROMPT_DIR, filename)
    return open(filepath).read() if exists else ""

def save_system_prompt(stage: str, content: str) -> bool:
    """保存提示词到文件"""
    filepath = os.path.join(SYSTEM_PROMPT_DIR, filename)
    with open(filepath, "w") as f:
        f.write(content)
```

## 3. 完整链路图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              前端 (web/)                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  index.html                                                                  │
│  ├── 配置页面 (⚙️ 配置 标签)                                                  │
│  │   └── 左侧菜单 (📝 Agent 提示词)                                          │
│  │       ├── 🎛️ 主调度 (MAIN)                                               │
│  │       ├── P1 作业预约                                                     │
│  │       ├── P2 作业任务                                                     │
│  │       └── ... P3-P10                                                     │
│  │                                                                            │
│  │       点击菜单项 → 显示编辑面板 (Markdown编辑器 + 实时预览 + 💾保存按钮)   │
│  │                                                                            │
│  └── app.js                                                                  │
│      ├── loadAllPrompts()     → GET /api/prompt/{stage}                     │
│      ├── saveAgentPrompt()    → POST /api/prompt/{stage}                    │
│      └── updatePreview()      实时渲染 Markdown 预览                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼ HTTP REST API
┌─────────────────────────────────────────────────────────────────────────────┐
│                           后端 (web/server.py)                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  GET  /api/prompt/{stage}   → load_system_prompt(stage)                     │
│  POST /api/prompt/{stage}   → save_system_prompt(stage, content)            │
│                                                                             │
│  from agents.utils.system_prompt import load_system_prompt, save_system_prompt│
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        核心模块 (agents/utils/system_prompt.py)              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  SYSTEM_PROMPT_DIR = "agents/system_prompt/"                                │
│                                                                             │
│  load_system_prompt(stage) ──────► 读取 .md 文件                             │
│         │                                                                    │
│         └── STAGE_TO_FILE[stage] ──► 对应文件名                              │
│                                                                             │
│  save_system_prompt(stage, content) ──► 写入 .md 文件                        │
│         │                                                                    │
│         └── STAGE_TO_FILE[stage] ──► 对应文件名                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         文件系统 (agents/system_prompt/*.md)                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼ Agent 运行时使用
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Agent 创建时 (agents/)                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  p1_permit_agent.py:                                                         │
│    create_agent(..., system_prompt=load_system_prompt("P1"))                │
│                                                                             │
│  main_agent.py:                                                             │
│    create_agent(..., system_prompt=load_system_prompt("MAIN"))              │
│                                                                             │
│  其他 P2-P10 Agent 同理                                                      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 4. 前端入口到后端保存流程

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              用户操作                                        │
│                                                                             │
│  1. 打开页面 → 点击「⚙️ 配置」标签                                            │
│  2. 点击左侧菜单「📝 P1 作业预约」                                            │
│  3. 右侧显示 Markdown 编辑器，预加载当前内容                                  │
│  4. 编辑内容                                                                  │
│  5. 点击「💾 保存」按钮                                                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  app.js:261-273                                                             │
│                                                                             │
│  function saveAgentPrompt(stage) {                                          │
│      const content = document.getElementById('prompt-' + stage).value;      │
│      fetch('/api/prompt/' + stage, {                                        │
│          method: 'POST',                                                    │
│          headers: { 'Content-Type': 'application/json' },                  │
│          body: JSON.stringify({ content })                                  │
│      });                                                                    │
│  }                                                                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼ HTTP POST /api/prompt/P1
                                      body: {"content": "..."}
┌─────────────────────────────────────────────────────────────────────────────┐
│  server.py: do_POST()                                                       │
│                                                                             │
│  elif path.startswith("/api/prompt/"):                                      │
│      stage = path.split("/")[-1]        # "P1"                              │
│      body = json.loads(...)                                                  │
│      save_system_prompt(stage, body.get("content"))                         │
│      self.send_json({"status": "ok"})                                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  system_prompt.py: save_system_prompt()                                     │
│                                                                             │
│  filename = STAGE_TO_FILE["P1"]          # "P1_PERMIT_SYSTEM_PROMPT.md"     │
│  filepath = SYSTEM_PROMPT_DIR + filename                                    │
│                                                                             │
│  with open(filepath, "w", encoding="utf-8") as f:                          │
│      f.write(content)                                                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  agents/system_prompt/P1_PERMIT_SYSTEM_PROMPT.md  (已更新)                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 5. 加载时机

| 场景 | 调用方式 |
|------|----------|
| 页面初始化 | `app.js: loadAllPrompts()` → GET `/api/prompt/{stage}` |
| Agent 创建 | `load_system_prompt("P1")` 传入 `create_agent(system_prompt=...)` |
| 切换编辑面板 | `app.js: updatePreview()` 实时预览 Markdown |

## 6. 关键文件汇总

| 文件路径 | 职责 |
|---------|------|
| `web/index.html` | 页面结构：菜单、编辑器、预览、按钮 |
| `web/app.js` | 前端逻辑：加载、保存、预览 |
| `web/server.py` | REST API 端点 |
| `agents/utils/system_prompt.py` | 核心模块：加载/保存函数、文件名映射 |
| `agents/system_prompt/*.md` | 提示词文件（11个） |
| `agents/p1_permit_agent.py` | 使用 `load_system_prompt("P1")` |
| `agents/main_agent.py` | 使用 `load_system_prompt("MAIN")` |

## 7. REST API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/prompt/{stage}` | 获取指定阶段的提示词内容 |
| POST | `/api/prompt/{stage}` | 保存指定阶段的提示词，请求体 `{"content": "..."}` |

**stage 取值**: `MAIN`, `P1`, `P2`, `P3`, `P4`, `P5`, `P6`, `P7`, `P8`, `P9`, `P10`