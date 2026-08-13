"""
模型配置快照 API
"""
import json
import os
import logging

logger = logging.getLogger("server")

SNAPSHOTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "snapshots"
)
SNAPSHOTS_FILE = os.path.join(SNAPSHOTS_DIR, "model_snapshots.json")


def list_snapshots():
    """返回所有已保存的配置快照"""
    if not os.path.exists(SNAPSHOTS_FILE):
        return []
    with open(SNAPSHOTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_snapshot(name, data):
    """保存一条配置快照（不写入 .env，仅存储快照）"""
    snapshots = list_snapshots()
    safe = {k: (v if k != "api_key" else _mask_api_key(v)) for k, v in data.items()}
    safe["name"] = name
    snapshots = [s for s in snapshots if s.get("name") != name]
    snapshots.insert(0, safe)
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    with open(SNAPSHOTS_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshots, f, ensure_ascii=False, indent=2)
    return snapshots


def delete_snapshot(name):
    """删除指定快照"""
    snapshots = [s for s in list_snapshots() if s.get("name") != name]
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    with open(SNAPSHOTS_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshots, f, ensure_ascii=False, indent=2)
    return snapshots


def _mask_api_key(key):
    if not key or len(key) <= 4:
        return "****"
    return key[:4] + "*" * min(len(key) - 4, 16)


def handle_list(handler):
    snapshots = list_snapshots()
    logger.info(f"[GET] /api/config/snapshots 响应: {len(snapshots)} 条")
    handler.send_json(snapshots)


def handle_save_post(handler, data):
    name = data.get("name", "").strip()
    config_data = data.get("config", {})
    if not name:
        handler.send_json({"status": "error", "error": "name 不能为空"})
        return
    snapshots = save_snapshot(name, config_data)
    logger.info(f"[POST] /api/config/snapshots/save 响应: name={name}, total={len(snapshots)}")
    handler.send_json({"status": "ok", "snapshots": snapshots})


def handle_delete_post(handler, data):
    name = data.get("name", "").strip()
    snapshots = delete_snapshot(name)
    logger.info(f"[POST] /api/config/snapshots/delete 响应: name={name}, total={len(snapshots)}")
    handler.send_json({"status": "ok", "snapshots": snapshots})