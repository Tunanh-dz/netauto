"""Nhật ký kiểm toán: ghi lại mọi lần apply, có chuỗi băm để phát hiện sửa hoặc xóa.

Chạy: python3 -m netauto.audit [--limit N] [--id N] [--verify] [--db file]
"""
import argparse
import getpass
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = "data/netauto.db"
GENESIS = "0" * 64
FIELDS = ("at", "actor", "intent_path", "intent_sha", "nodes", "prune",
          "result", "exit_code", "duration_s", "detail")

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT    NOT NULL,
    actor       TEXT    NOT NULL,
    intent_path TEXT    NOT NULL,
    intent_sha  TEXT    NOT NULL,
    nodes       TEXT    NOT NULL,
    prune       INTEGER NOT NULL,
    result      TEXT    NOT NULL,
    exit_code   INTEGER NOT NULL,
    duration_s  REAL    NOT NULL,
    detail      TEXT    NOT NULL,
    prev_hash   TEXT    NOT NULL,
    hash        TEXT    NOT NULL
);
"""


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def compute_hash(prev_hash: str, entry: dict) -> str:
    """Băm = SHA-256(băm bản ghi trước + nội dung bản ghi này)."""
    payload = json.dumps([entry[k] for k in FIELDS], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()


def new_entry(intent_path, nodes, prune, result, exit_code, duration_s, detail: dict) -> dict:
    try:
        sha = hashlib.sha256(Path(intent_path).read_bytes()).hexdigest()[:12]
    except OSError:
        sha = "không rõ"
    return {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "actor": getpass.getuser(),
        "intent_path": str(intent_path),
        "intent_sha": sha,
        "nodes": ",".join(sorted(nodes)) if nodes else "tất cả",
        "prune": int(bool(prune)),
        "result": result,
        "exit_code": int(exit_code),
        "duration_s": round(duration_s, 2),
        "detail": json.dumps(detail, ensure_ascii=False),
    }


def record(conn: sqlite3.Connection, entry: dict) -> int:
    row = conn.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
    prev = row["hash"] if row else GENESIS
    values = dict(entry, prev_hash=prev, hash=compute_hash(prev, entry))
    cur = conn.execute(
        "INSERT INTO audit (at, actor, intent_path, intent_sha, nodes, prune, result, "
        "exit_code, duration_s, detail, prev_hash, hash) VALUES (:at, :actor, :intent_path, "
        ":intent_sha, :nodes, :prune, :result, :exit_code, :duration_s, :detail, "
        ":prev_hash, :hash)", values)
    conn.commit()
    return cur.lastrowid


def verify(conn: sqlite3.Connection):
    """Duyệt chuỗi từ đầu. Trả (hợp lệ?, id bản ghi sai, lý do)."""
    prev = GENESIS
    for row in conn.execute("SELECT * FROM audit ORDER BY id"):
        if row["prev_hash"] != prev:
            return False, row["id"], "bản ghi liền trước bị xóa hoặc chèn"
        if compute_hash(prev, {k: row[k] for k in FIELDS}) != row["hash"]:
            return False, row["id"], "nội dung bản ghi bị sửa"
        prev = row["hash"]
    return True, None, ""


def print_list(conn, limit: int) -> None:
    rows = conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    if not rows:
        print("Chưa có bản ghi kiểm toán.")
        return
    print(f"{'ID':>4}  {'THỜI ĐIỂM':<25} {'NGƯỜI':<12} {'KẾT QUẢ':<12} {'MÃ':>2}  "
          f"{'CỜ':<6} {'INTENT':<13} {'THIẾT BỊ':<9} {'GIÂY':>6}")
    for r in reversed(rows):
        print(f"{r['id']:>4}  {r['at']:<25} {r['actor']:<12} {r['result']:<12} "
              f"{r['exit_code']:>2}  {'prune' if r['prune'] else '-':<6} "
              f"{r['intent_sha']:<13} {r['nodes']:<9} {r['duration_s']:>6.1f}")


def print_detail(conn, entry_id: int) -> None:
    r = conn.execute("SELECT * FROM audit WHERE id = ?", (entry_id,)).fetchone()
    if r is None:
        sys.exit(f"Không có bản ghi #{entry_id}")
    d = json.loads(r["detail"])
    print(f"Bản ghi #{r['id']}: {r['result']} (mã {r['exit_code']}), {r['at']}, "
          f"{r['actor']}, {r['duration_s']:.1f} giây")
    print(f"Intent: {r['intent_path']} (sha {r['intent_sha']}); thiết bị: {r['nodes']}; "
          f"prune: {'có' if r['prune'] else 'không'}")
    if d.get("reason"):
        print(f"Lý do: {d['reason']}")
    print(f"Luồng thông: trước {d.get('flows_before', '?')}, sau {d.get('flows_after', '?')}"
          + (f", sau hoàn tác {d['flows_after_rollback']}" if "flows_after_rollback" in d else ""))
    if d.get("regressions"):
        print("Luồng xấu đi: " + "; ".join(d["regressions"]))
    print("Các bước:")
    for s in d.get("steps", []):
        note = f"   # {s['note']}" if s.get("note") else ""
        print(f"  {s['status']:<7} {s['node']:<8} {s['label']}{note}")
    if d.get("undo"):
        print("Hoàn tác:")
        for u in d["undo"]:
            print(f"  {'OK' if u['ok'] else 'LỖI':<7} {u['node']:<8} {u['command']}")
    print(f"Băm: {r['hash'][:16]}... (liền trước: {r['prev_hash'][:16]}...)")


def main():
    ap = argparse.ArgumentParser(prog="netauto.audit")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--id", type=int, help="xem chi tiết một bản ghi")
    ap.add_argument("--verify", action="store_true", help="kiểm tra chuỗi băm")
    args = ap.parse_args()
    conn = connect(args.db)
    if args.verify:
        ok, bad_id, reason = verify(conn)
        total = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        if ok:
            print(f"CHUỖI HỢP LỆ: {total} bản ghi, không phát hiện can thiệp.")
        else:
            print(f"PHÁT HIỆN SAI LỆCH tại bản ghi #{bad_id}: {reason}")
            sys.exit(1)
    elif args.id:
        print_detail(conn, args.id)
    else:
        print_list(conn, args.limit)


if __name__ == "__main__":
    main()