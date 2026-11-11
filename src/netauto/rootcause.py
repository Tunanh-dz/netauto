"""Nhóm sự cố theo nguyên nhân gốc (hàm thuần, không đụng DB).

Một lỗi ở tầng thấp kéo theo lỗi ở tầng cao. Ví dụ r1 mất eth1.10 (tầng 1) làm r2 mất
route OSPF (tầng 3) và các luồng ping đứt (tầng 4): 4 sự cố nhưng chỉ 1 nguyên nhân gốc.

Quy tắc: sự cố A là TRIỆU CHỨNG của sự cố B khi
  1. tầng của B thấp hơn tầng của A,
  2. hai bên có chung ít nhất một VLAN bị ảnh hưởng (scope),
  3. B không mở muộn hơn A quá `skew` snapshot (probe ping có thể thấy lỗi trước
     một vòng vì thu thập chạy trước, ping chạy sau; B mở sớm hơn A bao nhiêu cũng được
     vì OSPF cần thời gian mới mất route).
Cha của A là B ở tầng gần nhất, rồi mở sớm nhất. Gốc là sự cố đi ngược lên cha đến hết.
Hạn chế: hai lỗi cùng VLAN cùng lúc thì triệu chứng chỉ gắn vào một gốc.
"""

LAYER = {
    "device.reachable": 1, "router.subinterface": 1, "router.uplink": 1,
    "switch.trunk": 1, "switch.access": 1, "host.interface": 1, "host.gateway": 1,
    "router.ospf": 2,
    "router.route": 3,
    "perf.flow": 4,
}


def layer_of(check_code: str) -> int:
    return LAYER.get(check_code, 1)


def parse_scope(text) -> frozenset:
    return frozenset(int(x) for x in str(text or "").split(",") if x.strip())


def format_scope(scope) -> str:
    return ",".join(str(v) for v in sorted(set(scope)))


def assign_roots(rows: list[dict], skew: int = 1) -> dict:
    """rows: dict có id, check_code, opened_snapshot, scope (chuỗi "10,20").

    Trả về {id: id_gốc}; sự cố là gốc thì giá trị None.
    """
    info = {r["id"]: (layer_of(r["check_code"]), parse_scope(r.get("scope")),
                      r["opened_snapshot"]) for r in rows}
    parent = {}
    for a, (la, sa, oa) in info.items():
        cands = [(-lb, ob, b) for b, (lb, sb, ob) in info.items()
                 if b != a and lb < la and sa & sb and ob <= oa + skew]
        if cands:
            parent[a] = min(cands)[2]
    roots = {}
    for a in info:
        cur = a
        while cur in parent:
            cur = parent[cur]
        roots[a] = None if cur == a else cur
    return roots