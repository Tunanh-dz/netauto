"""So sánh snapshot (thực tế) với intent (mong muốn), trả về các finding.

Chạy: python3 -m netauto.check [--snapshot file.json] [--intent file.yml]
"""
import argparse
import ipaddress
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from netauto.collector import collect_all
from netauto.intent import INTENT_PATH, load_intent


@dataclass
class Finding:
    node: str
    check: str      # mã kiểm tra, ví dụ router.subinterface
    severity: str   # critical | warning
    kind: str       # missing | wrong | extra | down
    message: str


def _want_ip(vlan: dict) -> str:
    return f'{vlan["gateway"]}/{ipaddress.ip_network(vlan["subnet"]).prefixlen}'


def check_router(node, device, intent):
    spec = intent["routers"][node]
    ifaces = {i["name"]: i for i in device["interfaces"]}
    out = []

    for vid, vlan in intent["vlans"].items():
        if vlan["router"] == node:
            name = f'{spec["trunk_port"]}.{vid}'
            iface = ifaces.get(name)
            if iface is None:
                out.append(Finding(node, "router.subinterface", "critical", "missing",
                                   f"thiếu {name} (gateway của VLAN {vid})"))
                continue
            if iface["state"] != "UP":
                out.append(Finding(node, "router.subinterface", "critical", "down",
                                   f"{name} không UP (trạng thái {iface['state']})"))
            if _want_ip(vlan) not in iface["addresses"]:
                out.append(Finding(node, "router.subinterface", "critical", "wrong",
                                   f"{name} thiếu IP {_want_ip(vlan)}"))
        else:
            learned = any(r["dst"] == vlan["subnet"] and r["protocol"] == "ospf"
                          for r in device["routes"])
            if not learned:
                out.append(Finding(node, "router.route", "critical", "missing",
                                   f"thiếu route OSPF tới {vlan['subnet']} (VLAN {vid})"))

    up = spec["uplink"]
    iface = ifaces.get(up["port"])
    if iface is None:
        out.append(Finding(node, "router.uplink", "critical", "missing", f"thiếu {up['port']}"))
    else:
        if iface["state"] != "UP":
            out.append(Finding(node, "router.uplink", "critical", "down", f"{up['port']} không UP"))
        if up["ip"] not in iface["addresses"]:
            out.append(Finding(node, "router.uplink", "critical", "wrong",
                               f"{up['port']} thiếu IP {up['ip']}"))

    for rid in spec["ospf"]["neighbors"]:
        full = any(n["router_id"] == rid and n["state"].startswith("Full")
                   for n in device["ospf_neighbors"])
        if not full:
            out.append(Finding(node, "router.ospf", "critical", "missing",
                               f"hàng xóm OSPF {rid} không ở trạng thái Full"))
    return out


def check_switch(node, device, intent):
    spec = intent["switches"][node]
    ports = {p["port"]: {v["vid"]: v for v in p["vlans"]} for p in device["vlans"]}
    out = []

    trunk = spec["trunk"]
    have = ports.get(trunk["port"], {})
    for vid in trunk["vlans"]:
        if vid not in have:
            out.append(Finding(node, "switch.trunk", "critical", "missing",
                               f"trunk {trunk['port']} thiếu VLAN {vid}"))
    for vid, v in have.items():
        if vid not in trunk["vlans"]:
            out.append(Finding(node, "switch.trunk", "warning", "extra",
                               f"trunk {trunk['port']} có VLAN {vid} thừa"))
        elif v["pvid"] or v["untagged"]:
            out.append(Finding(node, "switch.trunk", "warning", "wrong",
                               f"trunk {trunk['port']} VLAN {vid} bị đặt PVID/untagged"))

    for port, want in spec["access"].items():
        have = ports.get(port, {})
        v = have.get(want)
        if v is None:
            out.append(Finding(node, "switch.access", "critical", "missing",
                               f"access {port} thiếu VLAN {want}"))
        elif not (v["pvid"] and v["untagged"]):
            out.append(Finding(node, "switch.access", "critical", "wrong",
                               f"access {port} VLAN {want} thiếu PVID/untagged"))
        for vid in have:
            if vid != want:
                out.append(Finding(node, "switch.access", "warning", "extra",
                                   f"access {port} có VLAN {vid} thừa"))
    return out


def check_host(node, device, intent):
    spec = intent["hosts"][node]
    vlan = intent["vlans"][spec["vlan"]]
    ifaces = {i["name"]: i for i in device["interfaces"]}
    out = []

    iface = ifaces.get("eth1")
    if iface is None:
        out.append(Finding(node, "host.interface", "critical", "missing", "thiếu eth1"))
    else:
        if iface["state"] != "UP":
            out.append(Finding(node, "host.interface", "critical", "down", "eth1 không UP"))
        if spec["ip"] not in iface["addresses"]:
            out.append(Finding(node, "host.interface", "critical", "wrong",
                               f"eth1 thiếu IP {spec['ip']}"))

    gw_ok = any(r["dst"] == "default" and r["gateway"] == vlan["gateway"]
                for r in device["routes"])
    if not gw_ok:
        out.append(Finding(node, "host.gateway", "critical", "wrong",
                           f"default route không đi qua gateway {vlan['gateway']}"))
    return out


ROLES = [("routers", check_router), ("switches", check_switch), ("hosts", check_host)]


def check_snapshot(snapshot: dict, intent: dict) -> list[Finding]:
    devices = {d["node"]: d for d in snapshot["devices"]}
    findings, known = [], set()
    for section, fn in ROLES:
        for node in intent[section]:
            known.add(node)
            device = devices.get(node)
            if device is None:
                findings.append(Finding(node, "device.reachable", "critical", "missing",
                                        "thiết bị có trong intent nhưng không có trong snapshot"))
            elif not device["reachable"]:
                findings.append(Finding(node, "device.reachable", "critical", "down",
                                        "không truy cập được thiết bị"))
            else:
                findings += fn(node, device, intent)
    for node in devices:
        if node not in known:
            findings.append(Finding(node, "device.unknown", "warning", "extra",
                                    "thiết bị không có trong intent"))
    return findings


def print_report(findings: list[Finding]) -> None:
    if not findings:
        print("ĐẠT: trạng thái thực tế khớp intent.")
        return
    order = {"critical": 0, "warning": 1}
    findings = sorted(findings, key=lambda f: (order[f.severity], f.node, f.check))
    print(f"{'MỨC':<10}{'NODE':<9}{'KIỂM TRA':<21}{'LOẠI':<9}MÔ TẢ")
    for f in findings:
        print(f"{f.severity.upper():<10}{f.node:<9}{f.check:<21}{f.kind:<9}{f.message}")
    crit = sum(1 for f in findings if f.severity == "critical")
    print(f"\nTổng: {crit} nghiêm trọng, {len(findings) - crit} cảnh báo")


def main():
    ap = argparse.ArgumentParser(prog="netauto.check")
    ap.add_argument("--intent", default=INTENT_PATH)
    ap.add_argument("--snapshot", help="đọc snapshot từ file JSON thay vì thu thập trực tiếp")
    args = ap.parse_args()

    intent = load_intent(args.intent)
    if args.snapshot:
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    else:
        snapshot = asdict(collect_all())

    findings = check_snapshot(snapshot, intent)
    print_report(findings)
    sys.exit(1 if any(f.severity == "critical" for f in findings) else 0)


if __name__ == "__main__":
    main()
