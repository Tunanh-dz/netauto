"""Cửa sổ bảo trì: người vận hành báo trước "tôi sắp tác động vào mạng".

Trong cửa sổ, monitor KHÔNG mở sự cố cho các lỗi nằm trong phạm vi bảo trì (vì đó là
hệ quả của việc cố ý), nhưng vẫn đo và ghi dữ liệu như thường. Ba bảo đảm an toàn:
  1. Tự hết hạn (expires_at): quên đóng cửa sổ cũng không tắt giám sát vĩnh viễn.
  2. Ân hạn GRACE_S giây sau khi đóng, để mạng kịp hội tụ rồi mới đánh giá lại.
  3. Lỗi vẫn còn sau bảo trì được mở thành sự cố có cờ after_maintenance.
Phạm vi: lỗi tầng 1 chỉ được bỏ qua nếu nằm trên thiết bị trong cửa sổ; lỗi tầng cao
(OSPF, route, ping) được bỏ qua nếu cùng VLAN với thiết bị đó. Thiết bị khác hỏng
thật vẫn báo.
"""
from datetime import datetime, timedelta

import argparse
import getpass
import sys

from netauto import db, rootcause, timefmt
from netauto.check import _node_scope

GRACE_S = 45
ALL = "*"
DEFAULT_TTL_S = 300

SCHEMA = """
CREATE TABLE IF NOT EXISTS maintenance (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    actor        TEXT NOT NULL,
    reason       TEXT NOT NULL,
    nodes        TEXT NOT NULL,          -- '*' hoặc danh sách cách nhau bằng dấu phẩy
    opened_at    TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    closed_at    TEXT,                   -- NULL = chưa đóng
    close_reason TEXT                    -- ended | expired
);
CREATE TABLE IF NOT EXISTS suppressed (
    node       TEXT NOT NULL,
    check_code TEXT NOT NULL,
    kind       TEXT NOT NULL,
    message    TEXT NOT NULL,
    window_id  INTEGER NOT NULL,
    first_at   TEXT NOT NULL,
    PRIMARY KEY (node, check_code, kind, message)
);
"""


def _t(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def window_end(w: dict) -> str:
    """Hàm thuần. Lúc cửa sổ thực sự kết thúc (đóng tay hoặc hết hạn, cái nào tới trước)."""
    if w.get("closed_at") and _t(w["closed_at"]) < _t(w["expires_at"]):
        return w["closed_at"]
    return w["expires_at"]


def covers_time(w: dict, at: str, grace: int = GRACE_S) -> bool:
    """Hàm thuần. at nằm trong [mở, kết thúc + ân hạn]."""
    return _t(w["opened_at"]) <= _t(at) <= _t(window_end(w)) + timedelta(seconds=grace)

def window_state(w: dict, at: str) -> str:
    """Hàm thuần: open | ended | expired. Cửa sổ quá hạn mà chưa ai ghi closed_at
    vẫn tính là expired (monitor chưa kịp quét không làm đổi sự thật)."""
    if w.get("close_reason") == "expired":
        return "expired"
    if w.get("closed_at"):
        return "ended"
    return "expired" if _t(at) > _t(w["expires_at"]) else "open"

def parse_nodes(text: str):
    return ALL if text.strip() == ALL else frozenset(n for n in text.split(",") if n.strip())


def covers_finding(f, w: dict, intent: dict) -> bool:
    """Hàm thuần. Finding này có nằm trong phạm vi bảo trì của cửa sổ w không?"""
    nodes = parse_nodes(w["nodes"])
    if nodes == ALL or f.node in nodes:
        return True
    if rootcause.layer_of(f.check) > 1 and f.scope:
        vlans = set()
        for n in nodes:
            if n in intent["hosts"] or n in intent["switches"] or n in intent["routers"]:
                vlans |= set(_node_scope(n, intent))
        return bool(vlans & set(f.scope))
    return False


def split_findings(findings, windows, at: str, intent: dict):
    """Hàm thuần. Trả về (findings báo bình thường, [(finding bị giữ, id cửa sổ)])."""
    live = [w for w in windows if covers_time(w, at)]
    active, held = [], []
    for f in findings:
        w = next((w for w in live if covers_finding(f, w, intent)), None)
        if w:
            held.append((f, w["id"]))
        else:
            active.append(f)
    return active, held

def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def open_window(conn, actor: str, reason: str, nodes, ttl_s: int = DEFAULT_TTL_S,
                at: str | None = None) -> int:
    at = at or now()
    nodes = ALL if nodes == ALL or not nodes else ",".join(sorted(nodes))
    expires = (_t(at) + timedelta(seconds=ttl_s)).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO maintenance (actor, reason, nodes, opened_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)", (actor, reason, nodes, at, expires))
    conn.commit()
    return cur.lastrowid


def close_window(conn, window_id: int | None = None, at: str | None = None) -> list[int]:
    """Đóng một cửa sổ (hoặc mọi cửa sổ đang mở nếu không chỉ định). Trả về các id đã đóng."""
    at = at or now()
    sql = "SELECT id FROM maintenance WHERE closed_at IS NULL"
    args: tuple = ()
    if window_id is not None:
        sql += " AND id = ?"
        args = (window_id,)
    ids = [r[0] for r in conn.execute(sql, args)]
    for i in ids:
        conn.execute("UPDATE maintenance SET closed_at = ?, close_reason = 'ended' WHERE id = ?",
                     (at, i))
    conn.commit()
    return ids


def sweep_expired(conn, at: str) -> list[int]:
    """Đánh dấu các cửa sổ đã quá hạn mà chưa ai đóng."""
    ids = [r[0] for r in conn.execute(
        "SELECT id, expires_at FROM maintenance WHERE closed_at IS NULL") if _t(r[1]) < _t(at)]
    for i in ids:
        conn.execute("UPDATE maintenance SET closed_at = expires_at, "
                     "close_reason = 'expired' WHERE id = ?", (i,))
    conn.commit()
    return ids


COLS = ("id", "actor", "reason", "nodes", "opened_at", "expires_at", "closed_at", "close_reason")


def list_windows(conn, limit: int = 20) -> list[dict]:
    rows = conn.execute(f"SELECT {', '.join(COLS)} FROM maintenance ORDER BY id DESC LIMIT ?",
                        (limit,)).fetchall()
    return [dict(zip(COLS, r)) for r in rows]


def windows_at(conn, at: str) -> list[dict]:
    """Các cửa sổ đang hiệu lực (kể cả ân hạn) tại thời điểm at."""
    return [w for w in list_windows(conn, 50) if covers_time(w, at)]


def hold(conn, held, at: str) -> None:
    """Ghi nhớ các lỗi đang bị giữ lại, để biết lỗi nào sống sót qua bảo trì."""
    for f, wid in held:
        conn.execute("INSERT OR IGNORE INTO suppressed VALUES (?, ?, ?, ?, ?, ?)",
                     (f.node, f.check, f.kind, f.message, wid, at))
    conn.commit()


def survivors(conn, active) -> set:
    """Trong các finding báo bình thường, những cái từng bị giữ lại = lỗi còn sau bảo trì.
    Xóa chúng khỏi bảng suppressed (chỉ gắn cờ một lần)."""
    keys = {(f.node, f.check, f.kind, f.message) for f in active}
    rows = conn.execute("SELECT node, check_code, kind, message FROM suppressed").fetchall()
    hit = {tuple(r) for r in rows} & keys
    for k in hit:
        conn.execute("DELETE FROM suppressed WHERE node=? AND check_code=? AND kind=? "
                     "AND message=?", k)
    conn.commit()
    return hit


def forget_resolved(conn, current_keys: set) -> None:
    """Lỗi từng bị giữ mà nay đã hết thì xóa khỏi bảng: bảo trì đã hội tụ."""
    for r in conn.execute("SELECT node, check_code, kind, message FROM suppressed").fetchall():
        if tuple(r) not in current_keys:
            conn.execute("DELETE FROM suppressed WHERE node=? AND check_code=? AND kind=? "
                         "AND message=?", tuple(r))
    conn.commit()

def describe(w: dict, at: str | None = None) -> str:
    end = window_end(w)
    nodes = "tất cả thiết bị" if w["nodes"] == ALL else w["nodes"]
    head = f"#{w['id']} {nodes}, {w['reason']}"
    if at and _t(at) > _t(end):
        grace_end = (_t(end) + timedelta(seconds=GRACE_S)).isoformat(timespec="seconds")
        return f"{head} (đã kết thúc {timefmt.clock(end)}, ân hạn đến {timefmt.clock(grace_end)})"
    return f"{head} (đến {timefmt.clock(end)})"


def _audit(result: str, nodes, detail: dict) -> None:
    """Ghi vào nhật ký kiểm toán; không ghi được thì chỉ cảnh báo (cửa sổ bảo trì
    không thay đổi mạng, nên không cần từ chối như apply)."""
    from netauto import audit
    from netauto.intent import INTENT_PATH
    try:
        c = audit.connect()
        audit.record(c, audit.new_entry(INTENT_PATH, None if nodes == ALL else set(nodes),
                                        False, result, 0, 0.0, detail))
        c.close()
    except Exception as exc:
        print(f"CẢNH BÁO: không ghi được nhật ký kiểm toán: {exc}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(prog="netauto.maintenance")
    ap.add_argument("action", choices=["start", "end", "list"])
    ap.add_argument("id", nargs="?", type=int)
    ap.add_argument("--nodes", default=ALL, help="vd. r1,r2 (mặc định: tất cả)")
    ap.add_argument("--reason", default="bảo trì thủ công")
    ap.add_argument("--ttl", type=int, default=DEFAULT_TTL_S, help="số giây trước khi tự hết hạn")
    ap.add_argument("--print-id", action="store_true",
                    help="start: chỉ in id cửa sổ (để script lưu lại và đóng đúng cửa sổ của mình)")
    ap.add_argument("--db", default=db.DB_PATH)
    args = ap.parse_args()
    conn = db.connect(args.db)
    ensure_schema(conn)

    if args.action == "start":
        nodes = parse_nodes(args.nodes)
        wid = open_window(conn, getpass.getuser(), args.reason, nodes, args.ttl)
        print(wid if args.print_id else
              f"Đã mở cửa sổ bảo trì #{wid} ({args.ttl} giây, tự hết hạn).")
        _audit("BẢO TRÌ MỞ", nodes, {"window": wid, "reason": args.reason, "ttl": args.ttl})
    elif args.action == "end":
        ids = close_window(conn, args.id)
        print(f"Đã đóng cửa sổ: {', '.join('#%d' % i for i in ids)}" if ids
              else "Không có cửa sổ nào đang mở.")
        if ids:
            _audit("BẢO TRÌ ĐÓNG", ALL, {"windows": ids})
    else:
        rows = list_windows(conn)
        if not rows:
            print("(chưa có cửa sổ bảo trì nào)")
        for w in rows:
            state = {"open": "ĐANG MỞ", "expired": "hết hạn",
                     "ended": "đã đóng"}[window_state(w, now())]
            print(f"#{w['id']:<3}{state:<9}{timefmt.stamp(w['opened_at']):<16}"
                  f"{w['nodes']:<10}{w['actor']:<10}{w['reason']}")
    conn.close()


if __name__ == "__main__":
    main()