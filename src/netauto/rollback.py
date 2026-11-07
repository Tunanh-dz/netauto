"""Hoàn tác (rollback) và kiểm chứng luồng mạng sau khi áp dụng."""
import shlex
import time
from dataclasses import dataclass

from netauto import transport
from netauto.health import flow_state
from netauto.plan import _ports
from netauto.probe import run_probes


def _flags(entry: dict) -> str:
    return " ".join(f for f, on in (("pvid", entry["pvid"]), ("untagged", entry["untagged"])) if on)


def _vlan_add(port: str, vid: int, entry: dict) -> str:
    return f"bridge vlan add dev {port} vid {vid} {_flags(entry)}".strip()


def _restore_vlan(port: str, vid: int, ports: dict) -> list[str]:
    """Đưa (cổng, VLAN) về đúng như trước: xóa, rồi thêm lại nếu trước đó đã có."""
    cmds = [f"bridge vlan del dev {port} vid {vid}"]
    old = ports.get(port, {}).get(vid)
    if old:
        cmds.append(_vlan_add(port, vid, old))
    return cmds


def inverse(item, device):
    """Các lệnh đưa thiết bị về trạng thái TRƯỚC khi chạy bước này, tính từ snapshot trước.
    Trả None nếu bước đó chưa đảo ngược được (OSPF, sysctl)."""
    if item.channel != "shell":
        return None
    kind, t = item.kind, item.target
    ports = _ports(device) if device else {}
    if kind == "bridge":
        return ["ip link del br0"]
    if kind == "bridge-up":
        return ["ip link set br0 down"]
    if kind == "port-master":
        return [f"ip link set {t} nomaster"]
    if kind in ("port-up", "subif-up"):
        return [f"ip link set {t} down"]
    if kind == "subif":
        return [f"ip link del {t}"]
    if kind == "addr":
        return [f"ip addr del {item.command.split()[3]} dev {t}"]
    if kind == "default-route":
        return [f"ip route del default via {item.command.split()[-1]}"]
    if kind == "addr-extra":                       # bước gỡ (prune): đảo ngược là thêm lại
        parts = item.command.split()
        return [f"ip addr add {parts[3]} dev {parts[5]}"]
    if kind == "vlan-trunk":
        port, _, vid = t.split()
        return _restore_vlan(port, int(vid), ports)
    if kind == "vlan-access":
        port, _, vid = t.split()
        vid = int(vid)
        cmds = _restore_vlan(port, vid, ports)
        # đặt lại VLAN đang giữ PVID trước đó, vì thêm PVID mới đã lấy mất PVID cũ
        cmds += [_vlan_add(port, v, e) for v, e in sorted(ports.get(port, {}).items())
                 if e["pvid"] and v != vid]
        return cmds
    if kind == "vlan-del-default":
        old = ports.get(t, {}).get(1)
        return [_vlan_add(t, 1, old)] if old else []
    return None


@dataclass
class Undo:
    node: str
    command: str
    ok: bool
    detail: str = ""


def rollback(executed, before: dict, run=transport.run):
    """executed: các PlanItem đã chạy thành công, theo thứ tự chạy.
    before: {tên thiết bị: dict thiết bị trong snapshot trước khi áp dụng}.
    Chạy ngược, best-effort. Trả (danh sách Undo, danh sách bước không đảo ngược được)."""
    undone, cannot = [], []
    for item in reversed(executed):
        cmds = inverse(item, before.get(item.node))
        if cmds is None:
            cannot.append(item)
            continue
        for cmd in cmds:
            try:
                res = run(item.node, shlex.split(cmd), timeout=20)
                text = (res.stderr + res.stdout).strip()
                detail = "" if res.ok or not text else text.splitlines()[0][:100]
                undone.append(Undo(item.node, cmd, res.ok, detail))
            except Exception as exc:
                undone.append(Undo(item.node, cmd, False, str(exc)[:100]))
    return undone, cannot


def flows_up(results) -> set:
    """Tập (nguồn, đích) của những luồng chưa mất kết nối."""
    return {(p.src, p.dst) for p in results if flow_state(p) != "down"}


def wait_for_flows(intent, watch, timeout=30, interval=3,
                   probe=run_probes, sleep=time.sleep, clock=time.monotonic):
    """Đo lặp lại cho đến khi mọi luồng trong `watch` đều thông, hoặc hết giờ.
    Trả (kết quả đo cuối, các luồng trong watch vẫn mất kết nối)."""
    deadline = clock() + timeout
    while True:
        results = probe(intent)
        bad = [p for p in results if (p.src, p.dst) in watch and flow_state(p) == "down"]
        if not bad or clock() >= deadline:
            return results, bad
        sleep(interval)