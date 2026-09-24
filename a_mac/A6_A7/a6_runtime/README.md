# A6 风险研判智能体 — 需求说明文档

> **文档版本**: v0.3
> **编制日期**: 2026-08-06
> **对应项目**: 广东石化作业场景闭环管理智能体工具链项目
> **状态**: 需求确认中，待开发

---

## 一、A6 智能体定位

### 1.1 角色定义

**A6 = 风险的"裁判"与"顾问"**

- **职责**: 接收 A5 输出的候选风险事件，进行**事件自主聚合**和**风险等级研判**，给出等级划分依据和处理建议
- **上游**: A5 作业过程监测智能体（输出候选事件，不判断等级）
- **下游**: A7 处置调度智能体（接收已研判的事件，执行处置）
- **不做什么**: 不直接采集数据、不发起处置动作、不下达停工指令

### 1.2 上下游关系

```
↑ 上游
├── A5 作业过程监测智能体 → 输出候选风险事件（带时间范围、无等级）
└── A2/A3 提供上下文和资源绑定

A6 自身 → 事件自主聚合 + 风险分级 + 建议输出

↓ 下游
└── A7 处置调度智能体 ← 接收已研判事件（带等级+依据+建议）
```

### 1.3 数据来源说明

A6 的输入数据来自 A5 生成的日志文件。

#### A5 日志文件体系

`AsyncCollector` 每秒并行运行 3 个流（CV@25fps、传感器@1Hz、定位@1Hz），每秒落盘 4 类文件：

| 文件类型 | 命名格式 | 内容 | 频率 |
|---------|---------|------|------|
| CV 检测 | `cv_{wall_time}.json` | 每帧检测结果，含 person_id、helmet/goggles/suit 状态 | 25fps |
| 传感器 | `sensor_{wall_time}.json` | 气体/火焰/温度传感器读数 | 1Hz |
| 定位 | `position_{wall_time}.json` | 每个人员的位置区域、是否危险区 | 1Hz |
| **快照** | `snapshot_{wall_time}.json` | 以上三类的每秒聚合摘要 | 1Hz |

#### A5 Agent 处理流程

`main_loop` 每隔 `agent_interval` 秒扫描 `snapshot_*.json` 文件：

1. 对每个人员运行 `EventDeduplicator` 状态机（NO_ACTIVE → NEW_EPISODE → ACTIVE → COOLDOWN）
2. 状态机检测到违规结束时，输出 `raw_event_{wall_time}.json`

`raw_event` 文件是 A6 的直接输入，格式如下：

```json
{
  "wall_time": "2026:08:06T15:43:05.504",
  "events": [
    {
      "event_id": "A5-P11-1722933125",
      "type": "PPE缺失-护目镜",
      "person": {"id": "P11", "name": "李师傅", "role": "焊工"},
      "wall_time": "2026-08-06T15:43:05",
      "first_seen": "2026-08-06T15:43:05",
      "last_seen": "2026-08-06T15:43:15",
      "status": "closed",
      "evidence": {
        "cv_frames": 250,
        "helmet_missing_ratio": 0.0,
        "goggles_missing_ratio": 1.0,
        "location": "动火区-A",
        "is_in_danger_zone": true
      },
      "explanation": "P11 在动火作业中未佩戴护目镜超过10秒"
    }
  ]
}
```

---

## 二、核心功能需求

### 2.1 A5 日志状态管理

#### 2.1.1 调用模式

A6 的触发方式是：**每当 A5 产生一条告警，就调用一次 A6**。

这意味着 A6 处理的是 A5 输出的单个 `raw_event` 文件，而非批量处理。

#### 2.1.2 重复告警问题

A5 的 `EventDeduplicator` 状态机在事件**结束时**才输出完整的 `raw_event`（包含 `first_seen` 和 `last_seen`）。

但如果事件持续较长，A5 可能在不同时刻输出多条关于**同一事件**的告警：

```
场景示例：
T=8s  A5 检测到 P7 摘头盔，开始追踪（状态机: NO_ACTIVE → NEW_EPISODE）
T=9s  A5 继续检测到 P7 摘头盔（状态机: ACTIVE）
T=10s A5 继续检测（状态机: ACTIVE）
...
T=15s P7 戴回头盔，状态机关闭（COOLDOWN 2秒后 → NO_ACTIVE）
T=17s A5 输出 raw_event: P7 摘头盔 [T=8s - T=15s]
```

上述情况只会输出 1 条 `raw_event`。但如果 A5 的触发逻辑是**每秒检测到违规就立即输出**，而不是等事件结束，则可能输出多条关于同一事件的告警。

**问题**：如果 A5 输出多条相同的 `raw_event`（同一 `event_id`），A6 会重复生成研判报告。

#### 2.1.3 解决方案：事件 ID 追踪

A6 维护一个 **事件 ID 追踪表**，记录每个 A5 事件 ID 是否已被处理过。

文件路径：`A6/logs/a5_log_status.json`

```json
{
  "log_dir": "A5/logs/frontend/B_20260806_154300",
  "last_updated": "2026-08-06T15:43:25",
  "files": {
    "raw_event_2026-08-06T15-43-05_504": {
      "status": "processed",
      "processed_at": "2026-08-06T15:43:15",
      "event_count": 1,
      "a6_output_id": "A6-20260806-154305-001"
    },
    "raw_event_2026-08-06T15-43-06_504": {
      "status": "unprocessed",
      "event_count": 0
    },
    "raw_event_2026-08-06T15-43-07_504": {
      "status": "error",
      "error_message": "JSON parse failed",
      "retry_count": 1
    }
  },
  "event_id_map": {
    "A5-P7-1722933125": {
      "a6_output_id": "A6-20260806-143215-001",
      "status": "completed",
      "first_processed_at": "2026-08-06T14:32:15"
    },
    "A5-P7-1722933135": {
      "a6_output_id": "A6-20260806-143220-001",
      "status": "completed",
      "first_processed_at": "2026-08-06T14:32:20"
    }
  },
  "summary": {
    "total": 30,
    "unprocessed": 5,
    "processing": 2,
    "error": 1,
    "processed": 22
  }
}
```

**event_id_map 字段说明**：

| 字段 | 含义 |
|------|------|
| `A5事件ID` | A5 输出的原始事件 ID |
| `a6_output_id` | A6 生成的研判报告 ID |
| `status` | 处理状态：`completed`（已完成）/ `updated`（已更新） |
| `first_processed_at` | 首次处理时间 |

**处理逻辑**：

1. A5 调用 A6 时，传入 `event_id`（如 `A5-P7-1722933125`）
2. A6 查询 `event_id_map`，检查该事件是否已处理
3. 如果 **未处理**：创建新的研判报告，`event_id_map` 中新增一条记录
4. 如果 **已处理**：更新已有研判报告（追加最新证据、更新 `last_seen`），`event_id_map` 中 `status` 变为 `updated`

**状态说明**：

| 状态 | 含义 |
|------|------|
| `unprocessed` | 还未处理 |
| `processing` | 正在处理 |
| `error` | 处理失败（可重试） |
| `processed` | 已处理完 |

---

### 2.2 功能一：Agent 输入工具（读取 A5 数据）

#### 2.2.1 调用模式

A6 的触发方式是**A5 每产生一条告警就调用一次 A6**。

调用时传入：
- `event_id`: A5 事件 ID（用于去重）
- `event_data`: 事件数据（可选，如果调用方已获取）

A6 通过 `event_id_map` 判断该事件是否已处理过，避免重复生成报告。

#### 2.2.2 Agent 工具清单

```python
# A6/agent/tools.py

class A5DataTools:
    """
    A6 Agent 输入工具集

    用于读取 A5 生成的日志数据
    """

    def check_event_status(
        self,
        event_id: str
    ) -> Dict:
        """
        查询 A5 事件 ID 的处理状态

        Args:
            event_id: A5 事件 ID（如 "A5-P7-1722933125"）

        Returns:
            {
                "event_id": str,           # 事件ID
                "exists": bool,            # 是否已存在
                "status": str,             # 状态：None/completed/updated
                "a6_output_id": str,       # 已有则返回 A6 输出ID
                "first_processed_at": str   # 首次处理时间
            }
        """

    def read_raw_event_by_id(
        self,
        event_id: str
    ) -> Dict:
        """
        根据 A5 事件 ID 读取原始事件数据

        Args:
            event_id: A5 事件 ID

        Returns:
            事件数据字典，包含 event_id、type、person、first_seen、last_seen、evidence 等
        """

    def read_surrounding_events(
        self,
        target_wall_time: str,
        time_window_minutes: int = 5
    ) -> List[Dict]:
        """
        读取目标时间前后时间窗口内的所有 raw_event

        Args:
            target_wall_time: 目标事件的时间戳（ISO格式）
            time_window_minutes: 时间窗口（分钟），默认前后5分钟

        Returns:
            时间窗口内的所有事件列表
        """
```

#### 2.2.3 工具使用流程（Agent 视角）

```
A5 调用 A6，传入 event_id

Step 1: 调用 check_event_status(event_id=X)
         → 查询该事件是否已处理过

Step 2: 如果是新的事件
         调用 read_raw_event_by_id(event_id=X)
         → 读取事件详情

Step 3: 如需理解上下文（如判断是否需要聚合）
         调用 read_surrounding_events(target_wall_time=X, time_window_minutes=5)
         → 获取周围的事件列表

Step 4: 完成研判后，调用输出工具记录结果（见 2.4 节）
```

---

### 2.3 功能二：风险等级研判（可编辑提示词）

#### 2.3.1 设计理念

风险分级使用 **LLM + 提示词** 方式实现。提示词保存在 `prompts.py` 文件中，支持前端页面编辑。

Agent 进行风险分级时，读取 `prompts.py` 中的提示词作为推理依据。

#### 2.3.2 提示词文件结构

```python
# A6/agent/prompts.py
# ============================================================
# A6 提示词配置文件
# ============================================================

# --------------- 风险分级提示词 ----------------

RISK_CLASSIFICATION_SYSTEM_PROMPT = """你是一个作业安全风险分析专家。你的职责是对告警事件进行风险等级研判。

## 风险等级定义（5级）

| 等级 | 名称 | 含义 | 典型场景 |
|------|------|------|----------|
| 1 | 轻微 | 偶发、单人、短暂、不在危险区 | 工人路边擦汗摘头盔3秒 |
| 2 | 一般 | 持续超过10秒、或在作业区内 | 焊工连续15秒不戴头盔 |
| 3 | 较重 | 多人、或叠加环境风险 | 两焊工同时不戴+气体接近警戒 |
| 4 | 严重 | 群体性违规+环境报警 | 全体不戴+可燃气体报警 |
| 5 | 危急 | 可能立即引发事故 | 动火区有人不戴+气体已报警+监护人不在 |

## 等级加成规则

以下情况可以在基础等级上提升风险等级：

1. **危险区域加成**: 人员在危险作业区内违规 → 等级+1
2. **持续时间加成**: 持续>30秒 → 等级+1；持续>60秒 → 等级+2（封顶）
3. **群体加成**: 2人同时违规 → 等级+1；3人及以上 → 等级+2
4. **环境叠加加成**: 有传感器报警 → 等级+1；2种及以上 → 等级+2
5. **管理失控加成**: 监护人不在+人员违规 → 等级+2；监护人不在+环境报警 → 等级置5

## 证据字段说明

事件证据（evidence）包含以下字段：
- cv_frames: CV分析帧数
- helmet_missing_ratio: 头盔缺失比例（0-1）
- goggles_missing_ratio: 护目镜缺失比例（0-1）
- suit_missing_ratio: 防护服缺失比例（0-1）
- location: 位置区域
- is_in_danger_zone: 是否在危险作业区
- sensor_status: 传感器状态
- guard_present: 监护人是否在场

## 输出格式

请以JSON格式输出研判结果：
{
    "risk_level": 1-5,
    "risk_level_name": "轻微/一般/较重/严重/危急",
    "risk_basis": "等级判定依据",
    "suggestions": ["处理建议1", "处理建议2", ...],
    "reasoning": "推理过程"
}"""

RISK_CLASSIFICATION_USER_PROMPT_TEMPLATE = """## 待研判事件信息

事件ID: {event_id}
事件类型: {event_type}
涉及人员: {involved_persons}
首次发现时间: {first_seen}
最近发现时间: {last_seen}
持续时长: {duration_sec}秒
证据详情: {evidence}

请进行风险等级研判。"""

# --------------- 事件聚合提示词 ----------------

AGGREGATION_SYSTEM_PROMPT = """你是一个事件聚合分析专家。你的职责是判断多个告警事件是否应该聚合为同一个事件。

## 聚合原则

1. **同一人员同类违规断续发生** → 应该聚合
   例: P7 10:32:05 摘头盔，10:32:20 又摘头盔，中间去喝水 → 聚合为同一事件

2. **同一区域多人同类型违规** → 可以聚合为群体性事件
   例: 动火区内 P7、P8 同时摘头盔 → 聚合为群体性PPE违规

3. **不同类型违规但存在因果关联** → 可以关联研判（不一定要聚合）
   例: 气体报警 + 人员不戴口罩 → 保持独立但关联报告

4. **完全独立的随机违规** → 不聚合
   例: P7 摘头盔 + P9 跨越围栏 → 时间接近但无关，不聚合

## 输出格式

{
    "should_aggregate": true/false,
    "aggregated_event_id": "建议的聚合事件ID",
    "reasoning": "判断理由",
    "original_event_ids": ["参与聚合的原始事件ID列表"]
}"""

AGGREGATION_USER_PROMPT_TEMPLATE = """## 待分析事件

目标事件: {target_event}
时间窗口内其他事件: {surrounding_events}

请判断这些事件是否应该聚合。"""

# --------------- 分级建议模板 ----------------

RISK_SUGGESTIONS_BY_LEVEL = {
    1: ["提醒人员注意安全", "记录本次事件"],
    2: ["现场口头警告并纠正", "加强该区域巡查频次", "记录违规情况"],
    3: ["现场口头警告并纠正", "加强该区域巡查频次", "记录违规情况", "报告班组长"],
    4: ["立即纠正违规行为", "监护人必须返回现场", "加强现场管控", "填写安全隐患整改单", "报告安全管理部门"],
    5: ["立即通知现场停止作业", "疏散无关人员", "启动应急预案", "上报安全管理部门", "保护现场配合调查"]
}
```

---

### 2.4 功能三：输出工具（记录处理结果）

#### 2.4.1 设计理念

Agent 完成研判后，通过输出工具将结果记录到文件中，同时更新 A5 日志状态索引。

输出文件按时间分目录，便于追溯。

#### 2.4.2 输出工具清单

```python
# A6/agent/tools.py（续）

class OutputTools:
    """
    A6 输出工具集

    用于记录聚合事件、风险等级研判结果等
    """

    def save_assessment(
        self,
        assessment: Dict,
        output_dir: str = None
    ) -> str:
        """
        保存单条风险研判结果

        Args:
            assessment: 研判结果字典，包含:
                - a6_event_id: A6 事件ID
                - aggregated_from: 源 A5 事件ID列表
                - event_type: 事件类型
                - first_seen: 首次发现时间
                - last_seen: 最近发现时间
                - duration_sec: 持续时长
                - involved_persons: 涉及人员
                - risk_level: 风险等级（1-5）
                - risk_level_name: 等级名称
                - risk_basis: 等级依据
                - suggestions: 处理建议列表
                - reasoning: 推理过程
                - timestamp: 研判时间

        Returns:
            保存的文件路径
        """

    def save_aggregation_result(
        self,
        aggregation: Dict,
        output_dir: str = None
    ) -> str:
        """
        保存事件聚合结果

        Args:
            aggregation: 聚合结果，包含:
                - aggregated_event_id: 聚合后事件ID
                - original_event_ids: 原始事件ID列表
                - aggregation_reasoning: 聚合判断理由
                - first_seen: 聚合后首次时间
                - last_seen: 聚合后最近时间

        Returns:
            保存的文件路径
        """

    def batch_save_assessments(
        self,
        assessments: List[Dict],
        output_dir: str = None
    ) -> List[str]:
        """
        批量保存研判结果

        Returns:
            保存的文件路径列表
        """

    def update_a5_log_status(
        self,
        filenames: List[str],
        new_status: str,
        extra_info: Dict = None
    ) -> bool:
        """
        更新 A5 日志状态索引文件

        Args:
            filenames: 要更新的文件名列表
            new_status: 新状态（processing/processed/error）
            extra_info: 额外信息（如 a6_output_id、error_message 等）

        Returns:
            是否更新成功
        """
```

#### 2.4.3 输出文件格式

**研判结果输出** (`A6/logs/assessments/YYYYMMDD/a6_*.json`):

```json
{
  "a6_event_id": "A6-20260806-154305-001",
  "aggregated_from": ["A5-P11-1722933125"],
  "event_type": "PPE缺失-护目镜",
  "first_seen": "2026-08-06T15:43:05",
  "last_seen": "2026-08-06T15:43:15",
  "duration_sec": 10.0,
  "involved_persons": ["P11"],
  "risk_level": 3,
  "risk_level_name": "较重",
  "risk_basis": "事件类型: PPE缺失-护目镜\n基础等级: 一般（2级）\n加成因素: 危险区+1\n最终等级: 较重（3级）",
  "suggestions": [
    "现场口头警告并纠正",
    "加强该区域巡查频次",
    "记录违规情况"
  ],
  "reasoning": "P11 在动火区连续10秒不戴护目镜，基础等级2，危险区加成+1，判定为较重",
  "timestamp": "2026-08-06T15:43:20"
}
```

**聚合结果输出** (`A6/logs/aggregations/YYYYMMDD/agg_*.json`):

```json
{
  "aggregated_event_id": "A6-AGG-20260806-154305-001",
  "original_event_ids": ["A5-P7-1722933125", "A5-P7-1722933135"],
  "aggregation_reasoning": "P7 在 15:43:05-15:43:10 摘头盔，15:43:15-15:43:20 又摘头盔，中间去喝水，具有连续性，聚合为同一事件",
  "first_seen": "2026-08-06T15:43:05",
  "last_seen": "2026-08-06T15:43:20",
  "timestamp": "2026-08-06T15:43:20"
}
```

---

### 2.5 功能四：前端提示词编辑（页面工具，非 Agent 工具）

#### 2.5.1 设计背景

`prompts.py` 中的提示词需要支持人工编辑和版本管理。

提供前端页面工具用于：
- 读取当前提示词
- 修改提示词内容
- 保存到文件
- 版本历史（可选）

这些工具是**前端页面调用**的 REST API，**不是 Agent 调用的工具**。

#### 2.5.2 提示词编辑 API

```python
# A6/agent/prompt_manager.py

class PromptManager:
    """
    提示词管理器

    提供提示词的读取、修改、保存功能
    供前端页面调用，非 Agent 工具
    """

    def read_prompt(self, prompt_name: str) -> Dict:
        """
        读取指定提示词内容

        Args:
            prompt_name: 提示词名称
                - "risk_classification": 风险分级系统提示词
                - "risk_user_template": 风险分级用户模板
                - "aggregation": 事件聚合提示词

        Returns:
            {
                "name": str,
                "content": str,
                "last_modified": str
            }
        """

    def update_prompt(
        self,
        prompt_name: str,
        new_content: str,
        reason: str = None
    ) -> bool:
        """
        修改提示词（仅更新内存中的副本）

        Args:
            prompt_name: 提示词名称
            new_content: 新的提示词内容
            reason: 修改原因（记录日志）

        Returns:
            是否修改成功
        """

    def save_to_file(self, prompt_name: str = None) -> bool:
        """
        将内存中的提示词保存到 prompts.py 文件

        Args:
            prompt_name: 提示词名称（None 表示保存所有）

        Returns:
            是否保存成功
        """

    def reset_to_default(self, prompt_name: str = None) -> bool:
        """
        重置提示词为默认内容

        Args:
            prompt_name: 提示词名称（None 表示重置所有）

        Returns:
            是否重置成功
        """
```

---

## 三、数据流设计

### 3.1 完整处理流程

```
┌─────────────────────────────────────────────────────────────────┐
│                          A6 处理流程                            │
└─────────────────────────────────────────────────────────────────┘

  A5 检测到告警 → 调用 A6（传入 event_id）
         │
         ▼
  ┌──────────────────────────────────┐
  │  查询事件处理状态                   │
  │  check_event_status(event_id)     │
  │  → 是否已处理过？                   │
  └────────┬─────────────────────────┘
           │
     ┌─────┴─────┐
     │  已处理    │  未处理
     ▼           ▼
  更新已有      读取事件详情
  研判报告      read_raw_event_by_id()
  (status=      │
  updated)            │
                      ▼
               ┌──────────────────────┐
               │  如需理解上下文        │
               │  read_surrounding_    │
               │  events()             │
               └──────────┬───────────┘
                          │
                          ▼
               ┌──────────────────────┐
               │  风险等级研判         │
               │  LLM + 提示词        │
               │  → 等级+依据+建议     │
               └──────────┬───────────┘
                          │
                          ▼
               ┌──────────────────────┐
               │  记录结果             │
               │  save_assessment()    │
               │  update_a5_log_status │
               └──────────┬───────────┘
                          │
                          ▼
                     返回给 A5/A7
```

### 3.2 重复告警处理流程

```
  A5 调用 #1: event_id=A5-P7-xxx-001, status=ongoing
         │
         ▼
  check_event_status() → 不存在 → 创建新研判报告
  → event_id_map["A5-P7-xxx-001"] = {status: "completed"}

  A5 调用 #2: event_id=A5-P7-xxx-001, status=ongoing
  （同一事件，A5 还在追踪中）
         │
         ▼
  check_event_status() → 已存在, status=completed
  → 更新研判报告（追加 evidence，更新 last_seen）
  → event_id_map["A5-P7-xxx-001"] = {status: "updated"}

  A5 调用 #3: event_id=A5-P7-xxx-001, status=closed
  （事件结束）
         │
         ▼
  check_event_status() → 已存在, status=updated
  → 最终更新研判报告，标记为最终版本
```

### 3.2 A6 输入格式（来自 A5）

```json
{
  "event_id": "A5-P7-1722933125",
  "type": "PPE缺失-头盔",
  "person": {
    "id": "P7",
    "name": "张师傅",
    "role": "焊工"
  },
  "wall_time": "2026-08-06T14:32:15",
  "first_seen": "2026-08-06T14:32:05",
  "last_seen": "2026-08-06T14:32:15",
  "status": "closed",
  "evidence": {
    "cv_frames": 250,
    "helmet_missing_ratio": 0.92,
    "location": "动火区-A",
    "is_in_danger_zone": true,
    "sensor_status": "normal"
  },
  "explanation": "P7 在动火作业中未佩戴安全帽超过10秒"
}
```

### 3.3 A6 输出格式（交付给 A7）

```json
{
  "a6_event_id": "A6-20260806-143215-001",
  "aggregated_from": ["A5-P7-1722933125"],
  "event_type": "PPE缺失-头盔",
  "first_seen": "2026-08-06T14:32:05",
  "last_seen": "2026-08-06T14:32:15",
  "duration_sec": 10.0,
  "involved_persons": ["P7"],
  "risk_level": 3,
  "risk_level_name": "较重",
  "risk_basis": "事件类型: PPE缺失-头盔\n基础等级: 一般（2级）\n加成因素: 危险区+1\n最终等级: 较重（3级）",
  "suggestions": [
    "现场口头警告并纠正",
    "加强该区域巡查频次",
    "记录违规情况"
  ],
  "reasoning": "P7 在动火区不戴头盔超过10秒，属于危险作业区违规，判定为较重等级",
  "timestamp": "2026-08-06T14:32:26"
}
```

---

## 四、文件结构

```
A6/
├── README.md                      # 本文档
├── agent/
│   ├── __init__.py
│   ├── a6_agent.py                # A6 主智能体类
│   ├── tools.py                   # 工具集（A5数据读取 + 输出记录）
│   ├── prompt_manager.py          # 提示词管理（前端页面调用）
│   └── prompts.py                 # 提示词配置文件（可编辑）
├── logs/
│   ├── a5_log_status.json         # A5 日志状态索引文件
│   ├── assessments/               # 风险研判结果
│   │   └── YYYYMMDD/
│   │       └── a6_*.json
│   └── aggregations/              # 事件聚合结果
│       └── YYYYMMDD/
│           └── agg_*.json
└── frontend/
    └── (前端页面，用于提示词编辑和状态查看)
```

---

## 五、工具清单

### Agent 工具（供 Agent 调用）

| 工具名 | 所属类 | 功能 |
|--------|--------|------|
| `check_event_status` | A5DataTools | 查询 A5 事件 ID 的处理状态（用于去重） |
| `read_raw_event_by_id` | A5DataTools | 根据事件 ID 读取原始事件数据 |
| `read_surrounding_events` | A5DataTools | 读取目标时间周围的事件（辅助上下文判断） |
| `save_assessment` | OutputTools | 保存单条风险研判结果 |
| `save_aggregation_result` | OutputTools | 保存事件聚合结果 |
| `batch_save_assessments` | OutputTools | 批量保存研判结果 |
| `update_a5_log_status` | OutputTools | 更新 A5 日志状态索引 |

### 前端工具（供前端页面调用，非 Agent）

| 工具名 | 所属类 | 功能 |
|--------|--------|------|
| `read_prompt` | PromptManager | 读取提示词内容 |
| `update_prompt` | PromptManager | 修改提示词（内存中） |
| `save_to_file` | PromptManager | 保存提示词到文件 |
| `reset_to_default` | PromptManager | 重置为默认内容 |
| `reset_to_default` | PromptManager | 重置为默认内容 |

---

## 六、待确认问题

### 6.1 数据处理相关

1. **时间窗口**: Agent 读取周围事件的时间窗口默认 5 分钟是否合适？
2. **批量处理**: 每次处理多少个 raw_event 文件合适？（影响处理延迟和内存占用）
3. **状态文件更新**: A5 日志状态文件由谁负责更新？A6 处理完成后自动更新，还是 A5 通知？

### 6.2 提示词相关

1. **提示词初始内容**: 文档中的 `prompts.py` 示例内容是否合适？
2. **修改权限**: 提示词修改后是否需要审批？还是直接生效？
3. **版本管理**: 是否需要记录提示词的修改历史？

---

## 七、后续开发计划

| 阶段 | 内容 | 产出 |
|------|------|------|
| **Phase 1** | 搭建项目结构 | `A6/` 目录、基础文件 |
| **Phase 2** | 实现 A5 日志状态管理 | `a5_log_status.json` 及读写工具 |
| **Phase 3** | 实现 Agent 输入/输出工具 | `tools.py` |
| **Phase 4** | 实现提示词管理 | `prompt_manager.py` 及前端页面 |
| **Phase 5** | 实现 A6Agent 主类 | `a6_agent.py` |
| **Phase 6** | 与 A5 集成 | 在 demo 中验证数据流 |

---

**编制人**: Claude (AI 辅助)
**等待确认**: 需求细节、各方意见
**下一步**: 与用户讨论待确认问题后开始开发
