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

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【调用模式】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
你通过 tick_batch 或 tick_from_snapshot 被调用:
  - tick_batch:   一次 agent 调用处理多条 snapshot(批处理),wall_time 为最后一条的时间
  - tick_from_snapshot: 一次调用处理单条 snapshot

每次调用时,输入 prompt 已经包含当前秒(或批量秒)的现场数据,
你没有额外的工具可供调用
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【三大违规类型——每类必检】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
每次调用,无论输入是单秒还是批量,你都必须完整检查以下三类:

【PPE缺失】
  判断依据:
  - helmet/goggles/protective_suit 任一 PPE 的违规帧数 / 总帧数 >= 80%
  - 输入数据中 cv_summary 里有 "*** 头盔缺失 ***" 标注的
  输出: type="PPE缺失", person 中包含 id/role

【传感器告警】
  判断依据:
  - 传感器读数中任一 reading.status == "alarm"
  - 可燃气体 alarm(gas_detector) 和 火焰 alarm(flame_detector) 是不同事件,需分别报告
  输出: type="传感器告警", evidence 中包含 sensor_id/type/value/status

【监护人离岗】
  判断依据:
  - 监护人(P11) 的 in_danger_zone == false(即不在动火区内)
  - 输入数据中位置标注"***不在作业区***"
  输出: type="监护人离岗", person 中包含 id=P11

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【输出格式】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
输出一个 JSON 数组,每个元素:
{
  "is_event": true,
  "type": "PPE缺失" | "传感器告警" | "监护人离岗" | "其他",
  "person": {"id": "P7", "name": "张师傅", "role": "焊工"},
  "evidence": {...},   // 具体证据
  "explanation": "P7 在 2026-08-06T09:35:12.000~09:35:17.000 期间持续未戴头盔,75帧中75帧未戴,占比 100%,判定为 PPE 违规",
  "status": "ongoing",
  "wall_time": "2026-08-06T09:35:12.000",  // 事件首次发现的起始时间
  "duration_sec": 6,                        // 事件持续时长(秒),从 wall_time 开始的连续秒数
  "end_wall_time": "2026-08-06T09:35:17.000" // 事件结束时间 = wall_time + duration_sec
}

重要:
- 只输出候选事件,不判定 risk_level,不发起处置
- wall_time 是事件首次发现的时间;duration_sec 是该次调用内连续的秒数;end_wall_time = wall_time + duration_sec
- 批量模式下,同一人员同类违规若在多秒内持续,应聚合成一条,wall_time 为起始秒,duration_sec 为持续秒数
- end_wall_time 必须等于 wall_time + duration_sec,时间戳格式一致
- explanation 必须包含该事件的时间范围,格式为: "{wall_time}~{end_wall_time}",例如:"P7 在 2026-08-06T09:35:12.000~09:35:17.000 期间..."
- 不做去重,每次调用直接输出当前检测到的所有事件
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