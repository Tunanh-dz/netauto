"""Giám sát định kỳ: thu thập, lưu DB, so sánh với lần trước, ghi sự kiện."""
import time
from dataclasses import asdict

from netauto import db
from netauto.collector import collect_all
from netauto.diff import diff_snapshots


def run_monitor(interval: int = 10, count: int = 0, db_path: str = db.DB_PATH) -> None:
    """Chạy `count` lần (0 = chạy mãi đến khi bấm Ctrl+C)."""
    conn = db.connect(db_path)
    prev = db.latest_snapshot(conn)
    done = 0
    try:
        while True:
            data = asdict(collect_all())
            sid = db.save_snapshot(conn, data)
            reach = sum(1 for d in data["devices"] if d["reachable"])
            errs = sum(len(d["errors"]) for d in data["devices"])
            changes = diff_snapshots(prev, data) if prev else {}
            for node, change in changes.items():
                db.save_events(conn, sid, data["taken_at"], node, change)
            n_changes = sum(len(c["mất"]) + len(c["mới"]) for c in changes.values())
            print(f"[{data['taken_at']}] snapshot #{sid}: reachable {reach}/"
                  f"{len(data['devices'])}, lỗi thu thập {errs}, thay đổi {n_changes}")
            for node, change in changes.items():
                for fact in change["mất"]:
                    print(f"    {node}  - {fact}")
                for fact in change["mới"]:
                    print(f"    {node}  + {fact}")
            prev = data
            done += 1
            if count and done >= count:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nĐã dừng giám sát.")
    finally:
        conn.close()
