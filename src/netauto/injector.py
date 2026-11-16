"""Bộ tiêm lỗi: gây lỗi có kiểm soát trên lab, đo monitor phát hiện sau bao lâu.

Cần monitor đang chạy ở terminal khác, dùng CÙNG file DB. Mỗi lần đo:
  1. kiểm tra điều kiện sạch (monitor còn sống, không sự cố mở, không cửa sổ bảo trì)
  2. ghi "đáp án" vào bảng injections, rồi gây lỗi
  3. đợi monitor mở đúng sự cố (quá hạn thì ghi "bỏ sót")
  4. KHÔI PHỤC lỗi, kể cả khi bị ngắt giữa chừng (try/finally)
  5. đợi mạng bình thường lại rồi nghỉ một lúc trước lần sau

Chạy:  python3 -m netauto.injector --fault r1-subif-down --runs 10          (xem trước)
       python3 -m netauto.injector --all --runs 4 --yes                     (chạy thật)
"""
import argparse
import sys
import time
from datetime import datetime

from netauto import db, incidents, injections, maintenance, timefmt, transport

MAX_SNAPSHOT_AGE_S = 20


class NotClean(Exception):
    """Điều kiện thí nghiệm không sạch: không được gây lỗi."""


class RecoveryFailed(Exception):
    """Sau khi khôi phục, mạng không về bình thường: cần người xem lab."""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def latest_snapshot_row(conn):
    return conn.execute("SELECT id, taken_at FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()


def check_clean(conn, at: str) -> int:
    """Trả về id snapshot mới nhất nếu điều kiện sạch; ngược lại ném NotClean."""
    snap = latest_snapshot_row(conn)
    if snap is None or incidents.duration_s(snap[1], at) > MAX_SNAPSHOT_AGE_S:
        raise NotClean(f"monitor không chạy (snapshot cuối quá {MAX_SNAPSHOT_AGE_S} giây). "
                       "Hãy chạy monitor cùng file DB ở terminal khác.")
    if incidents.open_incidents(conn):
        raise NotClean("đang có sự cố mở. Hãy đưa lab về trạng thái khỏe "
                       "(bash scripts/ensure.sh) và đợi monitor đóng hết sự cố.")
    if maintenance.windows_at(conn, at):
        raise NotClean("đang có cửa sổ bảo trì (hoặc ân hạn): monitor sẽ giữ lỗi, số đo vô nghĩa.")
    return snap[0]

def run_once(conn, fault, *, run=transport.run, clock=now, sleep=time.sleep, poll_s=0.5,
             detect_timeout=45.0, recover_timeout=90.0, settle_s=10.0) -> dict:
    """Một lần đo. Trả về dòng injections (dict). Ném NotClean / RecoveryFailed."""
    before = check_clean(conn, clock())
    t0 = clock()
    inj_id = injections.start_injection(conn, fault, t0, before)

    try:
        res = run(fault.node, list(fault.inject))
        if not res.ok:
            injections.mark_status(conn, inj_id, "invalid")
            return injections.list_injections(conn, 1)[0] | {
                "note": f"không gây được lỗi: {res.stderr.strip()}"}
        inj = {"id": inj_id, "node": fault.node, "expect_check": fault.expect_check,
               "injected_at": t0, "before_snapshot": before}
        found, seen_at = None, None
        while incidents.duration_s(t0, clock()) < detect_timeout:
            found = injections.find_detection(inj, injections.incidents_since(conn, before))
            if found:
                seen_at = clock()          # lúc cảnh báo thật sự có mặt trong DB
                break
            sleep(poll_s)
        injections.mark_status(conn, inj_id, "detected" if found else "missed", found, seen_at)
    finally:
        # luôn khôi phục, kể cả khi Ctrl+C hay lỗi bất ngờ
        back = run(fault.node, list(fault.restore))
        injections.mark_restored(conn, inj_id, clock())
        if not back.ok:
            raise RecoveryFailed(f"lệnh khôi phục thất bại trên {fault.node}: {back.stderr.strip()}")

    t1 = clock()
    while incidents.open_incidents(conn):
        if incidents.duration_s(t1, clock()) > recover_timeout:
            raise RecoveryFailed(f"sau {recover_timeout:.0f} giây vẫn còn sự cố mở")
        sleep(poll_s)
    sleep(settle_s)
    return injections.list_injections(conn, 1)[0]


def main():
    ap = argparse.ArgumentParser(prog="netauto.injector")
    ap.add_argument("--fault", action="append", choices=sorted(injections.FAULTS),
                    help="tên lỗi (có thể lặp lại)")
    ap.add_argument("--all", action="store_true", help="dùng mọi lỗi")
    ap.add_argument("--runs", type=int, default=10, help="số vòng (mỗi vòng chạy mỗi lỗi một lần)")
    ap.add_argument("--yes", action="store_true", help="thật sự gây lỗi (mặc định chỉ xem trước)")
    ap.add_argument("--detect-timeout", type=float, default=45)
    ap.add_argument("--recover-timeout", type=float, default=90)
    ap.add_argument("--settle", type=float, default=10, help="giây nghỉ giữa hai lần đo")
    ap.add_argument("--db", default=db.DB_PATH)
    args = ap.parse_args()

    names = sorted(injections.FAULTS) if args.all else (args.fault or [])
    if not names:
        sys.exit("Chọn lỗi bằng --fault TÊN hoặc --all. Có: " + ", ".join(sorted(injections.FAULTS)))
    faults = [injections.FAULTS[n] for n in names]

    print(f"Kế hoạch: {args.runs} vòng × {len(faults)} lỗi = {args.runs * len(faults)} lần đo")
    for f in faults:
        print(f"  {f.name:<16} {f.desc}")
    if not args.yes:
        print("\nXEM TRƯỚC: chưa gây lỗi nào. Thêm --yes để chạy (cần monitor đang chạy cùng DB).")
        return

    conn = db.connect(args.db)
    incidents.ensure_schema(conn)
    maintenance.ensure_schema(conn)
    injections.ensure_schema(conn)
    ids, total = [], args.runs * len(faults)
    try:
        for i in range(args.runs):
            for f in faults:
                n = len(ids) + 1
                r = run_once(conn, f, detect_timeout=args.detect_timeout,
                             recover_timeout=args.recover_timeout, settle_s=args.settle)
                ids.append(r["id"])
                seen = f"{r['observed_seconds']:.1f}" if r["observed_seconds"] is not None else "-"
                took = f"{r['seconds']:.1f}" if r["seconds"] is not None else "-"
                print(f"[{n}/{total}] {f.name:<16} {r['status']:<9} thấy lỗi sau {seen} giây, "
                      f"cảnh báo sau {took} giây"
                      + (f"  ({r['note']})" if r.get("note") else ""))
    except NotClean as exc:
        print(f"\nDỪNG, chưa gây lỗi: {exc}")
    except RecoveryFailed as exc:
        print(f"\nDỪNG: {exc}\nHãy chạy `bash scripts/ensure.sh` để đưa lab về trạng thái khỏe.")
    except KeyboardInterrupt:
        print("\nBị ngắt. Lỗi đang gây (nếu có) đã được khôi phục.")
    finally:
        rows = [r for r in injections.list_injections(conn, 500) if r["id"] in set(ids)]
        if rows:
            print()
            injections.print_table(rows)
            injections.print_summary(injections.summarize(rows))
        conn.close()


if __name__ == "__main__":
    main()