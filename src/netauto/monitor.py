"""Giám sát định kỳ: thu thập, lưu DB, so sánh, kiểm tra intent, quản lý sự cố."""
import time
from dataclasses import asdict

from netauto import db, incidents
from netauto.check import check_snapshot
from netauto.collector import collect_all
from netauto.diff import diff_snapshots
from netauto.intent import INTENT_PATH, load_intent


def run_monitor(interval: int = 10, count: int = 0, db_path: str = db.DB_PATH,
                intent_path: str = INTENT_PATH) -> None:
    """Chạy `count` lần (0 = chạy mãi đến khi bấm Ctrl+C)."""
    intent = load_intent(intent_path)
    conn = incidents.connect(db_path)
    prev = db.latest_snapshot(conn)
    if prev:
        print(f"(so sánh với snapshot lưu trước đó, lúc {prev['taken_at']})")
    carried = incidents.open_incidents(conn)
    if carried:
        print(f"(còn {len(carried)} sự cố mở từ lần chạy trước)")
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

            findings = check_snapshot(data, intent)
            opened, closed = incidents.record(conn, findings, data["taken_at"], sid)
            n_open = len(incidents.open_incidents(conn))

            print(f"[{data['taken_at']}] snapshot #{sid}: reachable {reach}/"
                  f"{len(data['devices'])}, lỗi thu thập {errs}, "
                  f"thay đổi {n_changes}, sự cố đang mở {n_open}")
            for node, change in changes.items():
                for fact in change["mất"]:
                    print(f"    {node}  - {fact}")
                for fact in change["mới"]:
                    print(f"    {node}  + {fact}")
            for f in opened:
                print(f"    MỞ   [{f.severity}] {f.node}: {f.message}")
            for r in closed:
                print(f"    ĐÓNG {r['node']}: {r['message']} "
                      f"(kéo dài {r['duration']:.0f} giây)")

            prev = data
            done += 1
            if count and done >= count:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nĐã dừng giám sát.")
    finally:
        conn.close()