"""Quản lý sự cố: mở khi finding xuất hiện, đóng khi finding biến mất.

Xem danh sách:  python3 -m netauto.incidents [--open] [--limit N] [--groups]

Mỗi sự cố đóng đều có lý do (close_reason):
  resolved            điều kiện đã hết
  escalated           thay bằng sự cố nặng hơn cho cùng đối tượng (successor_id)
  deescalated         thay bằng sự cố nhẹ hơn cho cùng đối tượng (successor_id)
  replaced            thay bằng sự cố khác cùng mức cho cùng đối tượng
  cleared_during_gap  hết trong lúc monitor không chạy: KHÔNG biết thời lượng thật
Sự cố triệu chứng trỏ về sự cố gốc qua root_id (NULL = chính nó là gốc).
"""
import argparse
from datetime import datetime

from netauto import db, rootcause, timefmt
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
    closed_snapshot INTEGER,
    subject         TEXT    NOT NULL DEFAULT '',   -- đối tượng, ví dụ h-a→h-b
    scope           TEXT    NOT NULL DEFAULT '',   -- các VLAN bị ảnh hưởng, ví dụ 10,20
    close_reason    TEXT,
    successor_id    INTEGER,
    root_id         INTEGER,                       -- NULL = sự cố gốc
    spans_gap       INTEGER NOT NULL DEFAULT 0     -- 1 = tồn tại qua khoảng trống giám sát
);
"""

# Cột thêm sau này: CSDL cũ được nâng cấp tại chỗ, không mất dữ liệu.
NEW_COLUMNS = {
    "subject": "TEXT NOT NULL DEFAULT ''",
    "scope": "TEXT NOT NULL DEFAULT ''",
    "close_reason": "TEXT",
    "successor_id": "INTEGER",
    "root_id": "INTEGER",
    "spans_gap": "INTEGER NOT NULL DEFAULT 0",
}

REASON_TEXT = {
    "resolved": "đã hết",
    "escalated": "nâng mức",
    "deescalated": "hạ mức",
    "replaced": "thay thế",
    "cleared_during_gap": "hết trong khoảng trống",
    None: "(không ghi)",
}
SEVERITY_RANK = {"warning": 1, "critical": 2}


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(incidents)")}
    for col, decl in NEW_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE incidents ADD COLUMN {col} {decl}")
    conn.commit()


def connect(path: str = db.DB_PATH):
    conn = db.connect(path)
    ensure_schema(conn)
    return conn


def duration_s(start: str, end: str) -> float:
    a = datetime.fromisoformat(start.replace("Z", "+00:00"))
    b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    return (b - a).total_seconds()


def open_incidents(conn) -> list[dict]:
    cols = ("id", "node", "check_code", "severity", "kind", "message", "opened_at",
            "opened_snapshot", "subject", "scope", "root_id", "spans_gap")
    rows = conn.execute(
        "SELECT id, node, check_code, severity, kind, message, opened_at, "
        "opened_snapshot, subject, scope, root_id, spans_gap "
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


def close_reason(row: dict, to_open: list, new_ids: dict):
    """Hàm thuần. Trả về (lý do đóng, id sự cố kế nhiệm hoặc None)."""
    succ = next((f for f in to_open if row["subject"] and f.node == row["node"]
                 and f.check == row["check_code"] and f.subject == row["subject"]), None)
    if succ is None:
        return "resolved", None
    old = SEVERITY_RANK.get(row["severity"], 0)
    new = SEVERITY_RANK.get(succ.severity, 0)
    reason = "escalated" if new > old else "deescalated" if new < old else "replaced"
    return reason, new_ids.get((succ.node, succ.check, succ.subject))


def refresh_roots(conn, snapshot_id: int) -> None:
    """Tính lại nhóm nguyên nhân gốc cho mọi sự cố đang mở; sự cố vừa đóng
    vì bị thay thế thì theo nhóm của sự cố kế nhiệm."""
    rows = open_incidents(conn)
    roots = rootcause.assign_roots(rows)
    for r in rows:
        if roots[r["id"]] != r["root_id"]:
            conn.execute("UPDATE incidents SET root_id = ? WHERE id = ?",
                         (roots[r["id"]], r["id"]))
    conn.execute(
        "UPDATE incidents SET root_id = (SELECT COALESCE(s.root_id, s.id) FROM incidents s "
        "WHERE s.id = incidents.successor_id) "
        "WHERE successor_id IS NOT NULL AND closed_snapshot = ?", (snapshot_id,))


def record(conn, findings: list[Finding], at: str, snapshot_id: int,
           after_gap: bool = False):
    """Áp dụng kế hoạch vào DB. Trả về (finding đã mở, sự cố đã đóng).

    after_gap=True: đây là vòng đầu sau khoảng trống giám sát. Sự cố đang mở được đánh dấu
    đã tồn tại qua khoảng trống, và sự cố đóng ở vòng này không có thời lượng đáng tin
    (duration = None) vì ta không biết nó hết lúc nào.
    """
    current_open = open_incidents(conn)
    if after_gap and current_open:
        conn.execute("UPDATE incidents SET spans_gap = 1 WHERE closed_at IS NULL")
    to_open, to_close = plan(current_open, findings)

    new_ids = {}
    for f in to_open:
        cur = conn.execute(
            "INSERT INTO incidents (node, check_code, severity, kind, message, "
            "opened_at, opened_snapshot, subject, scope) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f.node, f.check, f.severity, f.kind, f.message, at, snapshot_id,
             f.subject, rootcause.format_scope(f.scope)))
        new_ids[(f.node, f.check, f.subject)] = cur.lastrowid

    by_id = {r["id"]: r for r in current_open}
    closed = []
    for incident_id in to_close:
        row = by_id[incident_id]
        reason, successor = close_reason(row, to_open, new_ids)
        if after_gap and successor is None:
            reason = "cleared_during_gap"
        conn.execute(
            "UPDATE incidents SET closed_at = ?, closed_snapshot = ?, "
            "close_reason = ?, successor_id = ? WHERE id = ?",
            (at, snapshot_id, reason, successor, incident_id))
        closed.append({**row, "closed_at": at, "close_reason": reason,
                       "successor_id": successor,
                       "duration": None if after_gap else duration_s(row["opened_at"], at)})
    refresh_roots(conn, snapshot_id)
    conn.commit()
    return to_open, closed


def opened_at_snapshot(conn, snapshot_id: int) -> list[dict]:
    """Sự cố mở ở snapshot này, kèm gốc của chúng (để in ra màn hình)."""
    cols = ("id", "node", "severity", "message", "root_id")
    rows = conn.execute(
        "SELECT id, node, severity, message, root_id FROM incidents "
        "WHERE opened_snapshot = ? ORDER BY id", (snapshot_id,)).fetchall()
    return [dict(zip(cols, r)) for r in rows]


LIST_COLS = ("id", "node", "severity", "kind", "message", "opened_at", "closed_at",
             "close_reason", "successor_id", "root_id", "spans_gap")


def list_incidents(conn, limit: int = 20, only_open: bool = False) -> list[dict]:
    sql = f"SELECT {', '.join(LIST_COLS)} FROM incidents"
    if only_open:
        sql += " WHERE closed_at IS NULL"
    sql += " ORDER BY id DESC LIMIT ?"
    return [dict(zip(LIST_COLS, r)) for r in conn.execute(sql, (limit,)).fetchall()]


def _span(r: dict) -> str:
    if not r["closed_at"]:
        return "ĐANG MỞ"
    if r["close_reason"] == "cleared_during_gap":
        return "không rõ"
    mark = "*" if r["spans_gap"] else ""
    return f"{duration_s(r['opened_at'], r['closed_at']):.0f} giây{mark}"


def _reason(r: dict) -> str:
    if not r["closed_at"]:
        return ""
    text = REASON_TEXT.get(r["close_reason"], r["close_reason"])
    return f"{text} → #{r['successor_id']}" if r["successor_id"] else text


def print_table(rows: list[dict]) -> None:
    print(f"{'ID':<4}{'MỨC':<10}{'NODE':<8}{'MỞ LÚC':<16}{'KÉO DÀI':<12}"
          f"{'GỐC':<6}{'LÝ DO ĐÓNG':<24}MÔ TẢ")
    for r in rows:
        root = f"#{r['root_id']}" if r["root_id"] else "-"
        print(f"{r['id']:<4}{r['severity'].upper():<10}{r['node']:<8}{timefmt.stamp(r['opened_at']):<16}"
              f"{_span(r):<12}{root:<6}{_reason(r):<24}{r['message']}")
    if any(r["spans_gap"] and r["closed_at"] and r["close_reason"] != "cleared_during_gap"
           for r in rows):
        print("(* thời lượng gồm cả khoảng trống giám sát, nên chỉ là cận trên)")


def print_groups(conn, rows: list[dict]) -> None:
    """In theo nhóm: mỗi nguyên nhân gốc kèm các triệu chứng của nó."""
    groups: dict[int, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["root_id"] or r["id"], []).append(r)
    for key in sorted(groups, reverse=True):
        members = sorted(groups[key], key=lambda r: r["id"])
        head = next((r for r in members if r["id"] == key), None)
        if head is None:                     # gốc nằm ngoài --limit
            fetched = conn.execute(
                f"SELECT {', '.join(LIST_COLS)} FROM incidents WHERE id = ?", (key,)).fetchone()
            head = dict(zip(LIST_COLS, fetched)) if fetched else members[0]
        others = [r for r in members if r["id"] != head["id"]]
        print(f"#{head['id']} {head['severity'].upper()} {head['node']}: {head['message']}"
              f"  [{_span(head)}, {len(others)} sự cố liên quan]")
        for r in others:
            print(f"    └ #{r['id']} {r['severity']} {r['node']}: {r['message']}"
                  f"  [{_span(r)}; {_reason(r) or 'đang mở'}]")


def main():
    ap = argparse.ArgumentParser(prog="netauto.incidents")
    ap.add_argument("--open", action="store_true", help="chỉ hiện sự cố đang mở")
    ap.add_argument("--groups", action="store_true", help="nhóm theo nguyên nhân gốc")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--db", default=db.DB_PATH)
    args = ap.parse_args()

    conn = connect(args.db)
    rows = list_incidents(conn, args.limit, args.open)
    if not rows:
        print("(không có sự cố nào)")
    elif args.groups:
        print_groups(conn, rows)
    else:
        print_table(rows)
    conn.close()


if __name__ == "__main__":
    main()