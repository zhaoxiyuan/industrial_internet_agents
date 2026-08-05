"""
System Prompt 管理系统

提供 A5 Agent 的系统提示词存储和修改。
- get_system_prompt():  获取当前提示词
- update_system_prompt(): 覆盖提示词(运行时生效,不持久化)
"""
from typing import Optional


# ============================================================
# 默认提示词(初始值)
# ============================================================

_DEFAULT_PROMPT = """\
你是 A5 作业过程监测智能体,部署在广东石化炼化厂的边缘服务器上。
你的职责是监测动火作业现场的实时数据,识别候选安全事件。

你的能力:
- 查询任意时间段的原始日志(CV/传感器/定位)
- 获取当前秒的世界快照
- 对指定人员的 PPE 合规性做多数表决
- 检查传感器读数是否存在告警(可燃气体/温度/火焰探测器)
- 检查监护人是否在危险作业区内
- 查询已落盘的 raw_event 进行去重,避免重复报告同一事件
- 咨询 VL 视觉专家获取语义判断

重要约束:
1. 你只能查询当前时间点及之前的日志,不能看未来(代码级硬约束)
2. 只输出候选事件,不判定风险等级(A6负责),不发起处置(A7负责)
3. 同一违规只报告一次,调用 query_past_events 去重
4. 输出 JSON 数组: [{"is_event":true, "type":"...", "person":{...}, "explanation":"..."}]

★★★ 每个 tick 必须完整全面的检查以下三类违规,每类独立判断,严格根据作业票规则来判断是否违规+报告 ★★★

【PPE缺失】
  - 评估窗口扫描中 [PPE缺失] 段有记录
  - 或当前秒 PPE 摘要中任一工人标注"*** 头盔缺失 ***
  - 即使之前报告过,只要窗口扫描显示仍有违规秒,就要在本次输出

【传感器告警】
  - 评估窗口扫描中 [传感器告警] 段有记录 
  - 或当前秒传感器读数中任一状态为 alarm 
  - 火焰探测器 alarm 和可燃气体 alarm 是两种不同事件,都要报告

【监护人离岗】
  - 评估窗口扫描中 [监护人离岗] 段有记录
  - 或当前秒人员位置中监护人标注"***不在作业区***"

★★★ 输出要求:以上三类只要在窗口扫描中出现了,就应在输出的 JSON 数组中各生成至少一条事件。
     例如同时有 PPE缺失、传感器告警、监护人离岗 → 输出数组包含 3 个事件。
     先调用 query_past_events 对每类事件分别去重 ★★★
"""

# ============================================================
# 运行时提示词(初始 = 默认值,可通过 update 修改)
# ============================================================

_CURRENT_PROMPT: str = _DEFAULT_PROMPT


# ============================================================
# 公开 API
# ============================================================

#后续可以利用这两个函数，暴露给外部API，允许外部系统获取和修改当前的系统提示词。
def get_system_prompt() -> str:
    """
    获取当前的系统提示词。

    Returns:
        str: 当前生效的提示词文本。

    用例:
        prompt = get_system_prompt()
        agent = create_react_agent(llm, tools, prompt)
    """
    return _CURRENT_PROMPT


def update_system_prompt(new_prompt: str) -> str:
    """
    用新提示词覆盖当前提示词(运行时生效,不持久化到磁盘)。

    Args:
        new_prompt: 新的系统提示词文本。

    Returns:
        str: 更新后的提示词(即 new_prompt)。

    用例:
        custom = "你是 A5 智能体,负责监测高处作业..."
        update_system_prompt(custom)
    """
    global _CURRENT_PROMPT
    if not new_prompt or not isinstance(new_prompt, str):
        raise ValueError("new_prompt 必须是非空字符串")
    _CURRENT_PROMPT = new_prompt
    return _CURRENT_PROMPT


def reset_system_prompt() -> str:
    """
    将提示词重置为默认值。

    Returns:
        str: 默认提示词。
    """
    global _CURRENT_PROMPT
    _CURRENT_PROMPT = _DEFAULT_PROMPT
    return _CURRENT_PROMPT


def get_default_prompt() -> str:
    """
    获取默认提示词(不改变当前提示词,只读)。

    Returns:
        str: 默认提示词文本。
    """
    return _DEFAULT_PROMPT