"""
作业票规则提示词管理系统

存储和修改作业票相关的业务规则(自然语言),供 Agent 在推理时参考。
前端可调用 get_rules()/update_rules() 进行编辑。
"""
from typing import Optional


# ============================================================
# 默认规则(初始值)
# ============================================================

_DEFAULT_RULES = """\
作业票业务规则(Agent 推理时参考):

1. 动火作业必戴 PPE: 安全帽、护目镜、防护服、安全鞋。
   但监护人不要求佩戴护目镜(需要观察现场情况),只检查安全帽。

2. 动火作业要求监护人全程在场,不得离开危险作业区。
   监护人一旦离开动火区,应立即报告。

3. 可燃气体浓度超过 1.0% LEL 时必须立即停止作业,
   超过 0.7% LEL 时需加强通风并准备暂停预案。

4. 不做去重:每次调用直接输出当前检测到的所有候选事件,
   同一人员同一类型的持续违规应逐秒报告,不合并也不过滤。

5. 判断是否违规时,要综合 CV 多数表决结果、传感器状态和人员位置,
   不要仅凭单一证据。
"""

# ============================================================
# 运行时规则
# ============================================================

_CURRENT_RULES: str = _DEFAULT_RULES


# ============================================================
# 公开 API
# ============================================================

def get_rules() -> str:
    """获取当前作业票规则提示词"""
    return _CURRENT_RULES


def update_rules(new_rules: str) -> str:
    """
    用新规则覆盖当前规则(运行时生效,不持久化到磁盘)。

    Args:
        new_rules: 新的规则文本。

    Returns:
        更新后的规则文本。
    """
    global _CURRENT_RULES
    if not new_rules or not isinstance(new_rules, str):
        raise ValueError("new_rules 必须是非空字符串")
    _CURRENT_RULES = new_rules
    return _CURRENT_RULES


def reset_rules() -> str:
    """将规则重置为默认值"""
    global _CURRENT_RULES
    _CURRENT_RULES = _DEFAULT_RULES
    return _CURRENT_RULES


def get_default_rules() -> str:
    """获取默认规则(只读)"""
    return _DEFAULT_RULES
