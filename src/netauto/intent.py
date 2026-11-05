"""Đọc file intent (trạng thái mong muốn) và kiểm tra tính hợp lệ."""
import ipaddress
from pathlib import Path

import yaml

INTENT_PATH = "intent/network.yml"


class IntentError(Exception):
    pass


def load_intent(path: str = INTENT_PATH) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    errors = validate_intent(data)
    if errors:
        raise IntentError("Intent không hợp lệ:\n  - " + "\n  - ".join(errors))
    return data


def validate_intent(data) -> list[str]:
    """Trả về danh sách lỗi (rỗng nếu intent hợp lệ)."""
    if not isinstance(data, dict):
        return ["file intent phải là một mapping (khóa: giá trị)"]
    errors = []
    for key in ("vlans", "routers", "switches", "hosts"):
        if not isinstance(data.get(key), dict) or not data[key]:
            errors.append(f"thiếu hoặc rỗng mục '{key}'")
    if errors:
        return errors

    nets = {}
    for vid, v in data["vlans"].items():
        if not isinstance(vid, int) or not 1 <= vid <= 4094:
            errors.append(f"VLAN id không hợp lệ: {vid!r}")
            continue
        if not isinstance(v, dict):
            errors.append(f"VLAN {vid}: nội dung phải là mapping")
            continue
        for field in ("name", "subnet", "gateway", "router"):
            if field not in v:
                errors.append(f"VLAN {vid} thiếu '{field}'")
        if "router" in v and v["router"] not in data["routers"]:
            errors.append(f"VLAN {vid}: router '{v['router']}' không có trong mục routers")
        if "subnet" in v and "gateway" in v:
            try:
                net = ipaddress.ip_network(str(v["subnet"]))
                nets[vid] = net
                if ipaddress.ip_address(str(v["gateway"])) not in net:
                    errors.append(f"VLAN {vid}: gateway {v['gateway']} không nằm trong {net}")
            except ValueError as exc:
                errors.append(f"VLAN {vid}: subnet hoặc gateway không hợp lệ ({exc})")

    for name, spec in data["routers"].items():
        for field in ("trunk_port", "uplink", "ospf"):
            if not isinstance(spec, dict) or field not in spec:
                errors.append(f"router {name} thiếu '{field}'")

    for name, spec in data["switches"].items():
        try:
            used = list(spec["trunk"]["vlans"]) + list(spec["access"].values())
        except (KeyError, TypeError, AttributeError):
            errors.append(f"switch {name}: cần có trunk.vlans và access")
            continue
        for vid in used:
            if vid not in data["vlans"]:
                errors.append(f"switch {name}: VLAN {vid} chưa được khai báo trong vlans")

    for name, spec in data["hosts"].items():
        vid = spec.get("vlan") if isinstance(spec, dict) else None
        if vid not in data["vlans"]:
            errors.append(f"host {name}: VLAN {vid!r} chưa được khai báo trong vlans")
            continue
        try:
            iface = ipaddress.ip_interface(str(spec.get("ip")))
        except ValueError:
            errors.append(f"host {name}: ip không hợp lệ ({spec.get('ip')!r})")
            continue
        if vid in nets and iface.ip not in nets[vid]:
            errors.append(f"host {name}: ip {iface.ip} không nằm trong subnet {nets[vid]} của VLAN {vid}")
    return errors
