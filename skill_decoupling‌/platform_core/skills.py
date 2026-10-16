"""标准 Skill 的导入、校验和只读资源管理。

Skill 由 SKILL.md 与可选资源组成；不保存 MCP 地址、凭据或服务启动声明。
导入复制到内容寻址快照，绝不执行脚本。无第三方 YAML 依赖时支持安全的常用
frontmatter 子集，超出该语法的包明确拒绝并提示，不宣称完整 YAML 解析能力。"""
import base64
import hashlib
import io
import json
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

from .common import Problem, safe_path, text


def scalar(value):
    """解析普通或引号字符串；双引号用 JSON 转义，单引号支持双单引号转义。
    拒绝 YAML 标签、锚点和内联集合，避免把配置字符串解释为可执行对象。"""
    value = value.strip()
    if value.startswith('"'):
        try:
            return json.loads(value)
        except ValueError:
            raise Problem("SKILL_INVALID", "frontmatter 双引号字符串格式错误")
    if value.startswith("'"):
        if not value.endswith("'"):
            raise Problem("SKILL_INVALID", "frontmatter 单引号未闭合")
        return value[1:-1].replace("''", "'")
    if value.startswith(("[", "{", "&", "*", "!")):
        raise Problem("SKILL_INVALID", "frontmatter 仅支持安全标量、块文本和 metadata 字符串映射")
    return value.split(" #", 1)[0].strip()


def parse_skill(content, folder):
    """校验 SKILL.md 前置元数据与正文，返回元数据对象和 Markdown 正文。
    folder 必须匹配 name；允许字符串、块文本和 metadata 字符串映射。"""
    lines = content.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        raise Problem("SKILL_INVALID", "SKILL.md 必须以 YAML frontmatter 开始")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise Problem("SKILL_INVALID", "frontmatter 缺少结束分隔符")
    header = lines[1:end]
    meta, i = {}, 0
    while i < len(header):
        line = header[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^([a-z][a-z-]*):\s*(.*)$", line)
        if not match or match[1] in meta:
            raise Problem("SKILL_INVALID", "frontmatter 字段不正确或重复")
        key, value = match.groups()
        if key == "metadata":
            if value.strip():
                raise Problem("SKILL_INVALID", "metadata 使用缩进字符串映射")
            nested = {}
            while i < len(header) and header[i].startswith(" "):
                item = re.match(r"^\s+([\w.-]+):\s*(.*)$", header[i])
                if not item or item[1] in nested:
                    raise Problem("SKILL_INVALID", "metadata 格式错误")
                nested[item[1]] = scalar(item[2])
                i += 1
            meta[key] = nested
        elif value.strip() in ("|", ">", "|-", ">-"):
            block = []
            while i < len(header) and (header[i].startswith(" ") or not header[i]):
                block.append(header[i].strip())
                i += 1
            meta[key] = ("\n" if value.startswith("|") else " ").join(block).strip()
        else:
            meta[key] = scalar(value)
    allowed = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
    if set(meta) - allowed:
        raise Problem("SKILL_INVALID", "不支持的标准 frontmatter 字段：" + ",".join(set(meta) - allowed))
    name = meta.get("name", "")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64 or name != folder:
        raise Problem("SKILL_INVALID", "name 须为小写连字符名称并与 Skill 目录名相同")
    text(meta.get("description"), "description", 1024)
    if "compatibility" in meta:
        text(meta["compatibility"], "compatibility", 500)
    body = "\n".join(lines[end + 1:]).strip()
    if not body:
        raise Problem("SKILL_INVALID", "Skill 正文不能为空")
    return meta, body


class Skills:
    """只管理 Skill 内容快照和注册记录，不拥有功能库或 MCP 的生命周期。"""
    def __init__(self, root, store):
        """建立快照根目录，保留项目根路径用于约束来源和资源访问。"""
        self.root, self.store = Path(root), store
        self.snapshots = store.data / "skills"
        self.snapshots.mkdir(exist_ok=True)

    def import_skill(self, data):
        """从项目内目录或 base64 ZIP 导入一个 Skill，返回其注册记录。
        临时解压先检查路径、链接、大小与文件数量；校验完成才建立快照。
        内容相同则复用记录；不执行 scripts，不启动任何外围服务。"""
        with tempfile.TemporaryDirectory(dir=self.snapshots, prefix=".import-") as staging:
            stage = Path(staging)
            if data.get("archive"):
                try:
                    raw = base64.b64decode(data["archive"], validate=True)
                    archive = zipfile.ZipFile(io.BytesIO(raw))
                except (ValueError, zipfile.BadZipFile):
                    raise Problem("SKILL_INVALID", "无效 ZIP")
                if len(raw) > 20 * 1024 * 1024:
                    raise Problem("UPLOAD_LIMIT", "压缩包不超过 20MB")
                total, seen = 0, set()
                with archive:
                    if len(archive.infolist()) > 2000:
                        raise Problem("UPLOAD_LIMIT", "包文件数超限")
                    for info in archive.infolist():
                        if stat.S_ISLNK(info.external_attr >> 16) or info.filename.casefold() in seen:
                            raise Problem("SKILL_INVALID", "ZIP 包不允许链接或重复路径")
                        seen.add(info.filename.casefold())
                        target = safe_path(stage, info.filename.rstrip("/"))
                        total += info.file_size
                        if total > 40 * 1024 * 1024:
                            raise Problem("UPLOAD_LIMIT", "解压文件不超过 40MB")
                        if info.is_dir():
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with archive.open(info) as src, target.open("wb") as dest:
                                shutil.copyfileobj(src, dest)
                candidates = [p for p in stage.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()]
                if (stage / "SKILL.md").is_file():
                    raise Problem("SKILL_INVALID", "ZIP 请包含以 Skill name 命名的根目录")
                if len(candidates) != 1:
                    raise Problem("SKILL_INVALID", "ZIP 必须有一个 Skill 根目录")
                source = candidates[0]
            else:
                source = safe_path(self.root, data.get("source", ""))
                if source.is_relative_to(self.store.data.resolve()) or not source.is_dir():
                    raise Problem("SKILL_INVALID", "使用项目内的 Skill 源目录，不能导入数据目录")
            if not (source / "SKILL.md").is_file():
                raise Problem("SKILL_INVALID", "没有 SKILL.md")
            meta, body = parse_skill((source / "SKILL.md").read_text(encoding="utf-8"), source.name)
            from .skill_tools import declarations
            tools = declarations(source)
            files, hashes, size, file_hashes = [], hashlib.sha256(), 0, {}
            for p in sorted(source.rglob("*")):
                if p.is_symlink():
                    raise Problem("SKILL_INVALID", "Skill 包不能包含符号链接")
                if not p.is_file():
                    continue
                rel = p.relative_to(source).as_posix()
                if '__pycache__' in p.relative_to(source).parts or p.suffix in ('.pyc', '.pyo'):
                    raise Problem('SKILL_INVALID', '包内不允许Python字节码缓存，请提交源码')
                if p.name.startswith(".env") or p.suffix in (".pem", ".key", ".db"):
                    raise Problem("SKILL_INVALID", "包内不允许凭据或运行数据库")
                if rel in ("skill-package.yaml", "deploy/compose.skill.yaml", "deploy/service_control.py"):
                    raise Problem("SKILL_COUPLED", "请将平台服务声明移入独立 MCP 管理")
                raw = p.read_bytes()
                size += len(raw)
                if size > 40 * 1024 * 1024 or len(files) > 2000:
                    raise Problem("UPLOAD_LIMIT", "Skill 包超出限制")
                hashes.update(rel.encode() + b"\0" + hashlib.sha256(raw).digest())
                file_hashes[rel] = hashlib.sha256(raw).hexdigest()
                files.append(rel)
            for ref in re.findall(r"\]\(([^)]+)\)", body):
                if "://" not in ref and not ref.startswith("#"):
                    target = safe_path(source, ref.split("#", 1)[0])
                    if not target.is_file():
                        raise Problem("SKILL_INVALID", "引用文件不存在：" + ref)
            checksum = hashes.hexdigest()
            dest = self.snapshots / checksum / meta["name"]
            if not dest.exists():
                dest.parent.mkdir(exist_ok=True)
                shutil.copytree(source, dest)
            for existing in self.store.list("skills"):
                if existing["digest"] == checksum:
                    return existing
            previous = next((s for s in self.store.list('skills') if s['name'] == meta['name']), None)
            return self.store.put("skills", {"name": meta["name"], "description": meta["description"],
                "metadata": meta, "body": body, "digest": checksum,
                "path": str(dest.relative_to(self.root)), "files": files, "file_hashes": file_hashes, "tools": tools, "enabled": True}, previous['id'] if previous else None)

    def resource(self, id, relative):
        """按快照文件清单读取受约束路径；未在导入清单中的文件不可访问。
        返回 Path，由上层决定文本读取或下载；此操作不执行资源内容。"""
        skill = self.store.get("skills", id)
        root = self.root / skill["path"]
        target = safe_path(root, relative)
        if relative not in skill["files"] or not target.is_file():
            raise Problem("NOT_FOUND", "Skill 资源不存在", 404)
        return target

    def remove(self, id):
        """有 Agent 引用时拒绝删除；无引用时删除注册，保留内容快照便于恢复。"""
        for agent in self.store.list("agents"):
            if id in agent.get("skill_ids", []):
                raise Problem("IN_USE", "请先从 Agent 解绑此 Skill", 409)
        self.store.delete("skills", id)
