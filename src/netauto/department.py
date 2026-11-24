"""Thêm một phòng ban (VLAN + máy trạm) vào mạng: phần TÍNH TOÁN, hàm thuần, không đụng thiết bị hay tệp.

Từ (tên, số VLAN, router) hàm `plan_department` sinh ra:
  - nội dung mới của intent/network.yml và lab/topology.clab.yml (sửa theo dòng để giữ chú thích và bố cục),
  - các lệnh Docker để dựng máy trạm mới trong lab và nối nó vào switch,
  - tóm tắt cho người đọc.
Mọi bản sửa văn bản đều được kiểm tra lại bằng cách đọc YAML và so với kết quả mong đợi; không khớp thì từ chối
thay vì ghi một tệp sai.
"""
import copy
import ipaddress
import re
import unicodedata

import yaml

from netauto import intent as intent_mod
from netauto.transport import LAB_NAME, container_name

IMAGE = "nicolaka/netshoot:latest"
NAME_RE = re.compile(r"^[^\W_][\w -]{0,29}$")       # chữ (kể cả có dấu), số, khoảng trắng, gạch; bắt đầu bằng chữ/số
VLAN_MIN, VLAN_MAX = 2, 254                          # subnet 192.168.<vlan>.0/24 nên vlan chỉ tới 254
SLUG_MAX = 12
HOST_PORT = "eth1"
VETH_SWITCH, VETH_HOST = "vsw{vlan}", "vhs{vlan}"


def slugify(name: str) -> str:
    """'Tài chính' -> 'tai-chinh': chữ thường không dấu, nối bằng gạch, tối đa SLUG_MAX ký tự."""
    text = unicodedata.normalize("NFKD", str(name).replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:SLUG_MAX].rstrip("-")


def _fail(*errors):
    return {"ok": False, "errors": list(errors), "steps": [], "undo": [], "summary": []}


def _lab_links(lab: dict) -> list[tuple]:
    out = []
    for link in (lab.get("topology") or {}).get("links") or []:
        ends = [e.split(":", 1) for e in link.get("endpoints", []) if ":" in e]
        if len(ends) == 2:
            out.append((tuple(ends[0]), tuple(ends[1])))
    return out


def _switch_behind(router: str, trunk_port: str, switches: dict, links):
    for a, b in links:
        for mine, other in ((a, b), (b, a)):
            if mine == (router, trunk_port) and other[0] in switches:
                return other[0]
    return None


def _free_port(switch: str, intent_spec: dict, links) -> str:
    used = {p for a, b in links for n, p in (a, b) if n == switch}
    used |= {intent_spec["trunk"]["port"], *intent_spec["access"]}
    n = 2
    while f"eth{n}" in used:
        n += 1
    return f"eth{n}"


def _block(lines: list[str], header: str, indent: int = 0):
    """Chỉ số dòng bắt đầu và kết thúc (không gồm) của khối con của `header` (khóa cấp trên)."""
    start = next((i for i, ln in enumerate(lines) if re.match(rf"^{' ' * indent}{re.escape(header)}\s*$", ln)), None)
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if ln.strip() and not ln.startswith(" " * (indent + 1)) and not ln.lstrip().startswith("#"):
            end = i
            break
    return start, end


def _insert_after_last(lines, start, end, pattern, new_line):
    last = None
    for i in range(start + 1, end):
        if re.match(pattern, lines[i]):
            last = i
    if last is None:
        return False
    lines.insert(last + 1, new_line)
    return True


def _edit_intent_text(text: str, spec: dict) -> str | None:
    lines = text.split("\n")
    vlan, sw, port = spec["vlan"], spec["switch"], spec["port"]
    blk = _block(lines, "vlans:")
    if not blk or not _insert_after_last(
            lines, *blk, r"^  \d+: \{",
            f"  {vlan}: {{name: {spec['name']}, subnet: {spec['subnet']}, gateway: {spec['gateway']}, router: {spec['router']}}}"):
        return None
    sblk = _block(lines, "switches:")
    if not sblk:
        return None
    # tìm khối của switch trong "switches:"
    s0 = next((i for i in range(sblk[0] + 1, sblk[1]) if re.match(rf"^  {re.escape(sw)}:\s*$", lines[i])), None)
    if s0 is None:
        return None
    s1 = next((i for i in range(s0 + 1, sblk[1]) if re.match(r"^  \S", lines[i])), sblk[1])
    trunk_ok = access_ok = False
    for i in range(s0 + 1, s1):
        m = re.match(r"^(    trunk: \{port: \S+, vlans: \[)([^\]]*)(\]\}\s*)$", lines[i])
        if m:
            lines[i] = f"{m.group(1)}{m.group(2)}, {vlan}{m.group(3)}"
            trunk_ok = True
        m = re.match(r"^(    access: \{)([^}]*)(\}\s*)$", lines[i])
        if m:
            lines[i] = f"{m.group(1)}{m.group(2)}, {port}: {vlan}{m.group(3)}"
            access_ok = True
    if not (trunk_ok and access_ok):
        return None
    hblk = _block(lines, "hosts:")
    if not hblk or not _insert_after_last(lines, *hblk, r"^  \S+:\s*\{vlan:",
                                          f"  {spec['host']}: {{vlan: {vlan}, ip: {spec['host_ip']}}}"):
        return None
    pblk = _block(lines, "probes:")
    probe = spec["probe"]
    if not pblk or not _insert_after_last(lines, *pblk, r"^  - \{src:",
                                          f"  - {{src: {probe['src']}, dst: {probe['dst']}}}"):
        return None
    return "\n".join(lines)


def _edit_lab_text(text: str, spec: dict) -> str | None:
    lines = text.split("\n")
    host = spec["host"]
    node_line = f"    {host}: {{ image: {IMAGE}, cmd: sleep infinity }}"
    link_line = f'    - endpoints: ["{spec["switch"]}:{spec["port"]}", "{host}:{HOST_PORT}"]'
    last_node = last_link = None
    for i, ln in enumerate(lines):
        if re.match(r"^    h-\S+:", ln):
            last_node = i
        if re.match(r"^    - endpoints:", ln):
            last_link = i
    if last_node is None or last_link is None:
        return None
    lines.insert(last_link + 1, link_line)      # chèn dòng sau trước để không làm lệch chỉ số
    lines.insert(last_node + 1, node_line)
    return "\n".join(lines)


def _expected_intent(intent: dict, spec: dict) -> dict:
    new = copy.deepcopy(intent)
    new["vlans"][spec["vlan"]] = {"name": spec["name"], "subnet": spec["subnet"],
                                  "gateway": spec["gateway"], "router": spec["router"]}
    sw = new["switches"][spec["switch"]]
    sw["trunk"]["vlans"] = [*sw["trunk"]["vlans"], spec["vlan"]]
    sw["access"][spec["port"]] = spec["vlan"]
    new["hosts"][spec["host"]] = {"vlan": spec["vlan"], "ip": spec["host_ip"]}
    new["probes"] = [*new.get("probes", []), spec["probe"]]
    return new


def _expected_lab(lab: dict, spec: dict) -> dict:
    new = copy.deepcopy(lab)
    new["topology"]["nodes"][spec["host"]] = {"image": IMAGE, "cmd": "sleep infinity"}
    new["topology"]["links"].append({"endpoints": [f"{spec['switch']}:{spec['port']}", f"{spec['host']}:{HOST_PORT}"]})
    return new


def infra_steps(spec: dict) -> tuple[list[dict], list[list[str]]]:
    """Lệnh Docker (danh sách tham số, không qua shell). {PID_*} được thay bằng PID thật lúc chạy."""
    host_c, sw_c = container_name(spec["host"]), container_name(spec["switch"])
    va, vb = VETH_SWITCH.format(vlan=spec["vlan"]), VETH_HOST.format(vlan=spec["vlan"])
    wire = (f"ip link add {va} type veth peer name {vb} && "
            f"ip link set {va} netns {{PID_SWITCH}} name {spec['port']} && "
            f"ip link set {vb} netns {{PID_HOST}} name {HOST_PORT}")
    steps = [
        {"id": "create-host", "label": f"Tạo container máy trạm {host_c}",
         "argv": ["docker", "run", "-d", "--name", host_c, "--hostname", spec["host"], "--network", "none",
                  "--cap-add", "NET_ADMIN", "--label", f"containerlab={LAB_NAME}",
                  "--label", f"clab-node-name={spec['host']}", IMAGE, "sleep", "infinity"]},
        {"id": "pid-switch", "label": f"Lấy PID của {sw_c}", "capture": "PID_SWITCH",
         "argv": ["docker", "inspect", "-f", "{{.State.Pid}}", sw_c]},
        {"id": "pid-host", "label": f"Lấy PID của {host_c}", "capture": "PID_HOST",
         "argv": ["docker", "inspect", "-f", "{{.State.Pid}}", host_c]},
        {"id": "wire", "label": f"Nối {spec['switch']}:{spec['port']} với {spec['host']}:{HOST_PORT}",
         "argv": ["docker", "run", "--rm", "--privileged", "--net=host", "--pid=host", IMAGE, "sh", "-c", wire]},
    ]
    return steps, [["docker", "rm", "-f", host_c]]


def plan_department(intent_text: str, lab_text: str, name, vlan, router) -> dict:
    """Kiểm tra yêu cầu và sinh toàn bộ thay đổi. Trả về {"ok", "errors", "spec", "intent_text", "lab_text",
    "steps", "undo", "summary"}. Hàm thuần: không ghi tệp, không chạy lệnh."""
    try:
        intent = yaml.safe_load(intent_text)
        lab = yaml.safe_load(lab_text)
    except yaml.YAMLError as exc:
        return _fail(f"Không đọc được intent hoặc topology: {exc}")
    errs = intent_mod.validate_intent(intent)
    if errs:
        return _fail("Intent hiện tại không hợp lệ: " + "; ".join(errs))

    name = str(name).strip() if name is not None else ""
    if not NAME_RE.match(name):
        return _fail("Tên phòng ban chỉ gồm chữ, số, khoảng trắng, dấu gạch; tối đa 30 ký tự, bắt đầu bằng chữ hoặc số.")
    slug = slugify(name)
    if not slug:
        return _fail("Tên phòng ban phải có ít nhất một chữ hoặc số thường.")
    if isinstance(vlan, bool) or not isinstance(vlan, int) or not VLAN_MIN <= vlan <= VLAN_MAX:
        return _fail(f"Số VLAN phải là số nguyên từ {VLAN_MIN} đến {VLAN_MAX}.")
    if vlan in intent["vlans"]:
        return _fail(f"VLAN {vlan} đã tồn tại.")
    subnet = f"192.168.{vlan}.0/24"
    for vid, v in intent["vlans"].items():
        if ipaddress.ip_network(str(v["subnet"])).overlaps(ipaddress.ip_network(subnet)):
            return _fail(f"Subnet {subnet} trùng với VLAN {vid}.")
    if router not in intent["routers"]:
        return _fail(f"Không có router '{router}' trong intent.")
    host = f"h-{slug}"
    existing = set(intent["hosts"]) | set((lab.get("topology") or {}).get("nodes") or {})
    if host in existing:
        return _fail(f"Thiết bị {host} đã tồn tại (tên phòng ban trùng sau khi bỏ dấu).")

    links = _lab_links(lab)
    switch = _switch_behind(router, intent["routers"][router]["trunk_port"], intent["switches"], links)
    if switch is None:
        return _fail(f"Không tìm thấy switch nối với {router} trong lab/topology.clab.yml.")
    probes = intent.get("probes") or []
    dst = probes[0]["src"] if probes else sorted(intent["hosts"])[0]
    spec = {"name": name, "vlan": vlan, "subnet": subnet, "gateway": f"192.168.{vlan}.1", "router": router,
            "switch": switch, "port": _free_port(switch, intent["switches"][switch], links),
            "host": host, "host_ip": f"192.168.{vlan}.10/24", "probe": {"src": host, "dst": dst}}

    new_intent_text = _edit_intent_text(intent_text, spec)
    new_lab_text = _edit_lab_text(lab_text, spec)
    unsupported = "Định dạng tệp hiện tại không hỗ trợ sửa tự động; hãy sửa tay theo mẫu."
    try:
        ok_intent = new_intent_text is not None and yaml.safe_load(new_intent_text) == _expected_intent(intent, spec)
        ok_lab = new_lab_text is not None and yaml.safe_load(new_lab_text) == _expected_lab(lab, spec)
    except yaml.YAMLError:
        ok_intent = ok_lab = False
    if not (ok_intent and ok_lab):
        return _fail(unsupported)
    if intent_mod.validate_intent(yaml.safe_load(new_intent_text)):
        return _fail("Intent mới không hợp lệ: " + "; ".join(intent_mod.validate_intent(yaml.safe_load(new_intent_text))))

    steps, undo = infra_steps(spec)
    summary = [
        f"Thêm VLAN {vlan} ({name}): subnet {subnet}, gateway {spec['gateway']} trên {router}.",
        f"Cho phép VLAN {vlan} đi qua cổng trunk của {switch}.",
        f"Tạo máy trạm {host} (IP {spec['host_ip']}), nối vào {switch}:{spec['port']}.",
        f"Thêm luồng đo {host} → {dst} để giám sát phòng ban mới.",
        "Ghi intent và topology mới (có sao lưu), áp dụng cấu hình, ghi nhật ký kiểm toán.",
    ]
    return {"ok": True, "errors": [], "spec": spec, "intent_text": new_intent_text, "lab_text": new_lab_text,
            "steps": steps, "undo": undo, "summary": summary}


# ---------------------------------------------------------------------------------------------- xóa phòng ban
PROTECTED_VLANS = frozenset({10, 20, 30, 99})       # VLAN gốc của lab demo: không xóa từ web


def _host_wiring(lab: dict, intent: dict, host: str):
    """(switch, cổng) mà máy trạm cắm vào theo topology; None nếu không rõ."""
    for a, b in _lab_links(lab):
        for mine, other in ((a, b), (b, a)):
            if mine[0] == host and other[0] in intent["switches"]:
                return other
    return None


def list_departments(intent_text: str, lab_text: str) -> list[dict]:
    """Danh sách phòng ban (VLAN) hiện có, kèm có xóa được từ web không và lý do nếu không."""
    intent = yaml.safe_load(intent_text)
    out = []
    for vid, v in sorted(intent["vlans"].items()):
        hosts = sorted(h for h, s in intent["hosts"].items() if s["vlan"] == vid)
        protected = vid in PROTECTED_VLANS
        out.append({"vlan": vid, "name": str(v["name"]), "router": v["router"], "subnet": str(v["subnet"]),
                    "hosts": hosts, "deletable": not protected,
                    "reason": "VLAN gốc của lab demo, không xóa từ web." if protected else ""})
    return out


def _edit_intent_remove(text: str, vlan: int, hosts: set) -> str | None:
    lines = text.split("\n")
    out, section, changed_trunk = [], None, False
    for ln in lines:
        top = re.match(r"^(\w+):", ln)
        if top:
            section = top.group(1)
        if section == "vlans" and re.match(rf"^  {vlan}: \{{", ln):
            continue
        if section == "hosts" and any(re.match(rf"^  {re.escape(h)}:\s*\{{vlan:", ln) for h in hosts):
            continue
        if section == "probes":
            m = re.match(r"^  - \{src: (\S+?),\s*dst: (\S+?)\}", ln)
            if m and (m.group(1) in hosts or m.group(2) in hosts):
                continue
        if section == "switches":
            m = re.match(r"^(    trunk: \{port: \S+, vlans: \[)([^\]]*)(\]\}.*)$", ln)
            if m:
                nums = [x.strip() for x in m.group(2).split(",") if x.strip()]
                if str(vlan) in nums:
                    nums.remove(str(vlan))
                    ln = f"{m.group(1)}{', '.join(nums)}{m.group(3)}"
                    changed_trunk = True
            m = re.match(r"^(    access: \{)([^}]*)(\}.*)$", ln)
            if m:
                pairs = [x.strip() for x in m.group(2).split(",") if x.strip()]
                keep = [p for p in pairs if not re.match(rf"^\S+:\s*{vlan}$", p)]
                if len(keep) != len(pairs):
                    ln = f"{m.group(1)}{', '.join(keep)}{m.group(3)}"
        out.append(ln)
    return "\n".join(out) if changed_trunk else None


def _edit_lab_remove(text: str, hosts: set) -> str:
    out = []
    for ln in text.split("\n"):
        if any(re.match(rf"^    {re.escape(h)}:", ln) or re.search(rf'"{re.escape(h)}:', ln) for h in hosts):
            continue
        out.append(ln)
    return "\n".join(out)


def plan_removal(intent_text: str, lab_text: str, vlan) -> dict:
    """Kiểm tra yêu cầu xóa phòng ban và sinh nội dung tệp mới. Hàm thuần, không ghi tệp hay chạy lệnh.
    Trả về {"ok", "errors", "spec", "intent_text", "lab_text", "steps", "restore", "summary"}."""
    try:
        intent = yaml.safe_load(intent_text)
        lab = yaml.safe_load(lab_text)
    except yaml.YAMLError as exc:
        return _fail(f"Không đọc được intent hoặc topology: {exc}")
    errs = intent_mod.validate_intent(intent)
    if errs:
        return _fail("Intent hiện tại không hợp lệ: " + "; ".join(errs))
    if isinstance(vlan, bool) or not isinstance(vlan, int) or vlan not in intent["vlans"]:
        return _fail(f"Không có VLAN {vlan} trong intent.")
    if vlan in PROTECTED_VLANS:
        return _fail(f"VLAN {vlan} là VLAN gốc của lab demo, không xóa từ web.")

    info = intent["vlans"][vlan]
    hosts = sorted(h for h, s in intent["hosts"].items() if s["vlan"] == vlan)
    restore = []
    for h in hosts:
        wired = _host_wiring(lab, intent, h)
        if wired is None or intent["switches"][wired[0]]["access"].get(wired[1]) != vlan:
            return _fail(f"Không xác định được cổng switch của {h}; hãy xóa tay.")
        restore.append({"name": str(info["name"]), "vlan": vlan, "host": h, "switch": wired[0], "port": wired[1]})
    ports = {(sw, p) for sw, s in intent["switches"].items() for p, v in s["access"].items() if v == vlan}
    if ports != {(r["switch"], r["port"]) for r in restore}:
        return _fail("VLAN này còn cổng switch không thuộc máy trạm nào; hãy xóa tay.")

    new_intent_text = _edit_intent_remove(intent_text, vlan, set(hosts))
    new_lab_text = _edit_lab_remove(lab_text, set(hosts))
    try:
        exp = copy.deepcopy(intent)
        del exp["vlans"][vlan]
        for s in exp["switches"].values():
            s["trunk"]["vlans"] = [v for v in s["trunk"]["vlans"] if v != vlan]
            s["access"] = {p: v for p, v in s["access"].items() if v != vlan}
        for h in hosts:
            del exp["hosts"][h]
        exp["probes"] = [p for p in exp.get("probes", []) if p["src"] not in hosts and p["dst"] not in hosts]
        exp_lab = copy.deepcopy(lab)
        for h in hosts:
            exp_lab["topology"]["nodes"].pop(h, None)
        exp_lab["topology"]["links"] = [l for l in exp_lab["topology"]["links"]
                                       if not any(e.split(":")[0] in hosts for e in l["endpoints"])]
        ok = (new_intent_text is not None and yaml.safe_load(new_intent_text) == exp
              and yaml.safe_load(new_lab_text) == exp_lab)
    except (yaml.YAMLError, KeyError, TypeError):
        ok = False
    if not ok:
        return _fail("Định dạng tệp hiện tại không hỗ trợ sửa tự động; hãy sửa tay theo mẫu.")
    errs = intent_mod.validate_intent(yaml.safe_load(new_intent_text))
    if errs:
        return _fail("Intent mới không hợp lệ: " + "; ".join(errs))

    steps = [["docker", "rm", "-f", container_name(h)] for h in hosts]
    summary = [
        f"Xóa VLAN {vlan} ({info['name']}) khỏi intent, khỏi cổng trunk và gateway trên {info['router']}.",
        *[f"Xóa máy trạm {r['host']} và cổng {r['switch']}:{r['port']}." for r in restore],
        "Xóa các luồng đo liên quan.",
        "Ghi intent và topology mới (có sao lưu), chạy apply --prune để gỡ cấu hình khỏi thiết bị, ghi nhật ký kiểm toán.",
    ]
    return {"ok": True, "errors": [], "spec": {"vlan": vlan, "name": str(info["name"]), "router": info["router"],
                                                 "hosts": hosts},
            "intent_text": new_intent_text, "lab_text": new_lab_text, "steps": steps, "restore": restore,
            "summary": summary}
