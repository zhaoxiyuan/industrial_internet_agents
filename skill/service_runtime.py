"""Run a package-declared service controller after explicit UI action."""
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from skill.package_validation import safe_file
except ImportError:
    from package_validation import safe_file


def profiles(skill):
    return json.loads(skill['manifest'])['spec']['deployment']['profiles']


def control(skill, profile_id, action):
    if action not in ('start', 'stop', 'status'):
        raise ValueError('不支持的服务操作')
    profile = next((p for p in profiles(skill) if p['id'] == profile_id), None)
    if profile is None:
        raise ValueError('未知部署方式')
    root = Path(skill['package_path']).resolve()
    controller = safe_file(root, profile['controlFile'])
    mcp = json.loads(skill['manifest'])['spec']['tools']['mcp']
    token = os.environ.get(mcp['auth']['secretEnv']) or mcp['auth'].get('token', '')
    if action == 'start' and len(token) < 24:
        raise ValueError('Skill 实例缺少有效的 MCP 服务令牌')
    environment = os.environ.copy()
    if token:
        environment[mcp['auth']['secretEnv']] = token
    result = subprocess.run([sys.executable, str(controller), action, profile_id],
                            cwd=root, env=environment, capture_output=True, text=True, timeout=1200)
    if result.returncode:
        raise ValueError((result.stderr or result.stdout or '服务控制失败').strip()[-2000:])
    try:
        outcome = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError('服务控制文件未返回 JSON 状态') from exc
    if not isinstance(outcome, dict) or (action == 'start' and outcome.get('healthy') is not True):
        raise ValueError('必要服务未通过健康检查：' + str(outcome))
    return outcome
