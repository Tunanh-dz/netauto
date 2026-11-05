"""Quản lý sự cố: mở khi finding xuất hiện, đóng khi finding biến mất.

Xem danh sách:  python3 -m netauto.incidents [--open] [--limit N]
"""
import argparse
from datetime import datetime

from netauto import db
from netauto.check import Finding

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node            TEXT    NOT NULL,
    check_code      TEXT    NOT NULL,
    severity        TEXT    NOT NULL,
    kind            TEXT    NOT NULL,
    message         TEXT    NOT NULL,
    opened_at       TEXT    NOT NULL,
    opened_snapshot INTEGER NOT NULL,
    closed_at       TEXT,               -- NULL = sự cố còn mở
    closed_snapshot INTEGER
);
"""


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def connect(path: str = db.DB_PATH):
    conn = db.connect(path)
    ensure_schema(conn)
    return conn


def duration_s(start: str, end: str) -> float:
    a = datetime.fromisoformat(start.replace("Z", "+00:00"))
    b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    return (b - a).total_seconds()


def open_incidents(conn) -> list[dict]:
    cols = ("id", "node", "check_code", "severity", "kind", "message", "opened_at")
    rows = conn.execute(
        "SELECT id, node, check_code, severity, kind, message, opened_at "
        "FROM incidents WHERE closed_at IS NULL ORDER BY id").fetchall()
    return [dict(zip(cols, r)) for r in rows]


def plan(open_rows: list[dict], findings: list[Finding]):
    """Hàm thuần. Trả về (finding cần mở, id sự cố cần đóng)."""
    current = {(f.node, f.check, f.kind, f.message): f for f in findings}
    # thiết bị không truy cập được = điểm mù: không đóng sự cố khác của nó
    blind = {f.node for f in findings if f.check == "device.reachable"}

    existing = {(r["node"], r["check_code"], r["kind"], r["message"]): r
                for r in open_rows}
    to_open = [f for key, f in current.items() if key not in existing]
    to_close = [r["id"] for key, r in existing.items()
                if key not in current
                and not (r["node"] in blind and r["check_code"] != "device.reachable")]
    return to_open, to_close


def record(conn, findings: list[Finding], at: str, snapshot_id: int):
    """Áp dụng kế hoạch vào DB. Trả về (finding đã mở, sự cố đã đóng)."""
    current_open = open_incidents(conn)
    to_open, to_close = plan(current_open, findings)

    for f in to_open:
        conn.execute(
            "INSERT INTO incidents (node, check_code, severity, kind, message, "
            "opened_at, opened_snapshot) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f.node, f.check, f.severity, f.kind, f.message, at, snapshot_id))

    by_id = {r["id"]: r for r in current_open}
    closed = []
    for incident_id in to_close:
        conn.execute(
            "UPDATE incidents SET closed_at = ?, closed_snapshot = ? WHERE id = ?",
            (at, snapshot_id, incident_id))
        row = by_id[incident_id]
        closed.append({**row, "closed_at": at,
                       "duration": duration_s(row["opened_at"], at)})
    conn.commit()
    return to_open, closed


def list_incidents(conn, limit: int = 20, only_open: bool = False):
    sql = ("SELECT id, node, severity, kind, message, opened_at, closed_at "
           "FROM incidents")
    if only_open:
        sql += " WHERE closed_at IS NULL"
    sql += " ORDER BY id DESC LIMIT ?"
    return conn.execute(sql, (limit,)).fetchall()


def main():
    ap = argparse.ArgumentParser(prog="netauto.incidents")
    ap.add_argument("--open", action="store_true", help="chỉ hiện sự cố đang mở")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--db", default=db.DB_PATH)
    args = ap.parse_args()

    conn = connect(args.db)
    rows = list_incidents(conn, args.limit, args.open)
    if not rows:
        print("(không có sự cố nào)")
    else:
        print(f"{'ID':<4}{'MỨC':<10}{'NODE':<8}{'MỞ LÚC':<27}{'KÉO DÀI':<12}MÔ TẢ")
        for iid, node, sev, kind, msg, opened, closed in rows:
            last = f"{duration_s(opened, closed):.0f} giây" if closed else "ĐANG MỞ"
            print(f"{iid:<4}{sev.upper():<10}{node:<8}{opened:<27}{last:<12}{msg}")
    conn.close()


if __name__ == "__main__":
    main()