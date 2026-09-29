"""Generic MCP discovery and LangChain tool adaptation for industrial Skill packages."""
import asyncio
import json
import os
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator


def catalog_for(skill):
    root = Path(skill['package_path']).resolve()
    manifest = json.loads(skill['manifest'])
    relative = manifest['spec']['tools']['catalog']
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('工具目录越过 Skill 包边界')
    return yaml.safe_load(path.read_text(encoding='utf-8'))['tools']


def connection(skill):
    config = json.loads(skill.get('runtime_config') or '{}')
    url = config.get('mcpUrl', '')
    ref = config.get('mcpTokenEnv', '')
    if not isinstance(url, str) or not url.startswith(('http://', 'https://')) or not url.endswith('/mcp'):
        raise ValueError('请配置以 /mcp 结尾的 MCP 服务地址')
    token = skill.get('mcp_token') or ''
    if not token and ref:
        if not isinstance(ref, str) or not ref.startswith(('AMAC_', 'SKILL_')):
            raise ValueError('MCP 令牌环境变量名无效')
        token = os.environ.get(ref)
    if not token:
        raise ValueError('请在页面填写 MCP 访问令牌，或设置已配置的令牌环境变量')
    return url, token


async def _request(url, token, name=None, arguments=None):
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with httpx.AsyncClient(headers={'Authorization': f'Bearer {token}'}, timeout=30) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                if name is None:
                    return (await session.list_tools()).tools
                return await session.call_tool(name, arguments or {})


def discover(skill):
    url, token = connection(skill)
    advertised = {tool.name: tool for tool in asyncio.run(_request(url, token))}
    matched, unavailable = [], []
    for declared in catalog_for(skill):
        if declared.get('adapter', {}).get('kind') != 'mcp':
            continue
        remote = advertised.get(declared['name'])
        if remote is None:
            unavailable.append({'name': declared['name'], 'reason': 'MCP 服务未公布该工具'})
            continue
        expected = json.loads((Path(skill['package_path']) / declared['inputSchema']).read_text(encoding='utf-8'))
        actual = remote.inputSchema
        missing = set(expected.get('required', [])) - set(actual.get('required', []))
        incompatible = [key for key, item in expected.get('properties', {}).items()
                        if key in actual.get('properties', {}) and item.get('type')
                        and item['type'] != actual['properties'][key].get('type')]
        if missing or incompatible:
            unavailable.append({'name': declared['name'], 'reason': 'MCP 输入 Schema 与包声明不兼容'})
            continue
        matched.append((declared, remote))
    return matched, unavailable


def invoke(skill, binding_id, declared, arguments, binding_is_active):
    if not binding_is_active(binding_id):
        raise ValueError('Skill 绑定已停用')
    schema = json.loads((Path(skill['package_path']) / declared['inputSchema']).read_text(encoding='utf-8'))
    Draft202012Validator(schema).validate(arguments)
    url, token = connection(skill)
    result = asyncio.run(_request(url, token, declared['name'], arguments))
    if result.isError:
        raise ValueError('MCP 工具执行失败：' + ' '.join(getattr(c, 'text', '') for c in result.content))
    value = result.structuredContent
    if value is None:
        value = '\n'.join(getattr(c, 'text', '') for c in result.content)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError('MCP 工具返回值不是声明的 JSON 对象') from exc
    output = json.loads((Path(skill['package_path']) / declared['outputSchema']).read_text(encoding='utf-8'))
    Draft202012Validator(output).validate(value)
    return json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value


def langchain_tools(skill, binding_id, binding_is_active):
    from langchain_core.tools import StructuredTool
    matched, unavailable = discover(skill)
    result = []
    for declared, remote in matched:
        def execute(_declared=declared, **kwargs):
            return invoke(skill, binding_id, _declared, kwargs, binding_is_active)
        name = skill['name'].replace('-', '_') + '__' + declared['name']
        result.append(StructuredTool(name=name, description=declared['description'],
                                     args_schema=remote.inputSchema, func=execute))
    return result, unavailable
