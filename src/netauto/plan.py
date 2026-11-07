"""Dry-run: so cấu hình mong muốn (render từ intent) với trạng thái thực tế, chưa thay đổi gì.

Chạy: python3 -m netauto.plan [--node r1] [--snapshot file.json] [--intent file.yml] [--verbose]
Mã thoát: 0 = không cần thay đổi, 2 = có thay đổi cần áp dụng
"""
import argparse
import ipaddress
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from netauto.collector import collect_all
from netauto.intent import INTENT_PATH, load_intent
from netauto.render import render


@dataclass(frozen=True)
class PlanItem:
    node: str
    action: str              # ok | add | remove | blocked | unknown
    kind: str
    target: str
    command: str
    channel: str = "shell"
    reason: str = ""


def _ifaces(device: dict) -> dict:
    return {i["name"]: i for i in device["interfaces"]}


def _ports(device: dict) -> dict:
    return {p["port"]: {v["vid"]: v for v in p["vlans"]} for p in device["vlans"]}


def satisfied(step, device: dict) -> Optional[bool]:
    """True = đã đúng, False = chưa đúng, None = snapshot không đủ thông tin."""
    ifaces, ports = _ifaces(device), _ports(device)
    kind, target = step.kind, step.target
    if step.channel == "vtysh" or kind == "sysctl":
        return None
    if kind == "bridge":
        return "br0" in ifaces
    if kind == "bridge-up":
        return ifaces.get("br0", {}).get("state") in ("UP", "UNKNOWN")
    if kind == "port-master":
        return target in ports
    if kind in ("port-up", "subif-up"):
        return ifaces.get(target, {}).get("state") == "UP"
    if kind == "subif":
        return target in ifaces
    if kind == "addr":
        return step.command.split()[3] in ifaces.get(target, {}).get("addresses", [])
    if kind == "vlan-del-default":
        return 1 not in ports.get(target, {})
    if kind == "vlan-access":
        port, _, vid = target.split()
        v = ports.get(port, {}).get(int(vid))
        return bool(v and v["pvid"] and v["untagged"])
    if kind == "vlan-trunk":
        port, _, vid = target.split()
        return int(vid) in ports.get(port, {})
    if kind == "default-route":
        gateway = step.command.split()[-1]
        return any(r["dst"] == "default" and r["gateway"] == gateway
                   for r in device["routes"])
    return None


def classify(step, device: dict) -> PlanItem:
    base = dict(node=step.node, kind=step.kind, target=step.target,
                command=step.command, channel=step.channel)
    if step.kind in ("port-up", "port-master") and step.target not in _ifaces(device):
        return PlanItem(action="blocked",
                        reason="cổng không tồn tại (lỗi hạ tầng, cần dựng lại lab)", **base)
    ok = satisfied(step, device)
    if ok is None:
        return PlanItem(action="unknown", reason="snapshot không chứa thông tin này", **base)
    if ok:
        return PlanItem(action="ok", **base)
    action = "remove" if step.kind == "vlan-del-default" else "add"
    return PlanItem(action=action, **base)


def _extra_addresses(node: str, ifaces: dict, want: dict) -> list[PlanItem]:
    out = []
    for name, wanted in want.items():
        for addr in ifaces.get(name, {}).get("addresses", []):
            if addr not in wanted:
                out.append(PlanItem(node, "remove", "addr-extra", f"{name} {addr}",
                                    f"ip addr del {addr} dev {name}",
                                    reason="địa chỉ không có trong intent"))
    return out


def extras(node: str, device: dict, intent: dict) -> list[PlanItem]:
    """Những thứ ĐANG CÓ trên thiết bị mà intent không muốn (cần gỡ)."""
    ifaces, ports = _ifaces(device), _ports(device)
    out = []
    if node in intent["switches"]:
        spec = intent["switches"][node]
        allowed = {spec["trunk"]["port"]: set(spec["trunk"]["vlans"])}
        for port, vid in spec["access"].items():
            allowed[port] = {vid}
        for port, want in allowed.items():
            for vid in sorted(ports.get(port, {})):
                if vid != 1 and vid not in want:
                    out.append(PlanItem(node, "remove", "vlan-extra", f"{port} vid {vid}",
                                        f"bridge vlan del dev {port} vid {vid}",
                                        reason="VLAN không có trong intent"))
    elif node in intent["routers"]:
        spec = intent["routers"][node]
        port = spec["trunk_port"]
        want = {spec["uplink"]["port"]: {spec["uplink"]["ip"]}}
        for vid, v in intent["vlans"].items():
            if v["router"] == node:
                prefix = ipaddress.ip_network(v["subnet"]).prefixlen
                want[f"{port}.{vid}"] = {f"{v['gateway']}/{prefix}"}
        for name, iface in sorted(ifaces.items()):
            if iface.get("parent") == port and name not in want:
                out.append(PlanItem(node, "remove", "subif-extra", name,
                                    f"ip link del {name}",
                                    reason="subinterface không có trong intent"))
        out += _extra_addresses(node, ifaces, want)
    elif node in intent["hosts"]:
        out += _extra_addresses(node, ifaces, {"eth1": {intent["hosts"][node]["ip"]}})
    return out


def build_plan(intent: dict, snapshot: dict, nodes: Optional[set] = None) -> list[PlanItem]:
    """Hàm thuần: chỉ đọc intent và snapshot, không chạy gì lên thiết bị."""
    devices = {d["node"]: d for d in snapshot["devices"]}
    items = []
    for node, steps in render(intent).items():
        if nodes and node not in nodes:
            continue
        device = devices.get(node)
        if device is None or not device["reachable"]:
            items.append(PlanItem(node, "blocked", "device", node, "",
                                  reason="thiết bị không truy cập được"))
            continue
        items += [classify(step, device) for step in steps]
        items += extras(node, device, intent)
    return items


def has_changes(items: list[PlanItem]) -> bool:
    return any(i.action in ("add", "remove", "blocked") for i in items)


LABEL = {"add": "THÊM", "remove": "GỠ", "blocked": "CHẶN"}


def print_plan(items: list[PlanItem], verbose: bool = False) -> None:
    by_node = {}
    for item in items:
        by_node.setdefault(item.node, []).append(item)
    for node, its in by_node.items():
        todo = [i for i in its if i.action in LABEL]
        unknown = [i for i in its if i.action == "unknown"]
        ok = [i for i in its if i.action == "ok"]
        print(f"== {node}: {len(ok)} đã đúng, {len(todo)} cần thay đổi, "
              f"{len(unknown)} chưa kiểm chứng")
        for i in todo:
            detail = i.command or i.reason
            note = f"   # {i.reason}" if i.command and i.reason else ""
            print(f"  {LABEL[i.action]:<5} {i.kind:<16} {i.target:<20} {detail}{note}")
        if unknown:
            kinds = ", ".join(sorted({i.kind for i in unknown}))
            print(f"  ?     {len(unknown)} bước ({kinds}) chưa đọc được từ snapshot, "
                  f"sẽ áp lại khi apply")
        if verbose:
            for i in ok:
                print(f"  ok    {i.kind:<16} {i.target:<20} {i.command}")

    n_add = sum(1 for i in items if i.action == "add")
    n_rm = sum(1 for i in items if i.action == "remove")
    n_block = sum(1 for i in items if i.action == "blocked")
    if not has_changes(items):
        print("\nKẾT LUẬN: không cần thay đổi (cấu hình khớp intent ở những phần đọc được).")
    else:
        print(f"\nKẾT LUẬN: cần thay đổi: {n_add} thêm, {n_rm} gỡ, {n_block} bị chặn.")
        if n_block:
            print("Có mục bị CHẶN: lỗi hạ tầng (thiếu cổng hoặc thiết bị không truy cập "
                  "được). Cần dựng lại lab (lab.sh reset) trước.")


def main():
    ap = argparse.ArgumentParser(prog="netauto.plan")
    ap.add_argument("--intent", default=INTENT_PATH)
    ap.add_argument("--snapshot", help="đọc snapshot từ file JSON thay vì thu thập trực tiếp")
    ap.add_argument("--node", help="chỉ lập kế hoạch cho một thiết bị")
    ap.add_argument("--verbose", action="store_true", help="in cả các mục đã đúng")
    args = ap.parse_args()

    intent = load_intent(args.intent)
    known = list(render(intent))
    if args.node and args.node not in known:
        sys.exit(f"Không có thiết bị '{args.node}'. Có: {', '.join(known)}")
    if args.snapshot:
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    else:
        snapshot = asdict(collect_all())

    items = build_plan(intent, snapshot, {args.node} if args.node else None)
    print_plan(items, args.verbose)
    sys.exit(2 if has_changes(items) else 0)


if __name__ == "__main__":
    main()