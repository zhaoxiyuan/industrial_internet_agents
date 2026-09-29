"""Industrial Skill package validation. Never imports or executes package code."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

SCHEMA_PATH = Path(__file__).parent / 'schemas' / 'skill-package.schema.json'


def load_document(path):
    import yaml
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('声明文件超过 4 MB')
    return yaml.safe_load(path.read_text(encoding='utf-8-sig'))


def safe_file(root, name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('文件引用必须是包内相对路径')
    rel = PurePosixPath(name)
    if rel.is_absolute() or '..' in rel.parts:
        raise ValueError('文件引用越过包边界')
    target = root.joinpath(*rel.parts)
    if any(p.is_symlink() for p in [target, *target.parents] if p != root.parent):
        raise ValueError('不接受符号链接')
    if not target.resolve().is_relative_to(root.resolve()) or not target.is_file():
        raise ValueError('引用文件不存在或位于包外')
    return target


def check_json_schema(schema):
    from jsonschema import Draft202012Validator
    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ('$ref','$dynamicRef') and (not isinstance(child,str) or not child.startswith('#')):
                    raise ValueError('JSON Schema 仅允许文件内引用，禁止远程加载')
                walk(child)
        elif isinstance(value,list):
            for child in value:
                walk(child)
    walk(schema)
    Draft202012Validator.check_schema(schema)


def package_files(root):
    result = {}
    total = 0
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('包包含符号链接')
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        if rel == 'package.lock.json':
            continue
        total += path.stat().st_size
        if total > 150 * 1024 * 1024 or len(result) >= 10000:
            raise ValueError('包超过 150 MB 或 10000 文件限制')
        if ((path.name.startswith('.env') and not path.name.endswith(('.example','.sample'))) or
            path.name in ('htpasswd', 'saved_configs.json') or '.local.' in path.name or path.suffix in ('.pem', '.key')):
            raise ValueError(f'包中含本地凭据文件：{rel}')
        content = path.read_bytes()
        if re.search(rb'sk-[A-Za-z0-9_-]{30,}',content):
            raise ValueError(f'包中疑似包含明文 API Key：{rel}')
        result[rel] = hashlib.sha256(content).hexdigest()
    return result


def validate_package(folder, configuration=None):
    from jsonschema import Draft202012Validator
    root = Path(folder).resolve()
    checks = []

    def add(code, status, message, field='', phase='package'):
        checks.append(dict(code=code, status=status, message=message, field=field, phase=phase))

    report = dict(valid=False, deployable=False, checks=checks, checkedAt=datetime.now(timezone.utc).isoformat(),
                  manifest=None, digest=None, name=None, version=None, description='')
    try:
        manifest = load_document(safe_file(root, 'skill-package.yaml'))
        schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        errors = sorted(Draft202012Validator(schema).iter_errors(manifest), key=lambda e: str(e.path))
        for error in errors:
            add('manifest.schema', 'failed', error.message, '.'.join(map(str, error.path)))
        if errors:
            return report
        report['manifest'] = manifest
        spec, meta = manifest['spec'], manifest['metadata']
        report.update(name=meta['name'], version=meta['version'])
        add('manifest.schema', 'passed', '工业清单结构符合 v1alpha1')
        import yaml
        skill = safe_file(root, spec['skill']['entrypoint']).read_text(encoding='utf-8-sig')
        match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', skill, re.S)
        if not match:
            raise ValueError('SKILL.md 缺少 YAML frontmatter 或正文')
        front = yaml.safe_load(match[1])
        if not isinstance(front, dict) or front.get('name') != meta['name'] or not isinstance(front.get('description'), str) or not front['description'].strip() or not match[2].strip():
            raise ValueError('SKILL.md 名称须与清单一致，并包含 description 和正文')
        report['description'] = front['description']
        add('skill.frontmatter', 'passed', '名称、描述和正文有效')
        # Resolve every explicitly declared file reference; no dynamic execution.
        def refs(value, field='spec'):
            if isinstance(value, dict):
                for key, child in value.items():
                    label = field + '.' + key
                    if key in ('entrypoint', 'catalog', 'schema', 'bundleRef', 'controlFile', 'inputSchema', 'outputSchema', 'sourceFile'):
                        safe_file(root, child)
                    else:
                        refs(child, label)
            elif isinstance(value, list):
                for i, child in enumerate(value):
                    refs(child, f'{field}[{i}]')
        refs(spec)
        for link in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)', match[2]):
            if '://' not in link:
                safe_file(root, link)
        add('files.references', 'passed', '引用文件存在，且均在包内')
        config_schema = load_document(safe_file(root, spec['configuration']['schema']))
        check_json_schema(config_schema)
        for key, definition in config_schema.get('properties', {}).items():
            if definition.get('x-scope') not in ('instance','binding','run'):
                raise ValueError(f'配置字段 {key} 缺少合法 x-scope')
            if not definition.get('x-apply') or not isinstance(definition.get('x-sensitive'), bool):
                raise ValueError(f'配置字段 {key} 缺少生效方式或敏感性声明')
            if definition['x-sensitive'] and definition.get('default'):
                raise ValueError(f'敏感配置字段 {key} 不得携带默认密钥')
        if configuration is not None:
            for err in Draft202012Validator(config_schema).iter_errors(configuration):
                add('config.values', 'blocked', err.message, '.'.join(map(str, err.path)), 'deployment')
        else:
            add('config.values', 'blocked', '尚未提供现场配置，Secret 引用和服务地址待检查', phase='deployment')
        add('config.schema', 'passed', '配置 JSON Schema 有效')
        services = spec['services']
        by_id = {s['serviceId']: s for s in services}
        if spec['tools']['mcp']['serviceRef'] not in by_id:
            raise ValueError('MCP serviceRef 必须引用已声明服务')
        if len(by_id) != len(services):
            raise ValueError('外围服务 serviceId 重复')
        visited, active = set(), set()
        def visit(service_id):
            if service_id not in by_id:
                raise ValueError(f'引用未知服务：{service_id}')
            if service_id in active:
                raise ValueError('服务依赖存在循环')
            if service_id in visited:
                return
            active.add(service_id)
            for dependency in by_id[service_id]['dependsOn']:
                visit(dependency)
            active.remove(service_id)
            visited.add(service_id)
        for sid in by_id:
            visit(sid)
        add('services.graph', 'passed', f'{len(services)} 个外围服务依赖无环')
        for service in services:
            sid = service['serviceId']
            digest = service['image'].get('verifiedDigest')
            if not digest:
                add('image.lock', 'blocked', '尚无已验证镜像 digest，禁止托管部署', sid, 'deployment')
            elif not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
                add('image.lock', 'failed', '镜像 digest 格式错误', sid)
            else:
                add('image.lock', 'passed', '镜像已声明内容摘要（运行时仍需核验）', sid)
            add('service.live', 'blocked', '目标节点资源、网络、镜像及健康未现场验证', sid, 'deployment')
            if service['placement']['serviceScope'] not in ('sameSite','sameNode','remoteAllowed'):
                raise ValueError(f'{sid} 的位置约束无效')
            if not set(service['image']['architectures']).issubset(spec['compatibility']['architectures']):
                raise ValueError(f'{sid} 镜像架构与包运行约束不一致')
            if not all(isinstance(port,int) and 1 <= port <= 65535 for port in service['network']['ports']):
                raise ValueError(f'{sid} 端口声明非法')
        catalog = load_document(safe_file(root, spec['tools']['catalog']))
        if not isinstance(catalog, dict) or not isinstance(catalog.get('tools'), list):
            raise ValueError('工具目录必须包含 tools 数组')
        names = set()
        required = {'name','description','inputSchema','outputSchema','adapter','services','timeoutSeconds',
                    'concurrency','effect','retry','idempotency','context','permission'}
        for tool in catalog['tools']:
            if not isinstance(tool, dict):
                raise ValueError('工具条目必须是对象')
            if required - tool.keys():
                raise ValueError('工具契约缺少必填字段：' + ','.join(sorted(required - tool.keys())))
            if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', tool['name']) or tool['name'] in names:
                raise ValueError('工具名称不合法或重复')
            names.add(tool['name'])
            if not set(tool['services']).issubset(by_id):
                raise ValueError(f"工具 {tool['name']} 引用未知服务")
            for key in ('inputSchema','outputSchema'):
                check_json_schema(load_document(safe_file(root, tool[key])))
            adapter = tool['adapter']
            if not isinstance(adapter, dict) or adapter.get('kind') not in ('langchain-tool','http-client','http','mcp'):
                raise ValueError('不支持的工具适配器类型')
            if adapter.get('sourceFile'):
                source = safe_file(root, adapter['sourceFile'])
                if adapter.get('symbol'):
                    import ast
                    symbols = {node.name for node in ast.walk(ast.parse(source.read_text(encoding='utf-8-sig'))) if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))}
                    if adapter['symbol'] not in symbols:
                        raise ValueError(f"工具源码找不到符号：{adapter['symbol']}")
            if adapter.get('status') == 'ready' and adapter.get('kind') != 'mcp':
                raise ValueError(f"工具 {tool['name']} 必须通过 MCP 调用")
            if adapter.get('status') != 'ready':
                add('tool.adapter', 'blocked', adapter.get('reason','工具执行适配器未就绪'), tool['name'], 'deployment')
            else:
                add('tool.registration', 'blocked', '包声明已就绪，但平台执行器尚未完成工具注册确认', tool['name'], 'deployment')
            if tool['effect'] not in ('read','write') or not isinstance(tool['timeoutSeconds'], int) or tool['timeoutSeconds'] <= 0:
                raise ValueError('工具读写性质或超时声明错误')
            if not isinstance(tool['concurrency'],int) or tool['concurrency'] < 1 or not tool['permission'] or not tool['idempotency']:
                raise ValueError('工具并发、权限或幂等声明无效')
            if tool['effect']=='write' and tool['retry'].get('maxAttempts',1) > 1:
                add('tool.retry','blocked','写操作重试策略需验证服务幂等',tool['name'],'deployment')
        add('tools.contract', 'passed', f'{len(names)} 个工具契约及 Schema 有效')
        for ui in spec.get('ui', []):
            if ui.get('serviceRef') and ui['serviceRef'] not in by_id:
                raise ValueError('UI 引用未知服务')
            if ui['type'] == 'service-page' and not re.fullmatch(r'https?://[^\s]+', ui.get('url', '')):
                raise ValueError('service-page 必须声明可展示的 http(s) URL')
        for profile in spec['deployment']['profiles']:
            if profile['mode'] != 'directory' and not profile.get('bundleRef'):
                raise ValueError('托管部署模式缺少 bundleRef')
            controller = safe_file(root, profile['controlFile'])
            if controller.suffix != '.py':
                raise ValueError('服务控制文件必须是包内 Python 文件')
            if profile['mode'] in ('compose','local-docker'):
                compose = load_document(safe_file(root,profile['bundleRef']))
                declared = compose.get('services',{})
                if not isinstance(declared,dict) or not set(declared).issubset(by_id):
                    raise ValueError('Compose 包含未声明的外围服务')
                for sid, service in declared.items():
                    if service.get('image','').endswith(':latest'):
                        raise ValueError('正式部署模板不得使用 latest 镜像')
                    build=service.get('build')
                    if build:
                        if isinstance(build,str):
                            build={'context':build}
                        bundle_dir=safe_file(root,profile['bundleRef']).parent
                        context=(bundle_dir/build.get('context','.')).resolve()
                        dockerfile=(context/build.get('dockerfile','Dockerfile')).resolve()
                        if not context.is_relative_to(root) or not dockerfile.is_relative_to(root) or not dockerfile.is_file():
                            raise ValueError(f'{sid} 的镜像构建上下文或 Dockerfile 无效')
                add('deployment.template','passed',f'{len(declared)} 个 Compose 服务均已声明',profile['id'])
        for service in services:
            if not set(service['profiles']).issubset({p['id'] for p in spec['deployment']['profiles']}):
                raise ValueError('服务引用未知部署 profile')
        for dependency in spec['dependencies'].get('skills',[]):
            name = dependency.get('name') if isinstance(dependency,dict) else dependency
            if name == meta['name']:
                raise ValueError('Skill 不得依赖自身')
            add('skill.dependency','blocked','需解析依赖 Skill 的版本与就绪状态',str(name),'deployment')
        files = package_files(root)
        content = json.dumps(files, sort_keys=True, separators=(',', ':')).encode()
        report['digest'] = 'sha256:' + hashlib.sha256(content).hexdigest()
        lock = load_document(safe_file(root, 'package.lock.json'))
        if lock.get('files') != files or lock.get('digest') != report['digest']:
            raise ValueError('package.lock.json 与实际文件不一致，请重新生成包锁文件')
        add('package.integrity', 'passed', f'已核验 {len(files)} 个文件的 SHA-256')
    except (ValueError, OSError, KeyError, TypeError) as exc:
        add('package.invalid', 'failed', str(exc))
    except Exception as exc:
        add('package.parse', 'failed', f'无法解析包：{type(exc).__name__}')
    report['valid'] = not any(c['status'] == 'failed' for c in checks)
    report['deployable'] = report['valid'] and not any(c['status'] == 'blocked' for c in checks)
    return report


def write_lock(root):
    root = Path(root)
    files = package_files(root)
    digest = 'sha256:' + hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    (root / 'package.lock.json').write_text(json.dumps({'digest': digest, 'files': files}, ensure_ascii=False, indent=2), encoding='utf-8')
    return digest


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('folder')
    parser.add_argument('--lock', action='store_true')
    args = parser.parse_args()
    if args.lock:
        print(write_lock(args.folder))
    else:
        result = validate_package(args.folder)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result['valid'] else 1)
