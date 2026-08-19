# WebSocket 通信机制

## 1. 概述

系统使用 WebSocket 实现前后端实时通信，共两个通道：

| 通道 | 端口 | 用途 | 消息类型 |
|------|------|------|----------|
| 状态通道 | 8081 | 推送工作流状态变更 | `state_update`, `pending_confirmations` |
| 日志通道 | 8082 | 推送执行日志 | `workflow_log`, `heartbeat` |

## 2. 启动链路

### 2.1 启动时机

WebSocket 服务器在 `server.py` 启动时自动初始化：

```
server.py (main)
    │
    └─► import web.ws.servers.start_websocket_threads
    │
    └─► start_websocket_threads()  ← HTTP 服务器启动前调用
```

```python
# web/server.py
from web.ws.servers import start_websocket_threads

def run():
    # ...
    start_websocket_threads()  # 第 137 行
    app.RunServer(...)        # 启动 Gradio/Flask
```

### 2.2 线程模型

```
┌─────────────────────────────────────────────────────────┐
│                     主线程 (Main)                         │
│              server.py 运行 HTTP 服务                     │
└─────────────────────────────────────────────────────────┘
           │                           │
           │  threading.Thread         │  threading.Thread
           ▼                           ▼
┌─────────────────────┐     ┌─────────────────────────────┐
│  WS-STATUS 线程      │     │  WS-LOGS 线程               │
│  run_status_ws_server│     │  run_logs_ws_server        │
│         ↓            │     │         ↓                  │
│  asyncio EventLoop   │     │  asyncio EventLoop         │
│         ↓            │     │         ↓                  │
│  websockets.serve()  │     │  websockets.serve()        │
└─────────────────────┘     └─────────────────────────────┘
```

### 2.3 启动流程详解

```python
# web/ws/servers.py

def start_websocket_threads():
    """启动所有 WebSocket 服务器线程"""
    # 状态通道线程
    ws_status_thread = threading.Thread(target=run_status_websocket_server, daemon=True)
    ws_status_thread.start()

    # 日志通道线程
    ws_logs_thread = threading.Thread(target=run_logs_websocket_server, daemon=True)
    ws_logs_thread.start()


def run_status_websocket_server():
    """在新线程中运行状态 WebSocket 服务器"""
    loop = asyncio.new_event_loop()        # ① 创建事件循环
    asyncio.set_event_loop(loop)           # ② 设置为当前线程默认
    loop.run_until_complete(_start_status_websocket_server())  # ③ 运行协程


async def _start_status_websocket_server():
    """异步启动状态 WebSocket 服务器（永久运行）"""
    while True:  # 异常恢复重试
        try:
            async with websockets.serve(
                status_websocket_handler,    # 连接处理器
                "127.0.0.1",                  # 只监听本地
                WS_STATUS_PORT,              # 8081
                process_request=process_request
            ):
                logger.info(f"[WS-STATUS] 服务器已启动: ws://localhost:{WS_STATUS_PORT}")
                await asyncio.Future()  # 永久等待，阻塞直到服务器关闭
        except Exception as e:
            logger.error(f"[WS-STATUS] 服务器错误: {e}")
            await asyncio.sleep(1)  # 出错后等待 1 秒重试
```

## 3. 异步线程详解

### 3.1 为什么用独立线程？

- WebSocket 服务器需要 `await asyncio.Future()` 永久阻塞
- HTTP 服务器也需要持续运行
- 两个服务不能串行阻塞，所以各自用独立线程

### 3.2 每个线程独立事件循环

```python
# 每个线程都有自己的事件循环
loop = asyncio.new_event_loop()    # 创建
asyncio.set_event_loop(loop)       # 设为默认
loop.run_until_complete(...)        # 运行直到完成（但这里永远不完成）
```

### 3.3 `await asyncio.Future()` 的作用

```python
# 这行代码会永久阻塞，直到...永远不会自动结束
await asyncio.Future()  # 类似于 while True: await asyncio.sleep(0)
```

这就是 WebSocket 服务器能"一直运行"而不退出的秘密。

## 4. 连接处理器

### 4.1 状态通道处理器

```python
# web/ws/handlers.py

async def status_websocket_handler(websocket):
    # 1. 解析 job_id
    job_id = path.split('/')[-1]

    # 2. 注册连接
    status_connections[job_id].add(websocket)

    # 3. 发送初始状态
    state = get_workflow_state(job_id)
    await websocket.send(json.dumps({"type": "state_update", "data": state}))

    # 4. 循环推送队列消息
    while True:
        msg = await asyncio.to_thread(status_broadcast_queue.get, True, 1.0)
        if msg.get("job_id") == job_id:
            await websocket.send(json.dumps(msg))
```

### 4.2 日志通道处理器

结构与状态通道类似，区别在于：
- 日志消息广播给所有订阅的客户端
- 包含 `level`（INFO/WARNING/ERROR）、`source`（来源）

## 5. 消息广播接口

### 5.1 发送状态消息

```python
from web.ws.manager import broadcast_workflow_state

# 推送工作流状态变更
broadcast_workflow_state(job_id)
```

消息格式：
```json
{
  "type": "state_update",
  "job_id": "xxx",
  "data": {
    "status": "running",
    "current_stage": "P3",
    "pending": ["P4"],
    "pending_data": {...},
    "confirmed": ["P1", "P2"]
  }
}
```

### 5.2 发送日志消息

```python
from web.ws.manager import broadcast_workflow_log

# 推送执行日志
broadcast_workflow_log(
    job_id=job_id,
    level="INFO",
    source="Agent",
    message="开始执行 P3 任务",
    data={"details": "..."}
)
```

消息格式：
```json
{
  "type": "workflow_log",
  "job_id": "xxx",
  "level": "INFO",
  "source": "Agent",
  "message": "开始执行 P3 任务",
  "timestamp": "2026-08-13T10:30:00.000000",
  "data": {"details": "..."}
}
```

### 5.3 广播原理（跨线程安全）

```
业务代码（任意线程）
    │
    │ broadcast_workflow_log(...)
    ▼
manager.py ──► queue.put_nowait(message)
                    │
                    │ asyncio.to_thread(queue.get, ...)
                    ▼
              handlers.py（WebSocket 线程）
                    │
                    │ websocket.send(...)
                    ▼
              前端 WebSocket 客户端
```

队列解耦：业务线程通过队列将消息传递给 WebSocket 线程，实现跨线程通信。

## 6. 前端连接

```javascript
// web/app.js

// 状态通道
const statusWsUrl = `ws://localhost:8081/ws/status/${jobId}`;
state.ws = new WebSocket(statusWsUrl);
state.ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === 'state_update') {
        // 更新工作流状态 UI
    }
};

// 日志通道
const logsWsUrl = `ws://localhost:8082/ws/logs/${jobId}`;
state.logsWs = new WebSocket(logsWsUrl);
state.logsWs.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === 'workflow_log') {
        // 显示日志
    }
};
```

## 7. 端口配置

| 端口 | 常量 | 用途 |
|------|------|------|
| 8080 | `PORT` | HTTP 服务端口 |
| 8081 | `WS_STATUS_PORT = PORT + 1` | 状态 WebSocket |
| 8082 | `WS_LOGS_PORT = PORT + 2` | 日志 WebSocket |

配置位置：`web/ws/manager.py`

```python
PORT = 8080
WS_STATUS_PORT = PORT + 1   # 8081
WS_LOGS_PORT = PORT + 2     # 8082
```

## 8. 心跳机制

WebSocket handler 每秒发送一次心跳，防止连接空闲被 OS/代理断开：

```python
# handlers.py
await websocket.send(json.dumps({"type": "heartbeat", "channel": "status"}))
# 或
await websocket.send(json.dumps({"type": "heartbeat", "channel": "logs"}))
```

前端收到心跳后忽略，不做处理。