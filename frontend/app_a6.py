"""
A6 风险研判智能体 - 后端接口

提供研判结果查询、提示词管理、状态监控等接口

启动方式: python frontend/app_a6.py
访问地址: http://localhost:5002
"""
import sys
from pathlib import Path

# 添加项目路径
# app_a6.py 位于 frontend/，A6 在项目根目录
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# 确定日志目录基础路径（项目根目录）
LOGS_BASE = project_root

from fastapi import FastAPI, Request, APIRouter, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Optional, Any
from datetime import datetime
import json
import uvicorn

from A6.agent.tools import A5DataTools, OutputTools
from A6.agent.prompt_manager import PromptManager
from A6.agent.a6_agent import A6Agent

# ============================================================
# FastAPI 应用
# ============================================================

app = FastAPI(
    title="A6 风险研判看板",
    description="A6 风险研判智能体的研判结果展示和提示词管理界面"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# 模板配置
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# ============================================================
# API 路由
# ============================================================

router = APIRouter(prefix="/api/a6", tags=["A6"])

# 初始化工具实例
_a5_tools = A5DataTools()
_output_tools = OutputTools()
_prompt_manager = PromptManager()
_a6_agent: Optional[A6Agent] = None


def get_a6_agent() -> A6Agent:
    """获取或创建 A6 Agent 实例（延迟初始化）"""
    global _a6_agent
    if _a6_agent is None:
        _a6_agent = A6Agent(
            a5_log_dir="A5/logs",
            a6_output_dir="A6/logs"
        )
    return _a6_agent


# ============================================================
# 数据模型
# ============================================================

class AssessmentResponse(BaseModel):
    a6_event_id: str
    event_type: str
    first_seen: str
    last_seen: str
    duration_sec: float
    involved_persons: List[str]
    risk_level: int
    risk_level_name: str
    risk_basis: str
    suggestions: List[str]
    reasoning: str
    timestamp: str


class AssessmentListResponse(BaseModel):
    total: int
    items: List[Dict]


class StatusResponse(BaseModel):
    event_id_map: Dict
    summary: Dict


class PromptUpdateRequest(BaseModel):
    content: str
    reason: Optional[str] = None


# ============================================================
# 研判结果接口
# ============================================================

@router.get("/assessments", response_model=AssessmentListResponse)
async def get_assessments(
    start: Optional[str] = None,
    end: Optional[str] = None,
    risk_level: Optional[int] = None,
    limit: int = 100
):
    """
    获取研判结果列表

    Query 参数:
        start: 开始时间（ISO 格式，可选）
        end: 结束时间（ISO 格式，可选）
        risk_level: 风险等级过滤（可选）
        limit: 返回数量限制（默认 100）
    """
    # 获取所有研判结果文件
    assessments_dir = LOGS_BASE / "A6" / "logs" / "assessments"
    if not assessments_dir.exists():
        return {"total": 0, "items": []}

    results = []

    # 扫描所有日期目录
    for date_dir in sorted(assessments_dir.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue

        for f in sorted(date_dir.glob("a6_*.json"), reverse=True):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)

                    # 时间过滤
                    if start and data.get("timestamp", "") < start:
                        continue
                    if end and data.get("timestamp", "") > end:
                        continue

                    # 风险等级过滤
                    if risk_level and data.get("risk_level") != risk_level:
                        continue
                    # 自动过滤无风险记录（risk_level=0），前端不展示
                    if data.get("risk_level", 0) == 0:
                        continue

                    results.append({
                        "a6_event_id": data.get("a6_event_id"),
                        "event_type": data.get("event_type"),
                        "first_seen": data.get("first_seen"),
                        "last_seen": data.get("last_seen"),
                        "duration_sec": data.get("duration_sec", 0),
                        "involved_persons": data.get("involved_persons", []),
                        "risk_level": data.get("risk_level"),
                        "risk_level_name": data.get("risk_level_name"),
                        "timestamp": data.get("timestamp")
                    })

                    if len(results) >= limit:
                        break

            except (json.JSONDecodeError, IOError):
                continue

        if len(results) >= limit:
            break

    return {"total": len(results), "items": results}


@router.get("/assessments/{a6_event_id}")
async def get_assessment(a6_event_id: str):
    """获取单条研判详情"""
    # 搜索所有评估目录
    assessments_dir = LOGS_BASE / "A6" / "logs" / "assessments"
    if not assessments_dir.exists():
        raise HTTPException(status_code=404, detail="评估结果不存在")

    # 扫描所有日期目录
    for date_dir in sorted(assessments_dir.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue

        for f in sorted(date_dir.glob("a6_*.json"), reverse=True):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                    if data.get("a6_event_id") == a6_event_id:
                        return data
            except (json.JSONDecodeError, IOError):
                continue

    raise HTTPException(status_code=404, detail="评估结果不存在")


# ============================================================
# 状态接口
# ============================================================

@router.get("/status", response_model=StatusResponse)
async def get_status():
    """获取 A6 状态和 event_id_map"""
    status = _a5_tools._load_status()
    return {
        "event_id_map": status.get("event_id_map", {}),
        "summary": status.get("summary", {})
    }


# ============================================================
# 提示词接口
# ============================================================

@router.get("/prompts")
async def get_all_prompts():
    """获取所有提示词"""
    return _prompt_manager.get_all_prompts()


@router.get("/prompts/{prompt_name}")
async def get_prompt(prompt_name: str):
    """获取指定提示词内容"""
    prompt = _prompt_manager.read_prompt(prompt_name)
    if prompt.get("error"):
        raise HTTPException(status_code=404, detail=prompt["error"])
    return prompt


@router.post("/prompts/{prompt_name}")
async def update_prompt(prompt_name: str, body: PromptUpdateRequest):
    """更新提示词（仅更新内存）"""
    success = _prompt_manager.update_prompt(
        prompt_name=prompt_name,
        new_content=body.content,
        reason=body.reason
    )
    if not success:
        raise HTTPException(status_code=400, detail=f"无法更新提示词: {prompt_name}")
    return {"status": "success", "message": "提示词已更新（未保存到文件）"}


@router.post("/prompts/{prompt_name}/save")
async def save_prompt(prompt_name: str):
    """保存提示词到文件"""
    success = _prompt_manager.save_to_file(prompt_name=prompt_name)
    if not success:
        raise HTTPException(status_code=500, detail="保存失败")
    return {"status": "success", "saved_at": datetime.now().isoformat()}


@router.post("/prompts/{prompt_name}/reset")
async def reset_prompt(prompt_name: str):
    """重置提示词为默认值"""
    try:
        _prompt_manager.reset_to_default(prompt_name)
        return {"status": "success", "reset_at": datetime.now().isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重置失败: {e}")


@router.post("/clear_logs")
async def clear_logs():
    """清理 A6/logs 目录下所有文件"""
    import shutil, os
    log_dir = project_root / "A6" / "logs"
    count = 0
    if log_dir.exists():
        for item in log_dir.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                    count += 1
                elif item.is_dir():
                    shutil.rmtree(item)
                    count += 1
            except Exception:
                pass
    return {"ok": True, "cleared": count}

    """重置提示词为默认"""
    success = _prompt_manager.reset_to_default(prompt_name=prompt_name)
    if not success:
        raise HTTPException(status_code=500, detail="重置失败")
    return {"status": "success", "message": f"提示词 {prompt_name} 已重置为默认"}


# ============================================================
# Agent 控制接口（可选）
# ============================================================

@router.post("/process/{event_id}")
async def process_event(event_id: str, body: Optional[Dict] = None):
    """手动触发单事件研判（用于测试）"""
    agent = get_a6_agent()
    result = await agent.process_event(event_id, event_data=body)
    return result


@router.get("/risk_suggestions")
async def get_risk_suggestions():
    """获取风险等级建议模板"""
    return _prompt_manager.get_risk_suggestions()


# ============================================================
# 页面路由
# ============================================================

# 注册 API 路由
app.include_router(router)


@app.get("/")
async def root(request: Request):
    """返回 A6 研判看板页面"""
    return templates.TemplateResponse(request, "index_a6.html", {"request": request})


@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "ok", "service": "A6 风险研判看板"}


# ============================================================
# 启动入口
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("A6 风险研判看板")
    print("=" * 60)
    print("启动地址: http://localhost:5002")
    print("按 Ctrl+C 停止")
    print("=" * 60)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=5002,
        reload=False
    )
