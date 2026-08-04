"""
MockCVPlayer - 把 CVEvent 时间表展开为 25 FPS 检测流

用法:
    python mock_cv_player.py --scenario B --speed 5.0
    python mock_cv_player.py --scenario B --speed 5.0 --max-logs 75    # 只跑前 75 条
"""
import asyncio
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Dict, Any, List

# 添加项目根目录到 sys.path,支持从 流式播放/ 子目录导入 demo.mock_data
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo.mock_data import (
    SCENARIOS_CV,
    get_person_events_at,
    SCENARIO_DURATION_SEC,
    CV_FPS,
)


class MockCVPlayer:
    """
    模拟 CV 模型,按 25 FPS 持续产出 PPE 检测日志。

    输入:场景 A-E 的 CVEvent 时间表(已定义在 demo/mock_data/mock_cv.py)
    输出:异步生成器,每帧为每个在场人员生成一条检测日志
    """

    # 不同 PPE 类别的置信度偏移(模拟真实模型对不同目标的检测精度差异)
    CONFIDENCE_OFFSETS = {
        "helmet":          0.00,   # 头盔:基准
        "goggles":        -0.03,   # 护目镜:略低(易被头发遮挡)
        "protective_suit":+0.02,   # 防护服:略高(特征明显)
    }

    def __init__(self, scenario: str, speed: float = 1.0):
        """
        Args:
            scenario: 场景 ID,A/B/C/D/E
            speed:   播放速度倍率。1.0=实时,5.0=5 倍速
        """
        if scenario not in SCENARIOS_CV:
            raise ValueError(
                f"未知场景: {scenario},可选: {sorted(SCENARIOS_CV.keys())}"
            )
        if speed <= 0:
            raise ValueError(f"speed 必须 > 0,当前: {speed}")

        self.scenario = scenario
        self.speed = speed
        self.person_ids = sorted(SCENARIOS_CV[scenario].keys())
        self._total_frames = int(SCENARIO_DURATION_SEC * CV_FPS)   # 750

    @property
    def total_frames(self) -> int:
        return self._total_frames

    @property
    def total_logs(self) -> int:
        """整场总日志数(总帧数 × 人数)"""
        return self._total_frames * len(self.person_ids)

    def _make_frame_log(
        self,
        person_id: str,
        scenario_time: float,
    ) -> Dict[str, Any]:
        """
        单人单帧的检测日志。

        输入:人员 ID + 场景时间
        处理:
          1. 查 get_person_events_at → 拿到该时刻的 CVEvent
          2. 把 CVEvent 展开为 3 个 detection(每个 PPE 类别一条)
          3. 应用置信度偏移
        输出:组装好的帧日志 dict
        """
        event = get_person_events_at(self.scenario, person_id, scenario_time)
        base_conf = event.confidence

        detections = []
        for class_name, value in [
            ("helmet",          event.helmet),
            ("goggles",         event.goggles),
            ("protective_suit", event.protective_suit),
        ]:
            offset = self.CONFIDENCE_OFFSETS[class_name]
            conf = max(0.0, min(1.0, base_conf + offset))
            detections.append({
                "class_name": class_name,
                "value":      value,
                "confidence": round(conf, 3),
            })

        return {
            "frame_id":      f"cam_03_{scenario_time:.3f}",
            "scenario_time": f"{scenario_time:.3f}s",
            "wall_time":     datetime.now().isoformat(timespec="milliseconds"),
            "person_id":     person_id,
            "detections":    detections,
        }

    async def stream(self) -> AsyncIterator[Dict[str, Any]]:
        """
        异步生成器:按 25 FPS 持续产出检测日志。

        每帧按人员顺序依次产出(同一帧的 3 条日志连续 yield)。
        """
        scenario_start_wall = time.time()

        for frame_no in range(self._total_frames):
            scenario_time = frame_no / CV_FPS

            # 速度控制:计算到这帧应该出现在现实时间的时刻
            target_wall_time = scenario_start_wall + scenario_time / self.speed
            sleep_sec = target_wall_time - time.time()
            if sleep_sec > 0:
                await asyncio.sleep(sleep_sec)

            # 为每个在场人员生成一条日志
            for person_id in self.person_ids:
                yield self._make_frame_log(person_id, scenario_time)


# ============================================================
# 流式打印(独立运行入口)
# ============================================================

async def stream_print(scenario: str, speed: float, max_logs: int = None):
    """
    流式打印场景的 CV 检测日志。

    Args:
        scenario: 场景 ID
        speed:    播放速度
        max_logs: 最大日志数(测试用,None 表示跑完全部)
    """
    player = MockCVPlayer(scenario, speed)
    persons = player.person_ids

    print("=" * 78)
    print(f"  MockCVPlayer 流式播放")
    print(f"  场景: {scenario}  |  速度: {speed}x  |  人员: {persons}")
    print(f"  总帧数: {player.total_frames}  |  总日志: {player.total_logs}")
    print("=" * 78)

    log_count = 0
    last_print_sec = -1

    try:
        async for log in player.stream():
            log_count += 1
            scenario_time = float(log["scenario_time"].rstrip("s"))
            current_sec = int(scenario_time)

            # 每秒打印一次摘要(同一秒内的 3 人只打印一行汇总)
            if current_sec != last_print_sec:
                last_print_sec = current_sec
                print(f"\n[T={log['scenario_time']}] 帧 {current_sec * 25}/{player.total_frames}")

            # 打印单人检测结果
            helmet = next(d for d in log["detections"] if d["class_name"] == "helmet")
            goggles = next(d for d in log["detections"] if d["class_name"] == "goggles")
            suit = next(d for d in log["detections"] if d["class_name"] == "protective_suit")

            helmet_mark = "[H]" if helmet["value"] else "[X]"
            goggles_mark = "[G]" if goggles["value"] else "[X]"
            suit_mark = "[S]" if suit["value"] else "[X]"

            print(
                f"  {log['person_id']}: "
                f"helmet={helmet_mark}({helmet['confidence']:.2f}) "
                f"goggles={goggles_mark}({goggles['confidence']:.2f}) "
                f"suit={suit_mark}({suit['confidence']:.2f})"
            )

            if max_logs and log_count >= max_logs:
                print(f"\n[达到 max_logs={max_logs},提前停止]")
                break

    except asyncio.CancelledError:
        print("\n[用户中断]")

    print("\n" + "=" * 78)
    print(f"  [OK] 播放完成,共 {log_count} 条检测日志")
    print("=" * 78)


def main():
    parser = argparse.ArgumentParser(
        description="MockCVPlayer 流式播放 CV 检测日志"
    )
    parser.add_argument(
        "--scenario", default="B",
        choices=["A", "B", "C", "D", "E"],
        help="场景 ID(默认 B)",
    )
    parser.add_argument(
        "--speed", type=float, default=1.0,
        help="播放速度倍率(默认 5x,1.0=实时)",
    )
    parser.add_argument(
        "--max-logs", type=int, default=None,
        help="最大日志数(测试用,默认跑完全部 750 条)",
    )
    args = parser.parse_args()

    asyncio.run(stream_print(args.scenario, args.speed, args.max_logs))


if __name__ == "__main__":
    main()