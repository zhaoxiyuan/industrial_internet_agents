"""
Skill 配置管理 API
每个 Skill 存储在 data/skills/{skill-name}/SKILL.md
使用 name 作为唯一标识符
"""
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

# 获取项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent
SKILLS_DIR = PROJECT_ROOT / "data" / "skills"


def _ensure_skills_dir():
    """确保 skills 目录存在"""
    if not SKILLS_DIR.exists():
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def _sanitize_name(name: str) -> str:
    """将 Skill 名称转换为合法的目录名（英文小写+连词符）"""
    # 如果是中文或混合名称，转换为拼音形式的英文
    # 这里假设前端已经传入了合法的英文名称
    name = re.sub(r'[<>:"/\\|?*\s]', '-', name.strip())
    name = re.sub(r'-+', '-', name)  # 合并多个连词符
    name = name.strip('-').lower()
    return name


def _load_skill_md(skill_dir: Path) -> dict:
    """从 SKILL.md 加载 Skill 配置"""
    skill_md_path = skill_dir / "SKILL.md"
    if not skill_md_path.exists():
        return None

    try:
        with open(skill_md_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return _parse_skill_md(content, skill_dir.name)
    except (IOError, Exception):
        return None


def _parse_skill_md(content: str, dir_name: str) -> dict:
    """解析 SKILL.md 内容"""
    skill = {
        "name": dir_name.replace('_', '-'),
        "description": "",
        "tools": [],
        "prompt_injection": "",
        "created_at": "",
        "updated_at": ""
    }

    # 解析 frontmatter
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            frontmatter = parts[1].strip()
            body = parts[2].strip()

            for line in frontmatter.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip()
                    value = value.strip()

                    if key == 'name':
                        skill['name'] = value
                    elif key == 'description':
                        skill['description'] = value
                    elif key == 'tools':
                        # 解析逗号或换行分隔的工具列表
                        tools_str = value.replace(',', '\n')
                        skill['tools'] = [t.strip() for t in tools_str.split('\n') if t.strip()]
                    elif key == 'created_at':
                        skill['created_at'] = value
                    elif key == 'updated_at':
                        skill['updated_at'] = value

            # 如果有正文内容，作为 prompt_injection
            if body.strip():
                skill['prompt_injection'] = body.strip()
    else:
        # 没有 frontmatter，整个内容作为 prompt_injection
        skill['prompt_injection'] = content.strip()

    return skill


def _save_skill_md(skill_dir: Path, skill: dict):
    """保存 Skill 到 SKILL.md"""
    skill_md_path = skill_dir / "SKILL.md"

    # 构建 frontmatter
    tools_str = ', '.join(skill.get('tools', [])) if skill.get('tools') else ''

    frontmatter = f"""---
name: {skill['name']}
description: {skill['description'] or ''}
tools: {tools_str}
created_at: {skill.get('created_at', datetime.now(timezone.utc).isoformat())}
updated_at: {datetime.now(timezone.utc).isoformat()}
---

{skill.get('prompt_injection', '')}
"""

    with open(skill_md_path, 'w', encoding='utf-8') as f:
        f.write(frontmatter)


def list_skills():
    """获取所有 Skill"""
    _ensure_skills_dir()
    skills = []

    for skill_dir in SKILLS_DIR.iterdir():
        if skill_dir.is_dir():
            skill = _load_skill_md(skill_dir)
            if skill:
                skills.append(skill)

    # 按名称排序
    skills.sort(key=lambda s: s.get('name', ''))
    return skills


def get_skill(name: str):
    """获取单个 Skill（通过 name）"""
    skills = list_skills()
    for skill in skills:
        if skill.get("name") == name:
            return skill
    return None


def create_skill(skill_data: dict):
    """创建新 Skill"""
    _ensure_skills_dir()

    name = skill_data.get("name", "").strip()
    if not name:
        return {"status": "error", "message": "Skill 名称不能为空"}

    # 转换为合法的目录名
    dir_name = _sanitize_name(name)

    # 检查是否已存在（通过目录名）
    skill_dir = SKILLS_DIR / dir_name
    if skill_dir.exists():
        return {"status": "error", "message": f"Skill '{name}' 已存在"}

    # 创建目录
    skill_dir.mkdir(parents=True, exist_ok=True)

    # 构建 Skill 数据
    new_skill = {
        "name": name,
        "description": skill_data.get("description", ""),
        "tools": skill_data.get("tools", []),
        "prompt_injection": skill_data.get("prompt_injection", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    _save_skill_md(skill_dir, new_skill)

    return {"status": "ok", "skill": new_skill}


def update_skill(name: str, skill_data: dict):
    """更新 Skill（通过 name）"""
    skills = list_skills()

    for skill in skills:
        if skill.get("name") == name:
            # 获取原始目录名
            old_dir_name = _sanitize_name(skill['name'])
            old_skill_dir = SKILLS_DIR / old_dir_name

            # 更新字段
            if "name" in skill_data and skill_data["name"] != skill["name"]:
                skill["name"] = skill_data["name"]
            if "description" in skill_data:
                skill["description"] = skill_data["description"]
            if "tools" in skill_data:
                skill["tools"] = skill_data["tools"]
            if "prompt_injection" in skill_data:
                skill["prompt_injection"] = skill_data["prompt_injection"]

            skill["updated_at"] = datetime.now(timezone.utc).isoformat()

            # 如果名称变更，需要移动目录
            new_dir_name = _sanitize_name(skill["name"])
            new_skill_dir = SKILLS_DIR / new_dir_name

            if old_dir_name != new_dir_name:
                # 检查目标目录是否已存在
                if new_skill_dir.exists():
                    return {"status": "error", "message": f"Skill '{skill['name']}' 已存在"}

                # 重命名目录
                old_skill_dir.rename(new_skill_dir)

            _save_skill_md(new_skill_dir, skill)
            return {"status": "ok", "skill": skill}

    return {"status": "error", "message": f"Skill '{name}' 不存在"}


def delete_skill(name: str):
    """删除 Skill（通过 name）"""
    skills = list_skills()

    for skill in skills:
        if skill.get("name") == name:
            dir_name = _sanitize_name(skill['name'])
            skill_dir = SKILLS_DIR / dir_name

            if skill_dir.exists():
                shutil.rmtree(skill_dir)

            return {"status": "ok"}

    return {"status": "error", "message": f"Skill '{name}' 不存在"}


def handle_skills_get(handler):
    """处理 GET /api/skills"""
    skills = list_skills()
    handler.send_json({"status": "ok", "skills": skills})


def handle_skills_post(handler, data):
    """处理 POST /api/skills"""
    action = data.get("action")

    if action == "create":
        result = create_skill(data)
        if result.get("status") == "ok":
            handler.send_json(result)
        else:
            handler.send_json(result, status=400)

    elif action == "update":
        skill_name = data.get("name")
        if not skill_name:
            handler.send_json({"status": "error", "message": "缺少 name 字段"}, status=400)
            return
        result = update_skill(skill_name, data)
        if result.get("status") == "ok":
            handler.send_json(result)
        else:
            handler.send_json(result, status=400)

    elif action == "delete":
        skill_name = data.get("name")
        if not skill_name:
            handler.send_json({"status": "error", "message": "缺少 name 字段"}, status=400)
            return
        result = delete_skill(skill_name)
        if result.get("status") == "ok":
            handler.send_json(result)
        else:
            handler.send_json(result, status=400)

    else:
        handler.send_json({"status": "error", "message": f"未知的 action: {action}"}, status=400)


def handle_skill_upload(handler):
    """处理 Skill 压缩包上传"""
    import zipfile
    import io

    content_type = handler.headers.get('Content-Type', '')
    if 'multipart/form-data' not in content_type:
        handler.send_json({"status": "error", "message": "需要 multipart/form-data 格式"}, status=400)
        return

    content_length = int(handler.headers.get('Content-Length', 0))
    if content_length == 0:
        handler.send_json({"status": "error", "message": "文件为空"}, status=400)
        return

    # 读取上传数据
    form_data = handler.rfile.read(content_length)

    # 解析 multipart 数据（简化版，查找 ZIP 文件）
    try:
        # 简单处理：直接尝试解压整个内容作为 ZIP
        zip_buffer = io.BytesIO(form_data)
        with zipfile.ZipFile(zip_buffer, 'r') as zip_ref:
            extracted_dirs = []
            for name in zip_ref.namelist():
                if name.endswith('/') or name.startswith('__MACOSX'):
                    continue
                # 获取顶层目录名作为 skill 名
                parts = name.split('/')
                if len(parts) > 1:
                    skill_dir_name = parts[0]
                else:
                    skill_dir_name = name.replace('.md', '')
                    if '/' in name:
                        skill_dir_name = name.split('/')[0]

                if skill_dir_name and skill_dir_name not in extracted_dirs:
                    extracted_dirs.append(skill_dir_name)

                # 解压到目标目录
                target_dir = SKILLS_DIR / skill_dir_name
                if not target_dir.exists():
                    target_dir.mkdir(parents=True, exist_ok=True)

                if not name.endswith('/'):
                    # 直接写文件
                    file_path = SKILLS_DIR / name
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    with zip_ref.open(name) as src, open(file_path, 'wb') as dst:
                        dst.write(src.read())

            logger.info(f"[UPLOAD] 解压 Skill 压缩包: {extracted_dirs}")
            handler.send_json({"status": "ok", "message": f"成功上传 {len(extracted_dirs)} 个 Skill", "skills": extracted_dirs})

    except zipfile.BadZipFile:
        handler.send_json({"status": "error", "message": "无效的 ZIP 文件"}, status=400)
    except Exception as exc:
        logger.exception("[UPLOAD] 解压 Skill 失败")
        handler.send_json({"status": "error", "message": f"解压失败: {str(exc)}"}, status=500)
