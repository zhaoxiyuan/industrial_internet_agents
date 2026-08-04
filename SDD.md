# 软件设计说明书 (SDD)

## 边缘智能作业监测系统

---

## 1. 引言

### 1.1 目的

本文档描述边缘智能作业监测系统的软件设计，用于指导开发、测试和维护工作。系统基于工业互联网技术栈，实现作业全流程（P1-P10）的智能化监测与风险管控。

### 1.2 范围

本系统涵盖以下功能范围：
- 作业预约、JSA分析与作业票生成（P1）
- 作业任务获取与实例化（P2）
- 作业上下文理解与标准化（P3）
- 监测资源绑定与数据关联（P4）
- 作业前条件核验（P5）
- 作业过程动态监测与违章识别（P6）
- 风险研判与分级（P7）
- 人机协同处置（P8）
- 闭环跟踪与报告（P9）
- 归档与复盘优化（P10）

### 1.3 定义、缩略语

| 术语 | 定义 |
|------|------|
| JSA | Job Safety Analysis，作业安全分析 |
| PPE | Personal Protective Equipment，个人防护装备 |
| CV | Computer Vision，计算机视觉 |
| VL | Vision-Language，视觉语言模型 |
| Task ID | 作业任务唯一标识符 |
| 上下文包 | 作业相关信息的标准化封装 |

### 1.4 参考资料

- task.md - 作业流程阶段定义
- task2.md - 智能化任务设计要求

---

## 2. 总体描述

### 2.1 产品概述

边缘智能作业监测系统是一套基于云边协同架构的工业安全监测平台，通过融合视频分析、传感器监测、规则引擎和大语言模型，实现作业全过程的风险识别、预警和处置。

### 2.2 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         云端平台层                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ 任务管理  │  │ 知识库   │  │ 规则引擎  │  │ 案例库   │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
├─────────────────────────────────────────────────────────────────┤
│                         模型服务层                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ CV模型   │  │ VL模型   │  │ LLM服务  │  │ 时序分析  │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
├─────────────────────────────────────────────────────────────────┤
│                         边缘计算层                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ 视频接入  │  │ 传感器   │  │ 边缘推理  │  │ 本地存储  │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
├─────────────────────────────────────────────────────────────────┤
│                         数据接入层                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ 作业票系统│  │ 人员系统 │  │ 设备台账  │  │ 气体检测 │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
└─────────────────────────────────────────────────────────────────┘
```

### 2.3 核心功能模块

| 模块 | 职责 |
|------|------|
| `作业管理模块` | P1-P2: 作业预约、票证管理、任务获取 |
| `上下文模块` | P3: 作业上下文理解与标准化 |
| `监测绑定模块` | P4: 监测资源与作业任务关联 |
| `核验模块` | P5: 作业前条件智能核验 |
| `动态监测模块` | P6: 实时视频流分析与事件检测 |
| `风险研判模块` | P7: 多源融合与风险分级 |
| `处置模块` | P8: 人机协同处置与跟踪 |
| `归档模块` | P9-P10: 闭环归档与案例沉淀 |

---

## 3. 功能详细设计

### 3.1 P1 - 作业预约、JSA与作业票

**模块**: `PermitService`

**功能列表**:

| 功能 | 说明 |
|------|------|
| `permit_intake` | 接收作业申请，提取类型、区域、设备、人员、时间信息 |
| `jsa_analyzer` | 调用JSA分析Skill，识别危害因素和对应措施 |
| `permit_generator` | 基于模板和JSA结果生成作业票草稿 |
| `completeness_checker` | 检查票证必填字段、风险措施完整性、人员资质冲突 |

**输入**:
- 作业申请表单
- 历史JSA记录
- 作业票模板
- 人员资质信息

**输出**:
- 结构化任务数据
- JSA分析结果
- 作业票草稿（含缺失项提示）

**智能化任务设计**:

```
Skill调用链:
1. 作业类型识别Skill → 识别特种作业类型
2. 票证结构化Skill → 表单字段提取与填充
3. JSA完整性审查Skill → 危害识别完整性检查
4. 危害-措施匹配Skill → 风险与防护措施关联验证

输出约束: 仅生成草稿，人工审批后方可生效
```

**关键接口**:

```python
class PermitService:
    async def submit_permit(self, application: PermitApplication) -> PermitDraft:
        """提交作业申请，返回作业票草稿"""

    async def analyze_jsa(self, task_id: str) -> JSAResult:
        """分析JSA，返回分析结果"""

    async def generate_draft(self, task_id: str, jsa: JSAResult) -> PermitDraft:
        """生成作业票草稿，包含缺失项提示"""
```

---

### 3.2 P2 - 作业任务获取

**模块**: `TaskService`

**功能列表**:

| 功能 | 说明 |
|------|------|
| `task_poller` | 按时间、区域、状态轮询作业票系统 |
| `task_subscriber` | 订阅作业票变更事件（实时推送） |
| `task_instance_creator` | 建立唯一Task ID和上下文容器 |
| `idempotency_handler` | 重复任务幂等处理 |

**输入**:
- 作业票数据
- 审批状态变更
- 执行计划

**输出**:
- 任务实例（Task ID唯一）
- 初始状态
- 权限上下文

**智能化任务设计**:

```
Task ID生成: {作业类型}_{区域代码}_{时间戳}_{序号}
上下文容器: 初始化场景信息、权限信息和关联数据源

幂等处理: 基于Task ID去重，重复推送仅更新状态不重复创建
```

**关键接口**:

```python
class TaskService:
    async def get_pending_tasks(self, region: str, status: str) -> list[TaskInstance]:
        """获取待执行任务"""

    async def create_task_instance(self, permit: Permit) -> TaskInstance:
        """创建任务实例，确保唯一性"""

    async def subscribe_task_updates(self, callback: Callable):
        """订阅任务变更事件"""
```

---

### 3.3 P3 - 作业上下文理解

**模块**: `ContextService`

**功能列表**:

| 功能 | 说明 |
|------|------|
| `context_aggregator` | 聚合作业类型、区域、设备等11维信息 |
| `context_standardizer` | 生成标准上下文包 |
| `data_lineage_tracker` | 记录数据来源和有效时间 |
| `completeness_validator` | 检查上下文完整性 |

**上下文包结构**:

```python
class WorkContext(TypedDict):
    task_id: str                           # 任务ID
    work_type: str                         # 作业类型
    region: str                            # 作业区域
    equipment: list[str]                   # 涉及设备
    medium: str                            # 介质
    personnel: list[Personnel]              # 作业人员
    qualifications: list[str]               # 资质要求
    risks: list[RiskItem]                  # 风险清单
    measures: list[MeasureItem]            # 措施清单
    time_range: tuple[datetime, datetime]   # 作业时间
    related_tasks: list[str]                # 关联作业
    data_sources: dict[str, str]            # 数据源映射
    valid_until: datetime                  # 有效时间
```

**智能化任务设计**:

```
标准上下文包 = 作业对象 + 区域 + 设备 + 介质 + 人员 + 资质 + 风险 + 措施 + 时间 + 关联作业 + 数据源

数据溯源: 每项数据记录来源系统、获取时间、有效期
缺失确认: 上下文不完整时暂停流程，等待人工补充
```

**关键接口**:

```python
class ContextService:
    async def build_context(self, task_id: str) -> WorkContext:
        """构建标准作业上下文"""

    async def validate_context(self, ctx: WorkContext) -> ValidationResult:
        """验证上下文完整性"""

    async def get_context_history(self, task_id: str) -> list[WorkContext]:
        """获取上下文变更历史"""
```

---

### 3.4 P4 - 摄像与数据关联

**模块**: `BindingService`

**功能列表**:

| 功能 | 说明 |
|------|------|
| `camera_matcher` | 根据作业区域匹配固定/移动摄像头 |
| `sensor_matcher` | 关联传感器点位和报警数据 |
| `location_matcher` | 绑定人员定位和设备定位 |
| `resource_list_generator` | 生成监测资源清单 |
| `manual_supplement_handler` | 处理无法自动匹配的人工补充 |

**输入**:
- 作业区域坐标/范围
- 资源台账（摄像头、传感器、定位设备）

**输出**:
- 数据源绑定关系表
- 监测资源配置清单

**智能化任务设计**:

```
匹配策略:
1. 固定摄像头: 区域覆盖分析 → 选择最近且覆盖最好的N个
2. 移动设备: 根据作业流动性动态调整
3. 传感器: 选择同区域、同介质类型点位
4. 人员定位: 作业人员工卡与定位基站关联

无法匹配时: 触发人工补充流程，标注缺失资源
```

**关键接口**:

```python
class BindingService:
    async def match_resources(self, task_id: str, region: Region) -> MonitoringResources:
        """匹配监测资源"""

    async def request_manual_binding(self, task_id: str, resource_type: str):
        """发起人工绑定请求"""

    async def get_binding_status(self, task_id: str) -> BindingStatus:
        """获取绑定状态"""
```

---

### 3.5 P5 - 作业前条件核验

**模块**: `VerificationService`

**功能列表**:

| 功能 | 说明 |
|------|------|
| `checklist_generator` | 按作业类型生成核验检查清单 |
| `personnel_verifier` | 调用人员资质验证服务 |
| `video_verifier` | 接入现场视频核验措施落实 |
| `gas_detector` | 读取气体检测数据 |
| `rule_executor` | 执行核验规则引擎 |
| `evidence_collector` | 收集核验证据 |

**核验结果枚举**:

```python
class CheckResult(Enum):
    PASS = "符合"
    PENDING = "待确认"
    FAIL = "不符合"
    N/A = "不适用"
```

**输入**:
- 措施清单
- 现场视频流
- 气体检测数据
- 人员资质数据

**输出**:
- 核验结果（四态）
- 缺失项清单
- 开工建议

**智能化任务设计**:

```
检查清单生成: 根据作业类型自动生成标准化核验项
调用链路:
  - 人员资质API → 资质证书有效期、作业授权
  - 视频分析API → 隔离状态、警戒标识、消防器材
  - 气体检测API → 可燃气体、有毒气体、氧气含量
  - 规则执行引擎 → 企业标准核验规则

输出格式: {检查项: 结果, 证据: [证据列表], 建议: 开工/整改}
```

**关键接口**:

```python
class VerificationService:
    async def generate_checklist(self, task_id: str) -> list[CheckItem]:
        """生成核验检查清单"""

    async def execute_verification(self, task_id: str, checklist: list[CheckItem]) -> VerificationResult:
        """执行核验并返回结果"""

    async def get_startup_recommendation(self, task_id: str) -> StartupRecommendation:
        """获取开工建议"""
```

---

### 3.6 P6 - 作业过程动态监测

**模块**: `MonitoringService`

**功能列表**:

| 功能 | 说明 |
|------|------|
| `stream_manager` | 管理视频流订阅和分发 |
| `model_selector` | 根据上下文动态选择CV/VL模型 |
| `temporal_aggregator` | 时序聚合避免单帧误报 |
| `candidate_generator` | 生成候选风险事件 |

**输入**:
- 视频流
- 传感器数据
- 定位数据
- 监测规则

**输出**:
- 候选风险事件列表
- 证据片段

**智能化任务设计**:

```
模型动态选择:
  - 作业类型 → 选择对应CV模型（如: 高空作业选择防坠落模型）
  - 区域特征 → 选择背景模型（区分正常设备和异常）
  - 介质类型 → 选择气体检测模型

采样策略:
  - 正常状态: 1帧/秒采样
  - 异常检测: 5帧/秒+全量存储
  - 告警触发: 连续10帧异常才上报

时序聚合:
  - 单帧误报过滤: 同位置、同类型事件需连续N帧确认
  - 跨镜头追踪: 同一目标多摄像头追踪
```

**关键接口**:

```python
class MonitoringService:
    async def start_monitoring(self, task_id: str, context: WorkContext) -> MonitoringSession:
        """启动监测会话"""

    async def select_models(self, context: WorkContext) -> list[BaseModel]:
        """选择CV/VL模型"""

    async def aggregate_results(self, frames: list[FrameResult]) -> list[CandidateEvent]:
        """时序聚合，输出候选事件"""
```

---

### 3.7 P7 - 风险研判与分级

**模块**: `RiskAnalysisService`

**功能列表**:

| 功能 | 说明 |
|------|------|
| `evidence_fusion` | 多源证据融合 |
| `rule_engine` | 执行风险判定规则 |
| `case_lookup` | 查询相似历史案例 |
| `risk_grader` | 计算风险等级 |
| `event_generator` | 生成最终风险事件 |

**风险等级定义**:

```python
class RiskLevel(Enum):
    LOW = "低风险"      # 绿色
    MEDIUM = "中风险"   # 黄色
    HIGH = "高风险"     # 橙色
    CRITICAL = "重大风险" # 红色
```

**输入**:
- 候选事件
- 上下文信息
- 规则库
- 历史案例

**输出**:
- 风险事件（含等级、依据、建议）

**智能化任务设计**:

```
研判流程:
1. 证据融合: 视频证据 + 传感器数据 + 定位数据 + 规则命中
2. 规则执行: 企业风险规则库匹配
3. 案例查询: Embedding相似度搜索相似历史事件
4. 等级计算: 规则+模型+历史综合评分

输出结构:
{
  "事件": "人员进入受限空间未佩戴呼吸器",
  "事实": ["视频画面显示", "检测到人员A", "未佩戴呼吸器"],
  "证据": [{"type": "video", "timestamp": "..."}],
  "规则依据": "GBXXXX-X 5.2.3",
  "置信度": 0.95,
  "等级": "HIGH",
  "建议": "立即整改"
}
```

**关键接口**:

```python
class RiskAnalysisService:
    async def analyze_risk(self, candidate: CandidateEvent, context: WorkContext) -> RiskEvent:
        """综合研判生成风险事件"""

    async def query_similar_cases(self, event: RiskEvent) -> list[HistoricalCase]:
        """查询相似历史案例"""

    async def calculate_risk_level(self, evidence: list[Evidence], rules: list[Rule]) -> RiskLevel:
        """计算风险等级"""
```

---

### 3.8 P8 - 人机协同处置

**模块**: `DispositionService`

**功能列表**:

| 功能 | 说明 |
|------|------|
| `receiver_selector` | 根据风险等级和角色确定接收人 |
| `task_pusher` | 推送处置任务 |
| `confirmation_handler` | 处理人工确认服务 |
| `action_executor` | 执行整改/暂停/恢复操作 |

**输入**:
- 风险事件
- 处置规则
- 组织关系

**输出**:
- 处置任务
- 通知记录
- 确认记录

**人工控制点**:

| 操作类型 | 控制要求 |
|---------|---------|
| 高风险事件 | 必须人工确认 |
| 写入操作 | 必须人工确认 |
| 暂停作业 | 必须人工确认 |
| 恢复作业 | 必须人工确认 |

**智能化任务设计**:

```
接收人确定:
  风险等级 → 属地责任 → 角色权限 → 接收人

推送策略:
  - 低风险: 消息推送，24h内处理
  - 中风险: 短信+消息，4h内处理
  - 高风险: 电话+短信+消息，1h内处理
  - 重大风险: 电话直达，实时处理

人工确认服务:
  - 确认页面: 展示证据、风险、建议
  - 操作选项: 确认下发 / 整改 / 升级 / 否决
  - 记录: 确认人、时间、操作类型
```

**关键接口**:

```python
class DispositionService:
    async def create_disposition_task(self, risk_event: RiskEvent) -> DispositionTask:
        """创建处置任务"""

    async def push_notification(self, task: DispositionTask) -> NotificationResult:
        """推送通知"""

    async def request_human_confirmation(self, task: DispositionTask) -> Confirmation:
        """请求人工确认"""

    async def execute_action(self, task: DispositionTask, action: str) -> ActionResult:
        """执行处置操作"""
```

---

### 3.9 P9 - 闭环跟踪与报告

**模块**: `ClosureService`

**功能列表**:

| 功能 | 说明 |
|------|------|
| `status_tracker` | 跟踪任务接收、整改、反馈状态 |
| `feedback_collector` | 收集复核证据 |
| `completeness_checker` | 执行闭环完整性检查 |
| `report_generator` | 生成作业过程报告 |

**输入**:
- 处置反馈
- 复核证据
- 任务日志

**输出**:
- 闭环状态
- 作业过程报告

**智能化任务设计**:

```
跟踪流程:
  任务下发 → 接收确认 → 整改执行 → 反馈提交 → 复核确认 → 闭环

完整性检查项:
  - 处置记录完整性
  - 证据材料齐全性
  - 复核签字有效性
  - 时间线连续性

报告生成:
  - 作业基本信息
  - 核验结果汇总
  - 监测事件时间线
  - 风险处置记录
  - 证据索引
```

**关键接口**:

```python
class ClosureService:
    async def track_status(self, task_id: str) -> ClosureStatus:
        """跟踪闭环状态"""

    async def verify_completeness(self, task_id: str) -> CompletenessResult:
        """执行闭环完整性检查"""

    async def generate_report(self, task_id: str) -> WorkReport:
        """生成作业过程报告"""
```

---

### 3.10 P10 - 归档与复盘

**模块**: `ArchiveService`

**功能列表**:

| 功能 | 说明 |
|------|------|
| `data_archiver` | 归档全过程记录 |
| `case_miner` | 挖掘误报、漏报、规则冲突案例 |
| `performance_analyzer` | 分析处置效果 |
| `knowledge_updater` | 更新知识规则 |

**输入**:
- 全过程数据

**输出**:
- 作业档案
- 案例库更新
- 规则优化建议

**智能化任务设计**:

```
归档内容:
  - 作业票证（结构化+扫描件）
  - 视频证据片段
  - 风险事件记录
  - 处置全记录
  - 作业报告

案例挖掘:
  - 误报案例: 检测模型误报，附修正标注
  - 漏报案例: 人工发现的风险事件，补充规则
  - 规则冲突: 同场景不同规则的冲突点

知识沉淀:
  - 案例摘要Embedding → 向量数据库
  - 规则冲突报告 → 规则管理系统
  - 模型优化建议 → 模型训练平台
```

**关键接口**:

```python
class ArchiveService:
    async def archive_task(self, task_id: str) -> ArchiveResult:
        """归档任务数据"""

    async def mine_cases(self, task_id: str) -> list[Case]:
        """挖掘案例"""

    async def analyze_performance(self, task_id: str) -> PerformanceMetrics:
        """分析处置效果"""

    async def suggest_rule_updates(self) -> list[RuleSuggestion]:
        """生成规则优化建议"""
```

---

## 4. 数据设计

### 4.1 核心数据模型

```python
# 作业任务
class WorkTask:
    task_id: str              # 唯一标识
    permit_id: str            # 作业票ID
    work_type: str            # 作业类型
    region: str               # 作业区域
    status: TaskStatus        # 状态
    context: WorkContext      # 上下文
    resources: MonitoringResources  # 监测资源
    created_at: datetime
    updated_at: datetime

# 风险事件
class RiskEvent:
    event_id: str
    task_id: str
    level: RiskLevel
    description: str
    evidence: list[Evidence]
    rule_bases: list[str]
    confidence: float
    suggestion: str
    status: EventStatus
    created_at: datetime

# 处置任务
class DispositionTask:
    task_id: str
    risk_event_id: str
    assignee: str
    action_type: str
    status: DispositionStatus
    confirmation: Confirmation
    completed_at: datetime
```

### 4.2 数据存储策略

| 数据类型 | 存储方案 | 保留周期 |
|---------|---------|---------|
| 作业票 | PostgreSQL | 永久 |
| 视频流 | 对象存储(OSS) | 30天 |
| 风险事件 | TimescaleDB | 1年 |
| 处置记录 | PostgreSQL | 永久 |
| 案例库 | VectorDB + PostgreSQL | 永久 |
| 规则库 | PostgreSQL + Redis缓存 | 永久 |

---

## 5. 接口设计

### 5.1 外部系统接口

| 系统 | 接口类型 | 协议 |
|------|---------|------|
| 作业票系统 | REST API | HTTPS |
| 人员系统 | RPC | gRPC |
| 设备台账 | REST API | HTTPS |
| 视频平台 | RTSP/GB28181 | RTP |
| 传感器平台 | MQTT | TCP |
| 消息通知 | HTTP Webhook | HTTPS |

### 5.2 内部服务接口

```
PermitService ←→ TaskService ←→ ContextService
                                      ↓
BindingService → VerificationService → MonitoringService
                                              ↓
                                      RiskAnalysisService
                                              ↓
                                      DispositionService
                                              ↓
                                      ClosureService
                                              ↓
                                      ArchiveService
```

---

## 6. 安全要求

### 6.1 认证授权

- 外部API调用使用OAuth2.0认证
- 内部服务使用JWT Token
- 敏感操作需二次验证

### 6.2 数据安全

- 视频流传输使用SRTP加密
- 敏感数据存储使用AES-256
- 个人隐私数据脱敏处理

### 6.3 审计日志

- 所有关键操作记录审计日志
- 日志保留不少于180天

---

## 7. 部署架构

```
┌─────────────────────────────────────────────────────────────┐
│                      Kubernetes Cluster                      │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │ API Gateway │  │  Auth Svc   │  │  Notify Svc │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Edge Computing Node (KubeEdge)          │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐         │    │
│  │  │ Video    │  │ Inference │  │ Local    │         │    │
│  │  │ Agent    │  │ Runtime   │  │ Storage  │         │    │
│  │  └──────────┘  └──────────┘  └──────────┘         │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

---

## 8. 附录

### 8.1 人工控制点汇总

| 阶段 | 控制点 | 要求 |
|------|--------|------|
| P1 | 作业票提交和审批 | 人工审批 |
| P2 | 是否纳入智能监测 | 人工选择 |
| P3 | 上下文缺失确认 | 人工补充 |
| P4 | 资源绑定确认 | 人工确认 |
| P5 | 允许开工或整改 | 人工决策 |
| P7 | 高风险判断确认 | 人工确认 |
| P8 | 下发、暂停、恢复 | 人工操作 |
| P9 | 关闭事件和作业 | 人工关闭 |
| P10 | 档案确认、规则发布 | 人工确认 |

### 8.2 智能化程度分级

| 等级 | 说明 | 适用场景 |
|------|------|---------|
| L1 | 人工操作，系统记录 | 特殊作业 |
| L2 | 系统辅助，人工决策 | 高风险作业 |
| L3 | 系统推荐，人工确认 | 常规作业 |
| L4 | 自动执行，事后报告 | 低风险作业 |
| L5 | 全自动，异常才干预 | 标准化作业 |