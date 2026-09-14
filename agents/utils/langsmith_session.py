"""LangSmith 追踪会话管理"""

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class TracingSession:
    """追踪会话信息"""

    job_id: str
    thread_id: str
    agent_name: str
    started_at: datetime = field(default_factory=datetime.now)
    langsmith_run_id: Optional[str] = None

    @property
    def dashboard_url(self) -> Optional[str]:
        """生成 LangSmith Dashboard 链接"""
        if not self.langsmith_run_id:
            return None
        project = os.getenv("LANGCHAIN_PROJECT", "industrial-internet-agents")
        return f"https://smith.langchain.com/projects/{project}/runs/{self.langsmith_run_id}"

    def to_dict(self) -> dict:
        """导出会话信息用于日志记录"""
        return {
            "job_id": self.job_id,
            "thread_id": self.thread_id,
            "agent_name": self.agent_name,
            "started_at": self.started_at.isoformat(),
            "langsmith_run_id": self.langsmith_run_id,
            "dashboard_url": self.dashboard_url,
        }