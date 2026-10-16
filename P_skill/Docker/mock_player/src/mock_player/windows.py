"""Read-only time windows over raw relational events; no consumer state."""
from .storage import parse_time, iso
from .models import ToolError


def list_windows(store, window_seconds=10, start_time=None, end_time=None, after=None, limit=20):
    if type(window_seconds) is not int or not 1 <= window_seconds <= 300:
        raise ToolError('window_seconds must be 1..300')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ToolError('limit must be 1..100')
    left = parse_time(start_time) if start_time else None
    right = parse_time(end_time) if end_time else None
    cursor = parse_time(after) if after else None
    with store.db() as db:
        db.execute('BEGIN')
        rows = db.execute('SELECT window_start_us,window_end_us FROM acquisition_seconds ORDER BY window_start_us').fetchall()
        player = store.get(db)
        latest = rows[-1][1] if rows else None
        groups, group = [], []
        for a, b in rows:
            if left is not None and b <= left: continue
            if right is not None and a >= right: break
            a, b = max(a, left) if left is not None else a, min(b, right) if right is not None else b
            if group and (a != group[-1][1] or len(group) >= window_seconds):
                groups.append(group); group = []
            group.append((a, b))
        if group: groups.append(group)
        result = []
        for group in groups:
            a, b = group[0][0], group[-1][1]
            if cursor is not None and a < cursor: continue
            if len(group) < window_seconds and b == latest and player['state'] in ('running', 'stopping') and (right is None or b < right): continue
            result.append({'time_range': [iso(a), iso(b)], 'sample_seconds': len(group)})
            if len(result) > limit: break
        more = len(result) > limit
        items = result[:limit]
        return {'items': items, 'has_more': more, 'next_start_time': items[-1]['time_range'][1] if more else None,
                'source_through': iso(latest) if latest else None, 'player_state': player['state']}
