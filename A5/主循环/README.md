# 主循环 — 设计说明

> **定位**:固定流程入口,协调数据流 + 可选集成 A5 智能体
> **文件**:`main_loop.py`
> **状态**:已完成

---

## 一、作用

主循环是 A5 项目的**唯一入口**,负责:

1. 启动 3 个模拟数据流(CV / 传感器 / 定位)
2. 每 1 秒落盘日志 + 取快照
3. (可选)每 1 秒回调 A5 智能体
4. 支持单场景或批量任意组合运行

---

## 二、调用方式

```bash
# 纯固定流程(无智能体,仅打印)
python 主循环/main_loop.py --scenario B --no-agent

# 默认带 A5 智能体
python 主循环/main_loop.py --scenario B

# 批量运行(任意字符组合)
python 主循环/main_loop.py --scenario ABCDE
python 主循环/main_loop.py --scenario AAAAA
python 主循环/main_loop.py --scenario ABCDEACC

# 指定日志输出目录
python 主循环/main_loop.py --scenario ABCDE --log-dir logs/batch
```

---

## 三、主循环流程

```
for 每个场景:
    collector = AsyncCollector(scenario, log_dir)
    agent = A5Agent(collector, work_permit)
    await collector.start()
    
    for sec in 0..29:
        await asyncio.sleep(1.0)          # 实时 1 秒
        await collector.flush_second(sec)  # 落盘
        wall_time = collector._sec_to_wall(sec+1)
        snapshot = collector.get_snapshot(wall_time)
        events = await agent.tick(wall_time, snapshot)
        # 打印事件(如有)
    
    await collector.stop()
```

---

## 四、与智能体的关系

- 主循环**驱动**智能体(每 1 秒回调)
- 智能体**不嵌入**主循环(独立模块)
- 通过 `--no-agent` 可跳过智能体

---

## 五、输出文件

```
logs/batch_20260804_144627/
├── run_01_A/
│   ├── cv_<ts>_T00.json ~ T29.json        (30 个文件,每个 75 条 CV)
│   ├── sensor_<ts>_T00.json ~ T29.json    (30 个文件)
│   ├── position_<ts>_T00.json ~ T29.json  (30 个文件)
│   ├── snapshot_<ts>_T00.json ~ T29.json  (30 个文件,每秒 1 快照)
│   └── raw_events/                         (A5 智能体输出,仅当有事件)
│       └── raw_event_<ts>_<wall>.json
├── run_02_B/
└── run_03_C/
```

---

## 六、时间约定

- **实时 1:1**:现实 1 秒 = 场景 1 秒
- **不支持倍速**(如需调试,用场景间等 30 秒)
- 所有时间参数使用**现实世界时间**(ISO 格式)
