"""Giám sát định kỳ: thu thập, kiểm tra intent, đo ping, quản lý sự cố, tính điểm."""
import time
from dataclasses import asdict
from netauto import db, health, incidents, maintenance, perf, probe, runs, timefmt
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
    runs.ensure_schema(conn)
    maintenance.ensure_schema(conn)
    prev = db.latest_snapshot(conn)
    run_id, gap = runs.start_run(conn, interval, prev["taken_at"] if prev else None)
    if prev:
        print(f"(so sánh với snapshot lưu trước đó, lúc {timefmt.full(prev['taken_at'])})")
    if gap:
        print(f"!! {runs.describe_gap(gap)}")
        print("   Thay đổi phát hiện ở vòng đầu là tích lũy trong khoảng trống, "
              "không rõ xảy ra lúc nào.")
    carried = incidents.open_incidents(conn)
    if carried:
        print(f"(còn {len(carried)} sự cố mở từ lần chạy trước)")
    after_gap = gap is not None              # chỉ đúng với vòng đầu tiên
    history = {}                             # lịch sử trạng thái từng luồng ping
    done = 0
    clean = False                            # True nếu dừng có chủ đích
    shown_day = None                         # in dòng ngày mỗi khi sang ngày mới
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
            perf_found = perf.perf_findings(results, history, intent)

            all_found = config_findings + perf_found
            for wid in maintenance.sweep_expired(conn, at):
                print(f"    (cửa sổ bảo trì #{wid} đã hết hạn tự động)")
            windows = maintenance.windows_at(conn, at)
            found, held = maintenance.split_findings(all_found, windows, at, intent)
            maintenance.hold(conn, held, at)
            maintenance.forget_resolved(conn, {incidents.key_of(f) for f in all_found})
            flagged = maintenance.survivors(conn, found)
            opened, closed = incidents.record(
                conn, found, at, sid, after_gap=after_gap,
                hold=[f for f, _ in held], flagged=flagged)
            n_open = len(incidents.open_incidents(conn))

            # điểm sức khỏe chỉ dùng lỗi cấu hình, vì luồng ping đã được tính riêng
            h = health.score(config_findings, results)
            health.save_health(conn, sid, at, h, windows[0]["id"] if windows else None)

            if timefmt.day(at) != shown_day:
                shown_day = timefmt.day(at)
                print(f"--- ngày {shown_day} ---")
            print(f"[{timefmt.clock(at)}] snapshot #{sid}: reachable {reach}/{len(data['devices'])}, "
                  f"lỗi thu thập {errs}, thay đổi {n_changes}, sự cố đang mở {n_open}, "
                  f"sức khỏe {h['score']} ({h['grade']})"
                  + (f", ĐANG BẢO TRÌ {maintenance.describe(windows[0], at)}" if windows else ""))
            if held:
                print(f"    (giữ lại {len(held)} lỗi vì đang bảo trì, chưa mở sự cố)")
            if after_gap and changes:
                print("    (các thay đổi dưới đây tích lũy trong khoảng trống giám sát)")
            for node, change in changes.items():
                for fact in change["mất"]:
                    print(f"    {node}  - {fact}")
                for fact in change["mới"]:
                    print(f"    {node}  + {fact}")
            for r in incidents.opened_at_snapshot(conn, sid):
                why = f"  ← triệu chứng của #{r['root_id']}" if r["root_id"] else ""
                print(f"    MỞ   #{r['id']} [{r['severity']}] {r['node']}: {r['message']}{why}"
                      + ("  [còn sau bảo trì]" if r["after_maintenance"] else ""))
            for r in closed:
                took = ("không rõ thời lượng" if r["duration"] is None
                        else f"kéo dài {r['duration']:.0f} giây")
                why = incidents.REASON_TEXT.get(r["close_reason"], r["close_reason"])
                if r["successor_id"]:
                    why += f" → #{r['successor_id']}"
                print(f"    ĐÓNG #{r['id']} {r['node']}: {r['message']} ({took}; {why})")
            for p in results:
                state = health.flow_state(p)
                if state != "ok":
                    rtt = f"{p.rtt_avg:.1f} ms" if p.rtt_avg is not None else "-"
                    print(f"    ĐO   {p.src} → {p.dst}: {state}, "
                          f"mất {p.loss_pct:.0f}% gói, RTT {rtt}")

            prev = data
            after_gap = False
            done += 1
            if count and done >= count:
                clean = True
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        clean = True
        print("\nĐã dừng giám sát.")
    finally:
        runs.stop_run(conn, run_id, clean)
        conn.close()