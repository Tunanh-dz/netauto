"""Sinh cấu hình từ intent: hàm thuần, chỉ tạo danh sách bước, chưa chạy gì.

Chạy: python3 -m netauto.render [--node r1] [--format steps|shell|vtysh] [--intent file.yml]
"""
import argparse
import ipaddress
import sys
from dataclasses import dataclass

from netauto.intent import INTENT_PATH, load_intent

OSPF_AREA = "0"


@dataclass(frozen=True)
class Step:
    node: str
    kind: str                # loại bước, dùng để so sánh với thực tế ở Ngày 16
    target: str              # đối tượng bị tác động, ví dụ eth1.10
    command: str             # lệnh shell, hoặc một dòng cấu hình vtysh
    channel: str = "shell"   # shell | vtysh


def render_switch(node: str, spec: dict) -> list[Step]:
    trunk, access = spec["trunk"], spec["access"]
    ports = sorted({trunk["port"], *access})
    steps = [
        Step(node, "bridge", "br0", "ip link add name br0 type bridge vlan_filtering 1"),
        Step(node, "bridge-up", "br0", "ip link set br0 up"),
    ]
    for p in ports:
        steps += [
            Step(node, "port-master", p, f"ip link set {p} master br0"),
            Step(node, "port-up", p, f"ip link set {p} up"),
            Step(node, "vlan-del-default", p, f"bridge vlan del dev {p} vid 1"),
        ]
    for p, vid in sorted(access.items()):
        steps.append(Step(node, "vlan-access", f"{p} vid {vid}",
                          f"bridge vlan add dev {p} vid {vid} pvid untagged"))
    for vid in sorted(trunk["vlans"]):
        steps.append(Step(node, "vlan-trunk", f"{trunk['port']} vid {vid}",
                          f"bridge vlan add dev {trunk['port']} vid {vid}"))
    return steps


def _ospf_steps(node: str, spec: dict, intent: dict, own: list[int]) -> list[Step]:
    port, up = spec["trunk_port"], spec["uplink"]
    up_net = ipaddress.ip_interface(up["ip"]).network

    def line(kind, target, text):
        return Step(node, kind, target, text, "vtysh")

    steps = [
        line("ospf", "router", "router ospf"),
        line("ospf", "router-id", f" ospf router-id {spec['ospf']['router_id']}"),
        line("ospf", f"network {up_net}", f" network {up_net} area {OSPF_AREA}"),
    ]
    for vid in own:
        subnet = intent["vlans"][vid]["subnet"]
        steps.append(line("ospf", f"network {subnet}", f" network {subnet} area {OSPF_AREA}"))
    steps.append(line("ospf", "router", "exit"))
    for vid in own:
        name = f"{port}.{vid}"
        steps += [line("ospf-iface", name, f"interface {name}"),
                  line("ospf-iface", name, " ip ospf passive"),
                  line("ospf-iface", name, "exit")]
    steps += [line("ospf-iface", up["port"], f"interface {up['port']}"),
              line("ospf-iface", up["port"], " ip ospf network point-to-point"),
              line("ospf-iface", up["port"], "exit")]
    return steps


def render_router(node: str, spec: dict, intent: dict) -> list[Step]:
    port, up = spec["trunk_port"], spec["uplink"]
    own = sorted(vid for vid, v in intent["vlans"].items() if v["router"] == node)
    steps = [Step(node, "port-up", port, f"ip link set {port} up")]
    for vid in own:
        steps.append(Step(node, "subif", f"{port}.{vid}",
                          f"ip link add link {port} name {port}.{vid} type vlan id {vid}"))
    for vid in own:
        v = intent["vlans"][vid]
        prefix = ipaddress.ip_network(v["subnet"]).prefixlen
        steps.append(Step(node, "addr", f"{port}.{vid}",
                          f"ip addr add {v['gateway']}/{prefix} dev {port}.{vid}"))
    for vid in own:
        steps.append(Step(node, "subif-up", f"{port}.{vid}", f"ip link set {port}.{vid} up"))
    steps += [
        Step(node, "addr", up["port"], f"ip addr add {up['ip']} dev {up['port']}"),
        Step(node, "port-up", up["port"], f"ip link set {up['port']} up"),
        Step(node, "sysctl", "ip_forward", "sysctl -w net.ipv4.ip_forward=1"),
    ]
    return steps + _ospf_steps(node, spec, intent, own)


def render_host(node: str, spec: dict, intent: dict) -> list[Step]:
    gateway = intent["vlans"][spec["vlan"]]["gateway"]
    return [
        Step(node, "port-up", "eth1", "ip link set eth1 up"),
        Step(node, "addr", "eth1", f"ip addr add {spec['ip']} dev eth1"),
        Step(node, "default-route", "default", f"ip route replace default via {gateway}"),
    ]


def render(intent: dict) -> dict[str, list[Step]]:
    """Hàm thuần: intent đã hợp lệ vào, danh sách bước cho từng thiết bị ra."""
    out = {}
    for node, spec in intent["switches"].items():
        out[node] = render_switch(node, spec)
    for node, spec in intent["routers"].items():
        out[node] = render_router(node, spec, intent)
    for node, spec in intent["hosts"].items():
        out[node] = render_host(node, spec, intent)
    return out


def shell_commands(steps: list[Step]) -> list[str]:
    return [s.command for s in steps if s.channel == "shell"]


def vtysh_text(steps: list[Step]) -> str:
    lines = [s.command for s in steps if s.channel == "vtysh"]
    return "\n".join(lines) + "\n" if lines else ""


def main():
    ap = argparse.ArgumentParser(prog="netauto.render")
    ap.add_argument("--intent", default=INTENT_PATH)
    ap.add_argument("--node", help="chỉ in một thiết bị")
    ap.add_argument("--format", choices=["steps", "shell", "vtysh"], default="steps")
    args = ap.parse_args()

    plan = render(load_intent(args.intent))
    if args.format != "steps" and not args.node:
        sys.exit("--format shell hoặc vtysh cần kèm --node <tên thiết bị>")
    if args.node:
        if args.node not in plan:
            sys.exit(f"Không có thiết bị '{args.node}'. Có: {', '.join(plan)}")
        plan = {args.node: plan[args.node]}

    for node, steps in plan.items():
        if args.format == "shell":
            print("\n".join(shell_commands(steps)))
        elif args.format == "vtysh":
            print(vtysh_text(steps), end="")
        else:
            print(f"== {node} ({len(steps)} bước)")
            for i, s in enumerate(steps, 1):
                print(f"{i:>3}  {s.channel:<6} {s.kind:<17} {s.command}")


if __name__ == "__main__":
    main()