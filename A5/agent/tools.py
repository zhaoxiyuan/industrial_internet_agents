"""
A5 agent的 LangChain 工具集

工具清单(全部用 @tool 声明):
  1. query_raw_logs         按 wall_time 范围查询原始日志
  2. get_current_snapshot   按 wall_time 取世界快照
  3. analyze_ppe_compliance 1 秒内多数表决
  4. call_vl_expert         调 VL 专家(占位,Mock 实现)

时间约定:所有时间参数使用现实世界时间(ISO 格式)。
        现实 1 秒 = 场景 1 秒(实时 1:1),wall_time ↔ scenario_time 直接转换。

调用方式:
  from langchain_core.tools import BaseTool
  tools_list = [query_raw_logs, get_current_snapshot, ...]
"""
from typing import Optional, List, Dict, Any
from langchain_core.tools import tool

# ============================================================
# 占位:真实的 collector/vl 通过 set_dependencies() 注入
# ============================================================

_collector = None
_vl_model = None
_work_permit = None


def set_dependencies(collector=None, vl_model=None, work_permit=None):
    """由 agent 在 __init__ 时注入依赖(避免循环引用)"""
    global _collector, _vl_model, _work_permit
    if collector is not None:
        _collector = collector
    if vl_model is not None:
        _vl_model = vl_model
    if work_permit is not None:
        _work_permit = work_permit


# ============================================================
# 工具 1:query_raw_logs
# ============================================================

@tool
def query_raw_logs(
    start_wall: str,
    end_wall: str,
    source_type: Optional[str] = None,
    person_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    通过时间查询没做任何修改的原始日志
    Args:
        start_wall:  起始时间(ISO 格式,包含),如 "2026-08-04T14:32:08.000"
        end_wall:    结束时间(ISO 格式,不包含),如 "2026-08-04T14:32:13.000"
        source_type: 日志类型过滤,"cv"/"sensor"/"position"/None(全部)
        person_id:   人员 ID 过滤(仅 CV 有效),如 "P7"

    Returns:
        List[Dict],每条 dict 结构因 source_type 而异:

        当 source_type=="cv":
        [
          {
            "frame_id":      "cam_03_8.040",
            "scenario_time": "8.040s",                  # 场景内时间(辅助)
            "wall_time":     "2026-08-04T14:32:08.040",  # 现实时间(主要时间维度)
            "person_id":     "P7",
            "detections": [
              {"class_name": "helmet",          "value": false, "confidence": 0.91},
              {"class_name": "goggles",         "value": true,  "confidence": 0.88},
              {"class_name": "protective_suit", "value": true,  "confidence": 0.93},
            ]
          },
          ...
        ]

        当 source_type=="sensor":
        [
          {
            "scenario_time": "12.5s",
            "wall_time":     "2026-08-04T14:32:12.500",
            "readings": {
              "gas_detector_03": {
                "type": "可燃气体浓度", "value": 0.7, "unit": "%LEL",
                "status": "warning", "threshold_warning": 0.7, "threshold_alarm": 1.0,
                "location": "动火区-A 东侧 3 米"
              },
              "temperature_03":   {"type": "环境温度", "value": 26.0, "unit": "°C", "status": "normal", ...},
              "flame_detector_03":{"type": "火焰探测", "value": false, "unit": "bool", "status": "normal", ...}
            }
          },
          ...
        ]

        当 source_type=="position":
        [
          {
            "scenario_time": "15.5s",
            "wall_time":     "2026-08-04T14:32:15.500",
            "positions": {
              "P7":  {"position": [102.45, 88.30], "area_id": "动火区-A",
                      "speed": 0.0, "is_in_danger_zone": true},
              ...
            }
          },
          ...
        ]

    Empty cases:
      - 无匹配:返回 []
      - collector 未注入:返回 [{"error": "collector 未注入"}]

    用例:
        - "P7 在 8-13 秒做了什么?" → query_raw_logs("2026-08-04T14:32:08", "2026-08-04T14:32:13", "cv", "P7")
        - "气体从何时起 alarm?" → query_raw_logs("...T14:32:00", "...T14:32:30", "sensor")
        - "P11 何时离开动火区?" → query_raw_logs("...T14:32:00", "...T14:32:30", "position", "P11")
    """
    if _collector is None:
        return [{"error": "collector 未注入"}]
    return _collector.query_raw_logs(start_wall, end_wall, source_type, person_id)


# ============================================================
# 工具 2:get_current_snapshot
# ============================================================

@tool
def get_current_snapshot(wall_time: str) -> Dict[str, Any]:
    """
    根据现实世界时间获取对应秒的世界快照,含 CV/传感器/定位三类数据。

    Args:
        wall_time: ISO 格式的现实时间,如 "2026-08-04T14:32:12.000"
                  系统会自动转换为对应的场景秒。

    Returns:
        {
          "wall_time":     "2026-08-04T14:32:12.000",   # 入参(原样返回)
          "scenario_time": "12.0s",                     # 转换后的场景秒
          "cv_logs":       [<CV 日志 × 75 条>],            # 该秒所有 CV 日志
          "sensors":       [<sensor 读数 × 1 条>],
          "positions":     [<position 读数 × 1 条>]
        }

        字段长度约定:
          cv_logs:    list,长度 = FPS(25) × 作业人数(3) = 75
          sensors:    list,长度 = 1(每秒 1 条)
          positions:  list,长度 = 1(每秒 1 条)

    Empty case:
      - collector 未注入:返回 {"error": "collector 未注入"}
    """
    if _collector is None:
        return {"error": "collector 未注入"}
    return _collector.get_snapshot(wall_time)


# ============================================================
# 工具 3:analyze_ppe_compliance
# ============================================================

@tool
def analyze_ppe_compliance(
    cv_logs: List[Dict[str, Any]],
    person_id: str,
    threshold: float = 0.8,
    required_ppe: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    在 1 秒的 CV 日志内对某人的 PPE 合规性做多数表决。

    Args:
        cv_logs: 1 秒内该人的全部 CV 日志(通常 25 条)
        person_id: 目标人员 ID
        threshold: 违规帧占比阈值,默认 0.8(80%)

    Returns:
        正常情况(有 cv_logs):
        {
          "person_id":         "P7",
          "total_frames":       25,            # 该人此秒的总帧数
          "helmet_violation":   22,            # 没戴头盔的帧数
          "helmet_ratio":       0.88,          # 22/25
          "goggles_violation":  0,             # 护目镜违规帧数
          "suit_violation":     0,             # 防护服违规帧数
          "is_violating":       True,          # 任一 PPE ratio >= threshold
          "violations":         ["helmet"]     # 违规的 PPE 类别列表
        }

        空情况(无 cv_logs):
        {
          "person_id":         "P7",
          "total_frames":       0,
          "helmet_violation":   0,
          "helmet_ratio":       0.0,
          "goggles_violation":  0,
          "suit_violation":     0,
          "is_violating":       False,
          "violations":         []
        }

        阈值默认 0.8 = 25 帧中至少 20 帧违规才算 violation。
        agent可在调用时覆盖 threshold(如想更严格可设 0.9)。

        注:此工具只做"看图说话"的事实聚合,不判断"算不算违规"
        —— 算不算违规由 A5 agent综合其他证据后决定。
    """
    person_logs = [l for l in cv_logs if l.get("person_id") == person_id]
    total = len(person_logs)

    # 默认检查全部 PPE;若指定 required_ppe,只检查其中
    ppe_to_check = required_ppe or ["helmet", "goggles", "protective_suit"]

    if total == 0:
        return {
            "person_id":        person_id,
            "total_frames":      0,
            "helmet_violation":  0,
            "helmet_ratio":      0.0,
            "goggles_violation": 0,
            "suit_violation":    0,
            "is_violating":      False,
            "violations":        [],
        }

    violations = []

    def count_missing(ppe_name: str):
        return sum(
            1 for l in person_logs
            if not next(
                (d["value"] for d in l.get("detections", [])
                 if d["class_name"] == ppe_name),
                True
            )
        )

    n_helmet = count_missing("helmet")
    n_goggles = count_missing("goggles")
    n_suit = count_missing("protective_suit")

    # 只检查 required_ppe 中的项目
    if "helmet" in ppe_to_check and n_helmet / total >= threshold:
        violations.append("helmet")
    if "goggles" in ppe_to_check and n_goggles / total >= threshold:
        violations.append("goggles")
    if "protective_suit" in ppe_to_check and n_suit / total >= threshold:
        violations.append("suit")

    return {
        "person_id":        person_id,
        "total_frames":      total,
        "helmet_violation":  n_helmet,
        "helmet_ratio":      n_helmet / total,
        "goggles_violation": n_goggles,
        "suit_violation":    n_suit,
        "is_violating":      len(violations) > 0,
        "violations":        violations,
    }


# ============================================================
# 工具 4:call_vl_expert(占位)
# ============================================================

@tool
def call_vl_expert(
    question: str,
    history: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    咨询 VL 视觉语言专家,获取对当前画面/上下文的语义判断。

    Args:
        question: A5 agent的提问(自然语言),如 "P7 当前 PPE 状态如何?"
        history:  CV 检测的统计摘要,如
                  {"helmet_ratio": 0.88, "frame_count": 22, "violations": ["helmet"]}
        context:  上下文信息,如
                  {"person_id": "P7",
                   "work_type": "动火",
                   "in_danger_zone": True,
                   "is_supervisor": False}

    Returns:
        成功(VL 可用):
        {
          "is_violation":   True,                  # VL 判定是否违规
          "violation_type": "PPE缺失-头盔",       # 违规类型字符串
          "evidence_text":  "工人 P7 处于动火作业点附近,未佩戴安全帽...",
          "confidence":     0.93,                 # VL 置信度 0~1
          "reasoning":      "动火作业中未佩戴安全帽是严重违章..."
        }

        占位(VL 不可用,_placeholder=True,agent降级处理):
        {
          "is_violation":   null,
          "violation_type": "unknown",
          "evidence_text":  "VL 模型未注入,agent需基于其他证据判断",
          "confidence":     0.0,
          "reasoning":      "VL 调用失败或不可用",
          "_placeholder":   true
        }

        可选字段(Demo 中可能返回):
        {
          "suggested_action": "立即提醒佩戴安全帽"  # VL 给出的处置建议(仅参考,A7 才会真用)
        }

    状态:
      - Demo 阶段:_vl_model 是 MockVLModel,基于 context 返回预定义响应
      - 未来:接入 Qwen-VL/GPT-4V 等真实视觉语言模型
    """
    if _vl_model is None:
        return {
            "is_violation": None,
            "violation_type": "unknown",
            "evidence_text": "VL 模型未注入,agent需基于其他证据判断",
            "confidence": 0.0,
            "reasoning": "VL 调用失败或不可用",
            "_placeholder": True,
        }
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(
        _vl_model.interpret(question=question, history=history, context=context)
    )


# ============================================================
# 工具 5:query_past_events(查已落盘 raw_event)
# ============================================================

_raw_event_dir: Optional[str] = None


def set_raw_event_dir(path: Optional[str]):
    """注入 raw_event 目录路径(供 query_past_events 使用)"""
    global _raw_event_dir
    _raw_event_dir = path


def _should_inject_raw_event_dir(path: Optional[str]):
    """由 agent.__init__ 调用"""
    global _raw_event_dir
    _raw_event_dir = path


@tool
def query_past_events(
    start_wall: str,
    end_wall: str,
    person_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    查询此前已落盘的 raw_event,用于去重和事件延续判断。

    Args:
        start_wall: 起始时间(ISO)
        end_wall:   结束时间(ISO)
        person_id:  人员 ID 过滤(可选)

    Returns:
        [
          {"event_id": "A5-P7-...", "type": "PPE缺失-头盔",
           "person": {"id": "P7", ...}, "status": "ongoing",
           "first_seen": "...", "last_seen": "..."},
          ...
        ]
        若无匹配返回 []

    实现:遍历 raw_event_dir 下所有 raw_event_*.json,
    按 wall_time + person_id + status 过滤。
    """
    import json
    import glob
    if not _raw_event_dir:
        return []
    results = []
    try:
        for f in sorted(glob.glob(f"{_raw_event_dir}/raw_event_*.json")):
            data = json.load(open(f, encoding="utf-8"))
            for ev in data.get("events", []):
                ev_wall = ev.get("wall_time") or ev.get("first_seen", "")
                if start_wall <= ev_wall < end_wall:
                    if person_id and ev.get("person", {}).get("id") != person_id:
                        continue
                    results.append(ev)
    except Exception:
        pass
    return results


# ============================================================
# 工具 6:query_timeline(查违规趋势)
# ============================================================

@tool
def query_timeline(
    person_id: str,
    lookback_seconds: int = 10,
) -> Dict[str, Any]:
    """
    查询某人在最近 N 秒内的 PPE 违规趋势,供 Agent 判断"新事件或延续"。

    Args:
        person_id:        目标人员 ID
        lookback_seconds: 回看秒数,默认 10

    Returns:
        {
          "person_id": "P7",
          "lookback_seconds": 10,
          "timeline": [
            {"second": 8,  "helmet_ratio": 0.88, "is_violating": true},
            {"second": 9,  "helmet_ratio": 0.92, "is_violating": true},
            ...
          ],
          "summary": "P7 从 T=8 开始违规,持续 5 秒,现已恢复"
        }

    实现:从 collector 的内存中按 person_id 过滤最近 N 秒的 CV 日志,
    调用 analyze_ppe_compliance 做聚合。
    """
    import time
    if _collector is None:
        return {"error": "collector 未注入", "timeline": [], "summary": ""}

    # 从 collector 读最近 N 秒的 CV 日志
    now = time.time()
    timeline = []
    # 简化:通过 get_current_snapshot 逐个秒获取
    for sec_offset in range(lookback_seconds):
        # 这一秒的场景时间
        try:
            start = _collector._sec_to_wall(sec_offset)
            end = _collector._sec_to_wall(sec_offset + 1)
            logs = _collector.query_raw_logs(start, end, "cv", person_id)
        except Exception:
            continue
        if not logs:
            continue
        stats = analyze_ppe_compliance.invoke({
            "cv_logs": logs, "person_id": person_id, "threshold": 0.8
        })
        if isinstance(stats, dict) and stats.get("total_frames", 0) > 0:
            timeline.append({
                "second": sec_offset,
                "helmet_ratio": stats.get("helmet_ratio", 0),
                "is_violating": stats.get("is_violating", False),
                "violations": stats.get("violations", []),
            })

    # 生成自然语言摘要
    violating_seconds = [t["second"] for t in timeline if t["is_violating"]]
    summary = f"{person_id}: 最近 {lookback_seconds} 秒无违规"
    if violating_seconds:
        summary = (f"{person_id}: 从 T={min(violating_seconds)} 开始违规,"
                   f"持续 {len(violating_seconds)} 秒")

    return {
        "person_id": person_id,
        "lookback_seconds": lookback_seconds,
        "timeline": timeline,
        "summary": summary,
    }


# ============================================================
# 工具 7: check_sensor_alarm
# ============================================================

@tool
def check_sensor_alarm(sensors: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    检查传感器读数是否有告警(alarm/warning)。

    Args:
        sensors: snapshot 中的 sensors 字段(每秒 1 条)

    Returns:
        {
          "has_alarm": True,
          "alarms": [
            {"sensor_id": "gas_detector_03", "type": "可燃气体浓度",
             "value": 1.0, "threshold": 1.0, "status": "alarm"}
          ]
        }
    """
    alarms = []
    if not sensors or not sensors[0].get("readings"):
        return {"has_alarm": False, "alarms": []}
    for sid, r in sensors[0]["readings"].items():
        if r.get("status") in ("alarm", "warning"):
            alarms.append({
                "sensor_id": sid,
                "type":      r.get("type", sid),
                "value":     r.get("value"),
                "threshold": r.get("threshold_alarm"),
                "status":    r.get("status"),
            })
    return {"has_alarm": len(alarms) > 0, "alarms": alarms}


# ============================================================
# 工具 8: check_supervisor_absence
# ============================================================

@tool
def check_supervisor_absence(
    positions: List[Dict[str, Any]],
    required: bool = True,
) -> Dict[str, Any]:
    """
    检查监护人是否在危险作业区内。

    Args:
        positions: snapshot 中的 positions 字段
        required:  是否要求监护人在场(从作业票读取)

    Returns:
        {
          "supervisor_absent": True,
          "supervisor_id": "P11",
          "area": "休息室",
          "is_supervisor_required": True
        }
    """
    if not required:
        return {"supervisor_absent": False, "reason": "监护人非必须"}
    if not positions or not positions[0].get("positions"):
        return {"supervisor_absent": False, "reason": "无定位数据"}

    for wid, p in positions[0]["positions"].items():
        # 通过角色判断：work_permit 中 role=="监护人" 的才算
        if _work_permit:
            worker = _work_permit.get("workers", {}).get(wid, {})
            if worker.get("role") != "监护人":
                continue
        if not p.get("is_in_danger_zone", True):
            return {
                "supervisor_absent":        True,
                "supervisor_id":            wid,
                "area":                     p.get("area_id", "未知"),
                "is_supervisor_required":   True,
            }
    return {"supervisor_absent": False, "reason": "监护人在岗"}


# ============================================================
# 工具清单(供 agent 调用)
# ============================================================

ALL_TOOLS = [
    query_raw_logs,
    get_current_snapshot,
    analyze_ppe_compliance,
    call_vl_expert,
    query_past_events,
    query_timeline,
    check_sensor_alarm,
    check_supervisor_absence,
]