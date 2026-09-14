"""
System Prompt 加载器
从 system_prompt/ 目录加载各 Agent 的提示词
"""
import os

# 获取 system_prompt 目录路径（在 agents/ 下）
# __file__ 是 agents/utils/system_prompt.py，需要向上两级到达 agents/
UTILS_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.dirname(UTILS_DIR)
SYSTEM_PROMPT_DIR = os.path.join(AGENTS_DIR, "system_prompt")

# 阶段到文件名映射
STAGE_TO_FILE = {
    "MAIN": "MAIN_AGENT_SYSTEM_PROMPT.md",
    "P1": "P1_PERMIT_SYSTEM_PROMPT.md",
    "P2": "P2_TASK_SYSTEM_PROMPT.md",
    "P3": "P3_CONTEXT_SYSTEM_PROMPT.md",
    "P4": "P4_BINDING_SYSTEM_PROMPT.md",
    "P5": "P5_VERIFY_SYSTEM_PROMPT.md",
    "P6": "P6_MONITOR_SYSTEM_PROMPT.md",
    "P7": "P7_RISK_SYSTEM_PROMPT.md",
    "P8": "P8_DISPOSITION_SYSTEM_PROMPT.md",
    "P8_CARD_ACTION": "P8_CARD_ACTION_SYSTEM_PROMPT.md",   # 2026-08-20 新增
    "P9": "P9_CLOSURE_SYSTEM_PROMPT.md",
    "P10": "P10_ARCHIVE_SYSTEM_PROMPT.md",
}


def load_system_prompt(stage: str) -> str:
    """加载指定阶段的系统提示词"""
    filename = STAGE_TO_FILE.get(stage, f"{stage}_SYSTEM_PROMPT.md")
    filepath = os.path.join(SYSTEM_PROMPT_DIR, filename)
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def save_system_prompt(stage: str, content: str) -> bool:
    """保存指定阶段的系统提示词

    Args:
        stage: 阶段名称 (MAIN, P1-P10)
        content: 提示词内容
    Returns:
        True 成功，False 失败
    """
    filename = STAGE_TO_FILE.get(stage, f"{stage}_SYSTEM_PROMPT.md")
    filepath = os.path.join(SYSTEM_PROMPT_DIR, filename)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except Exception:
        return False
