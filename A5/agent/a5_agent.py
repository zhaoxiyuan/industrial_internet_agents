"""
A5Agent -- 作业过程监测智能体

调用模式: 主循环每 1 秒调一次 tick(wall_time, snapshot)。
设计: LangChain create_agent + 6 工具 + 无状态(磁盘驱动)。

LangChain 版本兼容:
  LangChain >= 1.0: 使用 create_agent(返回 CompiledStateGraph)
  LangChain < 0.2: 使用 create_react_agent + AgentExecutor
"""
import json
import os
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent.tools import (
    ALL_TOOLS,
    set_dependencies,
    set_raw_event_dir,
    analyze_ppe_compliance,
)
from agent.system_prompt import get_system_prompt


# ============================================================
# Mock LLM(Demo 用,无 .env 配置时自动生效)
# ============================================================

class MockChatModel(BaseChatModel):
    """
    Demo 用模拟 LLM。返回空 -> Agent 走 _rule_fallback。
    生产环境替换为 ChatOpenAI / ChatOllama。
    """

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="{}"))])

    def _llm_type(self) -> str:
        return "mock"

    @property
    def _identifying_params(self) -> Dict[str, Any]:
        return {"model": "mock"}


# ============================================================
# A5 Agent
# ============================================================

class A5Agent:
    """A5 作业过程监测 Agent(无状态磁盘驱动)"""

    def __init__(
        self,
        collector,
        work_permit: Dict[str, Any],
        raw_event_dir: Optional[str] = None,
    ):
        self.collector = collector
        self.work_permit = work_permit

        # 注入依赖到工具层
        set_dependencies(collector=collector, vl_model=None, work_permit=work_permit)

        # raw_event 落盘目录
        self.raw_event_dir = raw_event_dir
        set_raw_event_dir(raw_event_dir)
        if self.raw_event_dir:
            Path(self.raw_event_dir).mkdir(parents=True, exist_ok=True)

        # LLM
        self.llm = self._build_llm()

        # system prompt
        self._system_prompt = get_system_prompt()

        # 构建 Agent
        try:
            # LangChain >= 1.0
            from langchain.agents import create_agent
            self._agent = create_agent(
                model=self.llm,
                tools=ALL_TOOLS,
                system_prompt=self._system_prompt,
            )
            self._use_v1 = True
        except (ImportError, AttributeError):
            # LangChain < 1.0 fallback
            from langchain.agents import create_react_agent, AgentExecutor
            from langchain_core.prompts import PromptTemplate
            self._react_prompt = PromptTemplate.from_template(
                "{system_prompt}\n\n" +
                "你有以下工具:\n{tools}\n工具名称: {tool_names}\n\n" +
                "{input}"
            )
            self._agent = AgentExecutor(
                agent=create_react_agent(
                    llm=self.llm, tools=ALL_TOOLS, prompt=self._react_prompt),
                tools=ALL_TOOLS, verbose=False, handle_parsing_errors=True, max_iterations=6,
            )
            self._use_v1 = False

    # ============================================================
    # LLM 构建
    # ============================================================

    def _build_llm(self) -> BaseChatModel:
        provider = os.getenv("LLM_PROVIDER", "").lower()
        if provider == "openai":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=os.getenv("LLM_MODEL", "gpt-4o"),
                api_key=os.getenv("OPENAI_API_KEY"),
                base_url=os.getenv("OPENAI_BASE_URL", None),
                temperature=0,
            )
        if provider == "ollama":
            from langchain_community.chat_models import ChatOllama
            return ChatOllama(
                model=os.getenv("LLM_MODEL", "qwen2.5:7b"),
                base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                temperature=0,
            )
        return MockChatModel()

    # ============================================================
    # 主入口(每 1 秒被主循环调用)
    # ============================================================

    async def tick(self, wall_time: str, snapshot: Dict) -> List[Dict]:
        """处理当前秒的快照,返回 0~N 个 raw_event"""
        self.collector.set_agent_now(wall_time)

        agent_input = self._build_input(wall_time, snapshot)

        # Mock LLM(无 bind_tools)直接走规则,跳过 create_agent
        if isinstance(self.llm, MockChatModel):
            raw_output = self._rule_fallback(wall_time, snapshot)
            intermediate = []
        else:
            try:
                if self._use_v1:
                    messages = [
                        SystemMessage(content=self._system_prompt),
                        HumanMessage(content=agent_input),
                    ]
                    result = await self._agent.ainvoke({"messages": messages})
                    ai_messages = [m for m in result.get("messages", [])
                                   if isinstance(m, AIMessage)]
                    raw_output = ai_messages[-1].content if ai_messages else "{}"
                    intermediate = []
                else:
                    result = await self._agent.ainvoke({
                        "input": json.dumps(agent_input, ensure_ascii=False)
                    })
                    raw_output = result.get("output", "{}")
                    intermediate = result.get("intermediate_steps", [])
            except Exception as e:
                raw_output = json.dumps(
                    {"is_event": False, "explanation": f"Agent error: {e}"})
                intermediate = []

            # LLM 返回空 -> 降级
            if not raw_output or raw_output.strip() in ("", "{}"):
                raw_output = self._rule_fallback(wall_time, snapshot)
                intermediate = []

        raw_events = self._parse_output(raw_output, wall_time, snapshot)
        for ev in raw_events:
            self._save_raw_event(ev, intermediate)
        return raw_events

    # ============================================================
    # 输入组装(自然语言)
    # ============================================================

    def _build_input(self, wall_time: str, snapshot: Dict) -> str:
        cv_logs = snapshot.get("cv_logs", [])
        sensors = snapshot.get("sensors", [{}])
        positions = snapshot.get("positions", [{}])

        # PPE 摘要
        cv_parts = ["PPE检测摘要(CV模型,每秒25帧,>=80%帧缺失即违规):"]
        for pid, winfo in self.work_permit.get("workers", {}).items():
            person_logs = [l for l in cv_logs if l.get("person_id") == pid]
            role = winfo.get("role", "")
            name = winfo.get("name", pid)
            if not person_logs:
                cv_parts.append(f"  {pid}({name},{role}): 无数据")
                continue
            n_bad = sum(1 for l in person_logs
                        if not next((d["value"] for d in l["detections"]
                                     if d["class_name"] == "helmet"), True))
            ratio = n_bad / len(person_logs)
            mark = "*** 头盔缺失 ***" if ratio >= 0.8 else "正常"
            cv_parts.append(
                f"  {pid}({name},{role}): {len(person_logs)}帧,"
                f"{n_bad}帧未戴头盔({ratio:.0%}) -> {mark}"
            )

        # 传感器
        sensor_parts = ["传感器读数(动火区-A):"]
        if sensors and sensors[0].get("readings"):
            for r in sensors[0]["readings"].values():
                tw = r.get("threshold_warning")
                ta = r.get("threshold_alarm")
                th = f"(警戒线{tw},报警线{ta})" if ta else ""
                mark = ("***异常***" if r["status"] == "alarm"
                        else "**接近警戒**" if r["status"] == "warning"
                        else "正常")
                sensor_parts.append(
                    f"  {r['type']}: {r['value']}{r['unit']} -> {r['status']} {mark} {th}"
                )

        # 位置
        pos_parts = ["人员位置(UWB定位):"]
        if positions and positions[0].get("positions"):
            for wid, p in positions[0]["positions"].items():
                zone = "危险区" if p.get("is_in_danger_zone") else "普通区"
                mark = "***不在作业区***" if not p.get("is_in_danger_zone") else ""
                pos_parts.append(f"  {wid}: {p['area_id']}({zone}) {mark}")

        # 作业票
        permit_parts = [
            f"作业票: {self.work_permit['permit_id']}({self.work_permit['level']})",
            f"必戴PPE: {', '.join(self.work_permit['required_ppe'])}",
        ]

        # 趋势(最近 5 秒)
        trend_parts = ["最近5秒违规趋势:"]
        try:
            cur_sec = int(self.collector._wall_to_sec(wall_time))
            for pid in self.work_permit.get("workers", {}):
                segs = []
                for off in range(min(4, cur_sec - 1), -1, -1):
                    s = cur_sec - off
                    start = self.collector._sec_to_wall(s)
                    end = self.collector._sec_to_wall(s + 1)
                    logs = self.collector.query_raw_logs(start, end, "cv", pid)
                    if not logs:
                        continue
                    n_bad = sum(1 for l in logs
                                if not next((d["value"] for d in l["detections"]
                                             if d["class_name"] == "helmet"), True))
                    r = n_bad / len(logs)
                    cur = "*" if off == 0 else ""
                    segs.append(f"T-{off}:{r:.0%}{cur}")
                if segs:
                    trend_parts.append(f"  {pid}: {' | '.join(reversed(segs))}")
                else:
                    trend_parts.append(f"  {pid}: 无历史")
        except Exception:
            pass

        # 组装
        return (
            f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
            f"!!! 注意: 当前现实时间是 {wall_time} !!!\n"
            f"!!! 你只能查询 <= 这个时间点的日志,不能看未来 !!!\n"
            f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
            f"\n"
            f"{chr(10).join(permit_parts)}\n"
            f"\n"
            f"{chr(10).join(trend_parts)}\n"
            f"\n"
            f"{chr(10).join(cv_parts)}\n"
            f"\n"
            f"{chr(10).join(sensor_parts)}\n"
            f"\n"
            f"{chr(10).join(pos_parts)}\n"
            f"\n"
            f"以上是当前现场数据。请判断是否存在需报告的候选安全事件。\n"
            f"如有,输出JSON格式化的事件;如无,输出 {{\"is_event\":false}}。\n"
            f"注意: 先调用 query_past_events 检查是否已报告过,避免重复。"
        )

    # ============================================================
    # 输出解析
    # ============================================================

    def _parse_output(self, raw: str, wall_time: str, snapshot: Dict) -> List[Dict]:
        if not raw:
            return []
        try:
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0]
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0]
            raw = raw.strip()
            if raw.startswith("{") and raw.endswith("}"):
                parsed = json.loads(raw)
            else:
                return []
        except json.JSONDecodeError:
            return []

        if not isinstance(parsed, dict) or not parsed.get("is_event"):
            return []

        sec = int(float(snapshot.get("scenario_time", "0s").rstrip("s")))
        person_info = parsed.get("person", {})
        person_id = person_info.get("id", "?")

        return [{
            "source":      "A5",
            "event_id":    f"A5-{person_id}-{int(time.time() * 1000)}",
            "type":        parsed.get("type", "未分类"),
            "person":      person_info,
            "second":      sec,
            "wall_time":   wall_time,
            "first_seen":  wall_time,
            "last_seen":   wall_time,
            "status":      "ongoing",
            "evidence":    parsed.get("evidence", {}),
            "explanation": parsed.get("explanation", ""),
            "note":        "A5不判定risk_level,由A6完成",
        }]

    # ============================================================
    # 降级规则
    # ============================================================

    def _rule_fallback(self, wall_time: str, snapshot: Dict) -> str:
        events = []
        cv_logs = snapshot.get("cv_logs", [])
        for pid, info in self.work_permit.get("workers", {}).items():
            req = ["helmet"] if info.get("role") == "监护人" else None
            stats = analyze_ppe_compliance.invoke({
                "cv_logs": cv_logs, "person_id": pid,
                "threshold": 0.8, "required_ppe": req,
            })
            if not stats.get("is_violating"):
                continue
            events.append({
                "is_event": True,
                "type": "PPE缺失-头盔",
                "person": {"id": pid, "name": info.get("name", pid), "role": info.get("role", "")},
                "explanation": (
                    f"CV多数表决: {stats.get('helmet_violation',0)}/"
                    f"{stats['total_frames']}帧未戴头盔({stats['helmet_ratio']:.0%})"
                ),
                "evidence": {"cv_ratio": stats["helmet_ratio"]},
            })
        return json.dumps(
            events[0] if events else {"is_event": False, "explanation": "无异常"},
            ensure_ascii=False
        )

    # ============================================================
    # 落盘
    # ============================================================

    def _save_raw_event(self, event: Dict, intermediate: List):
        if not self.raw_event_dir:
            return
        safe_ts = event["wall_time"].replace(":", "").replace("-", "").replace(".", "_")
        filepath = Path(self.raw_event_dir) / f"raw_event_{safe_ts}.json"
        payload = {
            "wall_time": event["wall_time"],
            "events": [event],
            "intermediate_steps": [
                {"action": str(a), "observation": str(o)[:200]}
                for a, o in (intermediate or [])
            ],
        }
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    # ============================================================
    # 辅助
    # ============================================================

    def summary(self) -> Dict[str, Any]:
        import glob
        events = []
        if self.raw_event_dir:
            for f in sorted(glob.glob(f"{self.raw_event_dir}/raw_event_*.json")):
                data = json.load(open(f, encoding="utf-8"))
                events.extend(data.get("events", []))
        by_person = {}
        for ev in events:
            pid = ev.get("person", {}).get("id", "?")
            by_person[pid] = by_person.get(pid, 0) + 1
        return {"total_events": len(events), "by_person": by_person}
