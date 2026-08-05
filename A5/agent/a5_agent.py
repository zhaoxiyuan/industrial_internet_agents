"""
A5Agent -- 作业过程监测智能体

调用模式: 主循环每 1 秒调一次 tick(wall_time, snapshot)。
设计: LangChain create_agent + 8 工具 + 无状态(磁盘驱动)。
"""
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

from langchain.agents import create_agent
from langchain_core.prompts import PromptTemplate
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent.tools import (
    ALL_TOOLS, set_dependencies, set_raw_event_dir,
    )
from agent.system_prompt import get_system_prompt
from agent.work_permit_rules import get_rules

class A5Agent:
    def __init__(self, collector, work_permit, raw_event_dir=None):
        self.collector = collector
        self.work_permit = work_permit
        set_dependencies(collector=collector, vl_model=None, work_permit=work_permit)
        self.raw_event_dir = raw_event_dir
        set_raw_event_dir(raw_event_dir)
        if self.raw_event_dir: Path(self.raw_event_dir).mkdir(parents=True, exist_ok=True)

        self.llm = self._build_llm()
        self._system_prompt = get_system_prompt()
        try:
            self._agent = create_agent(model=self.llm, tools=ALL_TOOLS,
                                        system_prompt=self._system_prompt)
            self._use_v1 = True
        except Exception:
            self._use_v1 = False
        self._last_tick_ms = 0.0

    def _build_llm(self):
        # 从 智能体配置/.env 读取(前端配置界面写入)
        from dotenv import dotenv_values
        env_path = Path(__file__).resolve().parent.parent / "智能体配置" / ".env"
        env = {}
        if env_path.exists():
            env = dotenv_values(env_path)
        # 也读取 OS 环境变量(兜底)
        env.update({k: v for k, v in os.environ.items() if v})

        protocol = env.get("A5_LLM_PROTOCOL", "").lower()
        if not protocol:
            raise RuntimeError(
                "LLM 未配置! 请先启动智能体配置前端(端口5000)并选择模型,"
                "或设置环境变量 A5_LLM_PROTOCOL/A5_LLM_API_KEY 等"
            )

        if protocol == "openai":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=env.get("A5_LLM_MODEL", "gpt-4o"),
                api_key=env.get("A5_LLM_API_KEY", ""),
                base_url=env.get("A5_LLM_BASE_URL", None),
                temperature=float(env.get("A5_LLM_TEMPERATURE", "0")),
            )
        if protocol == "ollama":
            from langchain_community.chat_models import ChatOllama
            return ChatOllama(
                model=env.get("A5_LLM_MODEL", "qwen2.5:7b"),
                base_url=env.get("A5_LLM_BASE_URL", "http://localhost:11434"),
                temperature=float(env.get("A5_LLM_TEMPERATURE", "0")),
            )
        raise RuntimeError(f"不支持的 LLM 协议: {protocol}")

    # ============================================================
    # tick
    # ============================================================

    async def tick(self, wall_time: str, snapshot: Dict, agent_now: str = None) -> List[Dict]:
        import time as _time
        t0 = _time.perf_counter()
        # ★ 将 _agent_now 设为评估窗口的结束时间(即 _query_end),
        #   而不是 wall_time+1s。这样 _build_input 扫描窗口时
        #   query_raw_logs 不会被误拦截。
        #   注意: get_snapshot 已改为默认不检查 _agent_now(仅前端用)。
        query_end = snapshot.get("_query_end", wall_time)
        self.collector.set_agent_now(query_end)
        agent_input = self._build_input(wall_time, snapshot)

        try:
            msgs = [SystemMessage(content=self._system_prompt), HumanMessage(content=agent_input)]
            res = await self._agent.ainvoke({"messages": msgs})
            ai = [m for m in res.get("messages", []) if isinstance(m, AIMessage)]
            raw_output = ai[-1].content if ai else "{}"
            intermediate = []
        except Exception as e:
            raw_output = json.dumps({"is_event": False, "explanation": f"Agent error: {e}"})
            intermediate = []

        raw_events = self._parse_output(raw_output, wall_time, snapshot)
        for ev in raw_events:
            self._save_raw_event(ev, intermediate)
        tick_ms = (_time.perf_counter() - t0) * 1000
        if raw_events: raw_events[-1]["_tick_ms"] = round(tick_ms, 2)
        else: self._last_tick_ms = round(tick_ms, 2)
        return raw_events

    # ============================================================
    # build_input
    # ============================================================
    def _build_input(self, wall_time: str, snapshot: Dict) -> str:
        cv_logs = snapshot.get("cv_logs", [])
        sensors = snapshot.get("sensors", [{}])
        positions = snapshot.get("positions", [{}])

        # ── 如果评估窗口 > 1 秒,扫描整个窗口找出所有异常秒 ──
        q_start = snapshot.get("_query_start")
        q_end   = snapshot.get("_query_end")
        window_violations = []      # PPE 违规秒
        window_sensor_alarms = []   # 传感器 alarm 秒
        window_supervisor_absent = []  # 监护人离岗秒
        if q_start and q_end and self.collector:
            try:
                from datetime import datetime as _dt, timedelta as _td
                t0 = _dt.fromisoformat(q_start)
                t1 = _dt.fromisoformat(q_end)
                total_win_sec = max(1, int((t1 - t0).total_seconds()))
                for offset in range(total_win_sec):
                    s_wall = (t0 + _td(seconds=offset)).isoformat(timespec="milliseconds")
                    e_wall = (t0 + _td(seconds=offset + 1)).isoformat(timespec="milliseconds")

                    # 1) PPE 扫描(CV 日志)
                    sec_logs = self.collector.query_raw_logs(s_wall, e_wall, "cv")
                    for pid in self.work_permit.get("workers", {}):
                        pl = [l for l in sec_logs if l.get("person_id") == pid]
                        if not pl: continue
                        n_h = sum(1 for l in pl if not next(
                            (d["value"] for d in l["detections"] if d["class_name"]=="helmet"), True))
                        if n_h / len(pl) >= 0.8:
                            st = pl[0].get("scenario_time", "?s").rstrip("s")
                            window_violations.append(
                                f"  ** T≈{st}s: {pid} 头盔缺失({n_h}/{len(pl)}帧) **")

                    # 2) 传感器扫描
                    sec_sensors = self.collector.query_raw_logs(s_wall, e_wall, "sensor")
                    if sec_sensors:
                        for r in sec_sensors[0].get("readings", {}).values():
                            if r.get("status") in ("alarm", "warning"):
                                window_sensor_alarms.append(
                                    f"  ** T≈{offset}s: {r['type']} {r['value']}{r.get('unit','')} -> {r['status']} **")

                    # 3) 监护人位置扫描
                    sec_pos = self.collector.query_raw_logs(s_wall, e_wall, "position")
                    if sec_pos:
                        for wid, p in sec_pos[0].get("positions", {}).items():
                            worker = self.work_permit.get("workers", {}).get(wid, {})
                            if worker.get("role") == "监护人" and not p.get("is_in_danger_zone"):
                                window_supervisor_absent.append(
                                    f"  ** T≈{offset}s: {wid}({worker.get('name',wid)}) 在 {p.get('area_id','?')}(不在危险区) **")
            except Exception:
                pass

        cv = ["PPE检测摘要(当前秒,CV模型,>=80%帧缺失即违规):"]
        for pid, w in self.work_permit.get("workers", {}).items():
            pl = [l for l in cv_logs if l.get("person_id") == pid]
            if not pl: cv.append(f"  {pid}({w.get('name',pid)},{w.get('role','')}): 无数据"); continue
            n = sum(1 for l in pl if not next((d["value"] for d in l["detections"] if d["class_name"]=="helmet"), True))
            r = n/len(pl); m = "*** 头盔缺失 ***" if r>=0.8 else "正常"
            cv.append(f"  {pid}({w.get('name',pid)},{w.get('role','')}): {len(pl)}帧,{n}帧未戴头盔({r:.0%}) -> {m}")
        se = ["传感器读数(当前秒,动火区-A):"]
        if sensors and sensors[0].get("readings"):
            for r in sensors[0]["readings"].values():
                tw = r.get("threshold_warning"); ta = r.get("threshold_alarm")
                th = f"(警戒线{tw},报警线{ta})" if ta else ""
                m = "***异常***" if r["status"]=="alarm" else ("**接近警戒**" if r["status"]=="warning" else "正常")
                se.append(f"  {r['type']}: {r['value']}{r['unit']} -> {r['status']} {m} {th}")
        po = ["人员位置(当前秒,UWB定位):"]
        if positions and positions[0].get("positions"):
            for wid, p in positions[0]["positions"].items():
                z = "危险区" if p.get("is_in_danger_zone") else "普通区"
                m = "***不在作业区***" if not p.get("is_in_danger_zone") else ""
                po.append(f"  {wid}: {p['area_id']}({z}) {m}")
        pe = [f"作业票: {self.work_permit['permit_id']}({self.work_permit['level']})",
              f"必戴PPE: {', '.join(self.work_permit['required_ppe'])}",
              f"\n业务规则:\n{get_rules()}"]

        # 窗口违规扫描结果(最重要:agent 必须先看这个)
        win_sec = ""
        if q_start and q_end:
            total_anomalies = len(window_violations) + len(window_sensor_alarms) + len(window_supervisor_absent)
            lines = []
            if window_violations:
                lines.append(f"  [PPE缺失] {len(window_violations)}秒:")
                lines.extend(window_violations)
            if window_sensor_alarms:
                # 去重
                seen = set(); uniq = []
                for a in window_sensor_alarms:
                    if a not in seen: seen.add(a); uniq.append(a)
                lines.append(f"  [传感器告警] {len(uniq)}条:")
                lines.extend(uniq)
            if window_supervisor_absent:
                seen = set(); uniq = []
                for a in window_supervisor_absent:
                    if a not in seen: seen.add(a); uniq.append(a)
                lines.append(f"  [监护人离岗] {len(uniq)}条:")
                lines.extend(uniq)
            win_sec = (
                f"\n=====================================================\n"
                f"!!! 评估窗口扫描({q_start[11:19]}~{q_end[11:19]},共{total_anomalies}个异常):\n"
                + ("\n".join(lines) if lines else "  未发现任何异常")
                + f"\n=====================================================\n"
            )

        return (
            f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
            f"!!! 注意: 当前现实时间是 {wall_time} !!!\n"
            f"!!! 你只能查询 <= 这个时间点的日志,不能看未来 !!!\n"
            f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n\n"
            + "\n".join(pe) + "\n"
            + win_sec + "\n"
            + "\n".join(cv) + "\n\n"
            + "\n".join(se) + "\n\n"
            + "\n".join(po) + "\n\n"
            + "以上是当前现场数据 + 评估窗口内的异常扫描。请判断是否存在需报告的候选安全事件。"
            + "先调用 query_past_events 检查是否已报告过,避免重复。"
            + (f"\n本次评估覆盖时间窗口: {q_start or '?'} ~ {q_end or '?'} "
               f"(可用 query_raw_logs 查此区间任意秒的趋势)" if q_start else "")
        )

    # ============================================================
    # parse
    # ============================================================
    def _parse_output(self, raw: str, wall_time: str, snapshot: Dict) -> List[Dict]:
        if not raw: return []
        original_raw = raw  # 保存原始输出用于调试
        try:
            # 提取 ``` 代码块中的 JSON
            if "```json" in raw:
                parts = raw.split("```json")
                # 取最后一段 ```json ... ``` (最可能是模型输出)
                raw = parts[-1].split("```")[0]
            elif "```" in raw:
                parts = raw.split("```")
                raw = parts[1].split("```")[0] if len(parts) > 1 else raw
            raw = raw.strip()

            # 尝试解析 JSON 数组
            if raw.startswith("["):
                # 找到匹配的 ]
                bracket_count = 0
                end_idx = -1
                for i, ch in enumerate(raw):
                    if ch == '[': bracket_count += 1
                    elif ch == ']':
                        bracket_count -= 1
                        if bracket_count == 0:
                            end_idx = i
                            break
                if end_idx > 0:
                    raw = raw[:end_idx + 1]
                parsed = json.loads(raw)
            elif raw.startswith("{"):
                # 可能多个独立 JSON 对象(非数组格式)
                # 尝试找到所有顶层 {...} 对象
                objects = []
                depth = 0; start = -1
                for i, ch in enumerate(raw):
                    if ch == '{':
                        if depth == 0: start = i
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0 and start >= 0:
                            objects.append(raw[start:i+1])
                if len(objects) > 1:
                    # 多个独立对象 → 合并为数组
                    parsed = [json.loads(obj) for obj in objects]
                else:
                    parsed = json.loads(raw)
            else:
                return []
        except json.JSONDecodeError:
            # 最后一次尝试: 正则提取所有 {...} 对象
            import re
            objects = re.findall(r'\{[^{}]*\}', raw)
            if objects:
                try:
                    parsed = [json.loads(obj) for obj in objects]
                except json.JSONDecodeError:
                    return []
            else:
                return []

        # 支持数组和单个 dict
        items = parsed if isinstance(parsed, list) else [parsed]
        results = []
        sec = int(float(snapshot.get("scenario_time","0s").rstrip("s")))
        for p in items:
            if not isinstance(p, dict) or not p.get("is_event"): continue
            pi = p.get("person", {}); pid = pi.get("id","?")
            results.append({"source":"A5","event_id":p.get("event_id",f"A5-{pid}-{int(time.time()*1000)}"),
                     "type":p.get("type","未分类"),"person":pi,"second":sec,"wall_time":wall_time,
                     "first_seen":p.get("first_seen",wall_time),"last_seen":wall_time,
                     "status":p.get("status","ongoing"),"evidence":p.get("evidence",{}),
                     "explanation":p.get("explanation",""),"note":"A5不判定risk_level,由A6完成"})
        return results

    # ============================================================
    # save
    # ============================================================
    def _save_raw_event(self, event: Dict, intermediate: List):
        if not self.raw_event_dir: return

        # 找已有文件(按 person_id + type 匹配 ongoing 事件)
        existing_fp = None
        pid = event.get("person", {}).get("id", "")
        etype = event.get("type", "")
        rd = Path(self.raw_event_dir) if isinstance(self.raw_event_dir, str) else self.raw_event_dir
        for f in sorted(rd.glob("raw_event_*.json"), reverse=True):
            try:
                d = json.load(open(f, encoding="utf-8"))
                ev = d["events"][0]
                if (ev.get("person", {}).get("id") == pid and ev.get("type") == etype
                        and ev.get("status") in ("ongoing", "ongoing_update")):
                    existing_fp = f
                    break
            except Exception: pass

        if existing_fp:
            # 覆盖写:更新 last_seen, 保持原 first_seen(供前端卡片匹配)
            old = json.load(open(existing_fp, encoding="utf-8"))
            old_ev = old["events"][0]
            old_ev["last_seen"] = event["wall_time"]
            if old_ev.get("first_seen"):
                t0 = __import__("datetime").datetime.fromisoformat(old_ev["first_seen"])
                t1 = __import__("datetime").datetime.fromisoformat(event["wall_time"])
                old_ev["duration_sec"] = round((t1 - t0).total_seconds(), 1)
            old_ev["explanation"] = event.get("explanation", old_ev.get("explanation", ""))
            if event.get("status") == "closed":
                old_ev["status"] = "closed"
            else:
                old_ev["status"] = "ongoing"
            # ★ 同步 event 的 first_seen(前端卡片用此匹配)
            event["first_seen"] = old_ev.get("first_seen", event.get("first_seen",""))
            event["event_id"] = old_ev.get("event_id", event.get("event_id",""))
            json.dump({"wall_time": event["wall_time"], "events": [old_ev]},
                      open(existing_fp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            return

        # 新文件
        safe = event.get("first_seen", event["wall_time"]).replace(":","").replace("-","").replace(".","_")
        fp = rd / f"raw_event_{safe}.json"
        pl = {"wall_time": event["wall_time"], "events": [event],
              "intermediate_steps": [{"action": str(a), "observation": str(o)[:200]}
                                     for a, o in (intermediate or [])]}
        fp.parent.mkdir(parents=True, exist_ok=True)
        json.dump(pl, open(fp,"w",encoding="utf-8"), ensure_ascii=False, indent=2)

    def summary(self) -> Dict[str, Any]:
        import glob
        evs = []
        if self.raw_event_dir:
            for f in sorted(glob.glob(f"{self.raw_event_dir}/raw_event_*.json")):
                evs.extend(json.load(open(f,encoding="utf-8")).get("events",[]))
        bp = {}
        for ev in evs: bp[ev.get("person",{}).get("id","?")] = bp.get(ev.get("person",{}).get("id","?"),0)+1
        return {"total_events":len(evs),"by_person":bp}


# ============================================================
# AgentQueue: 主循环不阻塞,Agent 后台串行
# ============================================================

class AgentQueue:
    """
    Agent 任务串行队列。主循环 fire-and-forget,Agent 排队执行。
    约束: 同一时刻只跑一个 tick,下一个等上一个完成。
    """

    def __init__(self, agent: A5Agent):
        self.agent = agent
        self._queue = asyncio.Queue()
        self._running = False
        self._task = None

    def submit(self, start_wall: str, end_wall: str, snapshot: Dict, callback=None):
        """主循环调用: 丢任务到队列,立即返回。
           start_wall/end_wall: agent 可查询的累积时间段"""
        self._queue.put_nowait((start_wall, end_wall, snapshot, callback))
        if not self._running:
            self._task = asyncio.create_task(self._worker())

    async def _worker(self):
        self._running = True
        try:
            while not self._queue.empty():
                start_wall, end_wall, snapshot, callback = await self._queue.get()
                # 追加趋势: 不替换当前秒 snapshot,而是额外提供累积趋势数据
                # (agent 用 query_raw_logs 工具按需查趋势,不稀释当前秒精度)
                snapshot["_query_start"] = start_wall
                snapshot["_query_end"]   = end_wall
                try:
                    import time
                    t0 = time.perf_counter()
                    events = await self.agent.tick(end_wall, snapshot)
                    tick_ms = (time.perf_counter() - t0) * 1000
                    if events:
                        events[-1]["_tick_ms"] = round(tick_ms, 2)
                    if callback:
                        await callback(events, tick_ms, None)
                except Exception as e:
                    import traceback
                    if callback:
                        await callback([], 0, traceback.format_exc())
        finally:
            self._running = False

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()
