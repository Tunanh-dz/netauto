"""33D: xem cấu hình thiết bị, CHỈ ĐỌC.

Với mỗi thiết bị: (1) chạy một bộ lệnh đọc cố định theo loại thiết bị (router/switch/host) để lấy cấu hình đang chạy,
(2) sinh từ intent đúng các lệnh hệ thống sẽ áp dụng cho thiết bị đó. Không nhận lệnh từ người dùng: tên thiết bị phải
nằm trong intent, danh sách lệnh là hằng số trong file này. Dòng chứa mật khẩu/bí mật bị che trước khi trả ra.
"""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from netauto import render, transport

OUT_MAX = 20000
COMMANDS = {
    "router": [
        ("Giao diện và địa chỉ", ["ip", "-br", "addr"]),
        ("Bảng định tuyến", ["ip", "route"]),
        ("Cấu hình FRR đang chạy", ["vtysh", "-c", "show running-config"]),
        ("Láng giềng OSPF", ["vtysh", "-c", "show ip ospf neighbor"]),
    ],
    "switch": [
        ("Cổng và trạng thái", ["ip", "-br", "link"]),
        ("VLAN trên từng cổng", ["bridge", "vlan", "show"]),
    ],
    "host": [
        ("Giao diện và địa chỉ", ["ip", "-br", "addr"]),
        ("Bảng định tuyến", ["ip", "route"]),
    ],
}
_SECRET = re.compile(r"(?i)\b(password|secret)\b(\s+\d+)?\s+\S+")


def role_of(node: str, intent: dict):
    for key, role in (("routers", "router"), ("switches", "switch"), ("hosts", "host")):
        if node in intent.get(key, {}):
            return role
    return None


def mask(text: str) -> str:
    return "\n".join(_SECRET.sub(lambda m: f"{m.group(1)} ********", line) for line in text.split("\n"))


def _section(node: str, title: str, argv: list[str], run) -> dict:
    res = run(node, argv)
    out = res.stdout if res.ok else (res.stderr.strip() or f"lệnh lỗi (mã {res.returncode})")
    out = mask(out)
    truncated = len(out) > OUT_MAX
    if truncated:
        out = out[:OUT_MAX] + "\n… (đã cắt bớt)"
    return {"title": title, "command": " ".join(argv), "ok": bool(res.ok), "output": out, "truncated": truncated}


def build_view(node: str, intent: dict, run=transport.run) -> dict:
    role = role_of(node, intent)
    if role is None:
        raise ValueError(f"Không có thiết bị '{node}'.")
    items = COMMANDS[role]
    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        sections = list(pool.map(lambda it: _section(node, it[0], it[1], run), items))
    steps = render.render(intent)[node]
    return {"node": node, "role": role, "sections": sections,
            "intended": {"shell": render.shell_commands(steps), "vtysh": render.vtysh_text(steps)},
            "taken_at": datetime.now().astimezone().isoformat(timespec="seconds")}
