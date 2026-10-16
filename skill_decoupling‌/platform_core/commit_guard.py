"""Prevent narrative-only completion while a business claim is unsubmitted."""
import json


def pending_commit(messages):
    pending = None
    for message in messages:
        if getattr(message, 'type', None) != 'tool':
            continue
        name = getattr(message, 'name', '') or ''
        short = name.split('__')[-1]
        try:
            envelope = json.loads(message.content)
        except (ValueError, TypeError):
            continue
        if not isinstance(envelope, dict) or envelope.get('isError') or envelope.get('ok') is False:
            continue
        value = envelope.get('structuredContent', envelope)
        if not isinstance(value, dict):
            continue
        if short == 'read_current_window':
            if value.get('status') == 'processing':
                pending = (name.rsplit('__', 1)[0]+'__save_current_description', 'fail_current_window')
            elif value.get('status') in ('completed', 'no_work', 'blocked'):
                pending = None
        elif short == 'claim_description':
            if value.get('status') == 'processing' and (value.get('current_claim') or value.get('claim')):
                pending = (name.rsplit('__', 1)[0]+'__complete_assessment', 'fail_description')
            elif value.get('status') in ('no_work', 'blocked'):
                # A second failed claim does not release the first valid claim.
                pass
        elif short in ('save_current_description', 'complete_assessment') and value.get('status') == 'completed':
            pending = None
        elif short in ('fail_current_window', 'fail_description'):
            pending = None
    return pending


def pending_p7_step(messages):
    """Only follow an explicit successful P7 receipt; blocked/no_work/errors stop it."""
    for message in reversed(messages):
        if getattr(message,'type',None)!='tool':continue
        name=getattr(message,'name','') or ''
        try:value=json.loads(message.content)
        except (ValueError,TypeError):return None
        if not isinstance(value,dict) or value.get('isError') or value.get('ok') is False:return None
        value=value.get('structuredContent',value)
        if not isinstance(value,dict):return None
        if name.endswith('__complete_assessment') and value.get('status')=='completed':
            return value.get('next_step')
        return None
