"""
A6 测试脚本

用于验证 A6 功能：
1. 生成测试用的 raw_event 数据
2. 运行 A6 进行风险研判
3. 验证输出结果
"""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from A6.agent.tools import A5DataTools, OutputTools
from A6.agent.prompt_manager import PromptManager
from A6.agent import a6_agent


# ============================================================
# 测试数据生成
# ============================================================

def create_test_raw_events():
    """创建测试用的 raw_event 数据"""
    test_events = [
        {
            "wall_time": "2026-08-06T15:43:05.504",
            "events": [
                {
                    "event_id": "A5-P11-1722933125",
                    "type": "PPE缺失-护目镜",
                    "person": {"id": "P11", "name": "李师傅", "role": "焊工"},
                    "wall_time": "2026-08-06T15:43:05",
                    "first_seen": "2026-08-06T15:43:05",
                    "last_seen": "2026-08-06T15:43:15",
                    "status": "closed",
                    "evidence": {
                        "cv_frames": 250,
                        "helmet_missing_ratio": 0.0,
                        "goggles_missing_ratio": 1.0,
                        "suit_missing_ratio": 0.0,
                        "location": "动火区-A",
                        "is_in_danger_zone": True,
                        "sensor_status": "normal",
                        "guard_present": True
                    },
                    "explanation": "P11 在动火作业中未佩戴护目镜超过10秒"
                }
            ]
        },
        {
            "wall_time": "2026-08-06T15:43:10.504",
            "events": [
                {
                    "event_id": "A5-P7-1722933130",
                    "type": "PPE缺失-头盔",
                    "person": {"id": "P7", "name": "张师傅", "role": "焊工"},
                    "wall_time": "2026-08-06T15:43:10",
                    "first_seen": "2026-08-06T15:43:10",
                    "last_seen": "2026-08-06T15:43:25",
                    "status": "closed",
                    "evidence": {
                        "cv_frames": 375,
                        "helmet_missing_ratio": 0.95,
                        "goggles_missing_ratio": 0.0,
                        "suit_missing_ratio": 0.0,
                        "location": "动火区-A",
                        "is_in_danger_zone": True,
                        "sensor_status": "warning",
                        "guard_present": False
                    },
                    "explanation": "P7 在动火区连续15秒不戴头盔，且监护人不在场"
                }
            ]
        },
        {
            "wall_time": "2026-08-06T15:43:15.504",
            "events": [
                {
                    "event_id": "A5-P8-1722933135",
                    "type": "PPE缺失-防护服",
                    "person": {"id": "P8", "name": "王师傅", "role": "铆工"},
                    "wall_time": "2026-08-06T15:43:15",
                    "first_seen": "2026-08-06T15:43:15",
                    "last_seen": "2026-08-06T15:43:30",
                    "status": "closed",
                    "evidence": {
                        "cv_frames": 375,
                        "helmet_missing_ratio": 0.0,
                        "goggles_missing_ratio": 0.0,
                        "suit_missing_ratio": 0.88,
                        "location": "焊接区-B",
                        "is_in_danger_zone": False,
                        "sensor_status": "normal",
                        "guard_present": True
                    },
                    "explanation": "P8 在焊接作业中未穿防护服超过15秒"
                }
            ]
        },
        {
            "wall_time": "2026-08-06T15:43:20.504",
            "events": [
                {
                    "event_id": "A5-P9-P11-1722933140",
                    "type": "群体PPE违规",
                    "person": {"id": "P9,P11", "name": "赵师傅,李师傅", "role": "焊工,焊工"},
                    "wall_time": "2026-08-06T15:43:20",
                    "first_seen": "2026-08-06T15:43:20",
                    "last_seen": "2026-08-06T15:43:35",
                    "status": "closed",
                    "evidence": {
                        "cv_frames": 375,
                        "helmet_missing_ratio": 0.9,
                        "goggles_missing_ratio": 0.85,
                        "suit_missing_ratio": 0.0,
                        "location": "动火区-A",
                        "is_in_danger_zone": True,
                        "sensor_status": "danger",
                        "guard_present": False
                    },
                    "explanation": "P9、P11 在动火区同时不戴头盔和护目镜，可燃气体报警，监护人不在"
                }
            ]
        }
    ]

    return test_events


def save_test_events(test_events, output_dir="A5/logs"):
    """保存测试事件到文件"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    saved_files = []
    for event_data in test_events:
        wall_time = event_data["wall_time"]
        safe_time = wall_time.replace(":", "-").replace(".", "_")
        filename = f"raw_event_{safe_time}.json"
        filepath = output_path / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(event_data, f, ensure_ascii=False, indent=2)

        saved_files.append(filename)
        print(f"  保存: {filename}")

    return saved_files


# ============================================================
# 工具测试
# ============================================================

def test_a5_tools():
    """测试 A5DataTools"""
    print("\n=== 测试 A5DataTools ===")

    tools = A5DataTools(a5_log_dir="A5/logs")

    # 测试 check_event_status
    print("\n1. 测试 check_event_status:")
    status = tools.check_event_status("A5-P11-1722933125")
    print(f"   事件 A5-P11-1722933125 状态: {status}")

    # 测试 read_raw_event_by_id
    print("\n2. 测试 read_raw_event_by_id:")
    event = tools.read_raw_event_by_id("A5-P11-1722933125")
    if event:
        print(f"   找到事件: {event.get('type')} - {event.get('person', {}).get('name')}")
    else:
        print("   未找到事件（预期，因为是新增测试数据）")

    # 测试 list_raw_events
    print("\n3. 测试 list_raw_events:")
    events = tools.list_raw_events(limit=5)
    print(f"   找到 {len(events)} 个 raw_event 文件")


def test_output_tools():
    """测试 OutputTools"""
    print("\n=== 测试 OutputTools ===")

    tools = OutputTools(output_dir="A6/logs")

    # 测试 save_assessment
    print("\n1. 测试 save_assessment:")
    test_assessment = {
        "a6_event_id": "A6-TEST-001",
        "event_type": "测试事件",
        "risk_level": 3,
        "risk_level_name": "较重",
        "risk_basis": "测试",
        "suggestions": ["建议1", "建议2"],
        "reasoning": "测试推理",
        "timestamp": datetime.now().isoformat()
    }
    path = tools.save_assessment(test_assessment)
    print(f"   保存到: {path}")

    # 测试 update_a5_log_status
    print("\n2. 测试 update_a5_log_status:")
    result = tools.update_a5_log_status(
        ["raw_event_2026-08-06T15-43-05_504.json"],
        "processed",
        {"a6_output_id": "A6-TEST-001"}
    )
    print(f"   更新结果: {result}")


def test_prompt_manager():
    """测试 PromptManager"""
    print("\n=== 测试 PromptManager ===")

    manager = PromptManager()

    # 测试 read_prompt
    print("\n1. 测试 read_prompt:")
    prompt = manager.read_prompt("risk_classification")
    print(f"   提示词名称: {prompt['name']}")
    print(f"   内容长度: {len(prompt.get('content', ''))} 字符")

    # 测试 get_all_prompts
    print("\n2. 测试 get_all_prompts:")
    all_prompts = manager.get_all_prompts()
    for name in all_prompts:
        print(f"   - {name}: {len(all_prompts[name]['content'])} 字符")

    # 测试 get_risk_suggestions
    print("\n3. 测试 get_risk_suggestions:")
    suggestions = manager.get_risk_suggestions()
    for level, items in suggestions.items():
        print(f"   等级 {level}: {items[0] if items else '无'}")


# ============================================================
# Agent 集成测试
# ============================================================

async def test_a6_agent():
    """测试 A6Agent"""
    print("\n=== 测试 A6Agent ===")

    # 创建 Agent
    agent = a6_agent.A6Agent(
        a5_log_dir="A5/logs",
        a6_output_dir="A6/logs"
    )

    # 保存测试数据
    print("\n1. 生成测试数据...")
    test_events = create_test_raw_events()
    saved_files = save_test_events(test_events)
    print(f"   保存了 {len(saved_files)} 个测试文件")

    # 测试 process_event
    print("\n2. 测试 process_event (A5-P11-1722933125):")
    result = await agent.process_event("A5-P11-1722933125")
    print(f"   状态: {result.get('status')}")
    if result.get('assessment'):
        a = result['assessment']
        print(f"   风险等级: {a.get('risk_level_name')}({a.get('risk_level')})")
        print(f"   依据: {a.get('risk_basis', '')[:100]}...")
    if result.get('tick_ms'):
        print(f"   耗时: {result['tick_ms']}ms")

    # 测试重复调用（应该返回 duplicate）
    print("\n3. 测试重复调用:")
    result2 = await agent.process_event("A5-P11-1722933125")
    print(f"   状态: {result2.get('status')}")

    # 测试批量处理
    print("\n4. 测试批量处理:")
    results = await agent.process_event_batch([
        "A5-P7-1722933130",
        "A5-P8-1722933135",
        "A5-P9-P11-1722933140"
    ])
    for i, r in enumerate(results):
        status = r.get('status')
        if r.get('assessment'):
            a = r['assessment']
            print(f"   [{i+1}] {status}: {a.get('risk_level_name')}({a.get('risk_level')})")
        else:
            print(f"   [{i+1}] {status}: {r.get('message', '')}")

    # 测试 rule_based_classification (无 LLM 模式)
    print("\n5. 测试规则分类 (无 LLM):")
    agent_no_llm = a6_agent.A6Agent()
    agent_no_llm.llm = None  # 强制使用规则
    result = await agent_no_llm.process_event("A5-P11-1722933125")
    if result.get('assessment'):
        a = result['assessment']
        print(f"   风险等级: {a.get('risk_level_name')}({a.get('risk_level')})")
        print(f"   推理: {a.get('reasoning')}")


# ============================================================
# 主函数
# ============================================================

async def main():
    print("=" * 60)
    print("A6 智能体测试")
    print("=" * 60)

    # 工具测试
    test_a5_tools()
    test_output_tools()
    test_prompt_manager()

    # Agent 测试
    await test_a6_agent()

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
