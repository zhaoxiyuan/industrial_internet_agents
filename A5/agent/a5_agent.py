"""
A5Agent -- 作业过程监测智能体

调用模式: 主循环每 1 秒调一次 tick(wall_time, snapshot)。
设计: LangChain create_agent + 8 工具 + 无状态(磁盘驱动)。
"""
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
    analyze_ppe_compliance, query_past_events,
)
from agent.system_prompt import get_system_prompt

class MockChatModel(BaseChatModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="{}"))])
    def _llm_type(self) -> str: return "mock"
    @property
    def _identifying_params(self) -> Dict[str, Any]: return {"model": "mock"}

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
        p = os.getenv("LLM_PROVIDER", "").lower()
        if p == "openai":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model=os.getenv("LLM_MODEL","gpt-4o"),
                              api_key=os.getenv("OPENAI_API_KEY"),
                              base_url=os.getenv("OPENAI_BASE_URL",None), temperature=0)
        if p == "ollama":
            from langchain_community.chat_models import ChatOllama
            return ChatOllama(model=os.getenv("LLM_MODEL","qwen2.5:7b"),
                              base_url=os.getenv("OLLAMA_BASE_URL","http://localhost:11434"),
                              temperature=0)
        return MockChatModel()

    # ============================================================
    # tick
    # ============================================================

    async def tick(self, wall_time: str, snapshot: Dict, agent_now: str = None) -> List[Dict]:
        import time as _time
        t0 = _time.perf_counter()
        if agent_now is None:
            from datetime import datetime as _dt, timedelta as _td
            agent_now = (_dt.fromisoformat(wall_time) + _td(seconds=1)).isoformat(timespec="milliseconds")
        self.collector.set_agent_now(agent_now)
        agent_input = self._build_input(wall_time, snapshot)

        if isinstance(self.llm, MockChatModel):
            raw_output = self._rule_fallback(wall_time, snapshot)
            intermediate = []
        else:
            try:
                if self._use_v1:
                    msgs = [SystemMessage(content=self._system_prompt), HumanMessage(content=agent_input)]
                    res = await self._agent.ainvoke({"messages": msgs})
                    ai = [m for m in res.get("messages",[]) if isinstance(m, AIMessage)]
                    raw_output = ai[-1].content if ai else "{}"
                    intermediate = []
                else:
                    res = await self._agent.ainvoke({"input": json.dumps(agent_input, ensure_ascii=False)})
                    raw_output = res.get("output","{}")
                    intermediate = res.get("intermediate_steps",[])
            except Exception as e:
                raw_output = json.dumps({"is_event":False, "explanation":f"Agent error:{e}"})
                intermediate = []
            if not raw_output or raw_output.strip() in ("","{}"):
                raw_output = self._rule_fallback(wall_time, snapshot)
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
        cv = ["PPE检测摘要(CV模型,>=80%帧缺失即违规):"]
        for pid, w in self.work_permit.get("workers", {}).items():
            pl = [l for l in cv_logs if l.get("person_id") == pid]
            if not pl: cv.append(f"  {pid}({w.get('name',pid)},{w.get('role','')}): 无数据"); continue
            n = sum(1 for l in pl if not next((d["value"] for d in l["detections"] if d["class_name"]=="helmet"), True))
            r = n/len(pl); m = "*** 头盔缺失 ***" if r>=0.8 else "正常"
            cv.append(f"  {pid}({w.get('name',pid)},{w.get('role','')}): {len(pl)}帧,{n}帧未戴头盔({r:.0%}) -> {m}")
        se = ["传感器读数(动火区-A):"]
        if sensors and sensors[0].get("readings"):
            for r in sensors[0]["readings"].values():
                tw = r.get("threshold_warning"); ta = r.get("threshold_alarm")
                th = f"(警戒线{tw},报警线{ta})" if ta else ""
                m = "***异常***" if r["status"]=="alarm" else ("**接近警戒**" if r["status"]=="warning" else "正常")
                se.append(f"  {r['type']}: {r['value']}{r['unit']} -> {r['status']} {m} {th}")
        po = ["人员位置(UWB定位):"]
        if positions and positions[0].get("positions"):
            for wid, p in positions[0]["positions"].items():
                z = "危险区" if p.get("is_in_danger_zone") else "普通区"
                m = "***不在作业区***" if not p.get("is_in_danger_zone") else ""
                po.append(f"  {wid}: {p['area_id']}({z}) {m}")
        pe = [f"作业票: {self.work_permit['permit_id']}({self.work_permit['level']})",
              f"必戴PPE: {', '.join(self.work_permit['required_ppe'])}"]
        return (
            f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
            f"!!! 注意: 当前现实时间是 {wall_time} !!!\n"
            f"!!! 你只能查询 <= 这个时间点的日志,不能看未来 !!!\n"
            f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n\n"
            + "\n".join(pe) + "\n\n"
            + "\n".join(cv) + "\n\n"
            + "\n".join(se) + "\n\n"
            + "\n".join(po) + "\n\n"
            + "以上是当前现场数据。请判断是否存在需报告的候选安全事件。"
            + "先调用 query_past_events 检查是否已报告过,避免重复。"
        )

    # ============================================================
    # parse
    # ============================================================
    def _parse_output(self, raw: str, wall_time: str, snapshot: Dict) -> List[Dict]:
        if not raw: return []
        try:
            if "```json" in raw: raw = raw.split("```json")[1].split("```")[0]
            elif "```" in raw: raw = raw.split("```")[1].split("```")[0]
            raw = raw.strip()
            if raw.startswith("["): parsed = json.loads(raw)       # 数组(多事件)
            elif raw.startswith("{") and raw.endswith("}"): parsed = json.loads(raw)
            else: return []
        except json.JSONDecodeError: return []

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
    # rule_fallback (Mock LLM)
    # ============================================================
    def _rule_fallback(self, wall_time: str, snapshot: Dict) -> str:
        """降级规则: Mock LLM 时使用,模拟 Agent 推理。支持 PPE/传感器/监护人三维检测 + 去重。"""
        events = []
        cv_logs = snapshot.get("cv_logs", [])
        sensor_data = snapshot.get("sensors", [{}])
        pos_data = snapshot.get("positions", [{}])

        # ── 辅助: 检查是否有 ongoing 事件 ──
        def _has_ongoing(person_id=None, event_type_hint=None):
            """只看最新匹配事件:若最新的是 ongoing 则返回 True"""
            past = query_past_events.invoke({
                "start_wall": "2026-08-01T00:00:00",
                "end_wall": wall_time,
                "person_id": person_id,
            })
            latest = None
            for e in past:
                if not isinstance(e, dict): continue
                if person_id and e.get("person",{}).get("id") != person_id: continue
                # event_type_hint 匹配: 对人员找 type 包含; 对 SYS 找 evidence.sensor 键包含
                if event_type_hint:
                    if person_id == "SYS":
                        ev_sensor = e.get("evidence",{}).get("sensor",{})
                        if event_type_hint not in str(ev_sensor.keys()):
                            continue
                    elif event_type_hint not in e.get("type",""):
                        continue
                if latest is None or e.get("wall_time","") > latest.get("wall_time",""):
                    latest = e
            return latest is not None and latest.get("status") == "ongoing"

        # ── 1. PPE 检测 ──
        for pid, info in self.work_permit.get("workers", {}).items():
            req = ["helmet"] if info.get("role") == "监护人" else None
            stats = analyze_ppe_compliance.invoke({
                "cv_logs": cv_logs, "person_id": pid,
                "threshold": 0.8, "required_ppe": req,
            })
            is_v = stats.get("is_violating", False)
            ongoing = _has_ongoing(pid, "PPE缺失")

            if is_v and not ongoing:
                events.append({"is_event":True,"type":"PPE缺失-头盔",
                    "person":{"id":pid,"name":info.get("name",pid),"role":info.get("role","")},
                    "explanation":f"CV多数表决: {stats.get('helmet_violation',0)}/{stats['total_frames']}帧({stats['helmet_ratio']:.0%})",
                    "evidence":{"cv_ratio":stats['helmet_ratio']},"status":"ongoing"})
            elif is_v and ongoing:
                # 持续违规: 写更新(覆盖 same event_id 的 last_seen)
                events.append({"is_event":True,"type":"PPE缺失-头盔",
                    "person":{"id":pid,"name":info.get("name",pid),"role":info.get("role","")},
                    "explanation":f"CV多数表决: {stats.get('helmet_violation',0)}/{stats['total_frames']}帧({stats['helmet_ratio']:.0%})",
                    "evidence":{"cv_ratio":stats['helmet_ratio']},"status":"ongoing_update"})
            elif not is_v and ongoing:
                events.append({"is_event":True,"type":"PPE缺失-头盔",
                    "person":{"id":pid,"name":info.get("name",pid),"role":info.get("role","")},
                    "explanation":"违规已结束,事件关闭","evidence":{},"status":"closed"})

        # ── 2. 传感器告警 ──
        if sensor_data and sensor_data[0].get("readings"):
            for sid, r in sensor_data[0]["readings"].items():
                is_alarm = r.get("status") == "alarm"
                # 限定同一传感器: 只在 ongoing 事件证据中包含此 sid 才算
                ongoing = _has_ongoing("SYS", sid)
                if is_alarm and not ongoing:
                    events.append({"is_event":True,"type":"环境异常-传感器告警",
                        "person":{"id":"SYS","name":"传感器系统","role":"设备"},
                        "explanation":f"{r.get('type',sid)}: {r['value']}{r.get('unit','')} 超过报警线({r.get('threshold_alarm','?')})",
                        "evidence":{"sensor":{sid:r}},"status":"ongoing"})
                elif is_alarm and ongoing:
                    events.append({"is_event":True,"type":"环境异常-传感器告警",
                        "person":{"id":"SYS","name":"传感器系统","role":"设备"},
                        "explanation":f"{r.get('type',sid)}: 持续告警 {r['value']}{r.get('unit','')}",
                        "evidence":{"sensor":{sid:r}},"status":"ongoing_update"})
                elif not is_alarm and ongoing:
                    events.append({"is_event":True,"type":"环境异常-传感器告警",
                        "person":{"id":"SYS","name":"传感器系统","role":"设备"},
                        "explanation":"传感器已恢复正常,事件关闭","evidence":{},"status":"closed"})

        # ── 3. 监护人离岗 ──
        if pos_data and pos_data[0].get("positions"):
            for wid, p in pos_data[0]["positions"].items():
                w = self.work_permit.get("workers", {}).get(wid, {})
                if w.get("role") != "监护人": continue
                is_absent = not p.get("is_in_danger_zone", True)
                ongoing = _has_ongoing(wid, "脱岗")
                if is_absent and not ongoing:
                    events.append({"is_event":True,"type":"监护人脱岗",
                        "person":{"id":wid,"name":w.get("name",wid),"role":"监护人"},
                        "explanation":f"监护人 {w.get('name',wid)} 离开动火区,当前在 {p.get('area_id','?')}",
                        "evidence":{"position":p},"status":"ongoing"})
                elif is_absent and ongoing:
                    events.append({"is_event":True,"type":"监护人脱岗",
                        "person":{"id":wid,"name":w.get("name",wid),"role":"监护人"},
                        "explanation":f"监护人持续离岗,当前在 {p.get('area_id','?')}",
                        "evidence":{"position":p},"status":"ongoing_update"})
                elif not is_absent and ongoing:
                    events.append({"is_event":True,"type":"监护人脱岗",
                        "person":{"id":wid,"name":w.get("name",wid),"role":"监护人"},
                        "explanation":"监护人已返回动火区,事件关闭","evidence":{},"status":"closed"})

        # 场景 E 可能同时有多个事件,全部返回
        if not events:
            return json.dumps({"is_event":False,"explanation":"无异常"}, ensure_ascii=False)
        # 单个事件直接返回,多个事件包装成列表(JSON 数组)
        if len(events) == 1:
            return json.dumps(events[0], ensure_ascii=False)
        return json.dumps(events, ensure_ascii=False)

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
