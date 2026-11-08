"""Thu thập trạng thái từ các thiết bị và gom thành Snapshot."""
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from netauto.intent import load_intent
from netauto.inventory import inventory_from_intent
from netauto.models import DeviceState, Snapshot
from netauto.parsers import (parse_interfaces, parse_ip_forward, parse_ospf_facts,
                             parse_ospf_neighbors, parse_routes, parse_vlans)
from netauto.transport import run


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _collect(state: DeviceState, label: str, command: list[str], parser):
    """Chạy một lệnh và phân tích kết quả; lỗi được ghi vào state.errors."""
    r = run(state.node, command)
    if not r.ok:
        state.errors.append(f"{label}: lệnh lỗi ({r.stderr.strip()[:120]})")
        return None
    try:
        return parser(r.stdout)
    except (ValueError, KeyError, TypeError) as exc:
        state.errors.append(f"{label}: không đọc được dữ liệu ({exc})")
        return None


def collect_device(node: str, role: str) -> DeviceState:
    state = DeviceState(node=node, role=role, collected_at=_now())

    # Reachable: có chạy được lệnh bên trong thiết bị không?
    if not run(node, ["true"]).ok:
        state.errors.append("không truy cập được thiết bị")
        return state
    state.reachable = True

    interfaces = _collect(state, "interfaces", ["ip", "-j", "addr"], parse_interfaces)
    if interfaces is not None:
        state.interfaces = interfaces

    routes = _collect(state, "routes", ["ip", "-j", "route"], parse_routes)
    if routes is not None:
        state.routes = routes

    if role == "switch":
        vlans = _collect(state, "vlans", ["bridge", "-j", "vlan", "show"], parse_vlans)
        if vlans is not None:
            state.vlans = vlans

    if role == "router":
        nbrs = _collect(state, "ospf", ["vtysh", "-c", "show ip ospf neighbor json"],
                        parse_ospf_neighbors)
        if nbrs is not None:
            state.ospf_neighbors = nbrs

        facts = _collect(state, "ospf-config", ["vtysh", "-c", "show running-config"],
                         parse_ospf_facts)
        if facts is not None:
            state.ospf_facts = facts

        forward = _collect(state, "ip_forward", ["sysctl", "-n", "net.ipv4.ip_forward"],
                           parse_ip_forward)
        if forward is not None:
            state.ip_forward = forward
    return state


def collect_all(inventory: dict | None = None, workers: int = 8) -> Snapshot:
    """Thu thập mọi thiết bị song song. Mặc định lấy danh sách thiết bị từ intent."""
    if inventory is None:
        inventory = inventory_from_intent(load_intent())
    snapshot = Snapshot(taken_at=_now())
    if inventory:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(inventory)))) as pool:
            snapshot.devices = list(pool.map(lambda item: collect_device(*item),
                                             inventory.items()))
    return snapshot


def save_snapshot(snapshot: Snapshot, directory: str = "data/snapshots") -> Path:
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    name = datetime.now().strftime("snapshot_%Y%m%d_%H%M%S.json")
    path = folder / name
    path.write_text(json.dumps(asdict(snapshot), indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path
