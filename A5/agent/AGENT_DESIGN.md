# A5 Agent 设计说明 — LangChain ReAct Agent

> **定位**:用 LangChain ReAct Agent 替代硬编码流水线
> **文件**:`agent/a5_agent.py`(待重写)
> **状态**:设计阶段,待实现

---

## 一、当前 vs 目标

| 维度 | 当前(流水线) | 目标(Agent) |
|---|---|---|
| 决策方式 | `tick()` 硬编码 if-else | LLM 自主推理:思考→行动→观察 |
| 工具调用 | 手动 `tool.invoke()` | Agent 自主选择调哪个工具 |
| 去重逻辑 | `EventDeduplicator` 状态机 | Agent 记忆 + 工具查询历史 |
| 最终判断 | 硬编码 `_is_real_violation()` | LLM 基于多轮推理输出 |
| 可解释性 | 固定模板 | 自然语言解释 |

---

## 二、Agent 身份(System Prompt)

```text
你是 A5 作业过程监测智能体,部署在广东石化炼化厂的边缘服务器上。
你的职责是监测动火作业现场的实时数据,识别候选安全事件。

你有以下能力:
- 查询任意时间段的原始日志(CV/传感器/定位)
- 获取当前秒的世界快照
- 对指定人员的 PPE 合规性做多数表决
- 咨询 VL 视觉专家获取语义判断

重要约束:
1. ★ 你只能查询当前时间点及之前的日志,不能看未来。
   代码级硬约束(§3.1),同时 Prompt 中 wall_time 用显著标记包围(§12.1)。
   两层防护:Prompt 层醒目提醒 + 代码层拒绝越界查询。
2. 你只输出"候选事件",不判定风险等级(由 A6 完成)
3. 你只输出"候选事件",不发起处置动作(由 A7 完成)
4. 同一违规事件只报告一次,不要重复报告
5. 输出前要给出自然语言解释:依据什么证据得出什么结论

当前作业票信息:
- 票号:{permit_id}
- 作业类型:{work_type}
- 作业人员:{workers}
- 必戴 PPE:{required_ppe}
- 监护人要求:{supervisor_required}
```

---

## 三、工具列表(5 个)

| 工具 | 作用 | 输入 |
|---|---|---|
| `get_current_snapshot` | 获取某时间点的世界快照 | wall_time(ISO) |
| `query_raw_logs` | 按时间范围查历史日志 | start_wall, end_wall, source_type, person_id |
| `analyze_ppe_compliance` | 1 秒内多数表决 | cv_logs, person_id, threshold, required_ppe |
| `call_vl_expert` | 咨询 VL 专家 | question, history, context |
| `query_past_events` | **查询已落盘的 raw_event**(新增) | start_wall, end_wall, person_id(可选) |

### 3.0 时序聚合:让 LLM 看到"趋势"而非"单点"

**问题**:当前 snapshot 只描述"这一秒",LLM 看到的是:

```
T=8: P7 头盔缺失(88%)
T=9: P7 头盔缺失(88%)
T=10: P7 头盔缺失(88%)
```

每秒单独看都是"违规",但 LLM **不知道这 3 秒是同一个事件还是 3 个独立事件**。

**对策:双层设计**

#### 第一层:Prompt 注入"最近 N 秒趋势摘要"

在 `_build_input` 中注入最近 5 秒的每人员摘要(见 §12.1 更新后的代码):

```text
【最近 5 秒违规趋势】
  数据来源:CV 多数表决的历史记录
  P7(张师傅,焊工):
    T+4 : 正常
    T+5 : 正常
    T+6 : 正常
    T+7 : 正常
    T+8 : 头盔缺失(88%) ← 当前秒 ★ 状态变化
    → 趋势:从 T+8 开始摘头盔,此前一直正常。这是新事件,不是延续。
```

LLM 看到"T+8 之前一直正常"就知道这是**新事件**;看到"T+4~T+8 一直缺失"就知道是**延续**。

#### 第二层:`query_timeline` 工具(更深查询)

```text
@tool
def query_timeline(
    person_id: str,
    lookback_seconds: int = 10,
) -> Dict[str, Any]:
    """
    查询某人在最近 N 秒内的违规趋势。
    返回趋势数组 + 自然语言摘要,供 Agent 判断"新事件或延续"。
    """
```

**两层配合**:

| 场景 | Prompt 注入(第一层) | 工具查询(第二层) |
|---|---|---|
| Agent 看一眼趋势 | ✅ 最近 5 秒趋势已注入 prompt | 不需要调工具 |
| Agent 想确认"他是从哪秒开始的" | — | 调 `query_timeline("P7", 15)` |
| Agent 想跨多人比较 | — | 调 `query_timeline` 多次 |

---

### 3.1 query_past_events 工具设计

```text
# agent/tools.py (新增)

@tool
def query_past_events(
    start_wall: str,
    end_wall: str,
    person_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    查询此前已输出过的 raw_event,用于去重判断。

    从 raw_event_dir 目录下读取 JSON 文件,按时间范围和人员过滤。

    Args:
        start_wall: 起始时间(ISO)
        end_wall:   结束时间(ISO)
        person_id:  人员过滤(可选)

    Returns:
        [
          {"event_id": "A5-P7-...", "type": "PPE缺失-头盔",
           "person": {"id": "P7", ...}, "wall_time": "..."},
          ...
        ]

    实现要点:
    - 遍历 raw_event_dir 下所有 raw_event_*.json
    - 按 wall_time + person_id 过滤
    - raw_event_dir 通过 set_dependencies() 注入
    """
```

**Agent 使用这个工具的去重逻辑**:

```
T=8: Agent 发现 P7 摘头盔
  → query_past_events("14:32:00", "14:32:12", "P7") → []
  → 无人报过 → 输出 raw_event

T=9: Agent 发现 P7 还在摘
  → query_past_events("14:32:00", "14:32:13", "P7") → [{"type":"PPE缺失-头盔"}]
  → 已报过 → 不重复输出
```

---

### 3.1 防未来:代码级硬约束

**Agent 只能看 `wall_time` 及之前的数据。** 这不是 Prompt 层的建议,而是 `AsyncCollector` 层的硬编码检查。

#### 实现位置

`stream_players/async_collector.py`:

```text
class AsyncCollector:
    def __init__(self, ...):
        self._agent_now = None    # Agent 当前可观测的时间上限

    def set_agent_now(self, wall_time: str):
        """主循环每 tick 调用,设置时间上限"""
        self._agent_now = wall_time

    def query_raw_logs(self, start_wall, end_wall, ...):
        # ★ 硬约束:拒绝任何超过 agent_now 的查询
        if self._agent_now and end_wall > self._agent_now:
            return [{"error": f"end_wall({end_wall}) > agent_now({self._agent_now})"}]
        ...

    def get_snapshot(self, wall_time):
        # ★ 硬约束:自动截断到 agent_now
        if self._agent_now and wall_time > self._agent_now:
            wall_time = self._agent_now
        ...
```

#### 调用链

```
主循环(T=N)                          Agent
---------------------------------- ----------------------------------------
collector.set_agent_now("14:32:12")  ->  _agent_now = "14:32:12"

                                     Agent 思考: "查一下 14:32:13 的数据"
                                       |
                                     query_raw_logs(end="14:32:13")
                                       |
                                     end_wall > agent_now?
                                       |  YES
                                     -> 返回 [{"error": "..."}]
                                        (Agent 拿不到未来数据)
```

#### 即使 LLM 试图越界也没用

| 攻击方式 | 结果 |
|---|---|
| Agent Prompt 里写"只能看当前" | ⚠️ LLM 可能不遵守(prompt 注入) |
| `query_raw_logs` 代码检查 `end_wall > _agent_now` | ✅ 硬拒绝,返回 error |
| `get_snapshot` 代码截断 `wall_time` 到 `_agent_now` | ✅ 硬截断,永不越界 |

**防未来是代码级保证,不依赖 LLM 的"自觉"。**

---

### 3.2 无状态设计:用磁盘替代内存记忆

**Agent 不维护跨 tick 的内存状态。** 原因:

1. **并行执行**:主循环可能同时跑多个场景(如 `ABCDE`),每个场景一个 Agent 实例。内存状态无法跨实例共享。
2. **可恢复性**:Agent 崩溃重启后,内存状态丢失;磁盘文件永久存在。
3. **可调试**:直接看 `raw_events/` 目录下的 JSON 文件即可复盘。

**对策**:

| 传统做法(内存) | 新做法(磁盘) |
|---|---|
| `self._reported_episodes = []` | 每个 tick 输出写入 `raw_events/raw_event_<ts>.json` |
| tick 开始时查内存"已报过没" | Agent 调 `query_past_events` 工具,从磁盘读取 |
| 状态丢失 = 重复报告 | 磁盘文件永久,不会重复 |
| 两个 Agent 实例互相看不到 | 两个 Agent 共用同一个 `raw_event_dir`,都能读到对方的输出 |

**并行场景示例**:

```
主循环同时启动场景 B 和场景 C:
  B-Agent(raw_event_dir="logs/run_B/raw_events")
  C-Agent(raw_event_dir="logs/run_C/raw_events")
  
B-Agent.tick(T=8): 发现 P7 摘头盔
  → 调 query_past_events("14:32:00", "14:32:08", "P7")
  → 返回 [] (无历史)
  → 输出 raw_event → 写磁盘

B-Agent.tick(T=9): 发现 P7 还在摘
  → 调 query_past_events("14:32:00", "14:32:09", "P7")  
  → 返回 [{"type":"PPE缺失-头盔","person":"P7"}]
  → Agent 判断:"已报告过,不重复"
  → 不输出

C-Agent 完全不受影响(读自己的目录)
```

---

## 四、上下文(Context)

每 1 秒主循环传给 Agent 的上下文:

```text
# 主循环每 tick 传入的上下文(无状态,纯注入)
context = {
    "wall_time":       "2026-08-04T14:32:12.000",  # 当前现实时间(ISO)
    "snapshot":        { ... },                    # 当前秒的世界快照
    "work_permit":     { ... },                    # 作业票(静态)
    "raw_event_dir":   "logs/run_B/raw_events",    # 落盘目录(供 query_past_events 读取)
}

# ★ 没有 self._reported_episodes、没有 ConversationBufferMemory ★
# Agent 的状态全部在磁盘上,通过 query_past_events 工具按需查询
```

---

## 五、tick 函数(Agent 入口)

```text
async def tick(self, wall_time: str, snapshot: Dict) -> List[Dict]:
    """
    每 1 秒被主循环调用一次。
    
    输入:
      wall_time: 当前现实时间(ISO),Agent 只能看 <= 这个时间的数据
      snapshot:  当前秒的世界快照
    
    返回:
      0~N 个 raw_event
    """
    
    # 1. 设置防未来边界
    self.collector.set_agent_now(wall_time)
    
    # 2. 组装本轮 prompt(上下文注入)
    prompt = self._build_prompt(wall_time, snapshot)
    
    # 3. 调用 LangChain Agent(ReAct 循环)
    result = await self._agent_executor.ainvoke({
        "input": prompt,
        "wall_time": wall_time,
        "snapshot": snapshot,
    })
    
    # 4. 解析 Agent 输出,提取 raw_event
    raw_events = self._parse_output(result["output"])
    
    # 5. 更新记忆(记下已报告的事件)
    self._update_memory(raw_events, wall_time)
    
    # 6. 落盘
    if raw_events:
        self._save_raw_events(raw_events, wall_time)
    
    return raw_events
```

---

## 六、Agent 的 ReAct 循环(思维链示例)

```
【T=8s,P7 摘头盔】

→ Agent 思考:
  "当前快照显示 P7 在第 8 秒的 25 帧中有 22 帧未佩戴安全帽,
   让我查一下历史:他之前有没有摘过?"

→ 调工具:query_raw_logs(start="14:32:00", end="14:32:08", "cv", "P7")
  观察:P7 在 0-7 秒全程佩戴,这是第一次摘

→ Agent 思考:
  "这是新事件。让我确认:他在动火区吗?传感器有没有告警?"

→ 调工具:get_current_snapshot("14:32:08.000")
  观察:P7 在动火区-A,可燃气体 0.2%LEL(正常)

→ Agent 思考:
  "第一次摘 + 在危险区 → 应该报告。不过先确认一下是否真的违规"
  
→ 调工具:call_vl_expert(question="P7 当前 PPE 状态?", history={...}, context={...})
  观察:VL 确认"未佩戴安全帽,违规"

→ Agent 最终输出:
  "基于以下证据:
   1) CV 多数表决:22/25 帧未戴头盔(88%)
   2) VL 确认:未佩戴安全帽属于 PPE 缺失
   3) 定位:在动火区-A 危险区内
   4) 作业票:要求佩戴 helmet
   综合判定:输出候选事件。
   事件类型:PPE缺失-头盔
   涉及人员:P7 张师傅(焊工)
   发生时间:T=8s"
```

---

## 七、依赖关系

```
a5_agent.py (待重写)
    |
    +-- agent/tools.py                 (4 个 @tool)
    +-- agent/event_deduplicator.py    (降级模式备用,或删除)
    +-- scenario_data/mock_work_permit.py  (作业票)
    +-- scenario_data/mock_vl.py        (Mock VL, 占位)
    +-- stream_players/async_collector.py  (query_raw_logs / get_snapshot)
    |
    +-- langchain.agents.create_react_agent    (LangChain ReAct 编排)
    +-- langchain_core.prompts.PromptTemplate
    +-- langchain_core.language_models.BaseChatModel
```

---

## 八、Prompt 模板

```text
REACT_PROMPT = PromptTemplate.from_template("""
你是 A5 作业过程监测智能体。

{system_prompt}

你可以使用以下工具:
{tools}

工具名称: {tool_names}

当前时间: {wall_time}
当前快照摘要:
{cv_summary}
{sensor_summary}
{position_summary}

使用以下格式:
Question: 输入的监测任务
Thought: 我应该做什么
Action: 工具名称
Action Input: 工具的输入参数
Observation: 工具返回的结果
... (可重复 Thought/Action/Observation 多轮)
Thought: 我已经有足够信息做出判断
Final Answer: 最终结论(JSON 格式,包含 is_event, type, person, evidence, explanation)

开始!

Question: 根据当前快照和历史数据,是否存在需要报告的候选安全事件?
""")
```

---

## 九、输出格式(Final Answer JSON)

```json
{
  "is_event": true,
  "type": "PPE缺失-头盔",
  "person": {"id": "P7", "name": "张师傅", "role": "焊工"},
  "second": 8,
  "wall_time": "2026-08-04T14:32:08.000",
  "evidence": {
    "cv_ratio": 0.88,
    "vl_confirms": true,
    "in_danger_zone": true,
    "sensor_alarm": false
  },
  "supporting_sources": ["video", "vl_semantic", "location", "work_permit"],
  "explanation": "CV 22/25帧未戴头盔(88%),VL确认违规,在动火区危险区内,作业票要求佩戴头盔。综合判定输出候选事件。"
}
```

若无事件:
```json
{
  "is_event": false,
  "explanation": "当前快照未检测到违规,所有 PPE 正常,传感器无告警。"
}
```

---

## 十、已确认的设计决策

| # | 问题 | 决策 | 理由 |
|---|---|---|---|
| 1 | LLM 选型 | **由 .env 配置**(LLM_PROVIDER/OPENAI_API_KEY 等) | 前端页面已实现模型配置,Agent 从 `os.getenv` 读取 |
| 2 | EventDeduplicator | **删除** | 由 Agent 推理 + `query_past_events` 工具替代 |
| 3 | Agent 跨 tick 状态 | **无状态,磁盘驱动** | 见 §3.2,多个 Agent 并行运行时互不干扰 |
| 4 | VL 工具 | **保留接口,暂不实现** | Mock 占位,每次调用返回 `_placeholder=true`,Agent 降级处理 |
| 5 | 异步 VL | **废弃** | Demo 用规则模式 <1ms,真 LLM 时同步 `await` 即可 |

---

## 十一、事件生命周期:同一个事件如何聚合

### 11.1 核心规则

**同一个 episode 只在首次检测时写文件,后续 tick 写"结束"文件,不覆盖已有文件。**

```
T=8  首次检测 → 写 raw_event_T08.json {"status": "ongoing"}
T=9  同一事件 → 不写文件
T=10 同一事件 → 不写文件
T=13 违规结束 → 写 raw_event_T13.json {"status": "closed", "event_id": 同}
```

### 11.2 两种 raw_event 格式

**① 首次检测**(status="ongoing"):

```json
{
  "source": "A5",
  "event_id": "A5-P7-1734567890",
  "type": "PPE缺失-头盔",
  "person": {"id": "P7", "name": "张师傅", "role": "焊工"},
  "first_seen": "2026-08-04T14:32:08.000",
  "last_seen": "2026-08-04T14:32:08.000",
  "status": "ongoing",
  "evidence": {...},
  "explanation": "P7 从 T=8 开始未戴头盔,在动火区危险区内..."
}
```

**② 事件结束**(status="closed"):

```json
{
  "source": "A5",
  "event_id": "A5-P7-1734567890",
  "type": "PPE缺失-头盔",
  "person": {"id": "P7", "name": "张师傅", "role": "焊工"},
  "first_seen": "2026-08-04T14:32:08.000",
  "last_seen": "2026-08-04T14:32:13.000",
  "duration_sec": 5,
  "status": "closed",
  "explanation": "P7 在 T=8~13 未戴头盔,现已恢复"
}
```

**前端/A6 按 `event_id` 去重**:同一个 event_id 的 "closed" 记录会更新显示。

### 11.3 Agent 如何判断"结束"

Agent 在每 tick 中:

1. 调 `query_past_events` 查此人是否有活跃事件(status="ongoing")
2. 若**有活跃事件 + 当前秒违规已消失** → 输出 status="closed"(写新文件,不覆盖)
3. 若**有活跃事件 + 当前秒还在违规** → 不输出(延续中)
4. 若**无活跃事件 + 当前秒违规** → 输出 status="ongoing"(新事件)

---

## 十二、tick() 代码示例

### 12.1 完整 tick 实现

```text
# agent/a5_agent.py

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

from langchain.agents import create_react_agent, AgentExecutor
from langchain_core.prompts import PromptTemplate
from langchain_core.language_models.chat_models import BaseChatModel

from agent.tools import ALL_TOOLS, set_dependencies
from agent.system_prompt import get_system_prompt


class A5Agent:
    """
    A5 作业过程监测 Agent(ReAct)。

    ★ 无状态设计 ★
    - 所有状态通过磁盘上的 raw_event JSON 文件共享
    - 多个 Agent 实例并行运行时互不干扰
    """

    def __init__(
        self,
        collector,                        # AsyncCollector 实例
        llm: BaseChatModel,              # LangChain LLM 实例(必填)
        work_permit: Dict[str, Any],      # 作业票数据
        raw_event_dir: Optional[str] = None,
    ):
        self.collector = collector
        self.work_permit = work_permit

        # 注入依赖到工具
        set_dependencies(collector=collector, vl_model=None, work_permit=work_permit)

        # 从 system_prompt.py 读取提示词
        self._system_prompt = get_system_prompt()

        # Prompt 模板
        self._react_prompt = PromptTemplate.from_template("""\
{system_prompt}

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!! 注意:当前现实时间是 {wall_time} !!!
!!! 你只能查询 <= 这个时间点的日志,不能看未来 !!!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

作业票信息:
{work_permit_summary}

【最近 5 秒违规趋势】
{timeline_summary}

【当前秒快照】
{cv_summary}
{sensor_summary}
{position_summary}

★ 重要:先调用 query_past_events 工具检查此人此前是否已报告过类似事件。
  若已报告且仍在持续 → 不重复输出。
  若已报告但已恢复 → 可能是新事件,结合最近趋势判断。

---

你有以下工具可调用:
{tools}

工具名称: {tool_names}

使用 ReAct 格式:
  Thought: 当前思考
  Action: 工具名称
  Action Input: {{JSON 参数}}
  Observation: 工具返回
  ...(可多轮)
  Thought: 已有足够信息
  Final Answer: {{"is_event": bool, "type": "...", ...}}

{input}
""")

        # LangChain Agent(直接创建,无条件分支)
        self._agent = create_react_agent(llm=llm, tools=ALL_TOOLS, prompt=self._react_prompt)
        self._executor = AgentExecutor(
            agent=self._agent, tools=ALL_TOOLS,
            verbose=False, handle_parsing_errors=True, max_iterations=8,
        )

        # 仅落盘,不存内存状态
        self.raw_event_dir = Path(raw_event_dir) if raw_event_dir else None

    # ============================================================
    # 主入口(★ 无状态 ★)
    # ============================================================

    async def tick(self, wall_time: str, snapshot: Dict) -> List[Dict]:
        """
        每 1 秒被主循环调一次。
        无内存状态,去重靠 query_past_events 工具查磁盘。
        """
        # ★ 硬约束
        self.collector.set_agent_now(wall_time)

        agent_input = self._build_input(wall_time, snapshot)

        # 直接调用 Agent(无 llm 分支,Agent 自己决定何时调工具)
        result = await self._executor.ainvoke(agent_input)
        raw_output = result.get("output", "")
        intermediate = result.get("intermediate_steps", [])

        raw_events = self._parse_output(raw_output, wall_time, snapshot)

        # ★ 直接落盘,命名: raw_event_{wall_time}.json
        if raw_events:
            self._save_raw_events(raw_events, wall_time, intermediate)

        return raw_events

    # ============================================================
    # 输入组装(无 reported_summary 字段)
    # ============================================================

    def _build_input(self, wall_time: str, snapshot: Dict) -> Dict[str, str]:
        """
        把 snapshot raw 数据转为 LLM 能理解的自然语言。

        ★ 核心原则:每个数据段带解释,不是简单拼接数字 ★
        ★ 新增:注入最近 5 秒趋势(时序聚合 §3.0) ★
        """
        cv_logs = snapshot.get("cv_logs", [])
        sensors = snapshot.get("sensors", [{}])
        positions = snapshot.get("positions", [{}])

        # ── 最近 5 秒趋势(§3.0 第一层) ──
        timeline_lines = [
            "【最近 5 秒违规趋势】",
            "  数据来源:CV 多数表决的历史记录",
        ]
        for pid, winfo in self.work_permit.get("workers", {}).items():
            history = self._get_recent_timeline(pid, lookback=5)
            if not history:
                timeline_lines.append(f"  {pid}({winfo['name']}): 无历史数据")
                continue
            last_status = None
            for entry in history:
                sec = entry["second"]
                ratio = entry["helmet_ratio"]
                mark = " ← 当前秒" if entry["is_current"] else ""
                if ratio >= 0.8:
                    timeline_lines.append(f"    T+{sec:2d}: 头盔缺失({ratio:.0%}){mark}")
                    last_status = "violating"
                else:
                    timeline_lines.append(f"    T+{sec:2d}: 正常{mark}")
                    last_status = "normal"
            # 趋势结论
            if last_status == "violating":
                first_bad = next((e for e in history if e["helmet_ratio"] >= 0.8), None)
                if first_bad and first_bad is not history[-1]:
                    timeline_lines.append(f"    → {pid} 已持续违规 {len([e for e in history if e['helmet_ratio']>=0.8])} 秒")
                else:
                    timeline_lines.append(f"    → {pid} 从当前秒开始违规,此前正常,这是新事件")
            else:
                timeline_lines.append(f"    → {pid} 当前秒正常")

        timeline = "\n".join(timeline_lines)

        # ── PPE 检测摘要(带解释) ──
        cv_lines = [
            "【PPE 检测摘要】",
            "  数据来源:CV 模型每秒 25 帧对每位工人的检测结果",
            "  判定标准:某 PPE 类别缺失帧数 >= 80% 总帧数 → 标记为违规",
        ]
        for pid, winfo in self.work_permit.get("workers", {}).items():
            person_logs = [l for l in cv_logs if l.get("person_id") == pid]
            if not person_logs:
                cv_lines.append(f"  {pid}({winfo['name']},{winfo['role']}): 本秒无数据")
                continue
            n_bad = sum(1 for l in person_logs
                        if not next((d["value"] for d in l["detections"] if d["class_name"]=="helmet"), True))
            ratio = n_bad / len(person_logs)
            mark = " ★ 违规" if ratio >= 0.8 else ""
            cv_lines.append(
                f"  {pid}({winfo['name']},{winfo['role']}): "
                f"{len(person_logs)} 帧,{n_bad} 帧未戴头盔({ratio:.0%}) → "
                f"{'头盔缺失' if ratio >= 0.8 else '正常'}{mark}"
            )

        # ── 传感器(带阈值说明) ──
        sensor_lines = [
            "【传感器读数】",
            "  数据来源:动火区-A 的 3 个固定传感器",
        ]
        if sensors:
            for sid, r in sensors[0].get("readings", {}).items():
                th = ""
                if r.get("threshold_alarm"):
                    th = f"(警戒线 {r['threshold_warning']}, 报警线 {r['threshold_alarm']})"
                mark = ""
                if r["status"] == "alarm":
                    mark = " ★ 异常"
                elif r["status"] == "warning":
                    mark = " ⚠ 接近警戒"
                sensor_lines.append(
                    f"  {r['type']}: {r['value']} {r['unit']} → {r['status']}{mark} {th}"
                )

        # ── 位置(带结论) ──
        pos_lines = [
            "【人员位置】",
            "  数据来源:UWB 定位标签",
        ]
        if positions:
            for wid, p in positions[0].get("positions", {}).items():
                zone = "危险区" if p.get("is_in_danger_zone") else "普通区"
                mark = " ★ 不在作业区" if not p.get("is_in_danger_zone") else ""
                pos_lines.append(
                    f"  {wid}: {p['area_id']}({zone}) → "
                    f"{'在危险作业区内' if p.get('is_in_danger_zone') else '不在危险作业区'}{mark}"
                )

        # ── 作业票要求 ──
        permit_lines = [
            "【作业票要求】",
            f"  票号: {self.work_permit['permit_id']}({self.work_permit['level']})",
            f"  必戴 PPE: {', '.join(self.work_permit['required_ppe'])}",
            f"  要求监护人全程在场: {'是' if self.work_permit.get('supervisor_required') else '否'}",
        ]

        return {
            "system_prompt":       self._system_prompt,
            "wall_time":           wall_time,
            "work_permit_summary": "\n".join(permit_lines),
            "timeline_summary":    timeline,        # ★ 新增:最近 5 秒趋势
            "cv_summary":          "\n".join(cv_lines),
            "sensor_summary":      "\n".join(sensor_lines),
            "position_summary":    "\n".join(pos_lines),
            "tools":               "{tools}",
            "tool_names":          "{tool_names}",
            "input":               "根据当前快照判断是否存在需要报告的候选安全事件?",
        }

    # ============================================================
    # 趋势查询(供 _build_input 调用)
    # ============================================================

    def _get_recent_timeline(self, person_id: str, lookback: int = 5) -> List[Dict]:
        """
        查询某人在最近 N 秒的 PPE 趋势。

        返回:
          [
            {"second": 4, "helmet_ratio": 0.0, "is_violating": False, "is_current": False},
            {"second": 5, "helmet_ratio": 0.88, "is_violating": True, "is_current": False},
            {"second": 6, "helmet_ratio": 0.88, "is_violating": True, "is_current": True},
          ]

        实现:
        - 调 collector.query_raw_logs 查 lookback 秒内的所有 CV 日志
        - 按秒分组,用 analyze_ppe_compliance 计算每帧违规率
        """
        sec_now = int(float(wall_time[-12:].split(":")[2].split(".")[0]) if False else 12)  # 简化为占位
        # 实际实现:基于 collector._cv_raw + 时间范围过滤
        # 详见 tools.py 集成后实现
        return []  # 占位:实际从 collector 读取

    # ============================================================
    # 输出解析(不变)
    # ============================================================

    def _parse_output(self, raw: str, wall_time: str, snapshot: Dict) -> List[Dict]:
        if not raw:
            return []
        try:
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0]
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0]
            parsed = json.loads(raw.strip())
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, dict) or not parsed.get("is_event"):
            return []
        sec = int(float(snapshot.get("scenario_time", "0s").rstrip("s")))
        return [{
            "source":      "A5",
            "event_id":    f"A5-{parsed.get('person', {}).get('id', '?')}-{int(time.time()*1000)}",
            "type":        parsed.get("type", "未分类"),
            "person":      parsed.get("person", {}),
            "second":      sec,
            "wall_time":   wall_time,
            "explanation": parsed.get("explanation", ""),
            "evidence":    parsed.get("evidence", {}),
            "note":        "A5 不判定 risk_level,由 A6 完成",
        }]

    def _save_raw_events(self, events: List[Dict], wall_time: str, intermediate: List):
        """
        落盘 raw_event,文件名按 wall_time 命名(与日志落盘模式一致)。

        文件名:raw_event_{wall_time}.json
        例:raw_event_20260804_143212_000.json
        """
        if not self.raw_event_dir:
            return
        self.raw_event_dir.mkdir(parents=True, exist_ok=True)
        # wall_time 标准化为文件名安全格式
        safe_ts = wall_time.replace(":", "").replace("-", "").replace(".", "_")
        filepath = self.raw_event_dir / f"raw_event_{safe_ts}.json"
        payload = {
            "wall_time": wall_time,
            "events": events,
            "intermediate_steps": [
                {"action": str(a), "observation": str(o)[:200]} for a, o in intermediate
            ],    # 工具调用轨迹(供前端展示)
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
```

### 12.1.1 设计说明:不能简单拼接数据

**核心原则:每个数据点周围都要有解释性文字,否则 LLM 看不懂。**

```text
# ❌ 简单拼接(LLM 不知道这些数字是什么)
prompt = f"""
wall_time: 2026-08-04T14:32:12.000
cv: P7 helmet False 0.88, P8 helmet True 0.95
sensor: gas 0.2 normal
pos: P7 动火区-A
"""
# LLM: "0.88 是什么? False 是违规吗? P7 在动火区是好事还是坏事?"
```

```text
# ✅ 每个字段带解释(LLM 能直接理解)
prompt = f"""
当前场景快照(以下数据来自现场传感器,已加工为自然语言):

【PPE 检测摘要】
  数据来源:CV 模型每秒 25 帧对每位工人的检测结果
  判定标准:某 PPE 类别缺失帧数 >= 80% 总帧数 → 标记为违规
  P7(张师傅,焊工): 25 帧中 22 帧未佩戴安全帽 → ★ 违规(88%)
  P8(王师傅,焊工): 25 帧中 0 帧违规 → 正常
  P11(赵师傅,监护人): 未检测到异常 → 正常

【传感器读数】
  数据来源:动火区-A 的 3 个固定传感器
  可燃气体浓度: 0.2 %LEL → 正常(警戒线 0.7,报警线 1.0)
  环境温度: 26.0 °C → 正常(警戒线 35°C)
  火焰探测器: 未触发 → 正常

【人员位置】
  数据来源:UWB 定位标签
  P7: 动火区-A(危险区) → 在危险作业区内
  P8: 动火区-A(危险区) → 在危险作业区内
  P11: 动火区-A(危险区) → 监护人在岗

【作业票要求】
  票号: PTW-2026-0803-动火-007(二级动火)
  必戴 PPE: helmet(安全帽), goggles(护目镜), protective_suit(防护服), safety_shoes(安全鞋)
  要求监护人全程在场: 是
```

**设计原则**:
- 每个数据段开头说明**数据来源**(CV 模型/传感器/定位/作业票)
- 每个字段说明**含义**(不是光给数字,而是"0.7→警戒线"、"88%→违规")
- 每个状态给出**结论**(不是光说"in_danger_zone=True",而是"在危险作业区内")
- 用 `★` 标记异常项,LLM 训练语料中对特殊字符敏感
- 用 `→` 引导推理链
"""

---

### 12.1.2 design note :wall_time 的显著标记

**为什么不用简单拼接?**

```text
# ❌ 弱提示(LLM 容易忽略)
prompt = f"当前时间: {wall_time}"
```

LLM 在长上下文中容易"忽略"中间位置的纯文本时间标记,从而导致幻觉查询未来数据。

```text
# ✅ 强提示(三层符号包围,LLM 难以忽略)
prompt = f"""
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!! 注意:当前现实时间是 {wall_time} !!!
!!! 你只能查询 <= 这个时间点的日志,不能看未来 !!!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
"""
```

**设计原则**:
- 用 `!!!` 包围关键约束(LLM 训练语料中 `!!!` 很少出现在正常文本中,模型对异常符号更敏感)
- 用多行框线(`!!!...!!!`) 形成视觉隔离
- 不依赖单一位置:Prompt 正文中也有 `你只能查询当前时间点及之前的日志`
- 最终兜底:代码层 `_agent_now` 硬检查(§3.1)

---

### 12.2 主循环调用示例

```text
# main_loop/main_loop.py (调用侧)

import os
from langchain_openai import ChatOpenAI
from langchain_community.chat_models import ChatOllama

from stream_players.async_collector import AsyncCollector
from scenario_data.mock_work_permit import WORK_PERMIT
from agent.a5_agent import A5Agent


# ========== 从环境变量构建 LLM ==========
# 前端在 .env 中写入的配置,代码通过 os.getenv 读取
# 注意:此处只是模式示范,实际 .env 中的敏感信息不可硬编码

def build_llm():
    """根据 .env 中的配置构建 LLM"""
    provider = os.getenv("LLM_PROVIDER", "").lower()     # "openai" | "ollama" | ""

    if provider == "openai":
        return ChatOpenAI(
            model=os.getenv("LLM_MODEL", "gpt-4o"),
            api_key=os.getenv("OPENAI_API_KEY"),          # ★ 敏感,从 .env 读取
            base_url=os.getenv("OPENAI_BASE_URL", None),
            temperature=0,
        )
    elif provider == "ollama":
        return ChatOllama(
            model=os.getenv("LLM_MODEL", "qwen2.5:7b"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0,
        )
    else:
        # 无 LLM → Agent 自动降级到规则模式
        print("[A5] LLM 未配置,使用规则模式")
        return None


# ========== 启动场景 ==========

async def run_with_agent(scenario: str):
    collector = AsyncCollector(scenario, log_dir=f"logs/{scenario}")
    llm = build_llm()
    agent = A5Agent(
        collector=collector,
        llm=llm,
        work_permit=WORK_PERMIT,
        raw_event_dir=f"logs/{scenario}/raw_events",
    )

    await collector.start()
    for sec in range(30):
        await asyncio.sleep(1.0)
        await collector.flush_second(sec)
        wall_time = collector._sec_to_wall(sec + 1)
        snapshot = collector.get_snapshot(wall_time)
        events = await agent.tick(wall_time, snapshot)
        for ev in events:
            print(f"[A5][T={ev['second']:2d}s] {ev['type']} | {ev['person']['name']}")
    await collector.stop()
```

### 12.3 工具调用轨迹(调试用)

Agent 每轮的 ReAct 中间步骤会记录到 `raw_event_*.json`:

```json
{
  "wall_time": "2026-08-04T14:32:12.000",
  "events": [...],
  "intermediate_steps": [
    {
      "action": "query_raw_logs(start_wall='2026-08-04T14:32:00', end_wall='2026-08-04T14:32:08', source_type='cv', person_id='P7')",
      "observation": "[{\"frame_id\": \"cam_03_0.000\", ...}, ...共 200 条]"
    },
    {
      "action": "get_current_snapshot(wall_time='2026-08-04T14:32:08.000')",
      "observation": "{\"scenario_time\": \"8.0s\", \"cv_logs\": [...], ...}"
    },
    {
      "action": "analyze_ppe_compliance(cv_logs=[...], person_id='P7', threshold=0.8)",
      "observation": "{\"person_id\": \"P7\", \"is_violating\": true, \"violations\": [\"helmet\"]}"
    },
    {
      "action": "call_vl_expert(question='P7 当前 PPE 状态如何?', history={...}, context={...})",
      "observation": "{\"is_violation\": true, \"violation_type\": \"PPE缺失-头盔\", \"confidence\": 0.93}"
    }
  ]
}
```

---

## 十三、存盘体系(三类日志)

### 完整存盘链

```
原始数据流(CV 25FPS / Sensor 1Hz / Position 1Hz)
          │
          ▼
┌─────────────────────────────────────┐
│ 第一类: 原始日志(per frame)          │
│ 存盘: AsyncCollector.flush_second()  │
│ 命名: {type}_{ts}_T{sec:02d}.json   │
│ 示例: cv_20260805_1000_T08.json     │
│ 内容: 原始 CV 检测(每帧 3 人 PPE)    │
│ 数量: 3 类 × 30 秒 = 90 文件/场景   │
└─────────────────────────────────────┘
          │
          │ AsyncCollector._build_single_snapshot()
          ▼
┌─────────────────────────────────────┐
│ 第二类: 加工快照(per second)         │
│ 存盘: 同上 _write_file               │
│ 命名: snapshot_{ts}_T{sec:02d}.json │
│ 内容: 该秒的 CV/sensor/position 聚合│
│  ★ 含每人 PPE 多数表决结果           │
│  ★ 含传感器状态 + 位置区域            │
│ 数量: 30 文件/场景                   │
└─────────────────────────────────────┘
          │
          │ A5Agent.tick(wall_time, snapshot)
          ▼
┌─────────────────────────────────────┐
│ 第三类: Agent 输出(raw_event)       │
│ 存盘: A5Agent._save_raw_event()      │
│ 命名: raw_event_{wall_time}.json    │
│ 内容: 候选事件 + 证据 + 解释         │
│ 数量: 0~N(仅当有事件时)             │
└─────────────────────────────────────┘
```

### 三类对比

| | 原始日志 | 加工快照 | Agent 输出 |
|---|---|---|---|
| **粒度** | 每帧(~75条/秒) | 每秒 1 条 | 按需(仅事件) |
| **CV 内容** | `detections[{helmet:false,...}]` | `cv_summary{P7:{helmet_ratio:0.88,...}}` | `evidence{cv_ratio:0.88}` |
| **谁写** | AsyncCollector | AsyncCollector | A5Agent |
| **可覆盖** | 否(新增文件) | 否(新增文件) | 否(新增文件) |

---

## 十四、实施步骤

1. 确认上述设计(LLM 选型、去重策略、记忆方案)
2. 写 `agent/prompts.py`(System Prompt + ReAct 模板)
3. 重写 `agent/a5_agent.py`(改用 `create_react_agent`)
4. 适配 `main_loop/main_loop.py`(调用方式不变,tick 接口兼容)
5. 测试 5 个场景
