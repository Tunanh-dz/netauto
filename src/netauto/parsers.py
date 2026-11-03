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
