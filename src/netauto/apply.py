"""Apply: thực thi kế hoạch (plan) lên thiết bị.

Chạy: python3 -m netauto.apply [--node r1] [--prune] [--yes] [--intent file.yml]
Mặc định chỉ XEM. Phải có --yes thì mới thay đổi thiết bị.
Mã thoát: 0 = xong hoặc không có gì, 1 = có bước lỗi,
          2 = (xem trước) còn thay đổi chờ áp dụng, 3 = từ chối vì có mục CHẶN
"""
import argparse
import shlex
import sys
import time
from dataclasses import asdict, dataclass

from netauto import transport
from netauto.collector import collect_all
from netauto.intent import INTENT_PATH, load_intent
from netauto.plan import build_plan, print_plan

PRUNE_KINDS = {"vlan-extra", "subif-extra", "addr-extra"}   # chỉ gỡ khi có --prune
EXISTS_MARKERS = ("File exists", "already exists")          # "đã có rồi" coi như thành công
TAG = {"add": "THÊM", "remove": "GỠ", "unknown": "ÁP LẠI"}
STATUS = {"done": "XONG", "exists": "ĐÃ CÓ", "failed": "LỖI"}


@dataclass
class Outcome:
    node: str
    label: str
    status: str          # done | exists | failed
    detail: str = ""


def select_items(items, prune=False):
    """Chọn các mục sẽ chạy. Bỏ 'ok'; mục thừa chỉ lấy khi có --prune."""
    chosen = []
    for i in items:
        if i.action in ("add", "unknown"):
            chosen.append(i)
        elif i.action == "remove" and (prune or i.kind not in PRUNE_KINDS):
            chosen.append(i)
    return chosen


def group_by_node(items, intent):
    """Gom theo thiết bị, theo thứ tự an toàn: switch, rồi router, rồi host."""
    order = [*intent["switches"], *intent["routers"], *intent["hosts"]]
    groups = {n: [] for n in order}
    for i in items:
        groups.setdefault(i.node, []).append(i)
    return {n: g for n, g in groups.items() if g}


def to_units(items):
    """Chia thành các đơn vị thực thi: lệnh shell chạy riêng, các dòng vtysh liền nhau gộp một gói."""
    units = []
    for i in items:
        if i.channel == "vtysh":
            if units and units[-1][0] == "vtysh":
                units[-1][1].append(i)
            else:
                units.append(("vtysh", [i]))
        else:
            units.append(("shell", [i]))
    return units


def label_of(channel, group):
    if channel == "vtysh":
        return f"vtysh ({len(group)} dòng)"
    return f"{group[0].kind} {group[0].target}"


def run_unit(node, channel, group, run=transport.run):
    label = label_of(channel, group)
    if channel == "vtysh":
        argv = ["vtysh", "-c", "configure terminal"]
        for i in group:
            argv += ["-c", i.command.strip()]
    else:
        argv = shlex.split(group[0].command)
    try:
        res = run(node, argv, timeout=20)
    except Exception as exc:                     # không truy cập được thiết bị, v.v.
        return Outcome(node, label, "failed", str(exc))
    text = (res.stderr + res.stdout).strip()
    first = text.splitlines()[0][:120] if text else ""
    vtysh_error = channel == "vtysh" and any(l.startswith("%") for l in text.splitlines())
    if res.ok and not vtysh_error:
        return Outcome(node, label, "done")
    if channel == "shell" and any(m in text for m in EXISTS_MARKERS):
        return Outcome(node, label, "exists", first)
    return Outcome(node, label, "failed", first or f"mã thoát {res.returncode}")


def apply_node(node, items, run=transport.run):
    """Chạy lần lượt các đơn vị của một thiết bị, dừng ngay khi có lỗi thật."""
    outcomes = []
    for channel, group in to_units(items):
        out = run_unit(node, channel, group, run)
        outcomes.append(out)
        if out.status == "failed":
            break
    return outcomes


def show_preview(groups):
    for node, items in groups.items():
        print(f"== {node}")
        for channel, group in to_units(items):
            if channel == "vtysh":
                text = "cấu hình OSPF, gửi lại (snapshot chưa đọc được)"
                tag = TAG["unknown"]
            else:
                i = group[0]
                tag = TAG[i.action]
                text = i.command
            print(f"  {tag:<7} {label_of(channel, group):<24} {text}")


def main():
    ap = argparse.ArgumentParser(prog="netauto.apply")
    ap.add_argument("--intent", default=INTENT_PATH)
    ap.add_argument("--node", help="chỉ áp dụng cho một thiết bị")
    ap.add_argument("--prune", action="store_true", help="gỡ cả các mục thừa ngoài intent")
    ap.add_argument("--yes", action="store_true", help="thực sự áp dụng (mặc định chỉ xem)")
    args = ap.parse_args()

    intent = load_intent(args.intent)
    known = [*intent["switches"], *intent["routers"], *intent["hosts"]]
    if args.node and args.node not in known:
        sys.exit(f"Không có thiết bị '{args.node}'. Có: {', '.join(known)}")
    nodes = {args.node} if args.node else None

    items = build_plan(intent, asdict(collect_all()), nodes)
    if any(i.action == "blocked" for i in items):
        print_plan(items)
        print("\nTỪ CHỐI áp dụng: kế hoạch có mục CHẶN (lỗi hạ tầng). "
              "Sửa hạ tầng (vd. bash scripts/lab.sh reset) rồi chạy lại.")
        sys.exit(3)

    chosen = select_items(items, args.prune)
    skipped = [i for i in items if i.action == "remove" and i not in chosen]
    groups = group_by_node(chosen, intent)
    pending = [i for i in chosen if i.action in ("add", "remove")]

    if not groups:
        print("Không có gì để làm.")
    else:
        show_preview(groups)
    if skipped:
        print(f"\nBỎ QUA {len(skipped)} mục thừa (thêm --prune để gỡ):")
        for i in skipped:
            print(f"  {i.node}: {i.command}")

    if not args.yes:
        print("\nXEM TRƯỚC: chưa thay đổi gì. Thêm --yes để áp dụng.")
        sys.exit(2 if pending else 0)
    if not groups:
        sys.exit(0)

    print("\n--- ĐANG ÁP DỤNG ---")
    failed = False
    for node, node_items in groups.items():
        print(f"== {node}")
        for out in apply_node(node, node_items):
            note = f"   # {out.detail}" if out.detail else ""
            print(f"  {STATUS[out.status]:<6} {out.label}{note}")
            failed = failed or out.status == "failed"

    time.sleep(1)
    after = build_plan(intent, asdict(collect_all()), nodes)
    left = [i for i in select_items(after, args.prune) if i.action in ("add", "remove")]
    left += [i for i in after if i.action == "blocked"]
    print()
    if left:
        print(f"CHƯA HỘI TỤ: còn {len(left)} mục:")
        for i in left:
            print(f"  {i.node} {TAG.get(i.action, i.action)} {i.kind} {i.target}")
    else:
        print("HỘI TỤ: plan lại không còn mục nào cần làm.")
    if skipped:
        print(f"(còn {len(skipped)} mục thừa chưa gỡ vì không có --prune)")
    sys.exit(1 if failed or left else 0)


if __name__ == "__main__":
    main()