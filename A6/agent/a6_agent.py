"""
A6Agent -- 风险研判智能体

职责:
  - 接收 A5 输出的候选风险事件
  - 进行事件自主聚合（可选，依赖 LLM 判断）
  - 进行风险等级研判（LLM + 可编辑提示词）
  - 输出研判结果给 A7

调用模式:
  - 每当 A5 产生一条告警，就调用一次 A6
  - A6 维护 event_id_map 避免重复处理
"""
import asyncio
import json
import os
import time as time_module
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

from langchain.agents import create_agent
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.tools import tool, Tool

from .tools import A5DataTools, OutputTools
from . import prompts


class A6Agent:
    """
    A6 风险研判智能体

    主要功能:
    1. 事件去重：维护 event_id_map，避免重复处理同一事件
    2. 风险分级：使用 LLM + prompts.py 中的提示词进行研判
    3. 结果输出：保存研判结果到 A6/logs/assessments/
    """

    def __init__(
        self,
        a5_log_dir: str = None,
        a6_output_dir: str = None,
        log_test_dir: str = None,
        llm_provider: str = None,
        llm_api_key: str = None,
        llm_base_url: str = None,
        llm_model: str = None,
    ):
        """
        初始化 A6 Agent

        Args:
            a5_log_dir: A5 日志目录
            a6_output_dir: A6 输出目录
            llm_provider: LLM 提供商（openai/ollama）
            llm_api_key: API 密钥
            llm_base_url: API 基础 URL
            llm_model: 模型名称
        """
        self.a5_log_dir = a5_log_dir
        self.a6_output_dir = a6_output_dir

        # log_test 目录：每次 process_event 都写一份详细日志
        if log_test_dir:
            self.log_test_dir = Path(log_test_dir)
        else:
            self.log_test_dir = Path(__file__).resolve().parent.parent / "log_test"
        self.log_test_dir.mkdir(parents=True, exist_ok=True)

        # 上次记录的 wall_time（用于判断是否清理 log_test）
        self._last_log_wall_time: Optional[str] = None
        # wall_time 跳变阈值：超过 30 分钟视为新一轮测试，清空 log_test
        self._log_test_reset_window = timedelta(minutes=30)

        # 初始化工具
        self.a5_tools = A5DataTools(a5_log_dir=a5_log_dir)
        self.output_tools = OutputTools(output_dir=a6_output_dir)

        # 初始化 LLM
        self.llm = self._build_llm(
            provider=llm_provider,
            api_key=llm_api_key,
            base_url=llm_base_url,
            model=llm_model,
        )

        # 加载提示词
        self._risk_system_prompt = prompts.RISK_CLASSIFICATION_SYSTEM_PROMPT
        self._risk_user_template = prompts.RISK_CLASSIFICATION_USER_PROMPT_TEMPLATE
        self._aggregation_system_prompt = prompts.AGGREGATION_SYSTEM_PROMPT
        self._aggregation_user_template = prompts.AGGREGATION_USER_PROMPT_TEMPLATE

        # 运行时提示词覆盖（可通过 set_prompts 更新）
        self._runtime_risk_system = None
        self._runtime_risk_user = None

        self._last_tick_ms = 0.0

        # 并发防护：每个 event_id 一把锁，避免同一事件并发重复研判
        self._locks: Dict[str, asyncio.Lock] = {}

        # 构建风险分级 agent（带工具调用能力）
        self._risk_agent = self._build_risk_agent()

    # ============================================================
    # 风险分级工具（供 agent 调用）
    # 注意：用 Tool 类 + 闭包代替 @tool 装饰器，避免 StructuredTool._run(self)
    #       多传一次 self 导致 "got multiple values for argument 'self'" 错误
    # ============================================================

    def _make_query_active_violations_tool(self) -> Tool:
        """创建 query_active_violations 工具（闭包绑定 self）"""
        a5_tools = self.a5_tools
        def query_active_violations(person_id: str = None) -> str:
            """
            查询当前活跃的违规事件（尚未关闭的）。
            当收到新事件时，先调用此工具看该人员是否有正在追踪的同类违规。
            - 返回空 → 全新违规，新建研判
            - 返回记录 → 已在追踪违规的延续，应聚合
            """
            result = a5_tools.query_active_violations(person_id=person_id if person_id else None)
            return json.dumps(result, ensure_ascii=False, indent=2)
        return Tool.from_function(
            func=query_active_violations,
            name="query_active_violations",
            description="查询当前正在追踪的违规事件（尚未关闭的）。当你收到一个新事件时，先调用此工具，看该人员是否有正在追踪的同类违规。如果返回了记录 → 当前事件可能是已在追踪违规的延续，尝试聚合。如果返回为空 → 当前事件是全新的违规，应新建研判。",
        )

    def _make_query_surrounding_raw_events_tool(self) -> Tool:
        """创建 query_surrounding_raw_events 工具（闭包绑定 self）"""
        a5_tools = self.a5_tools
        def query_surrounding_raw_events(wall_time: str, window_sec: int = 60) -> str:
            """
            查询指定 wall_time 前后 window_sec 秒内的所有 A5 原始检测输出。
            当你对是否该聚合存在疑问时，调用此工具查看上下文辅助判断。
            """
            result = a5_tools.query_surrounding_raw_events(wall_time, window_sec=window_sec)
            return json.dumps(result, ensure_ascii=False, indent=2)
        return Tool.from_function(
            func=query_surrounding_raw_events,
            name="query_surrounding_raw_events",
            description="查询指定 wall_time 前后 window_sec 秒内的所有 A5 原始检测输出。当你对是否该聚合存在疑问时（比如duration很短但周边有相关事件），调用此工具查看指定 wall_time 前后 window_sec 秒内的 A5 原始检测输出，结合上下文做出更准确的判断。",
        )

    def _build_risk_agent(self):
        """构建带工具调用的风险分级 agent"""
        if self.llm is None:
            return None
        try:
            system_prompt = self._runtime_risk_system or self._risk_system_prompt
            tools = [
                self._make_query_active_violations_tool(),
                self._make_query_surrounding_raw_events_tool(),
            ]
            return create_agent(
                self.llm,
                tools=tools,
                system_prompt=system_prompt,
            )
        except Exception as e:
            print(f"构建风险 agent 失败: {e}")
            return None

    def _get_lock(self, event_id: str) -> asyncio.Lock:
        """获取或创建指定 event_id 的锁"""
        if event_id not in self._locks:
            self._locks[event_id] = asyncio.Lock()
        return self._locks[event_id]

    def _release_lock(self, event_id: str):
        """处理完成后释放锁（从字典中移除，释放内存）"""
        if event_id in self._locks:
            del self._locks[event_id]

    def _build_llm(
        self,
        provider: str = None,
        api_key: str = None,
        base_url: str = None,
        model: str = None,
    ):
        """构建 LLM 实例"""
        # 尝试从环境变量或配置文件读取
        from dotenv import dotenv_values
        env_path = Path(__file__).resolve().parent.parent.parent / "agent_config" / ".env"
        env = {}
        if env_path.exists():
            env = dotenv_values(env_path)
        env.update({k: v for k, v in os.environ.items() if v})

        protocol = provider or env.get("A6_LLM_PROTOCOL", "") or env.get("A5_LLM_PROTOCOL", "").lower()
        if not protocol:
            # 默认使用 openai
            protocol = "openai"

        api_key = api_key or env.get("A6_LLM_API_KEY", "") or env.get("A5_LLM_API_KEY", "") or env.get("OPENAI_API_KEY", "")
        base_url = base_url or env.get("A6_LLM_BASE_URL", "") or env.get("A5_LLM_BASE_URL", "") or env.get("OPENAI_BASE_URL", "")
        model = model or env.get("A6_LLM_MODEL", "") or env.get("A5_LLM_MODEL", "gpt-4o")

        if protocol == "openai":
            # 如果没有 API key，返回 None（将使用规则分类）
            if not api_key:
                return None
            try:
                from langchain_openai import ChatOpenAI
                return ChatOpenAI(
                    model=model,
                    api_key=api_key,
                    base_url=base_url if base_url else None,
                    temperature=0,
                )
            except ImportError:
                return None

        if protocol == "ollama":
            try:
                from langchain_community.chat_models import ChatOllama
                return ChatOllama(
                    model=model or "qwen2.5:7b",
                    base_url=base_url or "http://localhost:11434",
                    temperature=0,
                )
            except ImportError:
                return None

        return None

    def set_prompts(
        self,
        risk_system: str = None,
        risk_user: str = None,
    ):
        """运行时更新提示词"""
        if risk_system:
            self._runtime_risk_system = risk_system
        if risk_user:
            self._runtime_risk_user = risk_user

    # ============================================================
    # 核心处理方法
    # ============================================================

    async def process_event(self, event_id: str, event_data: Dict = None) -> Dict[str, Any]:
        """
        处理单个 A5 事件。

        并发防护：同一 event_id 会排队（通过 per-event-id asyncio.Lock）。
        已完成（completed）的事件再次触发时，视为更新而非重复：
          - 加载已有研判，追加新证据
          - 重新评估风险等级（duration 变了，风险等级可能变）
          - 更新后的研判替代旧研判

        Args:
            event_id: A5 事件 ID（如 "A5-P7-1722933125"）或 batch wall_time
            event_data: 事件数据（dict 或 {"events": [...]} 包装），可选

        Returns:
            研判结果字典（含 status/results/tick_ms）
        """
        import time as _time
        t0 = _time.perf_counter()

        # ── wall_time 提取 + 跳变清理 ──
        current_wall_time = self._extract_wall_time(event_data) or event_id
        self._maybe_reset_log_test(current_wall_time)

        # ── 并发防护：获取 event_id 锁 ──
        lock = self._get_lock(event_id)
        async with lock:
            try:
                # 委托给 process_batch：若 event_data 是 batch（events 列表>1），批量处理
                events = self._extract_events(event_data)
                if len(events) > 1:
                    results = await self._process_events_batch(event_id, events, t0)
                    resp = {
                        "status": "success",
                        "message": f"批量研判完成（{len(results)} 条）",
                        "results": results,
                        "tick_ms": round((time_module.perf_counter() - t0) * 1000, 2),
                    }
                    self._write_log_test(event_id, event_data, resp, current_wall_time)
                    return resp
                # 单事件：取第一个或空
                single = events[0] if events else {}
                resp = await self._process_event_inner(event_id, single, t0)
                self._write_log_test(event_id, event_data, resp, current_wall_time)
                return resp
            finally:
                self._release_lock(event_id)

    @staticmethod
    def _extract_events(event_data: Dict) -> List[Dict]:
        """从传入 event_data 抽取事件列表。"""
        if not event_data:
            return []
        if isinstance(event_data, dict) and "events" in event_data:
            return [e for e in (event_data.get("events") or []) if e]
        # 兼容直接传单事件 dict
        return [event_data]

    @staticmethod
    def _extract_wall_time(event_data: Dict) -> Optional[str]:
        """从 event_data 中抽取 wall_time（兼容 batch wall_time / 单事件 wall_time / first_seen）。"""
        if not event_data or not isinstance(event_data, dict):
            return None
        if "wall_time" in event_data and event_data["wall_time"]:
            return str(event_data["wall_time"])
        # batch 包装，wall_time 在顶层
        events = event_data.get("events") or []
        if events and isinstance(events[0], dict):
            ev = events[0]
            return str(ev.get("wall_time") or ev.get("first_seen") or "")
        return None

    def _parse_wall_time(self, wall_time: str) -> Optional[datetime]:
        """将 wall_time 字符串解析为 datetime，支持多种格式。"""
        if not wall_time:
            return None
        s = str(wall_time).strip()
        # 常见格式：2026-08-12T15:18:07.550, 2026:08:12T15:18:07.550
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y:%m:%dT%H:%M:%S.%f",
            "%Y:%m:%dT%H:%M:%S",
        ):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    def _maybe_reset_log_test(self, current_wall_time: str) -> None:
        """若与上次 wall_time 相差超过 30 分钟，清空 log_test 目录。"""
        if not current_wall_time:
            return
        cur = self._parse_wall_time(current_wall_time)
        if cur is None:
            return
        prev = self._parse_wall_time(self._last_log_wall_time) if self._last_log_wall_time else None
        if prev is not None and (cur - prev) > self._log_test_reset_window:
            try:
                count = 0
                for f in self.log_test_dir.glob("tick_*.json"):
                    try:
                        f.unlink()
                        count += 1
                    except OSError:
                        pass
                print(f"[A6 log_test] wall_time 跳变 {self._last_log_wall_time} -> {current_wall_time}，已清理 {count} 个历史日志")
            except Exception as e:
                print(f"[A6 log_test] 清理失败: {e}")
        self._last_log_wall_time = current_wall_time

    def _write_log_test(self, event_id: str, event_data: Any, result: Any, wall_time: str) -> None:
        """写入一次 A6 研判的详细日志到 log_test/。"""
        try:
            # 过滤单事件 dict 中的 event_id/event_data 包装
            payload_event_data = event_data
            if isinstance(event_data, dict) and "events" in event_data:
                payload_event_data = {
                    "wall_time": event_data.get("wall_time"),
                    "events": event_data.get("events"),
                }
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_evt = (event_id or "unknown").replace(":", "-").replace("/", "-")
            log_path = self.log_test_dir / f"tick_{safe_evt}_{ts}.json"
            log_record = {
                "event_id": event_id,
                "wall_time": wall_time,
                "event_data": payload_event_data,
                "result": result,
                "timestamp": datetime.now().isoformat(),
            }
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(log_record, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[A6 log_test] 写入失败: {e}")

    async def _process_events_batch(
        self, batch_id: str, events: List[Dict], t0: float
    ) -> List[Dict[str, Any]]:
        """批量研判 batch 内多个事件。

        对每个事件获取其独立 event_id 锁（避免不同 person/type 的并发冲突），
        顺序处理并返回每条研判结果。
        """
        results: List[Dict[str, Any]] = []
        for ev in events:
            ev_id = ev.get("event_id") or f"{batch_id}-{len(results)}"
            ev_lock = self._get_lock(ev_id)
            async with ev_lock:
                try:
                    res = await self._process_event_inner(ev_id, ev, t0)
                finally:
                    self._release_lock(ev_id)
            results.append({"event_id": ev_id, **res})
        return results

    async def _process_event_inner(self, event_id: str, event_data: Dict, t0: float) -> Dict[str, Any]:
        """process_event 的内部实现（已持有锁）"""

        # Step 1: 读取并补全事件数据
        # 策略：始终以 raw_event 文件中的权威数据为基础，
        #       event_data（传入参数）中的字段仅当文件数据缺失时才补充
        raw = self.a5_tools.read_raw_event_by_id(event_id)
        if raw and raw.get("events"):
            file_event = raw["events"][0]
        else:
            file_event = {}

        # 抽取传入的 event_data（外层已解包 batch；此处仅取单事件 dict）
        param_event = event_data if isinstance(event_data, dict) else {}

        # 以文件数据为基础，补充传入参数中文件没有的字段
        event_data = dict(file_event)  # 复制，避免修改原始文件数据
        if param_event:
            for k, v in param_event.items():
                if k not in event_data or not event_data.get(k):
                    event_data[k] = v

        if not event_data:
            return {
                "status": "error",
                "message": f"无法找到事件数据: {event_id}"
            }

        # Step 2: 检查 violation key（person_id + violation_type）是否有活跃记录
        violation_key = self._build_violation_key(event_data)
        active = self.a5_tools.get_active_violation(violation_key)

        # Step 3: 调用带工具的 agent 进行风险分级和聚合决策
        risk_result = await self._classify_risk(event_data, active_context=active)
        agg_decision = risk_result.get("aggregation_decision", "new")

        # Step 4: 构建研判报告并更新 active_violations
        person = event_data.get("person") or {}
        person_id = person.get("id", "unknown")
        violation_type = event_data.get("type", "未知")
        first_seen = self._normalize_wall_time(event_data.get("first_seen", ""))
        last_seen = self._normalize_wall_time(event_data.get("last_seen", ""))

        if agg_decision == "aggregate" and active:
            # 聚合到已有研判：追加 evidence，重新评估，更新 last_seen
            existing = self.output_tools.load_assessment(active["a6_event_id"])
            if existing:
                updated = await self._update_existing_assessment(
                    existing, event_data, active["a6_event_id"]
                )
                self.a5_tools.update_active_violation(
                    key=violation_key,
                    a6_event_id=active["a6_event_id"],
                    person_id=person_id,
                    violation_type=violation_type,
                    first_seen=active["first_seen"],
                    last_seen=last_seen,
                    status="ongoing",
                )
                tick_ms = (time_module.perf_counter() - t0) * 1000
                return {
                    "status": "updated",
                    "message": "已聚合到已有研判",
                    "assessment": updated,
                    "tick_ms": round(tick_ms, 2),
                }

        # Step 5: 新建研判（agg_decision=="new" 或聚合失败）
        a6_event_id = self.output_tools._generate_a6_event_id()
        # 如果之前有 active 记录但本次判定为 new，先关闭旧的
        if active:
            self.a5_tools.close_active_violation(violation_key)

        assessment = {
            "a6_event_id": a6_event_id,
            "aggregated_from": [event_id],
            "event_type": violation_type,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "wall_time": last_seen,
            "duration_sec": self._calc_duration(event_data),
            "involved_persons": [person_id],
            "risk_level": risk_result.get("risk_level", 2),
            "risk_level_name": risk_result.get("risk_level_name", "一般"),
            "risk_basis": risk_result.get("risk_basis", ""),
            "suggestions": risk_result.get("suggestions", []),
            "reasoning": risk_result.get("reasoning", ""),
            "evidence": event_data.get("evidence", {}),  # 保留 A5 原始 evidence（LLM 输出不含此字段）
            "timestamp": datetime.now().isoformat(),
        }

        self.output_tools.save_assessment(assessment)
        self.a5_tools.update_event_status(
            event_id=event_id,
            a6_output_id=a6_event_id,
            new_status="completed",
        )

        # 新建 active_violations 记录（不管 risk_level 是几，活跃记录都维护）
        self.a5_tools.update_active_violation(
            key=violation_key,
            a6_event_id=a6_event_id,
            person_id=person_id,
            violation_type=violation_type,
            first_seen=first_seen,
            last_seen=last_seen,
            status="ongoing",
        )

        tick_ms = (time_module.perf_counter() - t0) * 1000
        self._last_tick_ms = round(tick_ms, 2)
        return {
            "status": "success",
            "message": "研判完成",
            "assessment": assessment,
            "tick_ms": round(tick_ms, 2),
        }

    async def process_event_batch(self, event_ids: List[str]) -> List[Dict[str, Any]]:
        """
        批量处理多个事件

        Args:
            event_ids: A5 事件 ID 列表

        Returns:
            研判结果列表
        """
        results = []
        for event_id in event_ids:
            result = await self.process_event(event_id)
            results.append(result)
        return results

    # ============================================================
    # 内部方法
    # ============================================================

    def _build_violation_key(self, event_data: Dict) -> str:
        """从事件数据提取 violation key = person_id + violation_type

        传感器事件（person=null）用 sensor_id 替代 person_id，保证 key 稳定。
        """
        person = event_data.get("person") or {}
        person_id = person.get("id", "")
        violation_type = event_data.get("type", "").replace(" ", "_").lower()

        # 传感器事件 person_id 为空，用 sensor_id 作为标识
        if not person_id:
            sensor_id = (event_data.get("evidence") or {}).get("sensor_id", "")
            if sensor_id:
                person_id = sensor_id

        return f"{person_id}+{violation_type}"

    async def _classify_risk(self, event_data: Dict, active_context: Dict = None) -> Dict:
        """
        使用带工具的 LLM Agent 进行风险分级

        Args:
            event_data: 事件数据
            active_context: 当前已有的活跃违规上下文（可从 active_violations 查到）
                           传入时表示已有记录，agent 应决定是聚合还是新建

        Returns:
            包含 risk_level、risk_level_name 等字段的字典，
            额外包含 aggregation_decision 字段： "aggregate" / "new"
        """
        if self._risk_agent is None:
            # 无 LLM Agent，降级到规则分类
            return self._rule_based_classification(event_data)

        try:
            user_prompt = self._build_risk_user_prompt(event_data, active_context)
            # ChatOpenAI 的 ainvoke 底层是同步 HTTP 请求，必须放到线程池
            # 否则会阻塞 FastAPI worker 导致 ReadTimeout
            res = await asyncio.to_thread(
                self._risk_agent.invoke,
                {"messages": [HumanMessage(content=user_prompt)]}
            )
            msgs = res.get("messages", [])
            ai_msgs = [m for m in msgs if isinstance(m, AIMessage)]
            raw_output = ai_msgs[-1].content if ai_msgs else "{}"
            return self._parse_llm_output(raw_output)
        except Exception as e:
            print(f"LLM Agent 调用失败: {e}")
            return self._rule_based_classification(event_data)

    def _build_risk_user_prompt(self, event_data: Dict, active_context: Dict = None) -> str:
        """构建风险分级的用户提示"""
        event_id = event_data.get("event_id", "")
        event_type = event_data.get("type", "")
        person = event_data.get("person") or {}
        person_id = person.get("id", "")
        person_name = person.get("name", "")
        person_role = person.get("role", "")
        first_seen = event_data.get("first_seen", "")
        last_seen = event_data.get("last_seen", "")
        duration = self._calc_duration(event_data)
        evidence = event_data.get("evidence", {})
        explanation = event_data.get("explanation", "")

        involved = f"{person_id}({person_name})" if person_name else person_id

        template = self._runtime_risk_user or self._risk_user_template
        prompt = template.format(
            event_id=event_id,
            event_type=event_type,
            involved_persons=involved,
            first_seen=first_seen,
            last_seen=last_seen,
            duration_sec=duration,
            evidence=json.dumps(evidence, ensure_ascii=False, indent=2),
        )

        # 如果传入了 active_context（该 person_id + violation_type 已有活跃记录），追加上下文
        if active_context:
            prompt += f"\n\n## 当前已有的活跃违规记录\n{json.dumps(active_context, ensure_ascii=False, indent=2)}\n\n请结合上述活跃记录判断：如果是已在追踪的违规的延续，请返回 aggregation_decision=aggregate；如果是全新的违规事件，请返回 aggregation_decision=new。"

        return prompt

    def _parse_llm_output(self, raw_output: str) -> Dict:
        """解析 LLM 的 JSON 输出"""
        try:
            # 尝试提取 JSON
            json_str = raw_output
            if "```json" in raw_output:
                json_str = raw_output.split("```json")[1].split("```")[0]
            elif "```" in raw_output:
                json_str = raw_output.split("```")[1].split("```")[0]
            else:
                # 尝试找到第一个 { 到最后一个 }
                start = raw_output.find("{")
                end = raw_output.rfind("}") + 1
                if start >= 0 and end > start:
                    json_str = raw_output[start:end]

            result = json.loads(json_str)

            # 验证字段
            return {
                "risk_level": result.get("risk_level", 2),
                "risk_level_name": result.get("risk_level_name", "一般"),
                "risk_basis": result.get("risk_basis", ""),
                "suggestions": result.get("suggestions", []),
                "reasoning": result.get("reasoning", ""),
            }

        except json.JSONDecodeError:
            # 解析失败，使用默认值
            return {
                "risk_level": 2,
                "risk_level_name": "一般",
                "risk_basis": "LLM 输出解析失败，使用默认分级",
                "suggestions": ["待人工确认"],
                "reasoning": f"原始输出: {raw_output[:200]}"
            }

    def _rule_based_classification(self, event_data: Dict) -> Dict:
        """
        基于规则的简单风险分级（LLM 不可用时的兜底方案）

        Args:
            event_data: 事件数据

        Returns:
            风险分级结果
        """
        evidence = event_data.get("evidence", {})
        is_in_danger_zone = evidence.get("is_in_danger_zone", False)
        sensor_status = evidence.get("sensor_status", "normal")
        guard_present = evidence.get("guard_present", True)
        person_id = (event_data.get("person") or {}).get("id", "")

        # 判断是否群体违规（多人）
        is_group = "," in str(person_id) or "P9,P11" in str(person_id)

        # 基础等级
        risk_level = 2
        risk_level_name = "一般"
        basis_parts = ["基础等级: 一般（2级）"]

        # 危险区加成: +1
        if is_in_danger_zone:
            risk_level = min(risk_level + 1, 5)
            basis_parts.append("危险区+1")

        # 群体加成: 2人+1, 3人及以上+2
        if is_group:
            risk_level = min(risk_level + 1, 5)
            basis_parts.append("群体违规+1")

        # 环境叠加加成: sensor报警 +1, danger +2
        if sensor_status == "warning":
            risk_level = min(risk_level + 1, 5)
            basis_parts.append("传感器报警+1")
        elif sensor_status == "danger":
            risk_level = min(risk_level + 2, 5)
            basis_parts.append("传感器危险+2")

        # 管理失控加成: 监护人不在+人员违规 +2, 监护人不在+环境报警 置5
        if not guard_present:
            if sensor_status in ("warning", "danger"):
                risk_level = 5
                basis_parts.append("管理失控(监护人不在+环境报警)=5")
            else:
                risk_level = min(risk_level + 2, 5)
                basis_parts.append("监护人不在+2")

        # 更新等级名称
        level_names = {1: "轻微", 2: "一般", 3: "较重", 4: "严重", 5: "危急"}
        risk_level_name = level_names.get(risk_level, "一般")

        basis = "事件类型: " + event_data.get("type", "未知") + "\n" + "\n".join(basis_parts) + f"\n最终等级: {risk_level_name}（{risk_level}级）"

        return {
            "risk_level": risk_level,
            "risk_level_name": risk_level_name,
            "risk_basis": basis,
            "suggestions": prompts.RISK_SUGGESTIONS_BY_LEVEL.get(risk_level, []),
            "reasoning": "基于规则的自动分级（LLM 不可用）"
        }

    def _normalize_wall_time(self, wall_time: str) -> str:
        """
        将 A5 输出的 wall_time 格式转为标准 ISO 格式。
        A5 格式: 2026:08:11T08:57:18.839  (日期部分用 : 分隔)
        ISO 格式: 2026-08-11T08:57:18.839  (标准 ISO)
        JavaScript Date() 可以解析 ISO 格式。
        """
        if not wall_time or not isinstance(wall_time, str):
            return wall_time or ""
        # 只替换前两个 : (日期部分的 : separators)
        return wall_time.replace(":", "-", 2)

    def _calc_duration(self, event_data: Dict) -> float:
        """计算事件持续时长（秒），精确到毫秒"""
        first_seen = event_data.get("first_seen", "")
        last_seen = event_data.get("last_seen", "")

        if not first_seen or not last_seen:
            return 0.0

        try:
            # A5 输出的 wall_time 格式: 2026:08:10T15:49:06.171
            # 日期部分用 : 分隔，需统一转为 -
            ts1 = first_seen.replace(":", "-", 2)   # 只替换前两个 : (日期部分)
            ts2 = last_seen.replace(":", "-", 2)
            # 完整解析，包含毫秒（%f 需要 6 位，不足补零）
            parts1 = ts1.split(".")
            parts2 = ts2.split(".")
            ms1 = parts1[1].ljust(6, "0") if len(parts1) > 1 else "000000"
            ms2 = parts2[1].ljust(6, "0") if len(parts2) > 1 else "000000"
            ts1_full = parts1[0] + "." + ms1
            ts2_full = parts2[0] + "." + ms2
            fmt = "%Y-%m-%dT%H:%M:%S.%f"
            dt1 = datetime.strptime(ts1_full, fmt)
            dt2 = datetime.strptime(ts2_full, fmt)
            return (dt2 - dt1).total_seconds()
        except ValueError:
            # 回退：尝试不带毫秒的格式
            try:
                fmt2 = "%Y-%m-%dT%H:%M:%S"
                ts1 = first_seen.replace(":", "-", 2)
                ts2 = last_seen.replace(":", "-", 2)
                dt1 = datetime.strptime(ts1.split(".")[0], fmt2)
                dt2 = datetime.strptime(ts2.split(".")[0], fmt2)
                return (dt2 - dt1).total_seconds()
            except ValueError:
                return 0.0

    async def _update_existing_assessment(
        self,
        existing: Dict,
        new_event_data: Dict,
        a6_output_id: str
    ) -> Dict:
        """
        更新已有研判报告。

        处理逻辑：
        1. 更新时间范围（first_seen 取更早的，last_seen 取更晚的）
        2. 合并 involved_persons（去重合并）
        3. 合并 evidence（追加新证据）
        4. 重新执行风险分级（因为 duration/persons/evidence 都可能变了）
        5. 覆盖保存到同一文件

        Args:
            existing: 已有研判报告
            new_event_data: 新到达的 A5 事件数据
            a6_output_id: A6 研判ID（用于定位保存路径）

        Returns:
            更新后的研判报告
        """
        # ── 1. 更新时间范围 ──
        # 注意：A5 的 first_seen/last_seen 格式为 2026:08:11T08:57:18.839，
        # 转为 ISO 格式后比较（字符串比较即可，因为格式固定）
        new_first = self._normalize_wall_time(new_event_data.get("first_seen", ""))
        new_last = self._normalize_wall_time(new_event_data.get("last_seen", ""))
        existing_first = existing.get("first_seen", "")
        existing_last = existing.get("last_seen", "")

        if new_first and (not existing_first or new_first < existing_first):
            existing["first_seen"] = new_first
        if new_last and (not existing_last or new_last > existing_last):
            existing["last_seen"] = new_last

        # 重新计算 duration
        existing["duration_sec"] = self._calc_duration(existing)

        # ── 2. 合并涉及人员 ──
        new_persons = new_event_data.get("involved_persons", [])
        if isinstance(new_event_data.get("person"), dict):
            new_persons = [new_event_data["person"].get("id", "")] + new_persons
        existing_persons: list = existing.get("involved_persons", [])
        for p in new_persons:
            if p and p not in existing_persons:
                existing_persons.append(p)
        existing["involved_persons"] = existing_persons

        # ── 3. 合并 evidence ──
        # 策略：对于关键字段（location, is_in_danger_zone, sensor_status, guard_present），
        #       始终以新数据为准（因为持续越久，这些字段越可能已知）；
        #       对于数值型字段，取更严重的值
        new_evidence = new_event_data.get("evidence", {})
        existing_evidence: dict = existing.get("evidence", {})
        CRITICAL_FIELDS = {"location", "is_in_danger_zone", "sensor_status", "guard_present"}
        if new_evidence:
            for k, v in new_evidence.items():
                if k in CRITICAL_FIELDS:
                    # 关键字段以新数据为准（覆盖旧值）
                    existing_evidence[k] = v
                elif k not in existing_evidence:
                    existing_evidence[k] = v
                elif isinstance(v, (int, float)) and isinstance(existing_evidence[k], (int, float)):
                    # 数值型字段取更大值
                    existing_evidence[k] = max(v, existing_evidence[k])
            existing["evidence"] = existing_evidence

        # ── 4. 重新评估风险等级 ──
        # 传入 active_context，让 LLM 知道这是已有违规的延续，应返回 aggregation_decision=aggregate
        risk_result = await self._classify_risk(existing, active_context=existing)
        existing["risk_level"] = risk_result.get("risk_level", existing.get("risk_level", 2))
        existing["risk_level_name"] = risk_result.get("risk_level_name", existing.get("risk_level_name", "一般"))
        existing["risk_basis"] = risk_result.get("risk_basis", existing.get("risk_basis", ""))
        existing["reasoning"] = risk_result.get("reasoning", existing.get("reasoning", ""))
        existing["suggestions"] = risk_result.get("suggestions", existing.get("suggestions", []))

        # ── 5. 添加到 aggregated_from ──
        new_event_id = new_event_data.get("event_id")
        aggregated: list = existing.get("aggregated_from", [])
        if new_event_id and new_event_id not in aggregated:
            aggregated.append(new_event_id)
        existing["aggregated_from"] = aggregated

        existing["timestamp"] = datetime.now().isoformat()

        # ── 6. 覆盖保存 ──
        file_data = self.output_tools.load_assessment_with_path(a6_output_id)
        if file_data:
            _, old_filepath = file_data
            self.output_tools.save_assessment(existing, filepath=old_filepath)
        else:
            # 文件不存在则新建
            self.output_tools.save_assessment(existing)

        return existing

    # ============================================================
    # 工具方法（供外部调用）
    # ============================================================

    def get_status(self) -> Dict:
        """获取 A6 状态"""
        return self.a5_tools._get_status()

    def get_pending_events(self, status_filter: str = None) -> List[Dict]:
        """获取待处理的事件列表"""
        return self.a5_tools.list_raw_events(status_filter=status_filter)
