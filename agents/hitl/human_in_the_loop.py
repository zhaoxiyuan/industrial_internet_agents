"""
HumanInTheLoop - 人工确认节点
管理所有需要人工确认的阶段，提供统一的确认接口
"""
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json


class ConfirmAction(str, Enum):
    """确认操作类型"""
    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"
    PAUSE = "pause"
    RESUME = "resume"
    RECTIFY = "rectify"
    CONFIRM = "confirm"


@dataclass
class HumanConfirmRequest:
    """人工确认请求"""
    stage: str                          # 阶段 P1-P10
    action: str                         # 操作类型
    task_id: str                        # 任务ID
    title: str                          # 确认标题
    description: str                    # 确认描述
    options: List[Dict[str, str]]       # 可选操作选项
    data: Dict[str, Any] = field(default_factory=dict)  # 附加数据
    requested_at: str = ""              # 请求时间

    def __post_init__(self):
        if not self.requested_at:
            self.requested_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "action": self.action,
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "options": self.options,
            "data": self.data,
            "requested_at": self.requested_at
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class HumanConfirmResult:
    """人工确认结果"""
    stage: str
    action: str
    task_id: str
    confirmed: bool
    selected_option: str
    notes: Optional[str] = None
    confirmed_by: str = "operator"
    confirmed_at: str = ""

    def __post_init__(self):
        if not self.confirmed_at:
            self.confirmed_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "action": self.action,
            "task_id": self.task_id,
            "confirmed": self.confirmed,
            "selected_option": self.selected_option,
            "notes": self.notes,
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class HumanInTheLoop:
    """人工确认管理器"""

    # 阶段配置：标题、描述和可选操作
    STAGE_CONFIG = {
        "P1": {
            "title": "作业申请确认",
            "description": "请确认作业申请信息是否正确",
            "next_action": "continue",
            "options": [
                {"value": "approve", "label": "批准作业申请", "color": "green"},
                {"value": "reject", "label": "驳回申请", "color": "red"},
            ]
        },
        "P1_permit_submit": {
            "title": "作业申请提交",
            "description": "请确认提交的作业申请信息",
            "next_action": "continue",
            "options": [
                {"value": "approve", "label": "确认申请", "color": "green"},
                {"value": "reject", "label": "取消", "color": "red"},
            ]
        },
        "P1_jsa_analyze": {
            "title": "JSA分析",
            "description": "请确认JSA分析结果",
            "next_action": "continue",
            "options": [
                {"value": "approve", "label": "确认分析结果", "color": "green"},
                {"value": "reject", "label": "重新分析", "color": "orange"},
            ]
        },
        "P1_generate_draft": {
            "title": "生成作业票草稿",
            "description": "请确认作业票草稿内容",
            "next_action": "continue",
            "options": [
                {"value": "approve", "label": "确认草稿", "color": "green"},
                {"value": "reject", "label": "重新生成", "color": "orange"},
            ]
        },
        "P1_approve_permit": {
            "title": "作业票审批",
            "description": "请最终确认作业票，审批通过后将生成正式作业票",
            "next_action": "continue",
            "options": [
                {"value": "approve", "label": "批准作业票", "color": "green"},
                {"value": "reject", "label": "驳回", "color": "red"},
            ]
        },
        "P2": {
            "title": "是否纳入监测",
            "description": "请确认是否将此作业纳入智能监测",
            "next_action": "continue",
            "options": [
                {"value": "confirm", "label": "纳入监测", "color": "green"},
                {"value": "skip", "label": "暂不监测", "color": "yellow"},
            ]
        },
        "P3": {
            "title": "信息缺失确认",
            "description": "检测到上下文信息缺失，请确认是否补充",
            "next_action": "continue",
            "options": [
                {"value": "confirm", "label": "确认缺失，继续流程", "color": "yellow"},
                {"value": "rectify", "label": "补充信息后再继续", "color": "orange"},
            ]
        },
        "P4": {
            "title": "资源绑定确认",
            "description": "请确认监测资源绑定是否正确",
            "next_action": "continue",
            "options": [
                {"value": "confirm", "label": "确认绑定", "color": "green"},
                {"value": "modify", "label": "修改绑定", "color": "blue"},
            ]
        },
        "P5": {
            "title": "允许开工或退回整改",
            "description": "作业前核验结果如下，请做出决策",
            "next_action": "continue",
            "options": [
                {"value": "approve", "label": "允许开工", "color": "green"},
                {"value": "reject", "label": "退回整改", "color": "red"},
            ]
        },
        "P7": {
            "title": "高风险事件确认",
            "description": "检测到高风险事件，请确认处置方案",
            "next_action": "continue",
            "options": [
                {"value": "approve", "label": "确认事件并下发处置", "color": "green"},
                {"value": "escalate", "label": "升级处理", "color": "orange"},
                {"value": "false_positive", "label": "标记为误报", "color": "yellow"},
            ]
        },
        "P8": {
            "title": "下发、暂停、恢复",
            "description": "请确认处置操作",
            "next_action": "continue",
            "options": [
                {"value": "approve", "label": "确认下发", "color": "green"},
                {"value": "pause", "label": "暂停作业", "color": "orange"},
                {"value": "resume", "label": "恢复作业", "color": "blue"},
            ]
        },
        "P9": {
            "title": "事件和作业关闭",
            "description": "请确认是否关闭事件和作业",
            "next_action": "continue",
            "options": [
                {"value": "confirm", "label": "确认关闭", "color": "green"},
                {"value": "reject", "label": "拒绝关闭", "color": "red"},
            ]
        },
        "P10": {
            "title": "报告确认",
            "description": "请确认归档报告内容",
            "next_action": "end",
            "options": [
                {"value": "confirm", "label": "确认报告", "color": "green"},
                {"value": "request_revision", "label": "要求修改", "color": "orange"},
            ]
        },
    }

    def __init__(self):
        self.pending_requests: Dict[str, HumanConfirmRequest] = {}
        self.completed_results: Dict[str, HumanConfirmResult] = {}

    def create_request(
        self,
        stage: str,
        action: str,
        task_id: str,
        data: Optional[Dict[str, Any]] = None
    ) -> HumanConfirmRequest:
        """创建人工确认请求"""
        config = self.STAGE_CONFIG.get(stage, {})
        title = config.get("title", f"{stage} 人工确认")
        description = config.get("description", "请进行人工确认")
        options = config.get("options", [
            {"value": "confirm", "label": "确认", "color": "green"},
            {"value": "cancel", "label": "取消", "color": "red"},
        ])

        request = HumanConfirmRequest(
            stage=stage,
            action=action,
            task_id=task_id,
            title=title,
            description=description,
            options=options,
            data=data or {}
        )

        key = f"{stage}_{task_id}"
        self.pending_requests[key] = request
        return request

    def get_request(self, stage: str, task_id: str) -> Optional[HumanConfirmRequest]:
        """获取待确认请求"""
        key = f"{stage}_{task_id}"
        return self.pending_requests.get(key)

    def submit_result(
        self,
        stage: str,
        action: str,
        task_id: str,
        selected_option: str,
        confirmed: bool = True,
        notes: Optional[str] = None
    ) -> HumanConfirmResult:
        """提交确认结果"""
        result = HumanConfirmResult(
            stage=stage,
            action=action,
            task_id=task_id,
            confirmed=confirmed,
            selected_option=selected_option,
            notes=notes
        )

        key = f"{stage}_{task_id}"
        self.completed_results[key] = result

        # 清除待确认请求
        if key in self.pending_requests:
            del self.pending_requests[key]

        return result

    def get_result(self, stage: str, task_id: str) -> Optional[HumanConfirmResult]:
        """获取确认结果"""
        key = f"{stage}_{task_id}"
        return self.completed_results.get(key)

    def has_pending(self, stage: str, task_id: str) -> bool:
        """检查是否有待确认请求"""
        key = f"{stage}_{task_id}"
        return key in self.pending_requests

    def has_confirmed(self, stage: str, task_id: str) -> bool:
        """检查是否已有确认结果（用于 workflow 恢复）"""
        key = f"{stage}_{task_id}"
        return key in self.completed_results

    def get_confirmed_option(self, stage: str, task_id: str) -> Optional[str]:
        """获取已确认的操作选项"""
        key = f"{stage}_{task_id}"
        result = self.completed_results.get(key)
        return result.selected_option if result else None

    def clear_confirmation(self, stage: str, task_id: str) -> None:
        """清除确认结果（用于 workflow 恢复后清理）"""
        key = f"{stage}_{task_id}"
        if key in self.completed_results:
            del self.completed_results[key]

    def get_triggered_stage(self, task_id: str) -> Optional[str]:
        """获取已触发但尚未提交的确认阶段"""
        for stage in ["P1", "P2", "P3", "P4", "P5", "P7", "P8", "P9", "P10"]:
            key = f"_triggered_{stage}_{task_id}"
            if key in self.__dict__:
                return stage
        return None

    def trigger_confirmation(self, stage: str, task_id: str) -> None:
        """标记阶段已触发确认（用于 workflow 恢复）"""
        key = f"_triggered_{stage}_{task_id}"
        self.__dict__[key] = True

    def clear_triggered(self, stage: str, task_id: str) -> None:
        """清除触发标记"""
        key = f"_triggered_{stage}_{task_id}"
        if key in self.__dict__:
            del self.__dict__[key]

    def is_triggered(self, stage: str, task_id: str) -> bool:
        """检查阶段是否已触发确认"""
        key = f"_triggered_{stage}_{task_id}"
        return key in self.__dict__

    def list_pending(self) -> List[HumanConfirmRequest]:
        """列出所有待确认请求"""
        return list(self.pending_requests.values())

    def is_confirmed_and_continue(self, stage: str, task_id: str) -> bool:
        """检查是否已确认且应该继续流程"""
        config = self.STAGE_CONFIG.get(stage, {})
        next_action = config.get("next_action", "continue")
        return self.has_confirmed(stage, task_id) and next_action == "continue"

    def is_confirmed_and_end(self, stage: str, task_id: str) -> bool:
        """检查是否已确认且应该结束流程"""
        config = self.STAGE_CONFIG.get(stage, {})
        next_action = config.get("next_action", "continue")
        return self.has_confirmed(stage, task_id) and next_action == "end"


# 全局实例
_hitl_manager = HumanInTheLoop()


def get_hitl_manager() -> HumanInTheLoop:
    """获取全局人工确认管理器"""
    return _hitl_manager


def create_confirm_request(
    stage: str,
    action: str,
    task_id: str,
    data: Optional[Dict[str, Any]] = None
) -> HumanConfirmRequest:
    """创建确认请求的便捷函数"""
    return _hitl_manager.create_request(stage, action, task_id, data)


def submit_confirm_result(
    stage: str,
    action: str,
    task_id: str,
    selected_option: str,
    confirmed: bool = True,
    notes: Optional[str] = None
) -> HumanConfirmResult:
    """提交确认结果的便捷函数"""
    return _hitl_manager.submit_result(stage, action, task_id, selected_option, confirmed, notes)