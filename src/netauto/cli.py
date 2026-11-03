"""Giao diện dòng lệnh: python3 -m netauto.cli {collect|diff|monitor|history}"""
import argparse
import json
from dataclasses import asdict

from netauto import db
from netauto.collector import collect_all, save_snapshot
from netauto.diff import diff_snapshots, load_snapshot
from netauto.monitor import run_monitor


def print_summary(snapshot):
    print(f"Snapshot lúc {snapshot.taken_at}\n")
    print(f"{'NODE':<9}{'VAI TRÒ':<9}{'REACH':<7}{'IF':<4}{'ROUTE':<7}"
          f"{'VLAN-PORT':<11}{'OSPF':<10}LỖI")
    for d in snapshot.devices:
        ospf = ",".join(n.state for n in d.ospf_neighbors) or "-"
        print(f"{d.node:<9}{d.role:<9}{str(d.reachable):<7}{len(d.interfaces):<4}"
              f"{len(d.routes):<7}{len(d.vlans):<11}{ospf:<10}{len(d.errors)}")
        for err in d.errors:
            print(f"    ! {err}")


def print_diff(diff):
    if not diff:
        print("Không có thay đổi giữa hai snapshot.")
        return
    for node, change in diff.items():
        print(f"[{node}]")
        for fact in change["mất"]:
            print(f"  - {fact}")
        for fact in change["mới"]:
            print(f"  + {fact}")


def print_history(path, limit):
    conn = db.connect(path)
    print(f"== {limit} snapshot gần nhất ==")
    print(f"{'ID':<5}{'THỜI ĐIỂM':<28}{'THIẾT BỊ':<10}{'REACH':<7}LỖI")
    for sid, at, dev, reach, errs in db.list_snapshots(conn, limit):
        print(f"{sid:<5}{at:<28}{dev:<10}{reach:<7}{errs}")
    print("\n== Sự kiện gần nhất (mới nhất ở trên) ==")
    events = db.list_events(conn, 30)
    if not events:
        print("(chưa có sự kiện nào)")
    for at, node, kind, fact in events:
        sign = "-" if kind == "mất" else "+"
        print(f"{at}  {node:<8}{sign} {fact}")
    conn.close()


def main():
    parser = argparse.ArgumentParser(prog="netauto")
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="thu thập trạng thái cả mạng")
    c.add_argument("--save", action="store_true", help="lưu snapshot ra file JSON")
    c.add_argument("--json", action="store_true", help="in toàn bộ dữ liệu JSON")

    d = sub.add_parser("diff", help="so sánh hai file snapshot")
    d.add_argument("old")
    d.add_argument("new")

    m = sub.add_parser("monitor", help="thu thập định kỳ, lưu vào SQLite")
    m.add_argument("--interval", type=int, default=10, help="giây nghỉ giữa hai lần")
    m.add_argument("--count", type=int, default=0, help="số lần chạy (0 = mãi mãi)")
    m.add_argument("--db", default=db.DB_PATH)

    h = sub.add_parser("history", help="xem lịch sử snapshot và sự kiện")
    h.add_argument("--limit", type=int, default=10)
    h.add_argument("--db", default=db.DB_PATH)

    args = parser.parse_args()
    if args.cmd == "collect":
        snapshot = collect_all()
        if args.json:
            print(json.dumps(asdict(snapshot), indent=2, ensure_ascii=False))
        else:
            print_summary(snapshot)
        if args.save:
            print("\nĐã lưu:", save_snapshot(snapshot))
    elif args.cmd == "diff":
        print_diff(diff_snapshots(load_snapshot(args.old), load_snapshot(args.new)))
    elif args.cmd == "monitor":
        run_monitor(args.interval, args.count, args.db)
    elif args.cmd == "history":
        print_history(args.db, args.limit)


if __name__ == "__main__":
    main()
