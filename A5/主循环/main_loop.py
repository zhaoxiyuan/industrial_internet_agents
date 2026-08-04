"""
主循环入口 — 支持任意场景组合的批量运行

用法:
  python 主循环/main_loop.py --scenario B          # 单个场景
  python 主循环/main_loop.py --scenario AAAAA       # A 连跑 5 遍
  python 主循环/main_loop.py --scenario ABCDE       # 5 个场景各一遍
  python 主循环/main_loop.py --scenario ABCDEACC    # 任意组合
  python 主循环/main_loop.py --scenario ABCDE --speed 5.0 --log-dir logs/batch
"""
import sys
import argparse
import asyncio
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from 流式播放.async_collector import run_demo


async def main():
    parser = argparse.ArgumentParser(description="A5 主循环(固定流程,无智能体)")
    parser.add_argument(
        "--scenario", default="B",
        help="场景串,每字符一个场景。例如: B / AAAAA / ABCDE / ABCDEACC"
    )
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--log-dir", default=None, help="日志根目录")
    args = parser.parse_args()

    scenario_str = args.scenario.upper()
    # 校验每个字符是否为合法场景
    valid = set("ABCDE")
    for ch in scenario_str:
        if ch not in valid:
            print(f"非法场景字符: '{ch}',可选: {sorted(valid)}")
            return

    n = len(scenario_str)
    print(f"批量运行: {n} 个场景 [{scenario_str}],速度={args.speed}x")

    # 统一时间戳(整个批次共用)
    batch_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    all_collectors = []
    for i, ch in enumerate(scenario_str):
        if args.log_dir:
            sub_dir = f"{args.log_dir}/run_{i+1:02d}_{ch}"
        else:
            sub_dir = f"logs/batch_{batch_ts}/run_{i+1:02d}_{ch}"

        print(f"\n── [{i+1}/{n}] 场景={ch} ──")
        collector = await run_demo(ch, args.speed, sub_dir)
        all_collectors.append(collector)

    # 批量汇总
    print(f"\n{'='*50}")
    print(f"批量运行完成: {n} 个场景")
    for i, (ch, c) in enumerate(zip(scenario_str, all_collectors)):
        cv_n = len(c._cv_raw)
        sensor_n = len(c._sensor_raw)
        pos_n = len(c._position_raw)
        print(f"  [{i+1:02d}] 场景={ch}  CV={cv_n}  传感器={sensor_n}  定位={pos_n}")
    print(f"{'='*50}")


if __name__ == "__main__":
    asyncio.run(main())
