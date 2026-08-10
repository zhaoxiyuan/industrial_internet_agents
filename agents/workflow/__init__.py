"""
Workflow 模块 - 工作流状态和持久化管理
"""
from .file_utils import (
    get_jobs_dir,
    get_job_dir,
    ensure_job_dir,
    get_stage_result_path,
    read_json_file,
    write_json_file,
    get_workflow_status_path,
)

from .workflow_state import (
    init_workflow_status,
    update_workflow_status,
    get_workflow_status,
    ALL_STAGES,
)

from .job_persistence import (
    save_job_application,
    add_job_log,
    save_confirmation,
    get_job_status,
)

__all__ = [
    # file_utils
    "get_jobs_dir",
    "get_job_dir",
    "ensure_job_dir",
    "get_stage_result_path",
    "read_json_file",
    "write_json_file",
    "get_workflow_status_path",
    # workflow_state
    "init_workflow_status",
    "update_workflow_status",
    "get_workflow_status",
    "ALL_STAGES",
    # job_persistence
    "save_job_application",
    "add_job_log",
    "save_confirmation",
    "get_job_status",
]
