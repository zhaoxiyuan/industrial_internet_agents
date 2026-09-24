# a/P8P9/ — 独立 P8 处置 + P9 审核项目(子项目 a/ 内部)
#
# 设计依据：docs/风险处置卡片交互设计.md
# 架构：1 状态机 + 7 业务动作 + 6 卡片模板 + 3 service + 1 agent 接口
#
# 不 import langchain / LLM SDK；纯 Python 文件 I/O + 飞书 CardKit。

# 暴露常用符号(lazy re-export,避免 import chain 触发 importlib)
__all__ = [
    "ClosureService",
    "StateNotFound",
    "IllegalTransition",
    "initialize_job_for_agent",
    "bind_card_for_agent",
    "get_state_for_agent",
    "ClosureLinkService",
    "UploadService",
    "UploadValidationError",
    "UploadSizeExceeded",
    "UploadJobSizeExceeded",
    "UploadFileMissing",
    "build_job_card",
    # 卡片渲染 service(2026-09-18 audit 补全)
    "send_all_open_closure_cards",
    "update_job_card",
    "send_event_card_for_web",
    # 业务动作(2026-09-18 audit 补全)
    "record_closure_review",
    "submit_rectification_materials",
    # 卡片模板构建(2026-09-18 audit 补全)
]


def __getattr__(name):
    if name in {"ClosureService", "StateNotFound", "IllegalTransition"}:
        from .state_machine import ClosureService, StateNotFound, IllegalTransition
        return {"ClosureService": ClosureService,
                "StateNotFound": StateNotFound,
                "IllegalTransition": IllegalTransition}[name]
    if name in {"initialize_job_for_agent", "bind_card_for_agent", "get_state_for_agent"}:
        from .agent_interface import (initialize_job_for_agent,
                                      bind_card_for_agent, get_state_for_agent)
        return {"initialize_job_for_agent": initialize_job_for_agent,
                "bind_card_for_agent":    bind_card_for_agent,
                "get_state_for_agent":    get_state_for_agent}[name]
    if name == "ClosureLinkService":
        from .links import ClosureLinkService
        return ClosureLinkService
    if name in {"UploadService", "UploadValidationError", "UploadSizeExceeded",
                "UploadJobSizeExceeded", "UploadFileMissing"}:
        from .upload_service import (
            UploadService, UploadValidationError, UploadSizeExceeded,
            UploadJobSizeExceeded, UploadFileMissing,
        )
        return {"UploadService": UploadService,
                "UploadValidationError": UploadValidationError,
                "UploadSizeExceeded": UploadSizeExceeded,
                "UploadJobSizeExceeded": UploadJobSizeExceeded,
                "UploadFileMissing": UploadFileMissing}[name]
    if name == "build_job_card":
        from .cards import build_job_card
        return build_job_card
    if name in {"send_all_open_closure_cards", "update_job_card", "send_event_card_for_web"}:
        from .services.card_render import (
            send_all_open_closure_cards, update_job_card, send_event_card_for_web,
        )
        return {"send_all_open_closure_cards": send_all_open_closure_cards,
                "update_job_card":              update_job_card,
                "send_event_card_for_web":      send_event_card_for_web}[name]
    if name == "record_closure_review":
        from .business_actions import record_closure_review
        return record_closure_review
    if name == "submit_rectification_materials":
        from .business_actions import submit_rectification_materials
        return submit_rectification_materials
    if name == "relinquish_job":
        from .business_actions import relinquish_job
        return relinquish_job
    if name == "escalate_risk":
        from .business_actions import escalate_risk
        return escalate_risk
    raise AttributeError(f"module 'P8P9' has no attribute {name!r}")