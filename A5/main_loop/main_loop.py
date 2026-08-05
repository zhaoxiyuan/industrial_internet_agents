"""
main_loop入口 — 支持任意场景组合的批量运行 + A5 agent集成

用法:
  python main_loop/main_loop.py --scenario B          # 单个场景
  python main_loop/main_loop.py --scenario AAAAA       # A 连跑 5 遍
  python main_loop/main_loop.py --scenario ABCDE       # 5 个场景各一遍
  python main_loop/main_loop.py --scenario ABCDEACC    # 任意组合
  python main_loop/main_loop.py --scenario ABCDE --log-dir logs/batch
  python main_loop/main_loop.py --scenario B --no-agent  # 不启动 A5 agent

注:实时 1:1(现实时间 = 场景时间),不支持倍速。
"""
import sys
import argparse
import asyncio
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stream_players.async_collector import run_demo


async def run_scenario_with_agent(
    scenario: str,
    log_dir: str,
    use_agent: bool,
):
    """运行单个场景(可选是否启动 A5 agent)"""
    import asyncio
    from datetime import datetime
    from pathlib import Path

    from stream_players.async_collector import AsyncCollector
    from scenario_data.mock_work_permit import WORK_PERMIT

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    collector = AsyncCollector(scenario, log_dir=log_dir)

    if use_agent:
        # 延迟导入
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from agent.a5_agent import A5Agent
        agent_raw_dir = f"{log_dir}/raw_events"
        agent = A5Agent(
            collector=collector,
            work_permit=WORK_PERMIT,
            raw_event_dir=agent_raw_dir,
        )
    else:
        agent = None

    await collector.start()
    print(f"[已启动 3 个流,实时 30 秒]")
    start_real = asyncio.get_event_loop().time()

    # main_loop:30 秒实时,每秒 agent.tick
    for sec in range(30):
        await asyncio.sleep(1.0)
        await collector.flush_second(sec)

        wall_time = collector._sec_to_wall(sec + 1)
        snapshot = collector.get_snapshot(wall_time)

        if use_agent:
            events = await agent.tick(wall_time, snapshot)
            if events:
                for ev in events:
                    print(f"  [A5][T={ev['second']:2d}s] {ev['type'][:20]:20s} | "
                          f"{ev['person']['name']:6s} | {','.join(ev['supporting_sources'])}")
        else:
            # 无 agent 模式:简单打印
            cv_n = len(snapshot["cv_logs"])
            gas = snapshot["sensors"][0]["readings"]["gas_detector_03"]["status"] if snapshot["sensors"] else "N/A"
            pos = snapshot["positions"][0]["positions"] if snapshot["positions"] else {}
            in_zone = sum(1 for p in pos.values() if p.get("is_in_danger_zone"))
            print(f"  [T={sec+1:2d}s {wall_time[11:19]}] CV={cv_n:3d}条  气体={gas:7s}  危险区={in_zone}人")

    await collector.stop()
    elapsed = asyncio.get_event_loop().time() - start_real

    if use_agent:
        summary = agent.summary()
        print(f"\n[A5Agent 摘要] 共 {elapsed:.1f}s 实际运行")
        print(f"  总事件:{summary['total_events']}")
        for pid, cnt in summary['by_person'].items():
            print(f"  {pid}: {cnt} 个事件")
    else:
        print(f"\n[完成] {len(collector._cv_raw)} CV + {len(collector._sensor_raw)} 传感器 + {len(collector._position_raw)} 定位 ({elapsed:.1f}s)")

    return collector


async def main():
    parser = argparse.ArgumentParser(description="A5 main_loop + agent")
    parser.add_argument(
        "--scenario", default="B",
        help="场景串,每字符一个场景。例如: B / AAAAA / ABCDE / ABCDEACC"
    )
    parser.add_argument("--log-dir", default=None, help="日志根目录")
    parser.add_argument("--no-agent", action="store_true",
                        help="不启动 A5 agent(纯固定流程)")
    args = parser.parse_args()

    scenario_str = args.scenario.upper()
    valid = set("ABCDE")
    for ch in scenario_str:
        if ch not in valid:
            print(f"非法场景字符: '{ch}',可选: {sorted(valid)}")
            return

    n = len(scenario_str)
    use_agent = not args.no_agent
    agent_label = "agent" if use_agent else "纯固定流程"
    print(f"批量运行: {n} 个场景 [{scenario_str}] ({agent_label},实时 1:1,每场景 30 秒)")

    batch_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    for i, ch in enumerate(scenario_str):
        if args.log_dir:
            sub_dir = f"{args.log_dir}/run_{i+1:02d}_{ch}"
        else:
            sub_dir = f"logs/batch_{batch_ts}/run_{i+1:02d}_{ch}"

        print(f"\n{'='*60}")
        print(f"── [{i+1}/{n}] 场景={ch} {'(带A5)' if use_agent else '(无A5)'} ──")
        print(f"{'='*60}")
        await run_scenario_with_agent(ch, sub_dir, use_agent)


if __name__ == "__main__":
    asyncio.run(main())