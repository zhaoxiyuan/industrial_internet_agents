"""
Mock VL 模型响应数据库(仅数据,不含模型调用工具)

设计原则:
- VL 模型按需调用,每次返回一条预定义响应
- 响应通过 key 索引,key 由 (违规类型, 上下文特征) 组合而成
- 实际播放时由 MockVLModel 根据历史和上下文匹配 key
"""
from typing import Dict
from .types import VLResponse


# ============================================================
# 预定义 VL 响应
# ============================================================

VL_RESPONSES: Dict[str, VLResponse] = {

    # ============================================================
    # PPE 相关
    # ============================================================

    "p7_no_helmet_near_welding": VLResponse(
        is_violation=True,
        violation_type="PPE缺失-头盔",
        evidence_description=(
            "工人 P7 处于动火作业点附近,未佩戴安全帽。"
            "周围可见焊接火花飞溅,作业人员头部暴露在危险区域内。"
        ),
        risk_level="high",
        reasoning=(
            "动火作业中未佩戴安全帽是严重违章。"
            "焊接火花温度可达 1000°C 以上,飞溅距离可达数米,"
            "可能造成头部烧伤或异物击伤。"
            "且该作业票的 JSA 已明确将『焊接火花』列为 high 风险。"
        ),
        suggested_action=(
            "立即提醒 P7 佩戴安全帽,持续观察 30 秒;"
            "若仍未恢复佩戴,升级为 high 风险事件推送监护人和作业负责人。"
        ),
        confidence=0.93,
    ),

    "p11_no_helmet_long_time": VLResponse(
        is_violation=True,
        violation_type="PPE缺失-头盔",
        evidence_description=(
            "监护人 P11 长时间未佩戴安全帽,且不在动火作业区内。"
            "可能已经离开作业区域。"
        ),
        risk_level="medium",
        reasoning=(
            "监护人作为现场安全管理者,长期不戴头盔属于管理性违章。"
            "但因其不在动火作业区内,风险等级略低于作业人员。"
        ),
        suggested_action=(
            "提醒 P11 佩戴头盔并返回作业区。"
        ),
        confidence=0.88,
    ),

    # ============================================================
    # 监护人 / 人员管理
    # ============================================================

    "p11_left_supervised_area": VLResponse(
        is_violation=True,
        violation_type="监护人脱岗",
        evidence_description=(
            "监护人 P11 不在动火作业区内,可能前往休息室或其他区域。"
            "动火作业现场失去现场监护。"
        ),
        risk_level="high",
        reasoning=(
            "动火作业要求监护人全程在场,不得擅离。"
            "P11 离开后,作业人员(P7、P8)在出现异常时无法被及时发现和处置,"
            "可能导致事故扩大。该作业票明确将『监护人在岗』列为必备安全措施。"
        ),
        suggested_action=(
            "立即通知 P11 返回作业区;"
            "若 30 秒内未返回,推送属地负责人(陈主任)介入;"
            "考虑暂停作业直至监护人归位。"
        ),
        confidence=0.95,
    ),

    # ============================================================
    # 环境异常
    # ============================================================

    "gas_level_alarm": VLResponse(
        is_violation=True,
        violation_type="环境异常-可燃气体",
        evidence_description=(
            "可燃气体探测器读数达到 1.0% LEL,触发 alarm。"
            "作业票要求作业前气体检测合格,且 JSA 将『焊接火花引燃可燃气体』"
            "列为 high 风险。"
        ),
        risk_level="high",
        reasoning=(
            "动火作业区域可燃气体浓度达到爆炸下限 1%,极其危险。"
            "焊接火花可能引燃可燃气体导致爆炸事故。"
            "按照规定,可燃气体浓度超过 0.5% LEL 应停止动火作业。"
        ),
        suggested_action=(
            "立即暂停动火作业;"
            "启动通风措施疏散可燃气体;"
            "气体浓度降至 0.2% LEL 以下后方可恢复作业;"
            "推送属地负责人和安全管理人员。"
        ),
        confidence=0.97,
    ),

    "gas_level_warning": VLResponse(
        is_violation=True,
        violation_type="环境异常-可燃气体",
        evidence_description=(
            "可燃气体探测器读数达到 0.7% LEL,触发 warning 状态。"
            "接近 1% LEL 的报警阈值。"
        ),
        risk_level="medium",
        reasoning=(
            "动火作业区域可燃气体浓度持续升高,接近警戒线。"
            "虽然尚未达到 1% LEL 报警阈值,但趋势危险。"
        ),
        suggested_action=(
            "加强通风,准备暂停预案;"
            "持续监测气体浓度变化;"
            "如继续升高,立即停止动火作业。"
        ),
        confidence=0.90,
    ),

    "flame_detector_triggered": VLResponse(
        is_violation=True,
        violation_type="环境异常-异常火焰",
        evidence_description=(
            "火焰探测器触发,动火作业区外出现异常火焰信号。"
            "可能是飞溅火花引燃周围可燃物。"
        ),
        risk_level="critical",
        reasoning=(
            "作业区域外出现火焰是重大安全隐患。"
            "可能已发生初期火灾,需要立即处置。"
        ),
        suggested_action=(
            "立即启动消防应急预案;"
            "疏散作业人员;"
            "通知属地负责人和安全管理人员;"
            "准备启动消防器材。"
        ),
        confidence=0.96,
    ),

    # ============================================================
    # 多重违规组合
    # ============================================================

    "p7_no_helmet_and_p11_absent": VLResponse(
        is_violation=True,
        violation_type="复合违规-PPE脱岗",
        evidence_description=(
            "P7 未佩戴安全帽 + 监护人 P11 不在作业区。"
            "两项违章同时发生,现场完全失去安全管理。"
        ),
        risk_level="critical",
        reasoning=(
            "工人无 PPE + 无现场监护 = 双重保护缺失。"
            "一旦发生紧急情况,既无防护又无人发现,事故后果将极为严重。"
        ),
        suggested_action=(
            "立即通知 P11 返回作业区;"
            "通过其他方式(如对讲机)提醒 P7 佩戴头盔;"
            "推送属地负责人和安全管理人员,考虑暂停作业。"
        ),
        confidence=0.94,
    ),

    # ============================================================
    # 无违规 / 默认
    # ============================================================

    "no_violation": VLResponse(
        is_violation=False,
        violation_type="无",
        evidence_description="画面中未见明显违章行为。",
        risk_level="low",
        reasoning="所有检测项均符合作业票要求。",
        suggested_action="继续常规监测。",
        confidence=0.95,
    ),
}


# ============================================================
# 响应 key 索引(方便按场景/类型查询)
# ============================================================

# 按"违规类型"索引的所有响应 key
VL_BY_TYPE: Dict[str, list] = {
    "PPE缺失-头盔":   ["p7_no_helmet_near_welding", "p11_no_helmet_long_time"],
    "监护人脱岗":      ["p11_left_supervised_area"],
    "环境异常-可燃气体": ["gas_level_alarm", "gas_level_warning"],
    "环境异常-异常火焰": ["flame_detector_triggered"],
    "复合违规":         ["p7_no_helmet_and_p11_absent"],
    "无":              ["no_violation"],
}


# ============================================================
# 数据查询辅助函数
# ============================================================

def get_vl_response(key: str) -> VLResponse:
    """根据 key 获取 VL 响应"""
    if key not in VL_RESPONSES:
        return VL_RESPONSES["no_violation"]
    return VL_RESPONSES[key]


def get_responses_by_type(violation_type: str) -> list:
    """获取某违规类型的所有响应 key"""
    return VL_BY_TYPE.get(violation_type, ["no_violation"])


def list_all_keys() -> list:
    """列出所有 VL 响应 key"""
    return list(VL_RESPONSES.keys())