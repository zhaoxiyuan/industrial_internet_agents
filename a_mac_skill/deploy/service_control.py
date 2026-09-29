"""Control the required a_mac services for directory and local Docker profiles.

Called only after an explicit platform start/stop action. Emits one JSON status.
"""
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(tempfile.gettempdir()) / ('amac-skill-' + __import__('hashlib').sha256(str(ROOT).encode()).hexdigest()[:12] + '.json')
LOG_DIR = Path(tempfile.gettempdir()) / ('amac-skill-logs-' + __import__('hashlib').sha256(str(ROOT).encode()).hexdigest()[:12])
PORTS = {'gateway': 8787, 'p6': 5002, 'p8p9': 8089, 'app_config': 5000, 'feishu_cfg': 5003, 'mcp_bridge': 8090}
DOCKER_SERVICES = [*PORTS, 'webui', 'chat_reply', 'nginx']
COMMANDS = {
    'gateway': ['node', 'gateway/start.mjs', '--config', 'gateway/config/config.feishu.local.json'],
    'p6': [sys.executable, 'agents/p6_monitor_agent.py', '--host', '127.0.0.1', '--port', '5002'],
    'p8p9': [sys.executable, 'P8P9/web_server.py'],
    'app_config': [sys.executable, 'frontend/app_config.py', '--host', '127.0.0.1', '--port', '5000'],
    'feishu_cfg': [sys.executable, '-m', 'feishu_gateway_cli.feishu_config_app', '--host', '127.0.0.1', '--port', '5003'],
    'mcp_bridge': [sys.executable, '-m', 'scripts.mcp_server'],
}


def listening(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False


def http_health(port):
    try:
        with urlopen(f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def snapshot():
    return {name: (http_health(port) if name in ('p6', 'p8p9', 'app_config', 'feishu_cfg') else listening(port))
            for name, port in PORTS.items()}


def output():
    services = snapshot()
    print(json.dumps({'healthy': all(services.values()), 'services': services}))


def site_files():
    configured = os.environ.get('AMAC_SITE_ROOT')
    candidates = [Path(configured)] if configured else [ROOT, *(parent / name for parent in ROOT.parents for name in ('a_mac', 'a'))]
    for site in candidates:
        env_file = site / '.env'
        gateway_env = site / 'gateway/.env'
        gateway_config = site / 'gateway/config/config.feishu.local.json'
        if env_file.is_file() and gateway_env.is_file() and gateway_config.is_file():
            return env_file.resolve(), gateway_env.resolve(), gateway_config.resolve()
    raise ValueError('缺少现场 .env、gateway/.env 或 gateway/config/config.feishu.local.json；请设置 AMAC_SITE_ROOT 指向已配置的 a_mac 目录')


def check_gateway_config(gateway_env, gateway_config, inherited):
    available = dict(inherited)
    for line in gateway_env.read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            available[key.strip()] = value.strip()
    required = set(re.findall(r'\$\{([A-Z_][A-Z0-9_]*)\}', gateway_config.read_text(encoding='utf-8')))
    missing = sorted(name for name in required if not available.get(name))
    if missing:
        raise ValueError('Gateway 现场配置缺少环境变量：' + ', '.join(missing) + '；请在现场 gateway/.env 中配置')


def failure_details(services):
    missing = [name for name, healthy in services.items() if not healthy]
    details = []
    for name in missing:
        log = LOG_DIR / f'{name}.log'
        if log.is_file():
            lines = log.read_text(encoding='utf-8', errors='replace').splitlines()
            details.append(f'{name}: ' + ' | '.join(lines[-4:])[-800:])
    return f'必要服务未就绪：{", ".join(missing)}。' + (' 日志：' + '；'.join(details) if details else '')


def main(action, profile):
    env = os.environ.copy()
    if action == 'start' or (profile == 'local-source' and action == 'stop'):
        env_file, gateway_env, gateway_config = site_files()
        if action == 'start':
            check_gateway_config(gateway_env, gateway_config, env)
        env['AMAC_SITE_ENV_FILE'] = str(env_file)
        env['AMAC_SITE_GATEWAY_ENV_FILE'] = str(gateway_env)
        env['AMAC_SITE_GATEWAY_CONFIG_DIR'] = str(gateway_config.parent)
        env['AMAC_GATEWAY_ENV_FILE'] = str(gateway_env)
        env['AMAC_SITE_DATA_DIR'] = str(env_file.parent / 'data')
        env['AMAC_SITE_AGENT_CONFIG_DIR'] = str(env_file.parent / 'agent_config')
        env['AMAC_SITE_A5_LOG_DIR'] = str(env_file.parent / 'A5' / 'logs')
        env['AMAC_SITE_NGINX_HTPASSWD'] = str(env_file.parent / 'deploy' / 'nginx' / 'htpasswd')
        if action == 'start' and profile == 'directory' and not shutil.which('node'):
            raise ValueError('目录运行需要 Node.js，当前找不到 node 命令')
    if profile == 'local-source':
        docker = shutil.which('docker') or '/Applications/Docker.app/Contents/Resources/bin/docker'
        command = [docker, 'compose', '-p', 'amac-skill', '-f', 'deploy/compose.skill.yaml']
        if action == 'start':
            command += ['up', '-d', '--build', *DOCKER_SERVICES]
        elif action == 'stop':
            command += ['stop', *DOCKER_SERVICES]
        elif action != 'status':
            raise ValueError('unsupported action')
        else:
            output()
            return
        result = subprocess.run(command, cwd=ROOT, env=env, timeout=1200, capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f'Docker Compose {"启动" if action == "start" else "停止"}失败：' + (result.stderr or result.stdout)[-1200:])
    elif profile == 'directory':
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        if action == 'start':
            if not os.environ.get('AMAC_MCP_TOKEN'):
                raise ValueError('AMAC_MCP_TOKEN must be configured on the platform host')
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            for name, args in COMMANDS.items():
                if listening(PORTS[name]):
                    continue
                if name == 'gateway':
                    args = [*args[:-1], str(gateway_config)]
                log = (LOG_DIR / f'{name}.log').open('ab')
                try:
                    proc = subprocess.Popen(args, cwd=ROOT, env=env,
                                            stdout=log, stderr=subprocess.STDOUT,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                finally:
                    log.close()
                state[name] = proc.pid
                STATE.write_text(json.dumps(state))
        elif action == 'stop':
            for name, pid in reversed(list(state.items())):
                if pid and name in COMMANDS:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            STATE.unlink(missing_ok=True)
        elif action != 'status':
            raise ValueError('unsupported action')
    else:
        raise ValueError('unknown profile')
    if action == 'start':
        for _ in range(30):
            if all(snapshot().values()):
                break
            time.sleep(1)
        services = snapshot()
        if not all(services.values()):
            raise ValueError(failure_details(services))
    output()


if __name__ == '__main__':
    try:
        main(sys.argv[1], sys.argv[2])
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
