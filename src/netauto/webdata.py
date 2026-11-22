"""Lớp dữ liệu cho giao diện web (hàm thuần theo nghĩa: chỉ ĐỌC, trả về dict/list).

Tách riêng khỏi FastAPI để (1) kiểm thử được mà không cần chạy máy chủ, (2) giao diện web
dùng lại đúng các hàm mà dòng lệnh đang dùng, không viết lại logic nghiệp vụ.
Mọi giá trị trả về đều chuyển được sang JSON.
"""
import json
import sqlite3

from netauto import intent as intent_mod
from netauto import (audit, db, health, incidents, injections, maintenance, remediation, runs,
                     service)

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
HEALTH_COLS = ("at", "score", "grade", "critical", "warning", "flows_down",
               "flows_degraded", "maintenance_id")


def clamp_limit(value, default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT) -> int:
    """Giới hạn số dòng trả về: thiếu thì dùng mặc định, quá nhỏ/lớn thì kẹp lại (không báo lỗi)."""
    if value is None:
        return default
    return max(1, min(int(value), maximum))


def ensure_all(conn) -> None:
    """Đảm bảo mọi bảng tồn tại, kể cả khi monitor chưa từng chạy trên DB này."""
    conn.executescript(db.SCHEMA)
    incidents.ensure_schema(conn)
    health.ensure_schema(conn)
    maintenance.ensure_schema(conn)
    remediation.ensure_schema(conn)
    runs.ensure_schema(conn)
    service.ensure_schema(conn)
    injections.ensure_schema(conn)
    conn.executescript(audit.SCHEMA)


def _health_row(row) -> dict:
    return dict(zip(HEALTH_COLS, row))


def overview(conn, now: str | None = None) -> dict:
    now = now or runs.now()
    latest = health.list_health(conn, 1)
    history = remediation.list_history(conn, 200)
    return {
        "generated_at": now,
        "monitor": service.status(conn, now),
        "health": _health_row(latest[0]) if latest else None,
        "open_incidents": len(incidents.open_incidents(conn)),
        "maintenance_active": len(maintenance.windows_at(conn, now)),
        "last_remediation": history[-1] if history else None,
        "breaker_open": remediation.breaker_open(history, now),
    }


def incident_list(conn, limit=None, only_open: bool = False) -> dict:
    return {"items": incidents.list_incidents(conn, clamp_limit(limit), only_open)}


def health_history(conn, limit=None) -> dict:
    rows = health.list_health(conn, clamp_limit(limit, 100))
    return {"items": [_health_row(r) for r in reversed(rows)]}      # cũ trước, mới sau (để vẽ biểu đồ)


def remediation_list(conn, limit=None, now: str | None = None) -> dict:
    now = now or runs.now()
    history = remediation.list_history(conn, 200)
    return {"items": history[-clamp_limit(limit):],
            "breaker_open": remediation.breaker_open(history, now)}


def injection_list(conn, limit=None) -> dict:
    rows = injections.list_injections(conn, clamp_limit(limit))
    done = [r for r in rows if r["status"] != "running"]
    return {"items": rows, "summary": injections.summarize(done)}


def maintenance_list(conn, limit=None, now: str | None = None) -> dict:
    now = now or runs.now()
    return {"items": maintenance.list_windows(conn, clamp_limit(limit, 20)),
            "active": maintenance.windows_at(conn, now)}


AUDIT_COLS = ("id", "at", "actor", "result", "exit_code", "nodes", "prune", "duration_s",
              "intent_sha", "hash")


def audit_list(conn, limit=None) -> dict:
    """Nhật ký kiểm toán (mới nhất trước) kèm kết quả kiểm tra chuỗi băm toàn bộ."""
    old, conn.row_factory = conn.row_factory, sqlite3.Row    # audit.verify đọc cột theo tên
    try:
        ok, bad_id, reason = audit.verify(conn)
        rows = conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?",
                            (clamp_limit(limit),)).fetchall()
        items = [{**{k: r[k] for k in AUDIT_COLS}, "hash": r["hash"][:12]} for r in rows]
    finally:
        conn.row_factory = old
    return {"items": items, "chain": {"ok": ok, "bad_id": bad_id, "reason": reason}}


def audit_detail(conn, entry_id: int):
    """Một bản ghi kiểm toán đầy đủ (các bước, hoàn tác...), hoặc None nếu không có."""
    old, conn.row_factory = conn.row_factory, sqlite3.Row
    try:
        r = conn.execute("SELECT * FROM audit WHERE id = ?", (entry_id,)).fetchone()
        if r is None:
            return None
        out = {k: r[k] for k in AUDIT_COLS}
        out.update(hash=r["hash"], prev_hash=r["prev_hash"], intent_path=r["intent_path"],
                   detail=json.loads(r["detail"]))
        return out
    finally:
        conn.row_factory = old


def node_names(intent_path: str | None = None) -> list[str]:
    """Tên mọi thiết bị trong intent (router, switch, host), theo thứ tự cố định."""
    data = intent_mod.load_intent(intent_path or intent_mod.INTENT_PATH)
    return sorted([*data["routers"], *data["switches"], *data["hosts"]])
