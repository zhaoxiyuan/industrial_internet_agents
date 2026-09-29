"""MCP facade for the a_mac HTTP operations that already have service APIs.

Local development: python -m scripts.mcp_server
The fixed development token below is the default. An explicit AMAC_MCP_TOKEN
overrides it when this service is deployed beyond the local proof of concept.
"""
import json
import os
import re
from pathlib import Path
import sys

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

if os.environ.get('AMAC_ENV_FILE'):
    load_dotenv(os.environ['AMAC_ENV_FILE'], override=False)
if os.environ.get('AMAC_DATA_DIR'):
    os.environ.setdefault('P8P9_BASE_DIR', str(Path(os.environ['AMAC_DATA_DIR']).resolve() / 'jobs'))

LOCAL_DEV_MCP_TOKEN = 'amac-local-dev-mcp-6f5294a908bd4ecaab7c18d031fc2e40'

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import call


def _p8_tool(tool_name: str, **arguments) -> dict:
    """Run the existing P8 implementation with MCP-owned data/config access."""
    from agents import p8_disposition_agent as p8
    result = getattr(p8, tool_name).func(**arguments)
    value = json.loads(result) if isinstance(result, str) else result
    if not isinstance(value, dict):
        raise ValueError(f'{tool_name} returned a non-object response')
    if value.get('error'):
        error = value['error']
        raise ValueError(error.get('message') or error.get('code') or f'{tool_name} failed')
    if value.get('status') == 'error':
        raise ValueError(value.get('message') or value.get('code') or f'{tool_name} failed')
    if tool_name in {'open_work_ticket', 'resend_current_card'}:
        sent = value.get('result', {}).get('cards_sent', 0)
        if sent < 1:
            raise ValueError(f'{tool_name} did not send a Feishu card; check gateway and job state')
    return value

mcp = FastMCP('a_mac_skill', host=os.getenv('AMAC_MCP_HOST', '127.0.0.1'),
              port=int(os.getenv('AMAC_MCP_PORT', '8090')), stateless_http=True,
              json_response=True)


@mcp.tool()
def monitor_start(job_id: str, scenario: str = '', duration_sec: int = 0,
                  interval_sec: int = 0, batch_size: int = 0, play_delay_sec: int = 0) -> dict:
    """Start P6 monitoring for a job."""
    data = {'job_id': job_id}
    if scenario:
        data['scenario'] = scenario
    data.update({k: v for k, v in {'duration_sec': duration_sec,
                                   'interval_sec': interval_sec,
                                   'batch_size': batch_size,
                                   'play_delay_sec': play_delay_sec}.items() if v})
    return call('monitor-start', data)


@mcp.tool()
def monitor_stop(job_id: str) -> dict:
    """Stop P6 monitoring for a job."""
    return call('monitor-stop', {'job_id': job_id})


@mcp.tool()
def monitor_status(job_id: str) -> dict:
    """Read P6 monitoring status for a job."""
    return call('monitor-status', {'job_id': job_id})


@mcp.tool()
def p6_events(job_id: str) -> dict:
    """Read P6 events for a job."""
    return call('p6-events', {'job_id': job_id})


@mcp.tool()
def p7_records(job_id: str, limit: int = 100) -> dict:
    """Read P7 assessment records for a job."""
    return call('p7-records', {'job_id': job_id, 'limit': limit})


@mcp.tool()
def closure_get(job_id: str) -> dict:
    """Read a P8/P9 closure record."""
    return call('closure-get', {'job_id': job_id})


@mcp.tool()
def feishu_events(after_sequence: int = 0, limit: int = 100) -> dict:
    """Read Feishu gateway events."""
    return call('feishu-events', {'after_sequence': after_sequence, 'limit': limit})


@mcp.tool()
def feishu_reply(eventId: str, text: str, idempotency_key: str = '') -> dict:
    """Reply to a Feishu event."""
    return call('feishu-reply', {'eventId': eventId, 'text': text,
                                 'idempotency_key': idempotency_key})


@mcp.tool()
def feishu_send(channel: str, account_id: str, to: dict, text: str,
                idempotency_key: str = '') -> dict:
    """Send a Feishu message."""
    return call('feishu-send', {'channel': channel, 'account_id': account_id,
                                'to': to, 'text': text, 'idempotency_key': idempotency_key})


@mcp.tool()
def feishu_ack(event_id: str, status: str, details: dict = {}) -> dict:
    """Acknowledge a Feishu event."""
    return call('feishu-ack', {'event_id': event_id, 'status': status, 'details': details or {}})


@mcp.tool()
def read_p7_events(job_id: str) -> dict:
    """Read P7 risk events from the shared job directory."""
    if not re.fullmatch(r'[A-Za-z0-9_-]+', job_id):
        raise ValueError('invalid job_id')
    return _p8_tool('read_p7_events', job_id=job_id)


@mcp.tool()
def open_work_ticket(events: list[dict], job_id: str, risk_basis: str = '',
                     chat_id: str = '', group_name: str = '', account_id: str = '') -> dict:
    """Create a P8/P9 work ticket and send its Feishu card to a specified group."""
    if not job_id or not (chat_id or group_name):
        raise ValueError('job_id and chat_id or group_name are required')
    from P8P9.services import card_render
    if not card_render._FEISHU_AVAILABLE:
        raise ValueError('Feishu card sender is unavailable')
    return _p8_tool('open_work_ticket', events=events, risk_basis=risk_basis,
                    job_id=job_id, chat_id=chat_id or None,
                    group_name=group_name or None, account_id=account_id or None)


@mcp.tool()
def resend_current_card(job_id: str) -> dict:
    """Resend the current P8/P9 Feishu card for a job."""
    from P8P9.services import card_render
    if not card_render._FEISHU_AVAILABLE:
        raise ValueError('Feishu card sender is unavailable')
    return _p8_tool('resend_current_card', job_id=job_id)


@mcp.tool()
def lookup_feishu_directory(entity_type: str, name: str = '', id: str = '') -> dict:
    """Look up groups or users in the MCP service's Feishu directory config."""
    return _p8_tool('lookup_feishu_directory', entity_type=entity_type, name=name, id=id)


@mcp.tool()
def recall_jobs(query: str = '', detail_job_id: str = '') -> dict:
    """Search archived P8 jobs in the shared job directory."""
    if detail_job_id and not re.fullmatch(r'[A-Za-z0-9_-]+', detail_job_id):
        raise ValueError('invalid detail_job_id')
    return _p8_tool('recall_jobs', query=query, detail_job_id=detail_job_id or None)


class BearerGate:
    def __init__(self, app, token):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and dict(scope.get('headers', [])).get(b'authorization') != b'Bearer ' + self.token:
            from starlette.responses import PlainTextResponse
            await PlainTextResponse('Unauthorized', status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)


if __name__ == '__main__':
    host = os.getenv('AMAC_MCP_HOST', '127.0.0.1')
    token = os.environ.get('AMAC_MCP_TOKEN')
    token = token or LOCAL_DEV_MCP_TOKEN
    if len(token) < 24:
        raise SystemExit('AMAC_MCP_TOKEN must contain at least 24 characters')
    import uvicorn
    uvicorn.run(BearerGate(mcp.streamable_http_app(), token),
                host=host,
                port=int(os.getenv('AMAC_MCP_PORT', '8090')))
