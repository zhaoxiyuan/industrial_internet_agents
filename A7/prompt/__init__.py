"""A7.prompt — P8 业务规则提示词（加载 / 修改 / 恢复默认）。

设计原则：
- 默认版内嵌为 Python 常量 (P8_RULES_DEFAULT)，不依赖任何外部 .md 文件存在性
- 用户自定义版写到 overrides/ 目录 (运行时生成)
- 加载优先级：overrides/*.user.md > P8_RULES_DEFAULT

公开 API（通过 p8_prompt 模块导出）：
- load_rules() -> str
- save_rules(content) -> bool
- reset_rules() -> bool
- get_default_rules() -> str
- is_overridden() -> bool
- load_rules_prompt(stage) -> str  [DEPRECATED]
"""
from .p8_prompt import (
    P8_RULES_DEFAULT,
    load_rules,
    save_rules,
    reset_rules,
    get_default_rules,
    is_overridden,
    load_rules_prompt,
)

__all__ = [
    "P8_RULES_DEFAULT",
    "load_rules",
    "save_rules",
    "reset_rules",
    "get_default_rules",
    "is_overridden",
    "load_rules_prompt",
]