"""
Mock 作业票数据
对应文档中的动火作业场景:加氢裂化装置 R-101 反应器底部管线补焊
"""
from typing import Dict, Any


WORK_PERMIT: Dict[str, Any] = {
    # ===== 基本信息 =====
    "permit_id": "PTW-2026-0803-动火-007",
    "permit_type": "动火作业许可证",
    "level": "二级动火",
    "work_content": "加氢裂化装置 R-101 反应器底部管线补焊",

    # ===== 作业地点 =====
    "location": {
        "area_id": "动火区-A",
        "device_id": "R-101",
        "device_name": "加氢裂化反应器",
        "specific_position": "反应器底部西侧",
        "expected_position": [102.45, 88.30],   # 期望作业位置(米)
        "danger_zones": ["动火区-A"],            # 危险作业区列表
    },

    # ===== 时间窗口 =====
    "planned_window": {
        "start": "2026-08-03T14:00:00",
        "end":   "2026-08-03T18:00:00",
        "duration_hours": 4,
    },

    # ===== 人员清单 =====
    "workers": {
        "P7": {
            "name": "张师傅",
            "role": "焊工",
            "department": "维修车间",
            "qualification": "焊工证-特级",
            "qualification_valid": True,
            "phone": "138-0000-7001",
        },
        "P8": {
            "name": "王师傅",
            "role": "焊工",
            "department": "维修车间",
            "qualification": "焊工证-二级",
            "qualification_valid": True,
            "phone": "138-0000-7002",
        },
        "P11": {
            "name": "赵师傅",
            "role": "监护人",
            "department": "安全科",
            "qualification": "监护人证",
            "qualification_valid": True,
            "phone": "138-0000-7011",
        },
    },

    # ===== 必戴 PPE =====
    "required_ppe": [
        "helmet",               # 安全帽
        "goggles",              # 护目镜
        "protective_suit",      # 防护服
        "safety_shoes",         # 安全鞋
    ],

    # ===== 是否要求监护人在场 =====
    "supervisor_required": True,

    # ===== JSA 风险识别结果 =====
    "jsa_risks": [
        {
            "risk_id": "JSA-001",
            "risk": "焊接火花引燃可燃气体",
            "level": "high",
            "control": "作业前气体检测合格 + 消防器材到位"
        },
        {
            "risk_id": "JSA-002",
            "risk": "高处坠落",
            "level": "medium",
            "control": "安全带 + 脚手架验收合格"
        },
        {
            "risk_id": "JSA-003",
            "risk": "触电",
            "level": "medium",
            "control": "断电 + 验电"
        },
    ],

    # ===== 已落实的安全措施 =====
    "implemented_measures": [
        "作业前气体检测合格(0.2% LEL)",
        "消防器材到位(灭火器 2 台)",
        "警戒范围 5 米已划定",
        "监护人到场(P11 赵师傅)",
    ],

    # ===== 审批链(四级全通过)=====
    "approvals": [
        {"role": "作业申请人",   "name": "李工",   "approved_at": "2026-08-03T13:30:00", "status": "approved"},
        {"role": "属地负责人",   "name": "陈主任", "approved_at": "2026-08-03T13:45:00", "status": "approved"},
        {"role": "安全管理人员", "name": "孙工",   "approved_at": "2026-08-03T13:50:00", "status": "approved"},
        {"role": "动火审批人",   "name": "周总",   "approved_at": "2026-08-03T13:58:00", "status": "approved"},
    ],

    # ===== 当前状态 =====
    "status": "in_progress",

    # ===== 风险焦点(供 A5 重点关注)=====
    "risk_focus": [
        "PPE完整性",
        "监护人在岗",
        "可燃气体浓度",
    ],
}


def load_work_permit() -> Dict[str, Any]:
    """
    加载作业票(Demo 版直接返回静态数据)。

    真实生产环境会通过 MCP 调用 MCP-01 作业任务与作业票服务获取。
    """
    return WORK_PERMIT.copy()


def is_required_ppe(ppe_name: str) -> bool:
    """检查某个 PPE 项目是否在作业票要求列表中"""
    return ppe_name in WORK_PERMIT["required_ppe"]


def is_danger_zone(area_id: str) -> bool:
    """检查某个区域是否为危险作业区"""
    return area_id in WORK_PERMIT["location"]["danger_zones"]


def get_worker_info(worker_id: str) -> Dict[str, Any]:
    """获取某个作业人员的信息,若不在作业人员名单中则返回空字典"""
    return WORK_PERMIT["workers"].get(worker_id, {})