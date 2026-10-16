import asyncio
from datetime import datetime, timedelta

from .aggregation import aggregate
from .models import ToolError, TERMINAL
from .scenario_data import SCENARIOS_CV, CV_FPS, scenario_duration, long_shift
from .storage import now, parse_time
from .streams.mock_cv_player import MockCVPlayer
from .streams.mock_sensor_stream import MockSensorStream
from .streams.mock_positioning_stream import MockPositioningStream


NAMES = dict(A="正常作业", B="工人摘头盔", C="可燃气体上升", D="监护人离岗", E="多重违规", F="一小时连续动火作业（60分钟）")


def scenarios():
    return [dict(scenario=s, name=NAMES[s], duration_sec=scenario_duration(s), cv_fps=CV_FPS, sensor_hz=1, position_hz=1,
                 person_ids=sorted(SCENARIOS_CV[s]), **(long_shift.manifest() if s == "F" else {})) for s in sorted(SCENARIOS_CV)]


def sources(scenario, start):
    return MockCVPlayer(scenario), MockSensorStream(scenario, start), MockPositioningStream(scenario, start)


def second_data(scenario, second, start):
    """Deterministic batch helper used by tests; live engine uses the same makers."""
    cv, sensor, pos = sources(scenario, start)
    batches = dict(cv=[], sensor=[], position=[])
    for frame in range(CV_FPS):
        t = second + frame / CV_FPS
        for pid in cv.person_ids_at(t):
            log = cv._make_frame_log(pid, t)
            log["generated_at"] = now()
            log["wall_time"] = (start + timedelta(seconds=t)).isoformat(timespec="milliseconds")
            batches["cv"].append(log)
    for kind, maker in (("sensor", sensor), ("position", pos)):
        log = maker._make_reading(second); log["generated_at"] = now(); batches[kind].append(log)
    return batches


class Engine:
    def __init__(self, store):
        self.store = store; self.task = None

    def start(self, scenario):
        if scenario not in SCENARIOS_CV: raise ToolError("Unknown scenario")
        if self.task and not self.task.done(): raise ToolError("A playback is already active", 409, "conflict")
        self.store.begin(scenario)
        self.task = asyncio.create_task(self.play())
        return self.store.get()

    async def play(self):
        state = self.store.get(); start = datetime.fromisoformat(state["started_at"])
        cv, sensor, pos = sources(state["scenario"], start)
        origin = asyncio.get_running_loop().time()
        try:
            for second in range(state["duration_sec"]):
                batches = dict(cv=[], sensor=[], position=[])
                for frame in range(CV_FPS):
                    # Absolute deadlines avoid cumulative sleep drift; all sources share this clock.
                    t = second + frame / CV_FPS
                    deadline = origin + second + (frame + 1) / CV_FPS
                    await asyncio.sleep(max(0, deadline - asyncio.get_running_loop().time()))
                    for pid in cv.person_ids_at(t):
                        log = cv._make_frame_log(pid, t); log["generated_at"] = now()
                        log["wall_time"] = (start + timedelta(seconds=t)).isoformat(timespec="milliseconds")
                        batches["cv"].append(log)
                    if frame == CV_FPS - 1:
                        for kind, maker in (("sensor", sensor), ("position", pos)):
                            log = maker._make_reading(second); log["generated_at"] = now(); batches[kind].append(log)
                snapshot = aggregate(second, batches["cv"], batches["sensor"], batches["position"])
                self.store.commit(state["scenario"], second, batches, snapshot, parse_time(state["started_at"]))
            self.store.update(state="completed", ended_at=now())
        except asyncio.CancelledError:
            self.store.update(state="stopped", ended_at=now())
            raise
        except Exception as exc:
            self.store.update(state="failed", ended_at=now(), error=dict(type=type(exc).__name__, message=str(exc)))

    async def stop(self):
        state = self.store.get()
        if state["state"] in TERMINAL | {"idle"}: return state
        if self.task:
            self.store.update(state="stopping")
            self.task.cancel()
            try: await self.task
            except asyncio.CancelledError: pass
            # Handles cancellation before the coroutine entered its try block.
            if self.store.get()["state"] == "stopping": self.store.update(state="stopped", ended_at=now())
        return self.store.get()

    async def close(self):
        if self.task and not self.task.done(): await self.stop()
