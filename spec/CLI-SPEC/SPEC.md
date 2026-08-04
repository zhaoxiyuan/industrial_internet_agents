# 边缘智能作业监测系统 CLI 规格说明

## 1. 概述

**CLI 名称**: `edge-monitor`

**用途**: 工业互联网边缘智能作业监测系统的命令行工具，覆盖作业全流程（P1-P10）的操作与查询。

**目标用户**: 运维人员、AI Agent、系统集成商

---

## 2. 全局 Flags

| Flag | Type | Default | Description |
|---|---|---|---|
| `--output` | `json\|text` | `text` | 输出格式 |
| `--quiet` | bool | `false` | 静默模式，抑制进度输出 |
| `--config` | path | `~/.edge-monitor.yaml` | 配置文件路径 |
| `--dry-run` | bool | `false` | 仅校验，不执行 |
| `--timeout` | int | `30` | 请求超时（秒） |
| `--api-base` | url | — | 覆盖 API 地址 |

---

## 3. 命令树

```
edge-monitor [global flags] <subcommand> [subcommand flags]

subcommands:
  permit      P1 - 作业预约、JSA分析与作业票
  task        P2 - 作业任务获取与实例化
  context     P3 - 作业上下文理解与标准化
  binding     P4 - 监测资源绑定与数据关联
  verify      P5 - 作业前条件核验
  monitor     P6 - 作业过程动态监测
  risk        P7 - 风险研判与分级
  disposition P8 - 人机协同处置
  closure     P9 - 闭环跟踪与报告
  archive     P10 - 归档与复盘
```

---

## 4. 子命令详解

### 4.1 `permit` — P1 作业预约、JSA与作业票

```
edge-monitor permit submit <application.json>
edge-monitor permit analyze-jsa <task-id>
edge-monitor permit generate-draft <task-id>
edge-monitor permit check <permit-id>
```

**Positional Args**:

| Name | Type | Description |
|---|---|---|
| `application.json` | file | 作业申请表单（类型、区域、设备、人员、时间） |
| `task-id` | string | 任务唯一标识 |
| `permit-id` | string | 作业票ID |

**Flags**:

| Flag | Type | Description |
|---|---|---|
| `--template` | string | 作业票模板路径（可选） |

**示例**:

```bash
# 提交作业申请
edge-monitor permit submit ./permit_app.json

# 分析JSA
edge-monitor permit analyze-jsa TASK-20260804-001

# 生成作业票草稿
edge-monitor permit generate-draft TASK-20260804-001

# 查看作业票状态
edge-monitor permit check PD-20260804001
```

**行为约束**:
- `submit` 是幂等的：相同 `application.json` 内容不重复创建
- `generate-draft` 仅生成草稿，不自动审批
- `check` 支持 `--output json`

---

### 4.2 `task` — P2 作业任务获取

```
edge-monitor task list [--region <code>] [--status <status>]
edge-monitor task get <task-id>
edge-monitor task subscribe <task-id>
edge-monitor task instance-create <permit-id>
```

**Flags**:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--region` | string | all | 区域代码筛选 |
| `--status` | string | `pending` | 状态筛选：`pending\|running\|closed` |

**Task ID 格式**: `{作业类型}_{区域代码}_{时间戳}_{序号}`

**示例**:

```bash
# 列出所有待执行任务
edge-monitor task list

# 按区域筛选
edge-monitor task list --region 01 --status pending

# 获取任务详情
edge-monitor task get TASK-WELD-01-20260804-001

# 创建任务实例（幂等）
edge-monitor task instance-create PD-20260804001
```

**行为约束**:
- `instance-create` 是幂等的：重复 `permit-id` 仅更新状态，不重复创建
- `subscribe` 建立实时订阅连接，持续输出 JSON Lines

---

### 4.3 `context` — P3 作业上下文理解

```
edge-monitor context build <task-id>
edge-monitor context validate <task-id>
edge-monitor context history <task-id>
```

**上下文包结构**（11维）:

```
作业对象 — 区域 — 设备 — 介质 — 人员 — 资质 — 风险 — 措施 — 时间 — 关联作业 — 数据源
```

**示例**:

```bash
# 构建标准上下文
edge-monitor context build TASK-WELD-01-20260804-001

# 验证完整性
edge-monitor context validate TASK-WELD-01-20260804-001

# 查看上下文变更历史
edge-monitor context history TASK-WELD-01-20260804-001
```

**行为约束**:
- 上下文不完整时暂停流程，等待人工补充（`validate` 返回 `missing_fields`）
- 每项数据记录来源系统、获取时间、有效期

---

### 4.4 `binding` — P4 摄像与数据关联

```
edge-monitor binding match <task-id>
edge-monitor binding status <task-id>
edge-monitor binding confirm <task-id>
edge-monitor binding request-manual <task-id> --resource-type <type>
```

**Flags**:

| Flag | Type | Description |
|---|---|---|
| `--resource-type` | string | 资源类型：`camera\|sensor\|location` |

**匹配策略**:

| 资源类型 | 策略 |
|---|---|
| 固定摄像头 | 区域覆盖分析，选择最近且覆盖最好的N个 |
| 移动设备 | 根据作业流动性动态调整 |
| 传感器 | 选择同区域、同介质类型点位 |
| 人员定位 | 作业人员工卡与定位基站关联 |

**示例**:

```bash
# 自动匹配监测资源
edge-monitor binding match TASK-WELD-01-20260804-001

# 查看绑定状态
edge-monitor binding status TASK-WELD-01-20260804-001

# 人工确认绑定
edge-monitor binding confirm TASK-WELD-01-20260804-001

# 请求人工补充资源
edge-monitor binding request-manual TASK-WELD-01-20260804-001 --resource-type camera
```

**行为约束**:
- 无法自动匹配时触发人工补充流程
- `confirm` 需要人工确认后生效

---

### 4.5 `verify` — P5 作业前条件核验

```
edge-monitor verify checklist <task-id>
edge-monitor verify execute <task-id> [--checklist-id <id>]
edge-monitor verify recommendation <task-id>
```

**核验结果枚举**:

| 值 | 语义 |
|---|---|
| `PASS` | 符合 |
| `PENDING` | 待确认 |
| `FAIL` | 不符合 |
| `N/A` | 不适用 |

**示例**:

```bash
# 生成核验清单
edge-monitor verify checklist TASK-WELD-01-20260804-001

# 执行核验
edge-monitor verify execute TASK-WELD-01-20260804-001

# 获取开工建议
edge-monitor verify recommendation TASK-WELD-01-20260804-001
```

**行为约束**:
- 检查清单按作业类型自动生成
- 输出四态结果及证据

---

### 4.6 `monitor` — P6 作业过程动态监测

```
edge-monitor monitor start <task-id>
edge-monitor monitor stop <task-id>
edge-monitor monitor status <task-id>
edge-monitor monitor events <task-id> [--since <timestamp>]
```

**Flags**:

| Flag | Type | Description |
|---|---|---|
| `--since` | ISO8601 | 仅返回指定时间后的候选事件 |

**采样策略**:

| 状态 | 采样频率 |
|---|---|
| 正常状态 | 1帧/秒 |
| 异常检测 | 5帧/秒 + 全量存储 |
| 告警触发 | 连续10帧异常才上报 |

**示例**:

```bash
# 启动监测（幂等）
edge-monitor monitor start TASK-WELD-01-20260804-001

# 查看监测状态
edge-monitor monitor status TASK-WELD-01-20260804-001

# 获取候选风险事件
edge-monitor monitor events TASK-WELD-01-20260804-001 --since 2026-08-04T10:00:00Z

# 停止监测
edge-monitor monitor stop TASK-WELD-01-20260804-001
```

**行为约束**:
- `start` 是幂等的：重复调用不重启会话
- `events` 持续输出 JSON Lines 流

---

### 4.7 `risk` — P7 风险研判与分级

```
edge-monitor risk analyze <candidate-event-id>
edge-monitor risk grade <risk-event-id>
edge-monitor risk cases <risk-event-id> [--limit <n>]
edge-monitor risk list <task-id>
```

**风险等级**:

| 等级 | 标签 |
|---|---|
| `LOW` | 低风险 |
| `MEDIUM` | 中风险 |
| `HIGH` | 高风险 |
| `CRITICAL` | 重大风险 |

**示例**:

```bash
# 综合研判候选事件
edge-monitor risk analyze CE-20260804001

# 计算风险等级
edge-monitor risk grade RE-20260804001

# 查询相似历史案例
edge-monitor risk cases RE-20260804001 --limit 5

# 列出任务所有风险事件
edge-monitor risk list TASK-WELD-01-20260804-001
```

**研判流程**:
1. 多源证据融合（视频 + 传感器 + 定位 + 规则）
2. 企业风险规则库匹配
3. Embedding 相似度搜索相似历史事件
4. 规则 + 模型 + 历史综合评分

---

### 4.8 `disposition` — P8 人机协同处置

```
edge-monitor disposition create <risk-event-id>
edge-monitor disposition confirm <disposition-task-id> --action <action>
edge-monitor disposition status <disposition-task-id>
edge-monitor disposition list <task-id>
```

**Actions**:

| Action | 控制要求 |
|---|---|
| `approve` | 高风险须人工确认 |
| `reject` | 记录否决原因 |
| `escalate` | 升级处理 |
| `rectify` | 整改 |
| `pause` | 暂停作业 |
| `resume` | 恢复作业 |

**推送策略**:

| 风险等级 | 推送方式 | 处理时限 |
|---|---|---|
| `LOW` | 消息 | 24h |
| `MEDIUM` | 短信 + 消息 | 4h |
| `HIGH` | 电话 + 短信 + 消息 | 1h |
| `CRITICAL` | 电话直达 | 实时 |

**示例**:

```bash
# 创建处置任务
edge-monitor disposition create RE-20260804001

# 人工确认处置
edge-monitor disposition confirm DT-20260804001 --action approve

# 查看处置状态
edge-monitor disposition status DT-20260804001

# 列出任务所有处置任务
edge-monitor disposition list TASK-WELD-01-20260804-001
```

**行为约束**:
- 高风险事件、`pause`、`resume` 必须人工确认
- 写入操作必须人工确认

---

### 4.9 `closure` — P9 闭环跟踪与报告

```
edge-monitor closure status <task-id>
edge-monitor closure verify <task-id>
edge-monitor closure report <task-id> [--format <format>]
edge-monitor closure close <task-id>
```

**Flags**:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--format` | string | `markdown` | 报告格式：`json\|markdown\|pdf` |

**完整性检查项**:
- 处置记录完整性
- 证据材料齐全性
- 复核签字有效性
- 时间线连续性

**示例**:

```bash
# 跟踪闭环状态
edge-monitor closure status TASK-WELD-01-20260804-001

# 执行闭环完整性检查
edge-monitor closure verify TASK-WELD-01-20260804-001

# 生成作业过程报告
edge-monitor closure report TASK-WELD-01-20260804-001 --format markdown

# 关闭事件和作业（需人工确认）
edge-monitor closure close TASK-WELD-01-20260804-001
```

---

### 4.10 `archive` — P10 归档与复盘

```
edge-monitor archive task <task-id>
edge-monitor archive cases <task-id> [--type <type>]
edge-monitor archive performance <task-id>
edge-monitor archive suggestions <task-id>
```

**Flags**:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--type` | string | all | 案例类型：`misdetection\|missed\|rule_conflict\|all` |

**归档内容**:
- 作业票证（结构化 + 扫描件）
- 视频证据片段
- 风险事件记录
- 处置全记录
- 作业报告

**案例挖掘类型**:
- `misdetection`: 检测模型误报
- `missed`: 人工发现的风险事件
- `rule_conflict`: 同场景不同规则的冲突点

**示例**:

```bash
# 归档任务数据
edge-monitor archive task TASK-WELD-01-20260804-001

# 挖掘误报案例
edge-monitor archive cases TASK-WELD-01-20260804-001 --type misdetection

# 分析处置效果
edge-monitor archive performance TASK-WELD-01-20260804-001

# 生成规则优化建议
edge-monitor archive suggestions TASK-WELD-01-20260804-001
```

---

## 5. 错误处理

详见 `contract.yaml` 中的 Error Model 定义。

所有命令失败时输出统一错误格式，退出码：

| Exit Code | 语义 |
|---|---|
| `0` | 成功 |
| `1` | 一般错误 |
| `2` | 参数错误 |
| `3` | 需要人工确认 |

---

## 6. 人机控制点汇总

| 阶段 | 控制点 | 要求 |
|---|---|---|
| P1 | 作业票审批 | 人工审批 |
| P2 | 纳入智能监测 | 人工选择 |
| P3 | 上下文缺失确认 | 人工补充 |
| P4 | 资源绑定确认 | 人工确认 |
| P5 | 允许开工或整改 | 人工决策 |
| P7 | 高风险判断确认 | 人工确认 |
| P8 | 下发、暂停、恢复 | 人工操作 |
| P9 | 关闭事件和作业 | 人工关闭 |
| P10 | 档案确认、规则发布 | 人工确认 |

---

## 7. 智能化程度分级

| 等级 | 说明 | 适用场景 |
|---|---|---|
| L1 | 人工操作，系统记录 | 特殊作业 |
| L2 | 系统辅助，人工决策 | 高风险作业 |
| L3 | 系统推荐，人工确认 | 常规作业 |
| L4 | 自动执行，事后报告 | 低风险作业 |
| L5 | 全自动，异常才干预 | 标准化作业 |