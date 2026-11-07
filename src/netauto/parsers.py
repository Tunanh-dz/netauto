"""Biến đầu ra JSON thô của các lệnh thành dữ liệu có cấu trúc.

Đây là các "hàm thuần": chỉ nhận chuỗi và trả dữ liệu, không đụng vào
thiết bị, nên kiểm thử được bằng chuỗi mẫu mà không cần lab chạy.
"""
import json

from netauto.models import Interface, OspfNeighbor, Route, VlanEntry, VlanPort


def _loads(text: str, default):
    text = text.strip()
    if not text:
        return default
    return json.loads(text)


def parse_interfaces(text: str) -> list[Interface]:
    """Đầu vào: kết quả của `ip -j addr`."""
    result = []
    for item in _loads(text, []):
        addresses = [
            f'{a["local"]}/{a["prefixlen"]}'
            for a in item.get("addr_info", [])
            if a.get("family") == "inet"
        ]
        result.append(Interface(
            name=item.get("ifname", ""),
            state=item.get("operstate", "UNKNOWN"),
            parent=item.get("link"),
            addresses=addresses,
            mac=item.get("address"),
        ))
    return result


def parse_routes(text: str) -> list[Route]:
    """Đầu vào: kết quả của `ip -j route`."""
    return [
        Route(
            dst=item.get("dst", ""),
            gateway=item.get("gateway"),
            dev=item.get("dev"),
            protocol=item.get("protocol"),
        )
        for item in _loads(text, [])
    ]


def parse_vlans(text: str) -> list[VlanPort]:
    """Đầu vào: kết quả của `bridge -j vlan show`."""
    ports: dict[str, VlanPort] = {}
    last_port = ""
    for item in _loads(text, []):
        port = item.get("ifname") or last_port
        last_port = port
        entries = item.get("vlans")
        if entries is None:          # định dạng phẳng: mỗi dòng một VLAN
            entries = [item]
        for e in entries:
            if "vlan" not in e:
                continue
            flags = e.get("flags", [])
            ports.setdefault(port, VlanPort(port=port)).vlans.append(VlanEntry(
                vid=e["vlan"],
                pvid="PVID" in flags,
                untagged="Egress Untagged" in flags,
            ))
    return list(ports.values())


def parse_ospf_neighbors(text: str) -> list[OspfNeighbor]:
    """Đầu vào: kết quả của `vtysh -c "show ip ospf neighbor json"`."""
    data = _loads(text, {})
    neighbors = data.get("neighbors", {}) if isinstance(data, dict) else {}
    result = []
    for router_id, entries in neighbors.items():
        if isinstance(entries, dict):
            entries = [entries]
        for e in entries:
            result.append(OspfNeighbor(
                router_id=router_id,
                state=e.get("nbrState") or e.get("state") or "",
                address=e.get("ifaceAddress") or e.get("address"),
                interface=e.get("ifaceName") or e.get("interface"),
            ))
    return result



import re as _re


def _norm_area(area: str) -> str:
    return "0" if area in ("0", "0.0.0.0") else area


def parse_ospf_facts(text: str) -> list[str]:
    """Đọc `show running-config` của FRR, trả về các sự kiện cấu hình OSPF đã chuẩn hóa.

    Ví dụ: 'router-id 1.1.1.1', 'network 10.0.12.0/30 area 0', 'eth1.10: ip ospf passive'.
    """
    facts, block = [], None
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s == "!":
            block = None
            continue
        if not raw[0].isspace():                       # dòng ở cấp cao nhất: mở hoặc đóng khối
            if s == "router ospf":
                block = "router"
            elif s.startswith("interface "):
                block = ("iface", s.split()[1])
            else:
                block = None
            continue
        if block == "router":
            m = _re.match(r"ospf router-id (\S+)$", s)
            if m:
                facts.append(f"router-id {m.group(1)}")
                continue
            m = _re.match(r"network (\S+) area (\S+)$", s)
            if m:
                facts.append(f"network {m.group(1)} area {_norm_area(m.group(2))}")
        elif isinstance(block, tuple) and s.startswith("ip ospf "):
            facts.append(f"{block[1]}: {s}")
    return facts


def parse_ip_forward(text: str) -> int:
    return int(text.strip())
