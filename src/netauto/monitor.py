"""Giám sát định kỳ: thu thập, kiểm tra intent, đo ping, quản lý sự cố, tính điểm."""
import time
from dataclasses import asdict

from netauto import db, health, incidents, perf, probe
from netauto.check import check_snapshot
from netauto.collector import collect_all
from netauto.diff import diff_snapshots
from netauto.intent import INTENT_PATH, ignore_interfaces, load_intent


def run_monitor(interval: int = 10, count: int = 0, db_path: str = db.DB_PATH,
                intent_path: str = INTENT_PATH) -> None:
    """Chạy `count` lần (0 = chạy mãi đến khi bấm Ctrl+C)."""
    intent = load_intent(intent_path)
    ignore = ignore_interfaces(intent)       # cổng quản lý: không đưa vào so sánh
    probe.flows_from_intent(intent)          # báo lỗi sớm nếu mục probes sai
    conn = incidents.connect(db_path)
    probe.ensure_schema(conn)
    health.ensure_schema(conn)
    prev = db.latest_snapshot(conn)
    if prev:
        print(f"(so sánh với snapshot lưu trước đó, lúc {prev['taken_at']})")
    carried = incidents.open_incidents(conn)
    if carried:
        print(f"(còn {len(carried)} sự cố mở từ lần chạy trước)")
    history = {}                             # lịch sử trạng thái từng luồng ping
    done = 0
    try:
        while True:
            data = asdict(collect_all())
            sid = db.save_snapshot(conn, data)
            at = data["taken_at"]
            reach = sum(1 for d in data["devices"] if d["reachable"])
            errs = sum(len(d["errors"]) for d in data["devices"])

            changes = diff_snapshots(prev, data, ignore) if prev else {}
            for node, change in changes.items():
                db.save_events(conn, sid, at, node, change)
            n_changes = sum(len(c["mất"]) + len(c["mới"]) for c in changes.values())

            config_findings = check_snapshot(data, intent)
            results = probe.run_probes(intent)
            probe.save_probes(conn, sid, at, results)
            perf.push(history, results)
            perf_found = perf.perf_findings(results, history)

            opened, closed = incidents.record(
                conn, config_findings + perf_found, at, sid)
            n_open = len(incidents.open_incidents(conn))

            # điểm sức khỏe chỉ dùng lỗi cấu hình, vì luồng ping đã được tính riêng
            h = health.score(config_findings, results)
            health.save_health(conn, sid, at, h)

            print(f"[{at}] snapshot #{sid}: reachable {reach}/{len(data['devices'])}, "
                  f"lỗi thu thập {errs}, thay đổi {n_changes}, sự cố đang mở {n_open}, "
                  f"sức khỏe {h['score']} ({h['grade']})")
            for node, change in changes.items():
                for fact in change["mất"]:
                    print(f"    {node}  - {fact}")
                for fact in change["mới"]:
                    print(f"    {node}  + {fact}")
            for f in opened:
                print(f"    MỞ   [{f.severity}] {f.node}: {f.message}")
            for r in closed:
                print(f"    ĐÓNG {r['node']}: {r['message']} "
                      f"(kéo dài {r['duration']:.0f} giây)")
            for p in results:
                state = health.flow_state(p)
                if state != "ok":
                    rtt = f"{p.rtt_avg:.1f} ms" if p.rtt_avg is not None else "-"
                    print(f"    ĐO   {p.src} → {p.dst}: {state}, "
                          f"mất {p.loss_pct:.0f}% gói, RTT {rtt}")

            prev = data
            done += 1
            if count and done >= count:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nĐã dừng giám sát.")
    finally:
        conn.close()