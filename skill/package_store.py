"""Safe ZIP extraction and immutable package snapshots."""
import base64
import io
import shutil
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

try:
    from skill.package_validation import validate_package
except ImportError:
    from package_validation import validate_package


def inspect_source(data, workspace, storage, install=False):
    storage.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='stage-', dir=storage) as staging:
        stage = Path(staging)
        if data.get('archive'):
            extracted = stage / 'extracted'
            extracted.mkdir()
            try:
                raw = base64.b64decode(data['archive'], validate=True)
                archive = zipfile.ZipFile(io.BytesIO(raw))
            except Exception as exc:
                raise ValueError('无法读取 ZIP 包') from exc
            total, seen = 0, set()
            with archive:
                if len(archive.infolist()) > 10000:
                    raise ValueError('ZIP 文件数超过限制')
                for entry in archive.infolist():
                    name = entry.filename
                    rel = PurePosixPath(name)
                    key = name.casefold()
                    if (not name or '\\' in name or ':' in name or rel.is_absolute() or '..' in rel.parts
                        or key in seen or stat.S_ISLNK(entry.external_attr >> 16)):
                        raise ValueError('ZIP 包包含不安全或重复路径')
                    seen.add(key)
                    total += entry.file_size
                    if total > 150 * 1024 * 1024:
                        raise ValueError('ZIP 解压大小超过 150 MB')
                    target = extracted.joinpath(*rel.parts)
                    if entry.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(entry) as src, target.open('wb') as dst:
                            shutil.copyfileobj(src, dst)
            candidates = [extracted] if (extracted/'skill-package.yaml').is_file() else [p for p in extracted.iterdir() if p.is_dir() and (p/'skill-package.yaml').is_file()]
            if len(candidates) != 1:
                raise ValueError('ZIP 须含唯一 Skill 包根目录')
            source = candidates[0]
        else:
            path = data.get('path')
            if not isinstance(path, str) or not path.strip():
                raise ValueError('请选择 ZIP 包或输入本地包目录；不再接受简化 JSON 清单')
            source = (workspace / path).resolve()
            if not source.is_relative_to(workspace.resolve()) or not source.is_dir():
                raise ValueError('本地包目录必须位于当前项目内')
            if storage.resolve().is_relative_to(source) or source.is_relative_to(storage.resolve()):
                raise ValueError('不能以平台数据目录作为导入源')
        report = validate_package(source, data.get('configuration'))
        if not install or not report['valid']:
            return report, None
        # Recheck copied content, preventing edits to the source between validation and import.
        destination = storage / report['digest'].split(':')[1]
        if not destination.exists():
            # TemporaryDirectory on Windows has a private ACL. Renaming a child of it
            # into storage preserves that ACL and can deny the interactive server user.
            # Create the install directory directly under storage to inherit its ACL.
            snapshot = storage / f'.install-{uuid.uuid4().hex}'
            try:
                shutil.copytree(source, snapshot, symlinks=True,
                                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
                copied = validate_package(snapshot, data.get('configuration'))
                if not copied['valid'] or copied['digest'] != report['digest']:
                    raise ValueError('校验期间包内容发生变化，请重试')
                snapshot.rename(destination)
            finally:
                if snapshot.exists() and snapshot.resolve().is_relative_to(storage.resolve()):
                    shutil.rmtree(snapshot)
        else:
            existing = validate_package(destination, data.get('configuration'))
            if not existing['valid'] or existing['digest'] != report['digest']:
                failed = [check['message'] for check in existing['checks'] if check['status'] == 'failed']
                reason = '；'.join(failed[:3]) or f"摘要不一致：预期 {report['digest']}，实际 {existing['digest']}"
                raise ValueError(f'已保存包的内容损坏（{destination}）：{reason}')
        return report, str(destination)
