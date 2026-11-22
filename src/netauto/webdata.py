"""Lớp dữ liệu cho giao diện web (hàm thuần theo nghĩa: chỉ ĐỌC, trả về dict/list).

Tách riêng khỏi FastAPI để (1) kiểm thử được mà không cần chạy máy chủ, (2) giao diện web
dùng lại đúng các hàm mà dòng lệnh đang dùng, không viết lại logic nghiệp vụ.
Mọi giá trị trả về đều chuyển được sang JSON.
"""
from netauto import (db, health, incidents, injections, maintenance, remediation, runs,
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
