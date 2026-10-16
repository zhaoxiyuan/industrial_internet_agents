"""SQLite 持久化层：注册信息、凭据引用、对话、入站事件和发送回执。

每个调用创建独立连接，避免 HTTP 线程共享 SQLite 连接；写入使用参数化 SQL。
私有凭据仅内部读取；public 展示和权限判断由上层负责。数据库为本机明文存储。"""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .common import Problem, dumps, now, uid


class Store:
    """平台唯一的持久化入口；通用实体与强一致回执/事件分别存储。"""
    def __init__(self, data):
        """创建数据目录和必要表，保留已有数据；尝试限制数据库的文件权限。
        Windows 权限主要由目录 ACL 决定，chmod 不能替代系统权限配置。"""
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.path = self.data / "platform.db"
        self.lock = threading.RLock()
        with self.db() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS entities(kind TEXT,id TEXT,data TEXT,created TEXT,updated TEXT,PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS secrets(id TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT,payload TEXT,created TEXT);
                CREATE TABLE IF NOT EXISTS receipts(request_id TEXT PRIMARY KEY,fingerprint TEXT,result TEXT,created TEXT);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,connection_id TEXT,event_id TEXT,data TEXT,status TEXT,created TEXT,UNIQUE(connection_id,event_id));
            """)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @contextmanager
    def db(self):
        """提供短事务连接，正常退出提交，异常回滚，最后始终关闭。
        timeout=5 限制等待写锁的时间；网络请求不得在此事务内执行。"""
        c = sqlite3.connect(self.path, timeout=5)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def put(self, kind, data, id=None):
        """按 kind/id 新增或更新实体 JSON，保留创建时间并更新修改时间。
        不提供 id 时生成新标识；返回完整保存后的实体，不做业务权限判断。"""
        id = id or data.get("id") or uid()
        existing = self.get(kind, id, required=False)
        value = dict(data, id=id, created=(existing or {}).get("created", now()), updated=now())
        with self.db() as c:
            c.execute("INSERT INTO entities VALUES(?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data,updated=excluded.updated",
                      (kind, id, dumps(value), value["created"], value["updated"]))
        return value

    def get(self, kind, id, required=True):
        """根据类型和标识读取实体；required=True 时不存在会抛出 404。"""
        with self.db() as c:
            row = c.execute("SELECT data FROM entities WHERE kind=? AND id=?", (kind, id)).fetchone()
        if not row and required:
            raise Problem("NOT_FOUND", f"{kind} 对象不存在", 404)
        return json.loads(row[0]) if row else None

    def list(self, kind):
        """按创建时间列出某类实体，返回反序列化对象列表。"""
        with self.db() as c:
            return [json.loads(row[0]) for row in c.execute("SELECT data FROM entities WHERE kind=? ORDER BY created", (kind,))]

    def delete(self, kind, id):
        """删除一条注册实体；引用检查由业务层执行，不级联删除文件和服务。"""
        self.get(kind, id)
        with self.db() as c:
            c.execute("DELETE FROM entities WHERE kind=? AND id=?", (kind, id))

    def secret(self, ref, value=None):
        """按私有引用读取或替换密钥；value=None 为读取，空字符串为清除。
        这个返回值不得直接放入管理 API 响应。"""
        with self.db() as c:
            if value is not None:
                c.execute("INSERT INTO secrets VALUES(?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (ref, value))
            row = c.execute("SELECT value FROM secrets WHERE id=?", (ref,)).fetchone()
            return row[0] if row else ""

    def add_message(self, session_id, payload):
        """追加一条标准模型消息，保留工具调用与工具回执的顺序。"""
        with self.db() as c:
            c.execute("INSERT INTO messages(session_id,payload,created) VALUES(?,?,?)", (session_id, dumps(payload), now()))

    def messages(self, session_id):
        """按数据库自增序号读取会话消息，供界面显示及模型历史重放。"""
        with self.db() as c:
            return [json.loads(row[0]) for row in c.execute("SELECT payload FROM messages WHERE session_id=? ORDER BY id", (session_id,))]

    def receipt(self, request_id):
        """查找幂等发送回执，返回内容指纹和结果；未发送过返回 None。"""
        with self.db() as c:
            row = c.execute("SELECT fingerprint,result FROM receipts WHERE request_id=?", (request_id,)).fetchone()
            return (row[0], json.loads(row[1])) if row else None

    def save_receipt(self, request_id, fingerprint, result):
        """首次写入固定指纹，后续只更新执行结果；不能覆盖原请求身份。
        发送前先保存 unknown，发送成功后更新为 sent，异常时保留真实状态。"""
        with self.db() as c:
            c.execute("INSERT INTO receipts VALUES(?,?,?,?) ON CONFLICT(request_id) DO UPDATE SET result=excluded.result",
                      (request_id, fingerprint, dumps(result), now()))

    def add_event(self, connection_id, event_id, value):
        """按连接和飞书事件 ID 去重保存，返回稳定序号。
        重复投递不会覆盖内容或把 processed 状态重置为 pending。"""
        with self.db() as c:
            c.execute("INSERT OR IGNORE INTO events(connection_id,event_id,data,status,created) VALUES(?,?,?,?,?)",
                      (connection_id, event_id, dumps(value), "pending", now()))
            row = c.execute("SELECT seq FROM events WHERE connection_id=? AND event_id=?", (connection_id, event_id)).fetchone()
        return row[0]

    def events(self, connection_id, after=0, limit=50):
        """读取指定连接 after 游标之后的事件，最多返回 100 条且不自动 ACK。"""
        with self.db() as c:
            return [dict(json.loads(r["data"]), seq=r["seq"], status=r["status"])
                    for r in c.execute("SELECT * FROM events WHERE connection_id=? AND seq>? ORDER BY seq LIMIT ?",
                                       (connection_id, after, min(limit, 100)))]

    def event(self, connection_id, event_id):
        """读取连接所属的单个事件，并附加序号和处理状态，避免跨连接访问。"""
        with self.db() as c:
            row = c.execute("SELECT * FROM events WHERE connection_id=? AND event_id=?", (connection_id, event_id)).fetchone()
        if not row:
            raise Problem("EVENT_NOT_FOUND", "当前连接没有该事件", 404)
        return dict(json.loads(row["data"]), status=row["status"], seq=row["seq"])

    def latest_events(self, connection_id, limit=50):
        """为前端返回最近消息，按序号倒序排列，确保首项是最新入站消息。

        MCP 的 events 仍保留正序游标语义；两种读取互不改变处理状态。
        只取最近有界条数，避免消息超过 50 条后前端始终看不到最新回复目标。
        """
        with self.db() as c:
            return [dict(json.loads(row["data"]), seq=row["seq"], status=row["status"])
                    for row in c.execute("SELECT * FROM events WHERE connection_id=? ORDER BY seq DESC LIMIT ?",
                                         (connection_id, min(limit, 100)))]

    def ack(self, connection_id, event_id, status):
        """更新事件处理状态并返回回执；成功回复资格由飞书业务层校验。"""
        self.event(connection_id, event_id)
        with self.db() as c:
            c.execute("UPDATE events SET status=? WHERE connection_id=? AND event_id=?", (status, connection_id, event_id))
        return {"event_id": event_id, "status": status}
