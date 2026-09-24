"""P8 业务规则提示词：加载 / 修改 / 恢复默认。

设计原则：
- 默认版（出厂）以 Python 常量 P8_RULES_DEFAULT 内嵌（source of truth）
- 用户自定义版写到 overrides/P8_RULES.user.md（运行时生成；git 忽略）
- 加载优先级：overrides > P8_RULES_DEFAULT

公开 API：
- load_rules()             加载当前生效的规则
- save_rules(content)      保存用户自定义版到 overrides/（不污染默认常量）
- reset_rules()            恢复默认：删除 override 文件
- get_default_rules()      返回默认常量（只读）
- is_overridden()          判断当前是否使用用户自定义版

兼容：
- load_rules_prompt(stage) 旧风格别名（已 deprecated）
"""
from __future__ import annotations

from pathlib import Path

# === 默认版（出厂；source of truth；Git 跟踪） ============================
# 与 docs/P8_人机协同处置_需求与Demo设计.md § 5.3 保持同步。
# 后续修改默认版只需改本常量；不依赖任何 .md 文件存在性。

P8_RULES_DEFAULT: str = """\
# P8 业务规则提示词（面向业务）

## 1. 风险叠加判定规则（哪些 risk_event 合并为 1 个 P8_job）

满足以下任一条件，**应**聚合为 1 个 P8_job：
- **时间窗口重叠**：多个 risk_event 的 first_seen / last_seen 在同一窗口内（默认 60s）
- **空间/人员重叠**：同一作业区域或同一人员的多类型违规
- **风险类型互补**：PPE + 噪声 + 受限区 = 协同违规场景
- **严重度提升**：合并后 max_level 触发更高决策通道阈值

不满足上述条件的 risk_event **保持独立**（N=1）。

## 2. 责任岗位映射

| max_level | 默认责任岗位       | 飞书消息前缀        |
|-----------|------------------|-------------------|
| LOW       | 作业负责人        | 🟢 [一般]          |
| MEDIUM    | 班组长            | 🟡 [重要]          |
| HIGH      | 车间主任          | 🟠 [紧急]          |
| CRITICAL  | 厂长 / HSE 经理  | 🔴 [🚨危急]        |

实际责任岗位可由用户在前端手动指定；LLM 看到用户指令时按指令执行。

## 3. 通道决策（HITL vs PUSH）

| 场景                                  | 默认 channel | 说明                              |
|---------------------------------------|-------------|-----------------------------------|
| 单 event max_level ≥ HIGH             | HITL        | 高风险必须人工确认                 |
| 单 event max_level ≤ MEDIUM           | PUSH        | 低风险消息直达即可                 |
| 聚合 P8_job（任意 N>1）              | HITL        | 多事件合并需综合决策               |
| 用户明确说"直接推送"                  | PUSH        | 覆盖默认                          |
| 用户明确说"等决策"                    | HITL        | 覆盖默认                          |

## 4. due_at 计算

按 max_level 对应的 RISK_PUSH_STRATEGY：

| max_level | due_at 距 walltime |
|-----------|-------------------|
| LOW       | 24h               |
| MEDIUM    | 4h                |
| HIGH      | 1h                |
| CRITICAL  | 0h（实时）         |

## 5. 哪些操作必须走 HITL（人工 gate）

- 创建 P8_job（避免误创建）
- 所有状态变更（防止误推进）
- 归档到长期记忆（防止误删）

## 6. 行业合规

- 所有 P8_job 必须保留 a6_event_ids[] 数组至少 90 天（审计要求）
- 聚合 P8_job 必须记录"聚合依据"到 note 字段（追溯要求）
- 所有 HITL 决策必须记录决策者 ID（note 字段）

## 7. 何时调 read_p7_events（拉取 P7 数据）

> P8 工作期间 P6/A6/P7 是持续运行的，p7_result.json.risk_events[] 会被持续追加。
> LLM 应按触发模式区分对待（详见主设计文档 § 7.8）。

| 触发模式            | 调 read_p7_events？ | 时机                                         |
|--------------------|--------------------|---------------------------------------------|
| 用户正常对话        | ✅ 按需             | 仅当用户消息隐含或明确需要 P7 数据时          |
| 主流程自动调用      | ⚠️ 不归 LLM 管      | execute_p8 后台线程负责；LLM 不调此工具      |
| 用户明确要求        | ✅ 立即调           | 用户问"刚才还有新的吗？"时立即拉取           |
"""

# === 路径常量 ============================================================
# __file__ = A7/prompt/p8_prompt.py
PROMPT_DIR: Path = Path(__file__).resolve().parent
OVERRIDES_DIR: Path = PROMPT_DIR / "overrides"
DEFAULT_FILE_NAME: str = "P8_RULES.user.md"


def _override_path() -> Path:
    """Override 文件路径：A7/prompt/overrides/P8_RULES.user.md

    Returns:
        Path 对象（不一定存在）
    """
    return OVERRIDES_DIR / DEFAULT_FILE_NAME


# === 公开 API ============================================================

def load_rules() -> str:
    """加载当前生效的业务规则提示词。

    加载优先级：
        1. A7/prompt/overrides/P8_RULES.user.md （用户自定义）
        2. P8_RULES_DEFAULT 常量                （出厂默认）

    Returns:
        markdown 文本（已 strip 首尾空白）。
        当 override 文件存在但读取失败时，**不静默回退** — 抛出异常，
        让上层知晓（避免"覆盖后丢失配置"等隐患）。
    """
    path = _override_path()
    if path.exists():
        # 显式读取失败需抛出；不静默回退到默认
        return path.read_text(encoding="utf-8").strip()
    return P8_RULES_DEFAULT


def save_rules(content: str, *, ensure_ascii: bool = True) -> bool:
    """保存业务规则提示词到 override 文件（覆盖式）。

    Args:
        content: 业务人员编辑后的完整 markdown 文本（**覆盖式**，不是增量）
        ensure_ascii: 写文件时是否使用 ensure_ascii（默认 True，跨平台兼容）

    Returns:
        True 成功；False 失败（异常被捕获并返回 False）

    Side effects:
        - 创建 A7/prompt/overrides/ 目录（如不存在）
        - 写入 A7/prompt/overrides/P8_RULES.user.md
        - **不修改** P8_RULES_DEFAULT 常量

    异常语义：
        - 失败时不抛异常（便于 CLI / 前端直接调用）；
          如需排查，请查看 logger 输出。
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        OVERRIDES_DIR.mkdir(parents=True, exist_ok=True)
        path = _override_path()
        path.write_text(content, encoding="utf-8")
        logger.info("[p8-prompt] saved rules to %s (%d chars)", path, len(content))
        return True
    except Exception as exc:
        logger.exception("[p8-prompt] save_rules failed: %s", exc)
        return False


def reset_rules() -> bool:
    """恢复默认：删除 override 文件。

    下次 load_rules() 自动回落到 P8_RULES_DEFAULT 常量。

    Returns:
        True 成功（无论文件原本是否存在，幂等）；
        False 失败（异常被捕获）。
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        path = _override_path()
        if path.exists():
            path.unlink()
            logger.info("[p8-prompt] reset rules; removed %s", path)
        return True
    except Exception as exc:
        logger.exception("[p8-prompt] reset_rules failed: %s", exc)
        return False


def get_default_rules() -> str:
    """返回出厂默认版常量（只读快照，不修改文件）。

    Returns:
        P8_RULES_DEFAULT 的当前值
    """
    return P8_RULES_DEFAULT


def is_overridden() -> bool:
    """判断当前是否使用用户自定义版（override 文件是否存在）。

    Returns:
        True → 当前 load_rules() 返回用户自定义版
        False → 当前 load_rules() 返回 P8_RULES_DEFAULT
    """
    return _override_path().exists()


# === 兼容旧名（过渡期别名） ============================================
def load_rules_prompt(stage: str = "P8") -> str:
    """[DEPRECATED] 加载指定阶段的业务规则提示词。

    与 agents/utils/system_prompt.py 的 load_system_prompt() 风格对称；
    新代码请直接用 load_rules()，本函数仅作过渡期别名保留。

    Args:
        stage: 阶段标识（当前仅支持 "P8"；其他返回空字符串）

    Returns:
        markdown 文本；非 P8 返回空字符串
    """
    if stage != "P8":
        return ""
    return load_rules()