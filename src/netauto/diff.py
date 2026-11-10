"""So sánh hai snapshot: liệt kê những gì mất đi và những gì mới xuất hiện."""
import json
from pathlib import Path

def device_facts(device: dict, ignore=frozenset()) -> set[str]:
    """Biến trạng thái một thiết bị thành tập hợp các 'sự kiện' dạng chữ.

    `ignore`: tên cổng bỏ qua (cổng quản lý), gồm cả route đi qua cổng đó.
    """
    facts = {f"reachable={device['reachable']}"}
    for i in device["interfaces"]:
        if i["name"] in ignore:
            continue
        ips = ",".join(i["addresses"]) or "-"
        facts.add(f"interface {i['name']} state={i['state']} ip={ips}")
    for r in device["routes"]:
        if r["dev"] in ignore:
            continue
        facts.add(f"route {r['dst']} via={r['gateway'] or '-'} "
                  f"dev={r['dev']} proto={r['protocol'] or '-'}")

    for p in device["vlans"]:
        for v in p["vlans"]:
            facts.add(f"vlan port={p['port']} vid={v['vid']} "
                      f"pvid={v['pvid']} untagged={v['untagged']}")
    for n in device["ospf_neighbors"]:
        facts.add(f"ospf neighbor {n['router_id']} state={n['state']}")
    return facts


def diff_snapshots(old: dict, new: dict, ignore_interfaces=frozenset()) -> dict:
    """Trả về {node: {"mất": [...], "mới": [...]}} cho các thiết bị có thay đổi."""
    old_devs = {d["node"]: d for d in old["devices"]}
    new_devs = {d["node"]: d for d in new["devices"]}
    result = {}
    for node in sorted(set(old_devs) | set(new_devs)):
        a = device_facts(old_devs[node], ignore_interfaces) if node in old_devs else set()
        b = device_facts(new_devs[node], ignore_interfaces) if node in new_devs else set()
        if a != b:
            result[node] = {"mất": sorted(a - b), "mới": sorted(b - a)}
    return result


def load_snapshot(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
