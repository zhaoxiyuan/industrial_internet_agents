"""
System Prompt 加载器
从场景目录下的 system_prompt/ 目录加载各 Agent 的提示词
"""
import os
from pathlib import Path

# 获取默认场景的 system_prompt 目录
DEFAULT_SCENE = "广东石化作业场景"
UTILS_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.dirname(UTILS_DIR)
PROJECT_ROOT = os.path.dirname(AGENTS_DIR)
DEFAULT_SYSTEM_PROMPT_DIR = os.path.join(
    PROJECT_ROOT, "data", "scenes", DEFAULT_SCENE, "system_prompt"
)

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
    "P9": "P9_CLOSURE_SYSTEM_PROMPT.md",
    "P10": "P10_ARCHIVE_SYSTEM_PROMPT.md",
}


def get_system_prompt_dir(scene_name: str = None) -> str:
    """获取场景对应的 system_prompt 目录"""
    if scene_name:
        return os.path.join(PROJECT_ROOT, "data", "scenes", scene_name, "system_prompt")
    return DEFAULT_SYSTEM_PROMPT_DIR


def load_system_prompt(stage: str, scene_name: str = None) -> str:
    """加载指定阶段的系统提示词"""
    prompt_dir = get_system_prompt_dir(scene_name)
    filename = STAGE_TO_FILE.get(stage, f"{stage}_SYSTEM_PROMPT.md")
    filepath = os.path.join(prompt_dir, filename)
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    # 兼容旧路径
    old_filepath = os.path.join(AGENTS_DIR, "system_prompt", filename)
    if os.path.exists(old_filepath):
        with open(old_filepath, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def save_system_prompt(stage: str, content: str, scene_name: str = None) -> bool:
    """保存指定阶段的系统提示词

    Args:
        stage: 阶段名称 (MAIN, P1-P10)
        content: 提示词内容
        scene_name: 场景名称（可选）
    Returns:
        True 成功，False 失败
    """
    prompt_dir = get_system_prompt_dir(scene_name)
    # 确保目录存在
    os.makedirs(prompt_dir, exist_ok=True)
    filename = STAGE_TO_FILE.get(stage, f"{stage}_SYSTEM_PROMPT.md")
    filepath = os.path.join(prompt_dir, filename)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except Exception:
        return False
