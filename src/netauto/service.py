"""Chạy monitor như một dịch vụ: khóa một tiến trình, heartbeat, trạng thái.

Khóa một tiến trình: hai monitor cùng ghi một DB sẽ làm snapshot nhảy số và sự cố sai.
Dùng khóa của hệ điều hành (flock) trên file <DB>.lock: tiến trình chết thế nào
(kể cả kill -9, mất điện) hệ điều hành cũng tự nhả khóa, không để lại "khóa mồ côi".

Heartbeat: mỗi vòng monitor ghi một dấu "còn sống" vào bảng heartbeat. Ai muốn biết
monitor còn chạy không chỉ cần đọc bảng này, không cần đoán qua snapshot.
"""
import fcntl
import os
from datetime import datetime
from pathlib import Path

from netauto import runs

SCHEMA = """
CREATE TABLE IF NOT EXISTS heartbeat (
    id          INTEGER PRIMARY KEY CHECK (id = 1),   -- chỉ một dòng: monitor hiện tại
    run_id      INTEGER NOT NULL,
    pid         INTEGER NOT NULL,
    started_at  TEXT    NOT NULL,
    beat_at     TEXT    NOT NULL,
    interval_s  INTEGER NOT NULL
);
"""

# mã thoát của lệnh `status`, theo tinh thần của systemctl (0 = đang chạy, 3 = không chạy)
EXIT_CODES = {"alive": 0, "dead": 2, "stopped": 3, "never": 4}


class AlreadyRunning(Exception):
    """Đã có monitor khác giữ khóa của DB này. str(exc) là PID của nó."""


class InstanceLock:
    """Mỗi vai trò (monitor, remediation) có khóa riêng, nên chạy song song không vướng nhau,
    nhưng không vai nào chạy được hai bản. Tên mặc định giữ nguyên file `<db>.lock` cũ."""
    def __init__(self, db_path: str, name: str = ""):
        suffix = f".{name}.lock" if name else ".lock"
        self.path = None if db_path == ":memory:" else db_path + suffix
        self.fd = None

    def acquire(self) -> None:
        if self.path is None:
            return
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = os.read(fd, 32).decode().strip() or "?"
            os.close(fd)
            raise AlreadyRunning(holder) from None
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self.fd = fd

    def release(self) -> None:
        if self.fd is not None:
            os.close(self.fd)                # đóng file là nhả khóa
            self.fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def beat(conn, run_id: int, at: str, interval: int) -> None:
    """Ghi dấu còn sống. started_at giữ nguyên trong cùng một lần chạy."""
    conn.execute(
        "INSERT INTO heartbeat (id, run_id, pid, started_at, beat_at, interval_s) "
        "VALUES (1, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "  started_at = CASE WHEN heartbeat.run_id = excluded.run_id "
        "                    THEN heartbeat.started_at ELSE excluded.started_at END, "
        "  run_id = excluded.run_id, pid = excluded.pid, "
        "  beat_at = excluded.beat_at, interval_s = excluded.interval_s",
        (run_id, os.getpid(), at, at, interval))
    conn.commit()


def liveness(beat_row, now: str, stopped_cleanly=None) -> str:
    """Hàm thuần: alive | stopped | dead | never.

    dead = không dừng có chủ đích mà im lặng quá ngưỡng khoảng trống (cùng ngưỡng với
    runs.gap_threshold, nên "dead" chính là lúc lần khởi động sau sẽ báo khoảng trống)."""
    if not beat_row:
        return "never"
    if stopped_cleanly:
        return "stopped"
    age = runs._seconds(beat_row["beat_at"], now)
    return "alive" if age <= runs.gap_threshold(beat_row["interval_s"]) else "dead"


def status(conn, now: str | None = None) -> dict:
    now = now or runs.now()
    row = conn.execute("SELECT run_id, pid, started_at, beat_at, interval_s "
                       "FROM heartbeat WHERE id = 1").fetchone()
    if row is None:
        return {"state": "never"}
    beat_row = dict(zip(("run_id", "pid", "started_at", "beat_at", "interval_s"), row))
    run = conn.execute("SELECT stopped_cleanly FROM monitor_runs WHERE id = ?",
                       (beat_row["run_id"],)).fetchone()
    stopped = bool(run[0]) if run and run[0] is not None else None
    beat_row["state"] = liveness(beat_row, now, stopped)
    beat_row["age_s"] = runs._seconds(beat_row["beat_at"], now)
    return beat_row


def describe(st: dict) -> str:
    if st["state"] == "never":
        return "Monitor: chưa từng chạy trên DB này (chưa có heartbeat)."
    from netauto import timefmt
    text = {"alive": "ĐANG CHẠY", "stopped": "đã dừng bình thường",
            "dead": "KHÔNG CÒN CHẠY (bị ngắt đột ngột)"}[st["state"]]
    return (f"Monitor: {text}\n"
            f"  PID {st['pid']}, chạy từ {timefmt.full(st['started_at'])}, "
            f"chu kỳ nghỉ {st['interval_s']} giây\n"
            f"  Dấu còn sống cuối: {timefmt.full(st['beat_at'])} "
            f"({runs.fmt_span(st['age_s'])} trước)")