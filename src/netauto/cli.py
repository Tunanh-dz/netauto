"""Giao diện dòng lệnh: python3 -m netauto.cli collect"""
import argparse
import json
from dataclasses import asdict

from netauto.collector import collect_all, save_snapshot


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


def main():
    parser = argparse.ArgumentParser(prog="netauto")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="thu thập trạng thái cả mạng")
    c.add_argument("--save", action="store_true", help="lưu snapshot ra file JSON")
    c.add_argument("--json", action="store_true", help="in toàn bộ dữ liệu JSON")
    args = parser.parse_args()

    if args.cmd == "collect":
        snapshot = collect_all()
        if args.json:
            print(json.dumps(asdict(snapshot), indent=2, ensure_ascii=False))
        else:
            print_summary(snapshot)
        if args.save:
            print("\nĐã lưu:", save_snapshot(snapshot))


if __name__ == "__main__":
    main()
