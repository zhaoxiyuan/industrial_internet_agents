"""工单页面占用（租约）管理。

单进程内的**页面级租约**：一个页面开始操作某张工单时取得租约，靠心跳续期。
心跳超时（页面关闭、浏览器崩溃、网络中断）后租约自动失效，其他页面即可接管，
因此不会因为某个页面消失而把工单永久锁死。

与 ``execution_guard`` 的区别：

- ``execution_guard`` 管的是**后台执行任务**，只在阶段真正执行期间持有，
  工单停在人工审批窗口时是释放的；
- ``job_lease`` 管的是**哪个页面在操作这张工单**，从开始操作持续到工单结束或
  页面主动释放，**工单停在人工审批窗口时同样算占用中**。

边界：租约只存在于当前服务进程内，与项目既有的单服务进程模型一致。
"""
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .file_utils import get_job_lock

# 页面每 10 秒发一次心跳；15 秒收不到心跳即判定该页面已失联。
HEARTBEAT_INTERVAL_SECONDS = 10.0
LEASE_TTL_SECONDS = 15.0

_leases: Dict[str, Dict[str, Any]] = {}
_leases_guard = threading.Lock()


def _now() -> float:
    return time.monotonic()


def _is_alive(lease: Dict[str, Any], now: float) -> bool:
    return (now - float(lease.get("heartbeat_at", 0.0))) <= LEASE_TTL_SECONDS


def to_public(lease: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """对外暴露的占用信息（不含内部时间戳）。"""
    if not lease:
        return None
    return {
        "holder_id": lease.get("holder_id"),
        "holder_label": lease.get("holder_label"),
        "acquired_at": lease.get("acquired_at_wall"),
        "last_heartbeat": lease.get("heartbeat_wall"),
        "ttl_seconds": LEASE_TTL_SECONDS,
        "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
    }


def _prune_expired(now: float) -> None:
    """清理已过期条目。调用方必须已持有 _leases_guard。"""
    expired = [key for key, lease in _leases.items() if not _is_alive(lease, now)]
    for key in expired:
        _leases.pop(key, None)


def get_occupant(job_id: str, holder_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """返回占用该工单的**其他**页面信息。

    工单空闲、租约已过期、或持有者就是 ``holder_id`` 自己时，返回 ``None``。
    """
    key = str(job_id)
    now = _now()
    with get_job_lock(key):
        with _leases_guard:
            lease = _leases.get(key)
            if not lease:
                return None
            if not _is_alive(lease, now):
                _leases.pop(key, None)
                return None
            if holder_id is not None and str(lease.get("holder_id")) == str(holder_id):
                return None
            return to_public(lease)


def get_job_lease(job_id: str) -> Optional[Dict[str, Any]]:
    """返回该工单当前的活跃租约；无租约或已过期时返回 None。"""
    return get_occupant(job_id, holder_id=None)


def is_job_lease_holder(job_id: str, holder_id: Optional[str]) -> bool:
    """严格判断页面是否持有该工单当前有效的租约。

    与 ``get_occupant`` 不同：没有租约、租约已过期、未提供页面标识，均返回
    ``False``。操作接口必须先通过此检查，不能把“没有别人占用”等同于“自己持有”。
    """
    if not holder_id:
        return False
    lease = get_job_lease(job_id)
    return bool(lease and str(lease.get("holder_id")) == str(holder_id))


def claim_job_lease(
    job_id: str,
    holder_id: str,
    holder_label: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """取得或续期租约。

    Args:
        job_id: 工单号
        holder_id: 页面标识（同标签页刷新不变，不同标签页不同）
        holder_label: 便于排查的可读标识，如 ``页面#3f2a``
    Returns:
        占用信息；被其他活跃页面持有时返回 ``None``。
    """
    key = str(job_id)
    holder = str(holder_id)
    now = _now()
    with get_job_lock(key):
        with _leases_guard:
            _prune_expired(now)

            lease = _leases.get(key)
            if lease and not _is_alive(lease, now):
                _leases.pop(key, None)
                lease = None

            if lease and str(lease.get("holder_id")) == holder:
                # 同一页面续期
                lease["heartbeat_at"] = now
                lease["heartbeat_wall"] = datetime.now(timezone.utc).isoformat()
                if holder_label:
                    lease["holder_label"] = holder_label
                return to_public(lease)

            if lease:
                return None

            wall = datetime.now(timezone.utc).isoformat()
            _leases[key] = {
                "holder_id": holder,
                "holder_label": holder_label,
                "acquired_at": now,
                "acquired_at_wall": wall,
                "heartbeat_at": now,
                "heartbeat_wall": wall,
            }
            return to_public(_leases[key])


def heartbeat_job_lease(job_id: str, holder_id: str) -> bool:
    """续期租约。

    租约不存在、已过期、或已被其他页面接管时返回 ``False``，
    调用方（页面）据此转为只读。
    """
    key = str(job_id)
    holder = str(holder_id)
    now = _now()
    with get_job_lock(key):
        with _leases_guard:
            lease = _leases.get(key)
            if not lease:
                return False
            if not _is_alive(lease, now):
                _leases.pop(key, None)
                return False
            if str(lease.get("holder_id")) != holder:
                return False
            lease["heartbeat_at"] = now
            lease["heartbeat_wall"] = datetime.now(timezone.utc).isoformat()
            return True


def release_job_lease(job_id: str, holder_id: str) -> bool:
    """主动释放；只有当前持有者能释放，避免旧页面误释放新页面的租约。"""
    key = str(job_id)
    holder = str(holder_id)
    with get_job_lock(key):
        with _leases_guard:
            lease = _leases.get(key)
            if not lease or str(lease.get("holder_id")) != holder:
                return False
            _leases.pop(key, None)
            return True


def clear_job_leases() -> None:
    """测试辅助：清空当前进程的所有租约。"""
    with _leases_guard:
        _leases.clear()


__all__ = [
    "HEARTBEAT_INTERVAL_SECONDS",
    "LEASE_TTL_SECONDS",
    "claim_job_lease",
    "heartbeat_job_lease",
    "release_job_lease",
    "get_job_lease",
    "get_occupant",
    "is_job_lease_holder",
    "to_public",
    "clear_job_leases",
]
