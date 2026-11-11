"""Ghi nhận các lần chạy monitor và phát hiện khoảng trống giám sát.

Khoảng trống = thời gian giữa snapshot cuối của lần chạy trước và lúc monitor khởi động
lại, khi dài hơn ngưỡng. Trong khoảng đó không ai nhìn mạng, nên không được suy ra
thời điểm xảy ra hay thời lượng của bất cứ điều gì.
"""
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS monitor_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       TEXT    NOT NULL,
    stopped_at       TEXT,               -- NULL = chưa dừng hoặc bị ngắt đột ngột
    stopped_cleanly  INTEGER,
    interval_s       INTEGER NOT NULL,
    gap_from         TEXT,               -- có giá trị nếu trước lần chạy này có khoảng trống
    gap_to           TEXT
);
"""

MIN_GAP_S = 30


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _seconds(a: str, b: str) -> float:
    fa = datetime.fromisoformat(a.replace("Z", "+00:00"))
    fb = datetime.fromisoformat(b.replace("Z", "+00:00"))
    return (fb - fa).total_seconds()


def gap_threshold(interval: int) -> float:
    return max(MIN_GAP_S, 3 * interval)


def detect_gap(last_seen, at: str, interval: int):
    """Hàm thuần. Trả về {"from", "to", "seconds"} hoặc None nếu không có khoảng trống."""
    if not last_seen:
        return None
    seconds = _seconds(last_seen, at)
    if seconds <= gap_threshold(interval):
        return None
    return {"from": last_seen, "to": at, "seconds": seconds}


def fmt_span(seconds: float) -> str:
    s = int(round(seconds))
    days, rest = divmod(s, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days} ngày {hours} giờ"
    if hours:
        return f"{hours} giờ {minutes} phút"
    if minutes:
        return f"{minutes} phút {secs} giây"
    return f"{secs} giây"


def start_run(conn, interval: int, last_seen):
    """Ghi lần chạy mới. Trả về (run_id, gap hoặc None)."""
    at = now()
    prev = conn.execute(
        "SELECT stopped_cleanly FROM monitor_runs ORDER BY id DESC LIMIT 1").fetchone()
    previous_clean = None if prev is None else bool(prev[0])
    gap = detect_gap(last_seen, at, interval)
    cur = conn.execute(
        "INSERT INTO monitor_runs (started_at, interval_s, gap_from, gap_to) "
        "VALUES (?, ?, ?, ?)",
        (at, interval, gap["from"] if gap else None, gap["to"] if gap else None))
    conn.commit()
    if gap:
        gap["previous_clean"] = previous_clean
    return cur.lastrowid, gap


def stop_run(conn, run_id: int, clean: bool) -> None:
    conn.execute("UPDATE monitor_runs SET stopped_at = ?, stopped_cleanly = ? WHERE id = ?",
                 (now(), 1 if clean else 0, run_id))
    conn.commit()


def describe_gap(gap: dict) -> str:
    how = {True: "lần chạy trước dừng bình thường",
           False: "lần chạy trước bị ngắt đột ngột",
           None: "không có bản ghi lần chạy trước"}[gap.get("previous_clean")]
    return (f"CÓ KHOẢNG TRỐNG GIÁM SÁT từ {gap['from']} đến {gap['to']} "
            f"({fmt_span(gap['seconds'])}; {how})")