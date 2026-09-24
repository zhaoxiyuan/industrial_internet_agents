# P8P9/cards.py — 6 态 build_job_card + 通用辅助
#
# 设计依据：
#   - 文档 §2.4 6 态卡片模板
#   - §2.2 标题颜色公式（closed → grey；else by display_risk_level）
#   - §6.1 form / button / link 元素
#   - §7.1 防误触（≥ 10 字 + 不含「驳回」）
#   - §7.2.1 退接按钮仅 accepted_by 本人 enable
#
# 不 import feishu_gateway_cli（解耦）；只构造 Card 2.0 dict。
# 卡片发送由 services/card_render.py 负责。

from __future__ import annotations
import json
from typing import Any, Callable, Dict, List, Optional

from .models import (
    CARD_SCHEMA_VERSION,
    JOB_STATUS_TO_TEMPLATE,
    RISK_LEVEL_COLOR_MAP,
    RISK_LEVEL_NAME_MAP,
    RISK_LEVEL_EMOJI_MAP,
)


# ─── 元素构造器（§6.1） ──────────────────────────────────────────────────────

def callback_button(
    label: str, action: str, value: Dict[str, Any], *,
    button_type: str = "primary", width: str = "default",
) -> Dict[str, Any]:
    """§6.1：飞书 callback button。value 强制 JSON string（Card 2.0 要求）。"""
    return {
        "tag": "button",
        "type": button_type,
        "text": {"tag": "plain_text", "content": label},
        "value": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        "width": width,
    }


def link_button(text: str, url: str, *, button_type: str = "default") -> Dict[str, Any]:
    """§6.1：URL 跳转 button（用 action + behaviors.open_url）。

    TODO(待开发·前端整合)：按 docs/风险处置卡片交互设计.md §2.4.1 + §1047，
          当前 entry_url 是 `http://...:8089/api/closure/jobs/<job_id>`（临时 JSON API）；
          前端整合时应改为 `http://.../closure/entry/<link_id>` 短时 token 链接，
          后端对应 `closure/entry/<link_id>` 渲染 HTML 详情页（复用 attachment token 机制）。
    """
    return {
        "tag": "button",
        "type": button_type,
        "text": {"tag": "plain_text", "content": text},
        "behaviors": [{"type": "open_url", "default_url": url}],
        "width": "default",
    }


def inline_textarea(
    name: str, placeholder: str, *, min_length: int = 10,
) -> Dict[str, Any]:
    """§6.1：Card 2.0 输入框组件（多行 multiline_text）。

    按飞书官方文档
    https://open.feishu.cn/document/feishu-cards/card-json-v2-components/interactive-components/input
    关键字段：
      - tag: input
      - input_type: multiline_text（不是 textarea！）
      - placeholder: {tag: plain_text, content: ...}
      - max_length: 1-1000
      - required: bool（true 时必填）
      - rows / auto_resize / max_rows：多行高度
      - label / label_position：可选

    注意：飞书**没有** ``min_length`` 字段；最早判定输入框是否必填用 ``required: true``。
    本函数把旧 ``min_length`` 参数映射到 ``required``（min_length>=1 → required=true）。
    """
    return {
        "tag": "input",
        "input_type": "multiline_text",
        "name": name,
        "required": min_length > 0,
        "placeholder": {"tag": "plain_text", "content": placeholder},
        "max_length": 500,
        "rows": 4,
        "auto_resize": True,
        "width": "fill",
    }


def inline_form(
    *, name: str, elements: List[Dict[str, Any]],
    submit_label: str, submit_action: str, submit_value: Dict[str, Any],
    button_type: str = "danger", width: str = "default",
    disabled: bool = False, disabled_reason: str = "",
) -> Dict[str, Any]:
    """§6.1：Card 2.0 form 容器 + 内置提交按钮（按官方文档）。

    官方提交按钮 schema：
      - ``action_type: "form_submit"``（**不是** form_action_type）
      - 按钮名 ``name`` = 业务 action 名（callback_router 通过 action.name 路由）
      - ``behaviors: [{type: callback, value: {action, job_id, expected_version}}]``
        携带路由信息（callback 事件中 event.action.value == behaviors.value）

    Args:
        name: form 名字（必填，全局唯一）
        elements: form 内 input 元素列表（如 [inline_textarea(...)]）
        submit_label: 提交按钮文字
        submit_action: 业务 action 名（路由表 key；也是按钮 name）
        submit_value: 业务回调参数（action/job_id/expected_version），放按钮 behaviors
        disabled: True → 按钮 disabled
        disabled_reason: disable 时附加在按钮 name 后的说明（用于日志）
    """
    # 2026-09-17：飞书 Card 2.0 要求整个卡片所有元素 name 全局唯一。
    # 同卡片里如果两个 form 的 submit 按钮都叫 submit_action（如 record_closure_review），
    # update_card_entity 会报 name(record_closure_review) duplicate 错误。
    # button.name 加 form_name 后缀保证唯一，路由信息放在 button.value.action（callback_router 优先读 value.action）。
    submit_button_name = f"{submit_action}_{name}" if name else submit_action
    submit_button: Dict[str, Any] = {
        "tag": "button",
        "text": {"tag": "plain_text", "content": submit_label},
        "type": button_type if not disabled else "default",
        "width": width,
        "name": submit_button_name,
        "action_type": "form_submit",  # ← 关键：触发 form 提交
        # 路由信息透传：通过 value 字段（同时是 behaviors value 的载体）。
        # 不能直接加 behaviors——飞书把 button 视为普通 callback button 而非
        # form_submit。改用 inline value（Card 2.0 button 支持 value 字段）。
        "value": submit_value,
    }
    if disabled:
        submit_button["disabled"] = True
        submit_button["disabled_reason"] = disabled_reason
    return {"tag": "form", "name": name, "elements": elements + [submit_button]}


def callback_button_with_form(
    label: str, action: str, value: Dict[str, Any],
    form_fields: List[Dict[str, Any]], *,
    button_type: str = "primary",
) -> Dict[str, Any]:
    """§6.1：内嵌 input form 按钮（Card 2.0 form action）。"""
    return {
        "tag": "button",
        "type": button_type,
        "text": {"tag": "plain_text", "content": label},
        "value": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        "form_action": {
            "form": form_fields,
            "on_action": {
                "action": action,
                "value": value,
            },
        },
        "width": "default",
    }


def markdown(content: str) -> Dict[str, Any]:
    return {"tag": "markdown", "content": content}


def hr() -> Dict[str, Any]:
    return {"tag": "hr"}


def column_set(columns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """列布局容器；每列内嵌 button。"""
    return {
        "tag": "column_set",
        "flex_mode": "stretch",
        "background_style": "default",
        "columns": columns,
    }


def column(elements: List[Dict[str, Any]], *, width: str = "auto", weight: int = 1) -> Dict[str, Any]:
    return {
        "tag": "column",
        "width": width if width != "weighted" else "weighted",
        "weight": weight,
        "vertical_align": "center",
        "elements": elements,
    }


# ─── 公共辅助 ────────────────────────────────────────────────────────────────

def _header_color(job_status: str, display_risk_level: int) -> str:
    """§2.2：closed → grey；否则按 display_risk_level。"""
    if job_status == "closed":
        return "grey"
    return RISK_LEVEL_COLOR_MAP.get(display_risk_level, "grey")


def _risk_title_prefix(risk_level: int) -> str:
    """§2.4 标题前缀：危急 → 🚨；其它 → emoji + 等级名。"""
    emoji = RISK_LEVEL_EMOJI_MAP.get(risk_level, "")
    name = RISK_LEVEL_NAME_MAP.get(risk_level, "")
    return f"{emoji} {name}级风险"


def _actor_match(actor_open_id: Optional[str], state: Dict[str, Any]) -> bool:
    """§7.2.1：actor_open_id 与 accepted_by.open_id 一致？

    2026-09-17：增加 fallback —— actor_open_id 为 None 时使用
    state.accepted_by.open_id 作为默认 actor。已接取态（acknowledged/
    rectifying/...）的状态变更触发的卡片刷新（_trigger_card_update
    不带 actor 上下文）下，必须确保退接按钮对"当前接取人自己" enable，
    否则飞书按钮 callback 会带 `relinquish_job_disabled` 而非
    `relinquish_job`，路由表不认识 → invalid_action → 状态不变。
    """
    accepted = state.get("accepted_by") or {}
    if not actor_open_id:
        # fallback：默认按 accepted_by 自己看待（已接取态 self-render）
        actor_open_id = accepted.get("open_id")
    if not actor_open_id:
        return False  # open 态没人接取 → 无所谓
    return accepted.get("open_id") == actor_open_id


def _has_accepted_by(state: Dict[str, Any]) -> bool:
    """accepted_by 字段是否有效（非 None 非空）。"""
    ab = state.get("accepted_by")
    return isinstance(ab, dict) and bool(ab.get("open_id"))


def _latest_review_text(state: Dict[str, Any]) -> str:
    """取最近一次 review.comment / materials.latest_review_text。"""
    review = state.get("review") or {}
    if review.get("last_comment"):
        return review["last_comment"]
    materials = state.get("materials") or {}
    if materials.get("latest_review_text"):
        return materials["latest_review_text"]
    return ""


def _risk_basis_inline(events: List[Dict[str, Any]], *, max_inline: int = 3) -> str:
    """§6.1：折叠展示多 event 的 risk_basis。"""
    if not events:
        return "_（无事件）_"
    lines: List[str] = []
    for idx, e in enumerate(events[:max_inline], start=1):
        rid = e.get("risk_event_id", "?")
        lvl = e.get("risk_level", 0)
        lname = RISK_LEVEL_NAME_MAP.get(lvl, "?")
        basis = (e.get("risk_basis", "") or "").replace("\n", " ")[:60]
        est = e.get("event_status", "pending")
        lines.append(f"{idx}. `{rid}` **{lname}级** · `{est}` · {basis}")
    if len(events) > max_inline:
        lines.append(f"…等 {len(events) - max_inline} 个事件已折叠")
    return "\n\n".join(lines)


def _uploads_to_markdown(uploads: List[Dict[str, Any]], *, max_inline: int = 10) -> str:
    """§7 方案 B：把 uploads metadata 列表渲染为 markdown 行。

    每行格式：`📎 {filename} ({size_human}) · upload_id={upload_id}`
    """
    if not uploads:
        return ""
    lines: List[str] = []
    for u in uploads[:max_inline]:
        filename = u.get("filename") or "?"
        size_b = u.get("size_bytes") or 0
        if size_b < 1024:
            size_h = f"{size_b} B"
        elif size_b < 1024 * 1024:
            size_h = f"{size_b / 1024:.1f} KB"
        else:
            size_h = f"{size_b / (1024 * 1024):.1f} MB"
        uid = u.get("upload_id", "?")
        by = u.get("uploaded_by_name") or u.get("uploaded_by") or "?"
        lines.append(f"📎 `{filename}` ({size_h}) · 上传人: {by} · id=`{uid}`")
    if len(uploads) > max_inline:
        lines.append(f"…等 {len(uploads) - max_inline} 个附件已折叠")
    return "\n\n".join(lines)


def _all_uploads(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """§7 方案 B：从 state 收集全部 uploads 列表（materials.submissions + risk_changes）。

    去重（按 upload_id），保留首次出现顺序。
    """
    seen: Dict[str, Dict[str, Any]] = {}
    # 1. materials.submissions[].uploads
    submissions = (state.get("materials") or {}).get("submissions") or []
    for sub in submissions:
        for u in sub.get("uploads") or []:
            uid = u.get("upload_id")
            if uid and uid not in seen:
                seen[uid] = u
    # 2. risk_changes[].uploads
    for rc in state.get("risk_changes") or []:
        for u in rc.get("uploads") or []:
            uid = u.get("upload_id")
            if uid and uid not in seen:
                seen[uid] = u
    return list(seen.values())


def _upload_link_button(
    label: str, state: Dict[str, Any], target: str,
    upload_url_factory: Optional[Callable[[str, str], str]],
    actor_open_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """§7 方案 B：生成"📎 上传附件"link_button。

    upload_url_factory(job_id, target) → URL（含 actor_open_id 校验）；
    无 factory 或参数缺失 → 返回 None（不渲染按钮）。
    """
    if upload_url_factory is None:
        return None
    job_id = state.get("job_id") or ""
    if not job_id:
        return None
    # accepted_by 默认值（同 _actor_match 兜底）
    if not actor_open_id:
        accepted = state.get("accepted_by") or {}
        actor_open_id = accepted.get("open_id")
    base_url = upload_url_factory(job_id, target)
    # 把 open_id 作为 query 参数追加
    sep = "&" if "?" in base_url else "?"
    url = f"{base_url}{sep}open_id={actor_open_id or ''}"
    return link_button(label, url, button_type="default")


# ─── 主入口：build_job_card ──────────────────────────────────────────────────

def build_job_card(
    state: Dict[str, Any], version: int, entry_url: str, *,
    actor_open_id: Optional[str] = None,
    dl_link_factory: Optional[Callable[[str], str]] = None,
    upload_url_factory: Optional[Callable[[str, str], str]] = None,
) -> Dict[str, Any]:
    """§2.4 主入口：按 job_status 路由到具体构造器。

    Args:
        state: ClosureService.get_state 返回的 state dict
        version: 当前 state version（写 button value 时携带，用于乐观锁）
        entry_url: Web 详情页 URL（link_button 跳转用）
        actor_open_id: 当前查看者 open_id（用于按钮 enable/disable）
        dl_link_factory: 附件下载链接工厂（closed 态用）
        upload_url_factory: §7 方案 B 上传入口 URL 工厂；
            签名 upload_url_factory(job_id, target) -> str
            返回形如 `/api/closure/upload/new?job_id=X&target=Y&open_id=Z` 的 URL，
            卡片 link_button 直接跳转，服务端会生成 fresh upload token 并 302 到上传页。

    Returns:
        Card 2.0 dict（schema + header + body.elements）
    """
    job_status = state.get("job_status")
    template = JOB_STATUS_TO_TEMPLATE.get(job_status)
    if template is None:
        raise ValueError(f"未知 job_status={job_status!r}")

    color = _header_color(job_status, state.get("display_risk_level", 0))
    title_prefix = _risk_title_prefix(state.get("display_risk_level", 0))
    title = f"{title_prefix} · {state.get('job_id', '?')}"

    builder = _BUILDERS.get(template)
    if builder is None:
        raise ValueError(f"未实现 job_status={job_status!r} 模板")

    elements = builder(
        state=state,
        version=version,
        entry_url=entry_url,
        actor_open_id=actor_open_id,
        dl_link_factory=dl_link_factory,
        upload_url_factory=upload_url_factory,
    )

    return {
        "schema": CARD_SCHEMA_VERSION,
        # 群内所有成员必须看到同一份作业状态；否则交互后的更新可能仅对操作人可见。
        "config": {"update_multi": True},
        "header": {
            "template": color,
            "title": {"tag": "plain_text", "content": title},
        },
        "body": {"elements": elements},
    }


# ─── 6 个状态模板构造器 ──────────────────────────────────────────────────────

def _build_open(
    *, state: Dict[str, Any], version: int, entry_url: str,
    actor_open_id: Optional[str], dl_link_factory: Optional[Callable[[str], str]],
    upload_url_factory: Optional[Callable[[str, str], str]] = None,
) -> List[Dict[str, Any]]:
    """§2.4.1：「接取任务」primary 按钮。"""
    elements: List[Dict[str, Any]] = [
        markdown(
            f"**告警ID**：`{state.get('job_id')}`\n"
            f"**风险等级**：{RISK_LEVEL_NAME_MAP.get(state.get('display_risk_level', 0), '?')}级\n"
            f"**状态**：待接取"
        ),
        hr(),
        markdown("**风险事件概览**\n\n" + _risk_basis_inline(state.get("events", []))),
        hr(),
        markdown("**⚠️ 请在 [30 分钟] 内接取，超时将自动升级**"),
    ]
    elements.append(column_set([
        column([
            callback_button(
                "接取任务", "acknowledge_disposition",
                value={
                    "action": "acknowledge_disposition",
                    "job_id": state.get("job_id"),
                    "expected_version": version,
                },
                button_type="primary", width="fill",
            )
        ], weight=1),
        column([
            link_button("查看详情", entry_url, button_type="default")
        ], weight=1),
    ]))
    return elements


def _build_acknowledged(
    *, state: Dict[str, Any], version: int, entry_url: str,
    actor_open_id: Optional[str], dl_link_factory: Optional[Callable[[str], str]],
    upload_url_factory: Optional[Callable[[str, str], str]] = None,
) -> List[Dict[str, Any]]:
    """§2.4.2：「提交/补充材料」+「退接」按钮（仅 accepted_by 本人 enable）。"""
    elements: List[Dict[str, Any]] = [
        markdown(
            f"**告警ID**：`{state.get('job_id')}`\n"
            f"**接取人**：{(state.get('accepted_by') or {}).get('name', '?')}\n"
            f"**接取时间**：{(state.get('accepted_by') or {}).get('accepted_at', '?')}\n"
            f"**状态**：已接取 · 处置中"
        ),
        hr(),
        markdown("**风险事件概览**\n\n" + _risk_basis_inline(state.get("events", []))),
    ]

    # §7 方案 B：接取时附带的附件（如接取说明 / 初步证据）
    accepted_uploads = ((state.get("accepted_by") or {}).get("uploads") or [])
    if accepted_uploads:
        md = _uploads_to_markdown(accepted_uploads)
        if md:
            elements.append(hr())
            elements.append(markdown("**📎 接取时附带材料**\n\n" + md))

    if state.get("materials", {}).get("submissions"):
        elements.append(hr())
        elements.append(markdown(
            "**📋 上次提交材料**\n\n" +
            (state.get("materials", {}).get("latest_review_text", "") or "_（无）_")[:200]
        ))

    # §7.2.1 退接按钮：仅 accepted_by 本人 enable
    can_relinquish = _actor_match(actor_open_id, state)
    job_id_str = state.get("job_id") or ""

    # "提交/补充材料"按钮（form 容器 + input）
    submit_form = inline_form(
        name=f"submit_form_{job_id_str}",
        elements=[
            inline_textarea("review_text", "请填写处置说明（至少 1 字）", min_length=1),
        ],
        submit_label="提交/补充材料",
        submit_action="submit_rectification_materials",
        submit_value={
            "action": "submit_rectification_materials",
            "job_id": job_id_str,
            "expected_version": version,
        },
        button_type="primary",
        width="fill",
        disabled=(not can_relinquish),
        disabled_reason="actor_mismatch",
    )

    # 退接 form（用户直接在卡片上看到输入框）
    relinquish_form = inline_form(
        name=f"relinquish_form_{job_id_str}",
        elements=[
            inline_textarea("reason", "请说明退接原因（至少 1 字）", min_length=1),
        ],
        submit_label="退接任务",
        submit_action="relinquish_job",
        submit_value={
            "action": "relinquish_job",
            "job_id": job_id_str,
            "expected_version": version,
        },
        button_type="danger",
        disabled=(not can_relinquish),
        disabled_reason="actor_mismatch",
    )

    elements.append(hr())
    # 提交 form
    elements.append(submit_form)
    elements.append(hr())
    # 退接 form
    elements.append(markdown("**退接任务**（请填理由后点提交按钮）："))
    elements.append(relinquish_form)

    # §7 方案 B：上传附件入口 link_button（仅 accepted_by 本人可见）
    if can_relinquish:
        up_btn = _upload_link_button(
            "📎 上传附件", state, "materials_submission",
            upload_url_factory, actor_open_id,
        )
        if up_btn is not None:
            elements.append(hr())
            elements.append(markdown("**补充附件**（先上传，再到上方表单提交说明）："))
            elements.append(up_btn)

    elements.append(hr())
    elements.append(link_button("查看详情", entry_url, button_type="default"))

    return elements


def _build_rectifying(
    *, state: Dict[str, Any], version: int, entry_url: str,
    actor_open_id: Optional[str], dl_link_factory: Optional[Callable[[str], str]],
    upload_url_factory: Optional[Callable[[str, str], str]] = None,
) -> List[Dict[str, Any]]:
    """§2.4.2.5：仅信息展示（驳回后回到此态）。"""
    elements: List[Dict[str, Any]] = [
        markdown(
            f"**告警ID**：`{state.get('job_id')}`\n"
            f"**状态**：⚠️ 已驳回 · 整改中\n"
            f"**驳回记录**：{len((state.get('review') or {}).get('history', []))} 条"
        ),
        hr(),
        markdown(
            "**📋 上次驳回意见**\n\n" + (
                (state.get("review") or {}).get("last_comment") or "_（无）_"
            )[:300]
        ),
        hr(),
        markdown("**风险事件概览**\n\n" + _risk_basis_inline(state.get("events", []))),
        hr(),
        markdown("**⏳ 处置人正在重新准备材料中…**"),
        column_set([column([
            link_button("查看详情", entry_url, button_type="default")
        ], weight=1)]),
    ]
    return elements


def _build_materials_in_audit(
    *, state: Dict[str, Any], version: int, entry_url: str,
    actor_open_id: Optional[str], dl_link_factory: Optional[Callable[[str], str]],
    upload_url_factory: Optional[Callable[[str, str], str]] = None,
) -> List[Dict[str, Any]]:
    """§2.4.3：v2.1（2026-09-17）—— 业务按钮 + P9 审核进度。

    v2.1 设计：P9 audit 不推状态，job 长期停留 materials_in_audit；卡片必须给人工
    「审核通过」/「驳回」按钮（决策权归人工）。

    P9 状态显示：
      - is_mock=true    → 「P9 审核待人工介入」
      - confidence>=0.8 → 「P9 审核已通过 · 建议关闭」（参考意见）
      - 0.5~0.8         → 「P9 审核中 · 建议人工复核」
      - <0.5            → 「P9 审核中 · 建议驳回」
    """
    p9 = (state.get("review") or {}).get("p9_opinion") or {}
    if p9.get("is_mock"):
        p9_status = "P9 审核待人工介入"
    elif p9.get("comment") is None:
        p9_status = "🔄 P9 审核中 · 等待意见"
    else:
        confidence = p9.get("confidence") or 0.0
        if confidence >= 0.8:
            p9_status = f"P9 审核已通过 · 建议关闭（参考意见 confidence={confidence:.2f}）"
        elif confidence >= 0.5:
            p9_status = f"P9 审核中 · 建议人工复核（confidence={confidence:.2f}）"
        else:
            p9_status = f"P9 审核中 · 建议驳回（confidence={confidence:.2f}）"

    p9_comment = (p9.get("comment") or "")[:200]
    elements: List[Dict[str, Any]] = [
        markdown(
            f"**告警ID**：`{state.get('job_id')}`\n"
            f"**状态**：🔍 P9 审核中\n"
            f"**审核结果**：{p9_status}"
        ),
        hr(),
        markdown(
            "**📋 P9 审核意见**\n\n" + (p9_comment or "_（P9 审核尚未完成；可先参考下方按钮决策）_")
        ),
        hr(),
        markdown(
            "**📋 提交材料**\n\n" +
            (state.get("materials", {}).get("latest_review_text", "") or "_（无）_")[:300]
        ),
    ]

    # §7 方案 B：本次提交材料附带的附件
    submissions = (state.get("materials") or {}).get("submissions") or []
    latest_uploads = (submissions[-1].get("uploads") if submissions else []) or []
    if latest_uploads:
        md = _uploads_to_markdown(latest_uploads)
        if md:
            elements.append(hr())
            elements.append(markdown("**📎 本次提交附件**\n\n" + md))

    elements.append(hr())
    elements.append(markdown("**风险事件概览**\n\n" + _risk_basis_inline(state.get("events", []))))
    elements.append(hr())
    elements.append(markdown("**P9 仅提供参考意见；决策权归人工 — 请点击下方按钮终审**"))

    # v2.1 业务按钮：人工终审（approved → ready_to_close → closed；rejected → rectifying + 清材料）
    #
    # 2026-09-17 修复：用独立 form 容器（Card 2.0 标准模式）而不是 callback_button_with_form。
    # form_action 字段在 Card 2.0 schema 中不存在，会导致 input 框不渲染（仅 max_length 提示）。
    # 改用 inline_form（form 容器内 inline_textarea + form_submit 按钮），与 _build_acknowledged
    # 的 submit_form/relinquish_form 一致。
    job_id_str = state.get("job_id") or ""
    # 2026-09-17：飞书 Card 2.0 要求整个卡片所有元素 name 全局唯一。
    # 两个 form 都用 name="comment" 会触发 ErrMsg: name(comment) duplicate → update_card_entity 失败。
    # 改用 approved_comment / rejection_reason 区分，callback_router 按 decision 映射回 comment。
    approved_form = inline_form(
        name=f"approved_form_{job_id_str}",
        elements=[
            inline_textarea("approved_comment", "请填写审核意见（≥ 10 字）", min_length=10),
        ],
        submit_label="✅ 审核通过 · 关闭",
        submit_action="record_closure_review",
        submit_value={
            "action": "record_closure_review",
            "decision": "approved",
            "job_id": job_id_str,
            "expected_version": version,
        },
        button_type="primary",
        width="fill",
    )
    rejected_form = inline_form(
        name=f"rejected_form_{job_id_str}",
        elements=[
            inline_textarea("rejection_reason", "请说明驳回理由（≥ 10 字）", min_length=10),
        ],
        submit_label="❌ 驳回 · 退回整改",
        submit_action="record_closure_review",
        submit_value={
            "action": "record_closure_review",
            "decision": "rejected",
            "job_id": job_id_str,
            "expected_version": version,
        },
        button_type="danger",
        width="fill",
    )
    elements.append(approved_form)
    elements.append(rejected_form)

    # §7 方案 B：上传补充材料 link_button（仅 accepted_by 本人可见）
    can_act = _actor_match(actor_open_id, state)
    if can_act:
        up_btn = _upload_link_button(
            "📎 上传补充材料", state, "materials_submission",
            upload_url_factory, actor_open_id,
        )
        if up_btn is not None:
            elements.append(hr())
            elements.append(up_btn)

    elements.append(column_set([column([
        link_button("查看详情", entry_url, button_type="default")
    ], weight=1)]))
    return elements


def _build_waiting_human_review(
    *, state: Dict[str, Any], version: int, entry_url: str,
    actor_open_id: Optional[str], dl_link_factory: Optional[Callable[[str], str]],
    upload_url_factory: Optional[Callable[[str, str], str]] = None,
) -> List[Dict[str, Any]]:
    """§2.4.4：input textarea + 「审核通过」/「驳回」/「升级风险」/「降级风险」。"""
    elements: List[Dict[str, Any]] = [
        markdown(
            f"**告警ID**：`{state.get('job_id')}`\n"
            f"**状态**：🧐 等待人工审核"
        ),
        hr(),
        markdown(
            "**📋 P9 审核意见**\n\n" +
            ((state.get("review") or {}).get("p9_opinion") or {}).get("comment", "P9 审核已通过；等待终审确认关闭。")[:400]
        ),
        hr(),
        markdown("**风险事件概览**\n\n" + _risk_basis_inline(state.get("events", []))),
        hr(),
        markdown("**请填写审核意见（≥ 10 字，不能含「驳回」）**"),
    ]

    # 4 个按钮：审核通过 / 驳回 / 升级风险 / 降级风险
    elements.append(callback_button_with_form(
        "✅ 审核通过 · 关闭", "record_closure_review",
        value={
            "action": "record_closure_review",
            "decision": "approved",
            "job_id": state.get("job_id"),
            "expected_version": version,
        },
        form_fields=[
            inline_textarea("comment", "请填写审核意见（≥ 10 字）", min_length=10),
        ],
        button_type="primary",
    ))
    elements.append(callback_button_with_form(
        "❌ 驳回 · 退回整改", "record_closure_review",
        value={
            "action": "record_closure_review",
            "decision": "rejected",
            "job_id": state.get("job_id"),
            "expected_version": version,
        },
        form_fields=[
            inline_textarea("comment", "请说明驳回理由（≥ 10 字）", min_length=10),
        ],
        button_type="danger",
    ))
    elements.append(callback_button_with_form(
        "⬆️ 升级风险等级", "escalate_risk",
        value={
            "action": "escalate_risk",
            "job_id": state.get("job_id"),
            "expected_version": version,
            "event_id": (state.get("events") or [{}])[0].get("risk_event_id"),
        },
        form_fields=[
            inline_textarea("reason", "请说明升级理由（≥ 10 字）", min_length=10),
        ],
        button_type="default",
    ))
    elements.append(callback_button_with_form(
        "⬇️ 降级风险等级", "downgrade_risk",
        value={
            "action": "downgrade_risk",
            "job_id": state.get("job_id"),
            "expected_version": version,
            "event_id": (state.get("events") or [{}])[0].get("risk_event_id"),
        },
        form_fields=[
            inline_textarea("reason", "请说明降级理由（≥ 20 字）", min_length=20),
        ],
        button_type="default",
    ))

    # §7 方案 B：上传审核材料 link_button
    up_btn = _upload_link_button(
        "📎 上传审核材料", state, "risk_change",
        upload_url_factory, actor_open_id,
    )
    if up_btn is not None:
        elements.append(hr())
        elements.append(up_btn)

    elements.append(column_set([column([
        link_button("查看详情", entry_url, button_type="default")
    ], weight=1)]))
    return elements


def _build_ready_to_close(
    *, state: Dict[str, Any], version: int, entry_url: str,
    actor_open_id: Optional[str], dl_link_factory: Optional[Callable[[str], str]],
    upload_url_factory: Optional[Callable[[str, str], str]] = None,
) -> List[Dict[str, Any]]:
    """§2.4.5：input textarea + 「确认关闭」+ 升级/降级。"""
    elements: List[Dict[str, Any]] = [
        markdown(
            f"**告警ID**：`{state.get('job_id')}`\n"
            f"**状态**：📦 待终审关闭"
        ),
        hr(),
        markdown(
            "**📋 关闭理由**\n\n" + (
                (state.get("materials") or {}).get("latest_review_text") or "_（无）_"
            )[:400]
        ),
        hr(),
        markdown("**风险事件概览**\n\n" + _risk_basis_inline(state.get("events", []))),
        hr(),
        markdown("**请确认关闭（≥ 10 字意见）**"),
    ]

    elements.append(callback_button_with_form(
        "✅ 确认关闭", "record_closure_review",
        value={
            "action": "record_closure_review",
            "decision": "approved",
            "job_id": state.get("job_id"),
            "expected_version": version,
        },
        form_fields=[
            inline_textarea("comment", "请填写关闭意见（≥ 10 字）", min_length=10),
        ],
        button_type="primary",
    ))
    elements.append(callback_button_with_form(
        "⬆️ 升级风险等级", "escalate_risk",
        value={
            "action": "escalate_risk",
            "job_id": state.get("job_id"),
            "expected_version": version,
            "event_id": (state.get("events") or [{}])[0].get("risk_event_id"),
        },
        form_fields=[
            inline_textarea("reason", "请说明升级理由（≥ 10 字）", min_length=10),
        ],
        button_type="default",
    ))
    elements.append(callback_button_with_form(
        "⬇️ 降级风险等级", "downgrade_risk",
        value={
            "action": "downgrade_risk",
            "job_id": state.get("job_id"),
            "expected_version": version,
            "event_id": (state.get("events") or [{}])[0].get("risk_event_id"),
        },
        form_fields=[
            inline_textarea("reason", "请说明降级理由（≥ 20 字）", min_length=20),
        ],
        button_type="default",
    ))

    # §7 方案 B：上传最终材料 link_button
    up_btn = _upload_link_button(
        "📎 上传最终材料", state, "risk_change",
        upload_url_factory, actor_open_id,
    )
    if up_btn is not None:
        elements.append(hr())
        elements.append(up_btn)

    elements.append(column_set([column([
        link_button("查看详情", entry_url, button_type="default")
    ], weight=1)]))
    return elements


def _build_closed(
    *, state: Dict[str, Any], version: int, entry_url: str,
    actor_open_id: Optional[str], dl_link_factory: Optional[Callable[[str], str]],
    upload_url_factory: Optional[Callable[[str, str], str]] = None,
) -> List[Dict[str, Any]]:
    """§2.4.6：只读 + 「查看附件」link_button（dl_link_factory）。"""
    review = state.get("review") or {}
    materials = state.get("materials") or {}
    elements: List[Dict[str, Any]] = [
        markdown(
            f"**告警ID**：`{state.get('job_id')}`\n"
            f"**状态**：✅ 已关闭（已归档）\n"
            f"**关闭时间**：{state.get('closed_at', '?')}\n"
            f"**关闭人**：{state.get('closed_by', '?')}"
        ),
        hr(),
        markdown(
            "**📋 关闭意见**\n\n" + (review.get("last_comment") or "_（无）_")[:400]
        ),
        hr(),
        markdown(
            "**📎 提交材料**\n\n" + (materials.get("latest_review_text") or "_（无）_")[:300]
        ),
    ]

    # §7 方案 B：所有 attachments（materials.submissions + risk_changes）
    all_uploads = _all_uploads(state)
    if all_uploads:
        md = _uploads_to_markdown(all_uploads)
        if md:
            elements.append(hr())
            elements.append(markdown(f"**📎 全部附件（{len(all_uploads)}）**\n\n" + md))

    elements.append(hr())
    elements.append(markdown("**📊 风险事件终态**\n\n" + _risk_basis_inline(state.get("events", []), max_inline=10)))
    # 附件链接按钮
    if dl_link_factory:
        for ev in state.get("events", []):
            evidence = ev.get("evidence") or {}
            eid = evidence.get("evidence_id") or ev.get("risk_event_id")
            if eid:
                short_url = dl_link_factory(eid)
                elements.append(link_button(
                    f"📎 查看附件 {ev.get('risk_event_id', '?')}",
                    short_url,
                    button_type="default",
                ))
    elements.append(column_set([column([
        link_button("查看详情", entry_url, button_type="default")
    ], weight=1)]))
    return elements


# 路由表
_BUILDERS: Dict[str, Callable[..., List[Dict[str, Any]]]] = {
    "open": _build_open,
    "acknowledged": _build_acknowledged,
    "rectifying": _build_rectifying,
    "materials_in_audit": _build_materials_in_audit,
    "waiting_human_review": _build_waiting_human_review,
    "ready_to_close": _build_ready_to_close,
    "closed": _build_closed,
}


__all__ = [
    "build_job_card",
    "callback_button",
    "callback_button_with_form",
    "link_button",
    "inline_textarea",
    "markdown",
    "hr",
    "column_set",
    "column",
]
