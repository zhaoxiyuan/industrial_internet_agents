"""
A5Agent -- 作业过程监测智能体

两种调用模式:
  1. tick(wall_time, snapshot) — 保留，内存模式（主循环直连）
  2. tick_from_snapshot(wall_time, snapshot) — 新增，文件模式（读 snapshot 文件）

设计: LangChain create_agent + 无工具 + prompt驱动 + 磁盘事件存储。
"""
import json
import os
import time as time_module
from pathlib import Path
from typing import Dict, Any, List

from langchain.agents import create_agent
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from A5.agent.system_prompt import get_system_prompt
from A5.agent.work_permit_rules import get_rules

class A5Agent:
    def __init__(self, collector, work_permit, raw_event_dir=None):
        self.collector = collector
        self.work_permit = work_permit
        self.raw_event_dir = raw_event_dir
        if self.raw_event_dir: Path(self.raw_event_dir).mkdir(parents=True, exist_ok=True)

        self.llm = self._build_llm()
        self._system_prompt = get_system_prompt()
        self._agent = create_agent(model=self.llm, tools=[],
                                    system_prompt=self._system_prompt)
        self._last_tick_ms = 0.0

    def _build_llm(self):
        # 从 agent_config/.env 读取(前端配置界面写入)
        from dotenv import dotenv_values
        env_path = Path(__file__).resolve().parent.parent.parent / "agent_config" / ".env"
        env = {}
        if env_path.exists():
            env = dotenv_values(env_path)
        # 也读取 OS 环境变量(兜底)
        env.update({k: v for k, v in os.environ.items() if v})

        protocol = env.get("A5_LLM_PROTOCOL", "").lower()
        if not protocol:
            raise RuntimeError(
                "LLM 未配置! 请先启动agent_config前端(端口5000)并选择模型,"
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
        """原有 tick 模式：内存 collector 直连（保留，但移除防未来逻辑）"""
        import time as _time
        t0 = _time.perf_counter()
        # 防未来机制已取消：依赖文件标记而非内存时间拦截
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

    async def tick_from_snapshot(self, wall_time: str, snapshot: Dict) -> Dict[str, Any]:
        """
        新增 tick_from_snapshot 模式：直接读取 snapshot 文件内容进行判断。

        Args:
            wall_time: 当前秒的 wall_time（ISO 格式）
            snapshot: 已解析的 snapshot dict（来自 snapshot_*.json 的 snapshots[0]）

        Returns:
            {"wall_time": "...", "events": [...]} 结构，写入 raw_event_T{xx}.json
        """
        import time as _time
        t0 = _time.perf_counter()

        # 格式兼容：如果 snapshot 只有 cv_summary（秒级聚合）而无 cv_logs，
        # 将 cv_summary 转为 cv_logs 格式供 _build_input 使用
        snapshot = self._normalize_snapshot(snapshot)

        # 构建输入（复用的是 _build_input，但 snapshot 直接传入，不走 collector）
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
        if raw_events:
            raw_events[-1]["_tick_ms"] = round(tick_ms, 2)

        # ── 调试日志 ──
        self._save_tick_log([wall_time], agent_input, raw_output,
                            raw_events, {"wall_time": wall_time, "events": raw_events}, tick_ms)

        return {
            "wall_time": wall_time,
            "events": raw_events,
            "tick_ms": round(tick_ms, 2),
        }

    # ============================================================
    # normalize_snapshot（格式兼容）
    # ============================================================

    def _normalize_snapshot(self, snapshot: Dict) -> Dict:
        """
        格式兼容：如果 snapshot 只有 cv_summary（秒级聚合）而无 cv_logs，
        将 cv_summary 转为 cv_logs 格式供 _build_input 使用。
        """
        if snapshot.get("cv_logs") or not snapshot.get("cv_summary"):
            return snapshot  # 已有 cv_logs 或无 cv_summary，无需转换

        cv_summary = snapshot["cv_summary"]
        cv_logs = []

        for pid, info in cv_summary.items():
            total = info.get("total_frames", 0)
            h_miss = info.get("helmet_missing_count", 0)
            g_miss = info.get("goggles_missing_count", 0)
            s_miss = info.get("suit_missing_count", 0)

            # 按帧数展开，每帧一条"检测记录"
            for frame_idx in range(total):
                is_helmet_missing = frame_idx < h_miss
                is_goggles_missing = frame_idx < g_miss
                is_suit_missing = frame_idx < s_miss

                cv_logs.append({
                    "person_id": pid,
                    "detections": [
                        {"class_name": "helmet",          "value": not is_helmet_missing, "confidence": 0.9},
                        {"class_name": "goggles",         "value": not is_goggles_missing, "confidence": 0.9},
                        {"class_name": "protective_suit", "value": not is_suit_missing,    "confidence": 0.9},
                    ],
                })

        snapshot = dict(snapshot)
        snapshot["cv_logs"] = cv_logs
        return snapshot

    # ============================================================
    # tick_batch 日志（调试用）
    # ============================================================

    def _save_tick_log(self, wall_times: list, batch_input: str, raw_output: str,
                        all_events: list, results: dict, tick_ms: float):
        """保存 tick_batch 的输入输出到 A5/log_test，供调试分析"""
        import time as _time
        if not wall_times:
            return
        key = wall_times[0].replace(":", "-").replace(".", "_")
        ts = _time.strftime("%Y%m%d_%H%M%S")
        log_name = f"tick_{key}_{ts}.json"
        log_dir = Path(__file__).resolve().parent.parent / "log_test"
        log_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "wall_times": wall_times,
            "batch_size": len(wall_times),
            "input_prompt": batch_input,
            "raw_output": raw_output,
            "parsed_events": all_events,
            "grouped_results": results,
            "tick_ms": round(tick_ms, 2),
        }
        try:
            with open(log_dir / log_name, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ============================================================
    # build_input
    # ============================================================
    def _build_input(self, wall_time: str, snapshot) -> str:
        # snapshot 支持单条 dict 或多条 list[dict]
        if isinstance(snapshot, list):
            return self._build_batch_input(wall_time, snapshot)

        cv_logs = snapshot.get("cv_logs", [])
        sensor_summary = snapshot.get("sensor_summary", {})
        pos_summary = snapshot.get("position_summary", {})

        cv = ["PPE检测摘要(当前秒,CV模型,>=80%帧缺失即违规):"]
        for pid, w in self.work_permit.get("workers", {}).items():
            pl = [l for l in cv_logs if l.get("person_id") == pid]
            if not pl: cv.append(f"  {pid}({w.get('name',pid)},{w.get('role','')}): 无数据"); continue
            def _missing_ratio(logs, class_name):
                n = sum(1 for l in logs if not next((d["value"] for d in l["detections"] if d["class_name"]==class_name), True))
                return n/len(logs), n
            h_r, h_n = _missing_ratio(pl, "helmet")
            g_r, g_n = _missing_ratio(pl, "goggles")
            s_r, s_n = _missing_ratio(pl, "protective_suit")
            h_m = "*** 头盔缺失 ***" if h_r>=0.8 else ("**头盔缺失**" if h_r>=0.5 else "")
            g_m = "*** 护目镜缺失 ***" if g_r>=0.8 else ("**护目镜缺失**" if g_r>=0.5 else "")
            s_m = "*** 防护服缺失 ***" if s_r>=0.8 else ("**防护服缺失**" if s_r>=0.5 else "")
            parts = []
            if h_m: parts.append(f"头盔{h_n}帧未戴({h_r:.0%})")
            if g_m: parts.append(f"护目镜{g_n}帧未戴({g_r:.0%})")
            if s_m: parts.append(f"防护服{s_n}帧未戴({s_r:.0%})")
            if parts:
                cv.append(f"  {pid}({w.get('name',pid)},{w.get('role','')}): {len(pl)}帧,{'; '.join(parts)} -> {''.join([h_m,g_m,s_m])}")
            else:
                cv.append(f"  {pid}({w.get('name',pid)},{w.get('role','')}): {len(pl)}帧,头盔/护目镜/防护服均正常")
        se = ["传感器读数(当前秒,动火区-A):"]
        for sid, r in sensor_summary.items():
            tw = r.get("threshold_warning"); ta = r.get("threshold_alarm")
            th = f"(警戒线{tw},报警线{ta})" if ta else ""
            m = "***异常***" if r.get("status")=="alarm" else ("**接近警戒**" if r.get("status")=="warning" else "正常")
            se.append(f"  {sid}({r.get('type', sid)}): {r.get('value','N/A')}{r.get('unit','')} -> {r.get('status','unknown')} {m} {th}")
        po = ["人员位置(当前秒,UWB定位):"]
        for wid, p in pos_summary.items():
            z = "危险区" if p.get("in_danger_zone") else "普通区"
            m = "***不在作业区***" if not p.get("in_danger_zone") else ""
            po.append(f"  {wid}: {p.get('area_id','未知')}({z}) {m}")
        pe = [f"作业票: {self.work_permit['permit_id']}({self.work_permit['level']})",
              f"必戴PPE: {', '.join(self.work_permit['required_ppe'])}",
              f"\n业务规则:\n{get_rules()}"]

        return (
            f"当前现实时间: {wall_time}\n\n"
            + "\n".join(pe) + "\n\n"
            + "\n".join(cv) + "\n\n"
            + "\n".join(se) + "\n\n"
            + "\n".join(po) + "\n\n"
            + "以上是当前现场数据。请判断是否存在需报告的候选安全事件，"
            + "同一人员同一类型的持续违规应逐秒报告，不做去重。"
        )

    # ============================================================
    # build_batch_input
    # ============================================================

    def _build_batch_input(self, wall_time: str, snapshots: list) -> str:
        """
        批量构建输入：多秒 snapshot 合并成一条 prompt。
        snapshots: list of (wall_time, snapshot) tuples
        """
        if not snapshots:
            return ""

        times = [wt for wt, _ in snapshots]
        t_start = times[0]
        t_end = times[-1]
        pe = [f"作业票: {self.work_permit['permit_id']}({self.work_permit['level']})",
              f"必戴PPE: {', '.join(self.work_permit['required_ppe'])}",
              f"\n业务规则:\n{get_rules()}"]

        sections = []
        for i, (wt, snap) in enumerate(snapshots):
            snap = self._normalize_snapshot(snap)
            cv_logs = snap.get("cv_logs", [])
            sensor_summary = snap.get("sensor_summary", {})
            pos_summary = snap.get("position_summary", {})

            cv = [f"[T{i+1}/{len(snapshots)} @ {wt}] PPE检测:"]
            for pid, w in self.work_permit.get("workers", {}).items():
                pl = [l for l in cv_logs if l.get("person_id") == pid]
                if not pl: cv.append(f"  {pid}({w.get('name',pid)}): 无数据"); continue
                def _missing_ratio(logs, class_name):
                    n = sum(1 for l in logs if not next((d["value"] for d in l["detections"] if d["class_name"]==class_name), True))
                    return n/len(logs), n
                h_r, h_n = _missing_ratio(pl, "helmet")
                g_r, g_n = _missing_ratio(pl, "goggles")
                s_r, s_n = _missing_ratio(pl, "protective_suit")
                h_m = "*** 头盔缺失 ***" if h_r>=0.8 else ("**头盔缺失**" if h_r>=0.5 else "")
                g_m = "*** 护目镜缺失 ***" if g_r>=0.8 else ("**护目镜缺失**" if g_r>=0.5 else "")
                s_m = "*** 防护服缺失 ***" if s_r>=0.8 else ("**防护服缺失**" if s_r>=0.5 else "")
                parts = []
                if h_m: parts.append(f"头盔{h_n}帧未戴({h_r:.0%})")
                if g_m: parts.append(f"护目镜{g_n}帧未戴({g_r:.0%})")
                if s_m: parts.append(f"防护服{s_n}帧未戴({s_r:.0%})")
                if parts:
                    cv.append(f"  {pid}({w.get('name',pid)}): {len(pl)}帧,{'; '.join(parts)}->{''.join([h_m,g_m,s_m])}")
                else:
                    cv.append(f"  {pid}({w.get('name',pid)}): {len(pl)}帧,头盔/护目镜/防护服均正常")

            se = [f"[@ {wt}] 传感器:"]
            for sid, r in sensor_summary.items():
                m = "***异常***" if r.get("status")=="alarm" else ("**警戒**" if r.get("status")=="warning" else "正常")
                se.append(f"  {sid}({r.get('type', sid)}): {r.get('value', 'N/A')}{r.get('unit','')} -> {r.get('status','unknown')} {m}")

            po = [f"[@ {wt}] 位置:"]
            for wid, p in pos_summary.items():
                z = "危险区" if p.get("in_danger_zone") else "普通区"
                m = "***不在作业区***" if not p.get("in_danger_zone") else ""
                po.append(f"  {wid}: {p.get('area_id','未知')}({z}) {m}")

            sections.append("\n".join(cv) + "\n" + "\n".join(se) + "\n" + "\n".join(po))

        return (
            f"处理 {len(snapshots)} 秒批量数据，时间范围: {t_start} ~ {t_end}\n\n"
            + "\n".join(pe) + "\n\n"
            + "\n\n".join(sections) + "\n\n"
            + "以上是批量现场数据，每条 [@ 时间] 标注了其归属时间。"
            + "请综合判断是否存在需报告的候选安全事件。"
            + "同一人员同一类型的持续违规，应合并为1个事件（覆盖整个batch时间范围），不逐秒重复输出。"
        )

    # ============================================================
    # tick_batch: 一次调用处理多条
    # ============================================================

    async def tick_batch(self, items: list) -> Dict[str, Dict]:
        """
        一次 agent 调用处理多条 snapshot。
        items: list of (wall_time, snapshot) tuples
        返回: {wall_time: {"wall_time": ..., "events": [...]}, ...}
        """
        import time as _time
        t0 = _time.perf_counter()

        wall_times = [wt for wt, _ in items]
        snapshots  = [snap for _, snap in items]

        # 构建批量输入
        batch_input = self._build_batch_input(wall_times[-1], items)

        try:
            msgs = [SystemMessage(content=self._system_prompt), HumanMessage(content=batch_input)]
            res = await self._agent.ainvoke({"messages": msgs})
            ai = [m for m in res.get("messages", []) if isinstance(m, AIMessage)]
            raw_output = ai[-1].content if ai else "{}"
        except Exception as e:
            raw_output = json.dumps({"is_event": False, "explanation": f"Agent error: {e}"})

        # 解析输出，每个 event 归属到对应秒
        all_events = self._parse_batch_output(raw_output, wall_times)

        # 按 wall_time 分组
        results = {}
        for wt in wall_times:
            results[wt] = {"wall_time": wt, "events": []}
        for ev in all_events:
            ev_wt = ev.get("wall_time")
            if ev_wt and ev_wt in results:
                results[ev_wt]["events"].append(ev)
            elif wall_times:
                results[wall_times[-1]]["events"].append(ev)

        tick_ms = (_time.perf_counter() - t0) * 1000
        if results:
            last = wall_times[-1]
            results[last]["tick_ms"] = round(tick_ms, 2)

        # ── 调试日志 ──
        self._save_tick_log(wall_times, batch_input, raw_output,
                            all_events, results, tick_ms)

        return results

    def _parse_batch_output(self, raw: str, wall_times: list) -> List[Dict]:
        """解析批量输出，尝试从每个 event 中提取 wall_time"""
        if not raw:
            return []
        import re
        batch_start = wall_times[0] if wall_times else ""
        try:
            # 策略1: 提取 ```json 代码块
            json_text = None
            if "```json" in raw:
                parts = raw.split("```json")
                json_text = parts[-1].split("```")[0].strip()
            elif "```" in raw:
                parts = raw.split("```")
                if len(parts) >= 3:
                    json_text = parts[1].strip()

            # 策略2: 尝试从 ```json 块内找到 JSON 数组或对象
            if json_text:
                try:
                    events = json.loads(json_text)
                except json.JSONDecodeError:
                    # 可能块内有 markdown 前缀，尝试找第一个 [ 或 {
                    for start_char, end_char in [("[", "]"), ("{", "}")]:
                        idx_s = json_text.find(start_char)
                        if idx_s >= 0:
                            # 找匹配结束符
                            depth = 0
                            for i, c in enumerate(json_text[idx_s:], idx_s):
                                if c == start_char:
                                    depth += 1
                                elif c == end_char:
                                    depth -= 1
                                    if depth == 0:
                                        try:
                                            events = json.loads(json_text[idx_s:i+1])
                                            break
                                        except Exception:
                                            pass
                            else:
                                continue
                            break
                    else:
                        events = None

                if events is not None:
                    if isinstance(events, dict):
                        events = [events]
                    if isinstance(events, list):
                        results = []
                        for ev in events:
                            if not isinstance(ev, dict) or not ev.get("is_event"):
                                continue
                            if "wall_time" not in ev or not ev["wall_time"]:
                                ev["wall_time"] = wall_times[-1] if wall_times else batch_start
                            if not ev.get("event_id"):
                                pid = (ev.get("person") or {}).get("id", "?")
                                ev["event_id"] = f"A5-{pid}-{int(time_module.time()*1000)}"
                            if not ev.get("first_seen"):
                                ev["first_seen"] = batch_start
                            if not ev.get("last_seen"):
                                ev["last_seen"] = ev.get("wall_time") or batch_start
                            results.append(ev)
                        return results
            # 策略3: 直接正则提取 {...} 对象
            objects = re.findall(r'\{[^{}]*"is_event"\s*:\s*(?:true|false|null)[^{}]*\}', raw)
            if not objects:
                # 更宽松: 包含 is_event 的 {
                objects = re.findall(r'\{[^}]*"is_event"[^}]*\}', raw)
            results = []
            for obj_str in objects:
                try:
                    ev = json.loads(obj_str)
                    if isinstance(ev, dict) and ev.get("is_event"):
                        if "wall_time" not in ev or not ev["wall_time"]:
                            ev["wall_time"] = wall_times[-1] if wall_times else batch_start
                        if not ev.get("event_id"):
                            pid = (ev.get("person") or {}).get("id", "?")
                            ev["event_id"] = f"A5-{pid}-{int(time_module.time()*1000)}"
                        if not ev.get("first_seen"):
                            ev["first_seen"] = batch_start
                        if not ev.get("last_seen"):
                            ev["last_seen"] = ev.get("wall_time") or batch_start
                        results.append(ev)
                except Exception:
                    pass
            return results
        except Exception:
            pass
        return []

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
            results.append({"source":"A5","event_id":p.get("event_id",f"A5-{pid}-{int(time_module.time()*1000)}"),
                     "type":p.get("type","未分类"),"person":pi,"wall_time":wall_time,
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
            # ★ 同步 event 的 first_seen/event_id 并返回给调用者（供下次 tick 复用）
            event["first_seen"] = old_ev.get("first_seen", event.get("first_seen",""))
            event["event_id"] = old_ev.get("event_id", event.get("event_id",""))
            json.dump({"wall_time": event["wall_time"], "events": [old_ev]},
                      open(existing_fp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            return event

        # 新文件：补全 event_id（若无）
        if not event.get("event_id"):
            pid = event.get("person", {}).get("id", "?")
            event["event_id"] = f"A5-{pid}-{int(time_module.time()*1000)}"
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
