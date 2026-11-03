"""Các mô hình dữ liệu: cách phần mềm mô tả trạng thái mạng."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Interface:
    name: str
    state: str                       # UP / DOWN / UNKNOWN
    parent: Optional[str] = None     # ví dụ eth1 là cha của eth1.10
    addresses: list[str] = field(default_factory=list)   # ["192.168.10.1/24"]
    mac: Optional[str] = None


@dataclass
class Route:
    dst: str
    gateway: Optional[str] = None
    dev: Optional[str] = None
    protocol: Optional[str] = None   # ospf, kernel, static...


@dataclass
class VlanEntry:
    vid: int
    pvid: bool = False
    untagged: bool = False


@dataclass
class VlanPort:
    port: str
    vlans: list[VlanEntry] = field(default_factory=list)


@dataclass
class OspfNeighbor:
    router_id: str
    state: str                       # ví dụ Full/-
    address: Optional[str] = None
    interface: Optional[str] = None


@dataclass
class DeviceState:
    """Trạng thái một thiết bị tại một thời điểm."""
    node: str
    role: str                        # router / switch / host
    collected_at: str
    reachable: bool = False
    interfaces: list[Interface] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    vlans: list[VlanPort] = field(default_factory=list)
    ospf_neighbors: list[OspfNeighbor] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class Snapshot:
    """Ảnh chụp trạng thái cả mạng tại một thời điểm."""
    taken_at: str
    devices: list[DeviceState] = field(default_factory=list)
