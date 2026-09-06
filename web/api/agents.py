"""
Agent 配置管理 API
使用 name 作为唯一标识符
"""
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

# 获取项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent
AGENTS_CONFIG_PATH = PROJECT_ROOT / "data" / "config" / "agents.json"
SCENES_DIR = PROJECT_ROOT / "data" / "scenes"


def _ensure_config_dir():
    """确保配置目录存在"""
    config_dir = AGENTS_CONFIG_PATH.parent
    if not config_dir.exists():
        config_dir.mkdir(parents=True, exist_ok=True)


def _load_agents():
    """加载 Agent 配置"""
    _ensure_config_dir()
    if not AGENTS_CONFIG_PATH.exists():
        return {"agents": []}
    try:
        with open(AGENTS_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"agents": []}


def _save_agents(data):
    """保存 Agent 配置"""
    _ensure_config_dir()
    with open(AGENTS_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_agents():
    """获取所有 Agent"""
    data = _load_agents()
    return data.get("agents", [])


def get_agent(name: str):
    """获取单个 Agent（通过 name）"""
    agents = list_agents()
    for agent in agents:
        if agent.get("name") == name:
            return agent
    return None


def create_agent(agent_data: dict):
    """创建新 Agent"""
    agents = list_agents()

    name = agent_data.get("name", "").strip()
    if not name:
        return {"status": "error", "message": "Agent 名称不能为空"}

    # 检查名称是否重复
    if any(a.get("name") == name for a in agents):
        return {"status": "error", "message": f"Agent '{name}' 已存在"}

    # 创建场景文件夹
    scene_dir = SCENES_DIR / name
    try:
        scene_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"status": "error", "message": f"创建场景文件夹失败: {e}"}

    # 创建新 Agent
    new_agent = {
        "name": name,
        "type": agent_data.get("type", "normal"),  # main | normal
        "model": agent_data.get("model", ""),
        "system_prompt": agent_data.get("system_prompt", ""),
        "skills": agent_data.get("skills", []),  # Skill name 列表
        "tools": agent_data.get("tools", []),  # 直接指定的工具列表
        "prompt_injection": agent_data.get("prompt_injection", ""),  # 追加到系统提示词
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    agents.append(new_agent)
    _save_agents({"agents": agents})

    return {"status": "ok", "agent": new_agent}


def update_agent(name: str, agent_data: dict):
    """更新 Agent（通过 name）"""
    agents = list_agents()

    for i, agent in enumerate(agents):
        if agent.get("name") == name:
            # 更新字段
            if "name" in agent_data:
                # 检查新名称是否与其他 Agent 冲突
                new_name = agent_data["name"]
                if new_name != name and any(a.get("name") == new_name for a in agents):
                    return {"status": "error", "message": f"Agent '{new_name}' 已存在"}
                agent["name"] = new_name
            if "type" in agent_data:
                agent["type"] = agent_data["type"]
            if "model" in agent_data:
                agent["model"] = agent_data["model"]
            if "system_prompt" in agent_data:
                agent["system_prompt"] = agent_data["system_prompt"]
            if "skills" in agent_data:
                agent["skills"] = agent_data["skills"]
            if "tools" in agent_data:
                agent["tools"] = agent_data["tools"]
            if "prompt_injection" in agent_data:
                agent["prompt_injection"] = agent_data["prompt_injection"]

            agent["updated_at"] = datetime.now(timezone.utc).isoformat()

            agents[i] = agent
            _save_agents({"agents": agents})
            return {"status": "ok", "agent": agent}

    return {"status": "error", "message": f"Agent '{name}' 不存在"}


def delete_agent(name: str):
    """删除 Agent（通过 name）"""
    agents = list_agents()

    # 检查是否为主 Agent（主 Agent 不能删除）
    for agent in agents:
        if agent.get("name") == name and agent.get("type") == "main":
            return {"status": "error", "message": "不能删除主 Agent"}

    new_agents = [a for a in agents if a.get("name") != name]

    if len(new_agents) == len(agents):
        return {"status": "error", "message": f"Agent '{name}' 不存在"}

    _save_agents({"agents": new_agents})

    # 删除场景文件夹
    scene_dir = SCENES_DIR / name
    if scene_dir.exists():
        try:
            shutil.rmtree(scene_dir)
        except Exception as e:
            pass  # 文件夹删除失败不影响删除结果

    return {"status": "ok"}


def handle_agents_get(handler):
    """处理 GET /api/agents"""
    agents = list_agents()
    handler.send_json({"status": "ok", "agents": agents})


def handle_agents_post(handler, data):
    """处理 POST /api/agents"""
    action = data.get("action")

    if action == "create":
        result = create_agent(data)
        if result.get("status") == "ok":
            handler.send_json(result)
        else:
            handler.send_json(result, status=400)

    elif action == "update":
        agent_name = data.get("name")
        if not agent_name:
            handler.send_json({"status": "error", "message": "缺少 name 字段"}, status=400)
            return
        result = update_agent(agent_name, data)
        if result.get("status") == "ok":
            handler.send_json(result)
        else:
            handler.send_json(result, status=400)

    elif action == "delete":
        agent_name = data.get("name")
        if not agent_name:
            handler.send_json({"status": "error", "message": "缺少 name 字段"}, status=400)
            return
        result = delete_agent(agent_name)
        if result.get("status") == "ok":
            handler.send_json(result)
        else:
            handler.send_json(result, status=400)

    else:
        handler.send_json({"status": "error", "message": f"未知的 action: {action}"}, status=400)
