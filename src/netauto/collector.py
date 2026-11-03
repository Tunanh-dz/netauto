"""Thu thập trạng thái từ các thiết bị và gom thành Snapshot."""
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from netauto.inventory import INVENTORY
from netauto.models import DeviceState, Snapshot
from netauto.parsers import (parse_interfaces, parse_ospf_neighbors,
                             parse_routes, parse_vlans)
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

    return state


def collect_all(inventory: dict = INVENTORY) -> Snapshot:
    snapshot = Snapshot(taken_at=_now())
    for node, role in inventory.items():
        snapshot.devices.append(collect_device(node, role))
    return snapshot


def save_snapshot(snapshot: Snapshot, directory: str = "data/snapshots") -> Path:
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    name = datetime.now().strftime("snapshot_%Y%m%d_%H%M%S.json")
    path = folder / name
    path.write_text(json.dumps(asdict(snapshot), indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path
