"""
A6 传感器报警测试

测试场景 C 气体报警 (gas_detector_03 alarm) 能否被 A6 正确研判。
A5 生成传感器事件 → A6 研判 → 输出评估结果

用法:
    python A6/test_sensor_alarm.py
"""
import asyncio
import json
import sys
import time
import shutil
from datetime import datetime
from pathlib import Path

# 添加项目路径
# A6/test_sensor_alarm.py -> A6/ -> 项目根目录
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from A6.agent.tools import A5DataTools, OutputTools
from A6.agent.a6_agent import A6Agent

# ============================================================
# 测试数据：传感器 alarm 事件（person=null）
# ============================================================

def create_sensor_alarm_events():
    """构造场景 C 气体报警的传感器事件"""
    return [
        {
            "event_id": "A5-gas-1786411297211",
            "type": "传感器告警",
            "person": None,
            "wall_time": "2026:08:11T08:57:18.839",
            "first_seen": "2026:08:11T08:57:18.839",
            "last_seen": "2026:08:11T08:57:22.839",
            "status": "active",
            "evidence": {
                "sensor_id": "gas_detector_03",
                "value": 1.0,
                "status": "alarm",
                "unit": "%LEL",
                "threshold_alarm": 1.0,
                "threshold_warning": 0.7,
                "location": "动火区-A 东侧 3 米",
                "sensor_status": "alarm",
                "guard_present": True,
            },
            "explanation": "可燃气体传感器 gas_detector_03 检测到可燃气体浓度达到 1.0 %LEL，触发报警阈值"
        },
        {
            "event_id": "A5-gas-1786411297212",
            "type": "传感器告警",
            "person": None,
            "wall_time": "2026:08:11T08:57:19.839",
            "first_seen": "2026:08:11T08:57:19.839",
            "last_seen": "2026:08:11T08:57:21.839",
            "status": "active",
            "evidence": {
                "sensor_id": "gas_detector_03",
                "value": 1.0,
                "status": "alarm",
                "unit": "%LEL",
                "threshold_alarm": 1.0,
                "threshold_warning": 0.7,
                "location": "动火区-A 东侧 3 米",
                "sensor_status": "alarm",
                "guard_present": True,
            },
            "explanation": "可燃气体持续报警"
        },
    ]


def create_ppe_violation_event():
    """构造 PPE 违规事件（person 有值）"""
    return {
        "event_id": "A5-P7-1786411297300",
        "type": "PPE缺失-头盔",
        "person": {"id": "P7", "name": "张师傅", "role": "焊工"},
        "wall_time": "2026:08:11T08:57:18.839",
        "first_seen": "2026:08:11T08:57:15.839",
        "last_seen": "2026:08:11T08:57:25.839",
        "status": "active",
        "evidence": {
            "cv_frames": 250,
            "helmet_missing_ratio": 0.95,
            "goggles_missing_ratio": 0.0,
            "suit_missing_ratio": 0.0,
            "location": "动火区-A",
            "is_in_danger_zone": True,
            "sensor_status": "alarm",
            "guard_present": True,
        },
        "explanation": "P7 在动火区连续10秒不戴头盔"
    }


# ============================================================
# Log test 写入（参照 A5 格式）
# ============================================================

def get_log_test_dir():
    return _ROOT / "A6" / "log_test"


def save_a6_tick_log(event_id: str, event_data: dict, result: dict):
    """保存 A6 研判日志到 A6/log_test/"""
    log_dir = get_log_test_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_id = event_id.replace(":", "-").replace(".", "_")
    filename = f"tick_{safe_id}_{ts}.json"
    filepath = log_dir / filename
    payload = {
        "event_id": event_id,
        "event_data": event_data,
        "result": result,
        "timestamp": datetime.now().isoformat(),
    }
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"  [log] 保存到 {filename}")
    except Exception as e:
        print(f"  [log] 保存失败: {e}")


def cleanup_log_test(keep_recent: int = 10):
    """清理旧日志，保留最近的 keep_recent 个"""
    log_dir = get_log_test_dir()
    if not log_dir.exists():
        return
    files = sorted(log_dir.glob("tick_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    removed = 0
    for f in files[keep_recent:]:
        try:
            f.unlink()
            removed += 1
        except Exception:
            pass
    print(f"  [cleanup] 清理了 {removed} 个旧日志，保留 {min(keep_recent, len(files))} 个")


# ============================================================
# 测试主体
# ============================================================

async def test_sensor_alarm():
    print("=" * 60)
    print("A6 传感器报警研判测试")
    print("=" * 60)

    agent = A6Agent(
        a5_log_dir=str(_ROOT / "A5" / "logs"),
        a6_output_dir=str(_ROOT / "A6" / "logs")
    )

    # 1. 测试传感器事件（person=None）
    print("\n【测试1】传感器告警事件 (person=None)")
    sensor_events = create_sensor_alarm_events()
    for ev in sensor_events:
        print(f"\n  处理事件: {ev['event_id']} type={ev['type']}")
        result = await agent.process_event(ev['event_id'], {"events": [ev]})
        save_a6_tick_log(ev['event_id'], ev, result)

        status = result.get("status")
        print(f"  状态: {status}")
        if result.get("assessment"):
            a = result["assessment"]
            print(f"  风险等级: {a.get('risk_level_name')}({a.get('risk_level')})")
            print(f"  推理: {a.get('reasoning', '')[:100]}")
        if result.get("tick_ms"):
            print(f"  耗时: {result['tick_ms']}ms")

    # 2. 测试 PPE 违规事件（person 有值）
    print("\n【测试2】PPE 违规事件 (person 有值)")
    ppe_ev = create_ppe_violation_event()
    print(f"\n  处理事件: {ppe_ev['event_id']} type={ppe_ev['type']}")
    result = await agent.process_event(ppe_ev['event_id'], {"events": [ppe_ev]})
    save_a6_tick_log(ppe_ev['event_id'], ppe_ev, result)

    status = result.get("status")
    print(f"  状态: {status}")
    if result.get("assessment"):
        a = result["assessment"]
        print(f"  风险等级: {a.get('risk_level_name')}({a.get('risk_level')})")
        print(f"  推理: {a.get('reasoning', '')[:100]}")
    if result.get("tick_ms"):
        print(f"  耗时: {result['tick_ms']}ms")

    # 3. 测试事件更新（同一个 event_id 再次触发）
    print("\n【测试3】事件更新（同一 event_id 再次触发）")
    ev = sensor_events[0]
    result2 = await agent.process_event(ev['event_id'], {"events": [ev]})
    save_a6_tick_log(ev['event_id'] + "-update", ev, result2)
    print(f"  状态: {result2.get('status')}")
    if result2.get("assessment"):
        a = result2["assessment"]
        print(f"  风险等级: {a.get('risk_level_name')}({a.get('risk_level')})")

    # 4. 清理旧日志
    print("\n【清理】")
    cleanup_log_test(keep_recent=10)

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


# ============================================================
# 定期清理协程（每5分钟一次，参照 A5）
# ============================================================

async def cleanup_loop(interval_sec: int = 300):
    """每 interval_sec 秒清理一次 A6/log_test"""
    while True:
        await asyncio.sleep(interval_sec)
        print(f"[A6 log_test cleanup] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        cleanup_log_test(keep_recent=10)


# ============================================================
# 主入口
# ============================================================

if __name__ == "__main__":
    # 清理旧测试日志
    cleanup_log_test(keep_recent=0)

    asyncio.run(test_sensor_alarm())
