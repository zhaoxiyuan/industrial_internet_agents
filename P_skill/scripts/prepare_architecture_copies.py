"""Create clean field-deployment trees while preserving pre-existing local files."""
import ast
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLATFORM = next(p for p in ROOT.iterdir() if p.is_dir() and p.name.startswith('skill_decoupling'))
BACKUP = ROOT/'P_skill/.runtime/architecture-backups'/datetime.now().strftime('%Y%m%d-%H%M%S')
EXCLUDED = {'.runtime','.venv','venv','__pycache__','.pytest_cache','node_modules','.git','data','state'}

def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(value,encoding='utf-8',newline='\n')

def preserve(path, destination):
    resolved = path.resolve()
    assert resolved.is_relative_to(ROOT) and resolved != ROOT
    assert destination.resolve().is_relative_to(ROOT/'P_skill/.runtime')
    destination.parent.mkdir(parents=True,exist_ok=True)
    shutil.move(str(path),str(destination))

def clean_existing(base):
    if not base.exists(): return
    for path in list(base.iterdir()):
        if path.is_dir() and path.name in EXCLUDED:
            preserve(path,BACKUP/path.relative_to(ROOT))
        elif path.is_dir(): clean_existing(path)
        elif path.suffix in {'.pyc','.pyo','.log','.db','.sqlite3'} or path.name=='.env' or (path.name.startswith('.env.') and path.name not in {'.env.example','.env.sample'}):
            preserve(path,BACKUP/path.relative_to(ROOT))

def copy_project(source,destination):
    paths=[]
    for directory,dirs,files in os.walk(source):
        dirs[:]=[name for name in dirs if name not in EXCLUDED]
        paths.extend(Path(directory)/name for name in files)
    for path in paths:
        rel=path.relative_to(source)
        if any(part in EXCLUDED for part in rel.parts): continue
        if not path.is_file() or path.suffix in {'.pyc','.pyo','.log','.db','.sqlite3'}: continue
        if path.name=='.env' or (path.name.startswith('.env.') and path.name not in {'.env.example','.env.sample'}): continue
        target=destination/rel
        if target.exists() and target.read_bytes()!=path.read_bytes():
            backup=BACKUP/'changed-files'/target.relative_to(ROOT)
            backup.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(target,backup)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,target)

IGNORE='**/.runtime\n**/.venv\n**/__pycache__\n**/data\nstate\n.git\n**/.env\n**/.env.*\n**/*.log\n'
results={}
for architecture in ('X86_64','arm_64'):
    target=ROOT/architecture
    target.mkdir(exist_ok=True)
    clean_existing(target)
    for source in (ROOT/'P_skill',PLATFORM):copy_project(source,target/source.name)
    write(target/'.dockerignore',IGNORE)
    write(target/'.gitignore',IGNORE+'!**/.env.example\n!**/.env.sample\n')
    if architecture=='X86_64':
        compose=(ROOT/'P_skill/deploy/panshi/compose.yaml').read_text(encoding='utf-8')
        compose=compose.replace('image: industrial-agents:clean','platform: linux/amd64\n    image: industrial-agents:amd64')
        dockerfile=(ROOT/'P_skill/deploy/panshi/Dockerfile').read_text(encoding='utf-8')
        dockerfile=dockerfile.replace('COPY platform/requirements.lock.txt /tmp/platform-requirements.txt',f'COPY ["{PLATFORM.name}/requirements.lock.txt", "/tmp/platform-requirements.txt"]')
        dockerfile=dockerfile.replace('COPY platform /opt/industrial-agents/platform',f'COPY ["{PLATFORM.name}", "/opt/industrial-agents/platform"]')
        write(target/'P_skill/deploy/panshi/Dockerfile',dockerfile)
        write(target/'P_skill/Docker/Dockerfile',dockerfile)
        write(target/'P_skill/deploy/panshi/compose.yaml',compose)
        write(target/'compose.yaml',compose)
        pack=target/'P_skill/deploy/panshi/package_clean.py'
        value=pack.read_text(encoding='utf-8').replace("(platform, 'platform',",f"(platform, {PLATFORM.name!r},")
        write(pack,value)
        write(target/'start.sh','''#!/bin/sh
set -eu
cd "$(dirname "$0")"
test "$(pwd -P)" = /opt/industrial-agents || { echo 'Install this tree at /opt/industrial-agents'; exit 1; }
mkdir -p platform/data/skills state/shared state/player
docker pull docker.1ms.run/library/python:3.12-slim
docker tag docker.1ms.run/library/python:3.12-slim python:3.12-slim
DOCKER_BUILDKIT=0 docker compose build
docker compose up -d --no-build
''')
        environment={'architecture':'linux/amd64','platform_mode':'docker','deployment_root':'/opt/industrial-agents',
            'platform_url':'http://127.0.0.1:18770','public_base':'http://103.236.71.83:18080',
            'nginx_listen':'0.0.0.0:18080','platform_data':'/opt/industrial-agents/platform/data',
            'business_storage':'/opt/industrial-agents/state/shared','executor_player_url':'http://127.0.0.1:18090',
            'executor_container':'p-skill-python-executor','ngrok':False}
        description='''# x86_64 现场版本（磐石云）

业务源码位于 P_skill 与 skill_decoupling‌，与 ARM64 版一致；Docker 固定 linux/amd64。
将本目录内容部署到 /opt/industrial-agents，然后执行 `sh start.sh`。
服务器须先安装 Docker Engine、Docker Compose v2、nginx；平台通过 socket 和只读 Docker CLI 调用执行容器。
平台、播放器、执行容器共用本机网络，平台/播放器只监听 loopback。

公网入口：http://103.236.71.83:18080/skill/ 和 /player/。
nginx 配置：P_skill/deploy/panshi/nginx.conf。使用 18080，不需要 ngrok。
nginx 认证文件须独立安装到 /etc/nginx/industrial-htpasswd.admin 和 industrial-htpasswd.ssh；此副本不携带密码或凭据。
语法检查通过后再 reload nginx。其他现场需同时修改 nginx Origin 映射和 public_base。

`platform/data`、`state/shared`、`state/player` 在首次启动时生成；平台/执行容器使用相同宿主机来源路径，保留挂载一致性校验。
首次为干净平台。Skill ZIP 位于 P_skill/dist，可导入四个包并配置模型、Agent 和授权。
已有 Mac 配置如需导入，仅使用脱敏/受控的 config-only.json；load_skills.py 要求无活动任务。
P10 不自动启用，未预设作业票、心跳计划或业务数据。
本地副本整理不会更新已经运行的服务器。
'''
    else:
        compose=f'''name: p-skill-validation
services:
  skill-executor:
    platform: linux/arm64
    image: industrial-p-skills:arm64
    container_name: p-skill-python-executor
    command: ["python", "-c", "import time; time.sleep(2147483647)"]
    restart: unless-stopped
    volumes:
      - ./{PLATFORM.name}/data/skills:/skills:ro
      - ./P_skill/.runtime/shared:/business
    networks: [pipeline]
  player:
    platform: linux/arm64
    image: industrial-p-skills:arm64
    build:
      context: .
      dockerfile: P_skill/Docker/Dockerfile
    command: ["python", "-m", "mock_player", "serve", "--host", "0.0.0.0", "--port", "8090", "--data-dir", "/player"]
    restart: unless-stopped
    volumes:
      - ./P_skill/.runtime/player:/player
    ports: ["127.0.0.1:18090:8090"]
    networks: [pipeline]
networks:
  pipeline: {{}}
'''
        write(target/'compose.yaml',compose)
        write(target/'P_skill/Docker/Dockerfile','''FROM python:3.12-slim
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV PIP_INDEX_URL=${PIP_INDEX_URL} PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY P_skill/runtime /opt/industrial-runtime
COPY P_skill/Docker/mock_player /opt/mock-player
RUN pip install --no-cache-dir /opt/industrial-runtime /opt/mock-player
CMD ["python", "-c", "import time; time.sleep(2147483647)"]
''')
        write(target/'start-mac.sh',f'''#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="$ROOT/{PLATFORM.name}"
export PATH="/Applications/Docker.app/Contents/Resources/bin:/opt/homebrew/bin:$PATH"
cd "$ROOT"
mkdir -p "$PLATFORM/data/skills" "$ROOT/P_skill/.runtime/shared" "$ROOT/P_skill/.runtime/player"
if [ ! -x "$PLATFORM/.venv/bin/python" ]; then
  python3.12 -m venv "$PLATFORM/.venv"
  "$PLATFORM/.venv/bin/python" -m pip install -i https://mirrors.aliyun.com/pypi/simple/ -r "$PLATFORM/requirements.lock.txt"
  "$PLATFORM/.venv/bin/python" -m pip install -i https://mirrors.aliyun.com/pypi/simple/ "$ROOT/P_skill/runtime" "$ROOT/P_skill/Docker/mock_player"
fi
docker compose build player
docker compose up -d --no-build
exec "$PLATFORM/.venv/bin/python" "$PLATFORM/app.py" --port 18770
''')
        # Replace the fixture-dependent legacy launcher with the native platform entry.
        write(target/'P_skill/scripts/serve_validation.py',f'''"""Mac native platform entry; no test fixture or historical data dependency."""
import os
import runpy
import sys
from pathlib import Path
root = Path(__file__).resolve().parents[2]
platform = root / {PLATFORM.name!r}
os.environ['PATH'] = '/Applications/Docker.app/Contents/Resources/bin:/opt/homebrew/bin:' + os.environ.get('PATH','')
sys.path.insert(0,str(platform))
sys.argv = [str(platform/'app.py'),'--port','18770']
runpy.run_path(str(platform/'app.py'),run_name='__main__')
''')
        environment={'architecture':'linux/arm64','platform_mode':'mac-native-python','platform_url':'http://127.0.0.1:18770',
            'public_base':'https://ladylike-overripe-resource.ngrok-free.dev','nginx_listen':'127.0.0.1:18080',
            'platform_data':f'<deployment-root>/{PLATFORM.name}/data','business_storage':'<deployment-root>/P_skill/.runtime/shared',
            'executor_player_url':'http://player:8090','executor_container':'p-skill-python-executor','ngrok':True}
        description='''# ARM64 现场版本（Apple Silicon Mac）

业务源码位于 P_skill 与 skill_decoupling‌，与 x86_64 版一致；Docker 固定 linux/arm64。
平台在 Mac 宿主机原生 Python 中运行，执行器和播放器由 Docker Desktop 运行。
先安装 Python 3.12、Docker Desktop，并启动 Docker Desktop；执行 `bash start-mac.sh`。
脚本创建本项目虚拟环境和干净数据目录，启动播放器/执行器，再启动 127.0.0.1:18770 平台。
不要与现有 Mac 平台同时启动：两者使用相同的 18770/18090 端口和执行容器名称。

nginx 配置：P_skill/deploy/mac-public.conf，沿用 Mac 的 127.0.0.1:18080 → ngrok 入口与原认证路径。
已有 Mac 的 nginx/ngrok/SSH 入口无需由本副本重新创建。其他 Mac 需修改配置中的用户名、Homebrew 路径、认证文件及域名。
平台数据为 skill_decoupling‌/data；共享数据为 P_skill/.runtime/shared；播放器数据为 P_skill/.runtime/player。
执行器通过 Docker 网络地址 http://player:8090 读取播放器；宿主机通过 http://127.0.0.1:18090 访问。

首次为干净平台。Skill ZIP 位于 P_skill/dist，可导入四个包，配置模型、Agent 和工具授权。
平台登记的业务共享目录必须使用实际 Mac 绝对路径，执行器代码目录为 /skills，业务目录为 /business。
不携带历史任务、会话、播放器记录、模型密钥、飞书凭据或虚拟环境。
P10 不自动启用，未预设作业票或心跳计划。P_skill/deploy/panshi 仅保留跨环境参考，不用于启动 Mac。
本地副本整理不会更新正在运行的 Mac。
'''
    write(target/'environment.json',json.dumps(environment,ensure_ascii=False,indent=2)+'\n')
    write(target/'README.md',description)
    standalone=target/'P_skill/Docker/mock_player/compose.yaml'
    standalone_text=standalone.read_text(encoding='utf-8').replace('  mock-player:\n','  mock-player:\n    platform: '+environment['architecture']+'\n')
    write(standalone,standalone_text)
    platform_readme=target/PLATFORM.name/'README.md'
    write(platform_readme,'> 当前现场部署请先阅读上级目录 README.md 与 environment.json；使用上级目录的启动脚本，端口为 18770。下文保留通用开发说明。\n\n'+platform_readme.read_text(encoding='utf-8'))
    ingress=target/'P_skill/PUBLIC_INGRESS.md'
    write(ingress,'> 当前副本为 '+architecture+'；实际入口、端口和启动方式以上级 README.md、environment.json 和本环境 nginx 配置为准。下文包含 Mac 历史验收记录。\n\n'+ingress.read_text(encoding='utf-8'))
    # Route old Docker entry points to the architecture-specific root composition.
    write(target/'P_skill/Docker/compose.yaml','include:\n  - ../../compose.yaml\n')
    write(target/'P_skill/Docker/compose.timeflow.yaml','# Use the architecture root compose.yaml; no historical image override.\nservices: {}\n')
    results[architecture]={'python_files':sum(1 for p in target.rglob('*.py')),'platform':environment['architecture']}
print(json.dumps({'copies':results,'preserved_existing_files':str(BACKUP),'online_deployments_modified':False},ensure_ascii=False))
