from typing import Dict
def aggregate(sec, cv_logs, sensor_logs, pos_logs) -> Dict:
    sensor_summary = {}
    if sensor_logs:
        for sid, r in sensor_logs[0].get('readings', {}).items():
            sensor_summary[sid] = {'value': r['value'], 'status': r['status']}
    pos_summary = {}
    if pos_logs:
        for wid, p in pos_logs[0].get('positions', {}).items():
            pos_summary[wid] = {'area_id': p['area_id'], 'in_danger_zone': p['is_in_danger_zone']}
    cv_summary = {}
    if cv_logs:
        person_ids = sorted(set((log.get('person_id', '') for log in cv_logs)))
        for pid in person_ids:
            if not pid:
                continue
            person_logs = [log for log in cv_logs if log.get('person_id') == pid]
            total = len(person_logs)
            if total == 0:
                continue

            def _count_missing(class_name):
                return sum((1 for log in person_logs if not next((d['value'] for d in log.get('detections', []) if d['class_name'] == class_name), True)))
            n_helmet = _count_missing('helmet')
            n_goggles = _count_missing('goggles')
            n_suit = _count_missing('protective_suit')
            cv_summary[pid] = {'total_frames': total, 'helmet_missing_count': n_helmet, 'helmet_missing_ratio': round(n_helmet / total, 3), 'goggles_missing_count': n_goggles, 'goggles_missing_ratio': round(n_goggles / total, 3), 'suit_missing_count': n_suit, 'suit_missing_ratio': round(n_suit / total, 3), 'is_violating': n_helmet / total >= 0.8 or n_goggles / total >= 0.8 or n_suit / total >= 0.8}
    return {'time_range': [float(sec), float(sec + 1)], 'cv_log_count': len(cv_logs), 'cv_summary': cv_summary, 'sensor_summary': sensor_summary, 'position_summary': pos_summary}
