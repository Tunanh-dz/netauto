"""Tự khắc phục, phần quyết định (Ngày 25A). Chưa sửa gì: chỉ trả lời "NÊN làm gì".

Xem quyết định hiện tại:  python3 -m netauto.remediation [--db f]
Mở lại cầu chì đã ngắt:   python3 -m netauto.remediation --reset-breaker

Chính sách là hàm thuần decide(): cùng đầu vào thì cùng quyết định, test được mà không
cần lab. Chỉ sửa sự cố GỐC thuộc danh sách REMEDIABLE, và chỉ khi mọi điều kiện an toàn
đều đạt (đủ tuổi, không bảo trì, chưa quá số lần thử, cầu chì đóng, không có việc khác
đang chạy). Mỗi lần chỉ sửa MỘT việc.
"""
import argparse
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

from netauto import db, incidents, maintenance, runs, service

SCHEMA = """
CREATE TABLE IF NOT EXISTS remediations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL,
    node        TEXT    NOT NULL,
    check_code  TEXT    NOT NULL,
    started_at  TEXT    NOT NULL,
    finished_at TEXT,                  -- NULL = đang chạy (hoặc bị ngắt giữa chừng)
    outcome     TEXT,                  -- fixed | ineffective | failed | rolled_back | refused | reset
    exit_code   INTEGER,
    detail      TEXT    NOT NULL DEFAULT '',
    audit_id    INTEGER                -- bản ghi nhật ký kiểm toán của lần apply
);
"""
NEW_COLUMNS = {"audit_id": "INTEGER"}      # CSDL cũ được nâng cấp tại chỗ

# Chỉ những lỗi đã được kiểm chứng là `plan` thấy và `apply` sửa được (TN18, Ngày 25).
# Mở rộng từng bước, mỗi bước đo trước. "wrong" cố ý KHÔNG có: sửa sai địa chỉ cần gỡ
# địa chỉ cũ, tức --prune, mà tự động thì không được gỡ.
REMEDIABLE = frozenset({
    ("router.subinterface", "down"), ("router.subinterface", "missing"),
    ("router.uplink", "down"), ("router.uplink", "missing"),
})
FAILURES = frozenset({"failed", "rolled_back", "refused", "ineffective"})
STALE_RUNNING_S = 300      # lần sửa "đang chạy" quá 5 phút coi như đã chết, không chặn mãi
ACTOR = "netauto-remediation"   # ghi vào nhật ký kiểm toán: phân biệt máy sửa với người sửa
APPLY_TIMEOUT_S = 300      # apply tối đa 5 phút (bình thường 30–60 giây)
WAIT_CLOSED_S = 45         # sau khi apply xong, đợi monitor đóng sự cố tối đa chừng này
POLL_S = 2
OUTPUT_TAIL = 600          # chỉ lưu phần cuối đầu ra của apply

OUTCOME_BY_CODE = {1: "failed", 2: "failed", 3: "refused", 4: "rolled_back", 5: "refused"}
OUTCOME_TEXT = {
    "fixed": "ĐÃ SỬA: sự cố đã đóng",
    "ineffective": "KHÔNG HIỆU QUẢ: apply thành công nhưng sự cố vẫn mở",
    "failed": "THẤT BẠI",
    "rolled_back": "ĐÃ HOÀN TÁC: apply thấy mạng xấu đi",
    "refused": "TỪ CHỐI: apply không cho chạy",
}


@dataclass(frozen=True)
class Policy:
    min_age_s: float = 15          # sự cố phải mở đủ lâu (khoảng 2 chu kỳ) mới hành động
    max_attempts: int = 2          # số lần thử tối đa cho mỗi (thiết bị, loại lỗi)
    attempt_window_s: float = 600  # ... trong khoảng thời gian này (10 phút)
    backoff_s: float = 60          # giãn cách tối thiểu giữa hai lần thử
    breaker_failures: int = 3      # từng này lần thất bại ...
    breaker_window_s: float = 900  # ... trong 15 phút thì ngắt cầu chì


POLICY = Policy()


@dataclass(frozen=True)
class Decision:
    incident_id: int
    node: str
    check_code: str
    action: str          # "fix" | "skip"
    why: str


# ---------------------------------------------------------------- hàm thuần
def frozen_nodes(windows) -> frozenset:
    """Các thiết bị đang trong cửa sổ bảo trì. {"*"} nghĩa là tất cả."""
    out = set()
    for w in windows:
        nodes = maintenance.parse_nodes(w["nodes"])
        if nodes == maintenance.ALL:
            return frozenset({maintenance.ALL})
        out |= nodes
    return frozenset(out)


def _age(start: str, now: str) -> float:
    return incidents.duration_s(start, now)


def breaker_open(history, now: str, policy: Policy = POLICY) -> bool:
    """Cầu chì ngắt khi có đủ thất bại gần đây SAU lần reset cuối."""
    rows = sorted(history, key=lambda r: r["id"])
    last_reset = max((r["id"] for r in rows if r["outcome"] == "reset"), default=0)
    fails = [r for r in rows if r["id"] > last_reset and r["outcome"] in FAILURES
             and _age(r["started_at"], now) <= policy.breaker_window_s]
    return len(fails) >= policy.breaker_failures


def _busy(history, now: str) -> bool:
    return any(r["outcome"] is None and _age(r["started_at"], now) <= STALE_RUNNING_S
               for r in history)


def is_busy(history, now: str) -> bool:
    """Có một lần tự sửa đang chạy không (để các bên khác, như giao diện web, tránh chạy chồng)."""
    return _busy(history, now)


def decide(open_rows, history, now: str, policy: Policy = POLICY,
           frozen=frozenset()) -> list[Decision]:
    """Hàm thuần. Một Decision cho mỗi sự cố GỐC đang mở (triệu chứng không được liệt kê)."""
    roots = sorted((r for r in open_rows if r.get("root_id") is None), key=lambda r: r["id"])
    breaker = breaker_open(history, now, policy)
    busy = _busy(history, now)
    out = []
    for r in roots:
        node, code, kind = r["node"], r["check_code"], r["kind"]
        tries = [h for h in history if h["node"] == node and h["check_code"] == code
                 and h["outcome"] != "reset"
                 and _age(h["started_at"], now) <= policy.attempt_window_s]
        age = _age(r["opened_at"], now)
        last_gap = min((_age(h["started_at"], now) for h in tries), default=None)

        def d(action, why):
            return Decision(r["id"], node, code, action, why)

        if (code, kind) not in REMEDIABLE:
            out.append(d("skip", f"không thuộc danh sách tự sửa ({code}/{kind})"))
        elif breaker:
            out.append(d("skip", "cầu chì đang ngắt, chờ người xem (--reset-breaker)"))
        elif busy:
            out.append(d("skip", "đang có một lần sửa khác chạy"))
        elif maintenance.ALL in frozen or node in frozen:
            out.append(d("skip", "đang bảo trì, không can thiệp"))
        elif age < policy.min_age_s:
            out.append(d("skip", f"mới mở {age:.0f} giây, chờ đủ {policy.min_age_s:.0f} giây"))
        elif len(tries) >= policy.max_attempts:
            out.append(d("skip", f"đã thử {len(tries)} lần trong "
                                 f"{policy.attempt_window_s / 60:.0f} phút, chờ người xem"))
        elif last_gap is not None and last_gap < policy.backoff_s:
            out.append(d("skip", f"giãn cách giữa hai lần thử, còn "
                                 f"{policy.backoff_s - last_gap:.0f} giây"))
        else:
            out.append(d("fix", f"đủ điều kiện (mở {age:.0f} giây, đã thử {len(tries)} lần)"))
    # mỗi lần chỉ sửa một việc: giữ việc đầu tiên, các việc khác chờ lượt
    seen = False
    for i, x in enumerate(out):
        if x.action == "fix":
            if seen:
                out[i] = Decision(x.incident_id, x.node, x.check_code, "skip",
                                  "chờ lượt (mỗi lần chỉ sửa một việc)")
            seen = True
    return out


# ---------------------------------------------------------------- cơ sở dữ liệu
def outcome_of(exit_code, closed: bool) -> str:
    """Hàm thuần: mã thoát của apply (và sự cố đã đóng chưa) -> kết quả của lần sửa."""
    if exit_code == 0:
        return "fixed" if closed else "ineffective"
    return OUTCOME_BY_CODE.get(exit_code, "failed")


def parse_audit_id(text: str):
    found = re.findall(r"Nhật ký kiểm toán: #(\d+)", text or "")
    return int(found[-1]) if found else None


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(remediations)")}
    for col, decl in NEW_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE remediations ADD COLUMN {col} {decl}")
    conn.commit()


def start(conn, incident_id: int, node: str, check_code: str, at: str) -> int:
    cur = conn.execute("INSERT INTO remediations (incident_id, node, check_code, started_at) "
                       "VALUES (?, ?, ?, ?)", (incident_id, node, check_code, at))
    conn.commit()
    return cur.lastrowid


def finish(conn, rid: int, at: str, outcome: str, exit_code, detail: str = "",
           audit_id=None) -> None:
    conn.execute("UPDATE remediations SET finished_at = ?, outcome = ?, exit_code = ?, "
                 "detail = ?, audit_id = ? WHERE id = ?",
                 (at, outcome, exit_code, detail, audit_id, rid))
    conn.commit()


def list_history(conn, limit: int = 200) -> list[dict]:
    cols = ("id", "incident_id", "node", "check_code", "started_at", "finished_at",
            "outcome", "exit_code", "detail", "audit_id")
    rows = conn.execute("SELECT id, incident_id, node, check_code, started_at, finished_at, "
                        "outcome, exit_code, detail, audit_id FROM remediations "
                        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(zip(cols, r)) for r in reversed(rows)]


def reset_breaker(conn, at: str) -> int:
    rid = start(conn, 0, "*", "breaker", at)
    finish(conn, rid, at, "reset", 0, "người mở lại cầu chì")
    return rid


# ---------------------------------------------------------------- thực thi
def run_apply(node: str, db_path: str, timeout_s: float = APPLY_TIMEOUT_S):
    """Chạy `apply --node X --yes` như một tiến trình riêng, nên được hưởng đủ cơ chế an
    toàn của apply: kiểm chứng, tự hoàn tác, nhật ký kiểm toán, cửa sổ bảo trì.
    Trả về (mã thoát, đầu ra)."""
    env = dict(os.environ, **{"NETAUTO_ACTOR": ACTOR})
    cp = subprocess.run(
        [sys.executable, "-m", "netauto.apply", "--node", node, "--yes", "--db", db_path],
        capture_output=True, text=True, timeout=timeout_s, env=env)
    return cp.returncode, cp.stdout + cp.stderr


def incident_closed(conn, incident_id: int) -> bool:
    row = conn.execute("SELECT closed_at FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    return bool(row and row[0])


def execute(conn, decision, db_path: str, *, run=run_apply, now=maintenance.now,
            sleep=time.sleep, wait_s: float = WAIT_CLOSED_S, poll_s: float = POLL_S) -> dict:
    """Thực hiện MỘT quyết định "fix". Ghi bản ghi TRƯỚC khi hành động (ghi trước, làm sau),
    và luôn ghi kết quả dù apply lỗi, quá giờ hay bị ngắt (finally)."""
    rid = start(conn, decision.incident_id, decision.node, decision.check_code, now())
    code, text, outcome = None, "", "failed"
    try:
        try:
            code, text = run(decision.node, db_path)
        except subprocess.TimeoutExpired:
            text = "apply chạy quá lâu nên đã bị dừng (có thể còn dở dang)"
        closed = False
        if code == 0:                    # chỉ chờ monitor đóng sự cố khi apply báo thành công
            t0 = now()
            while True:
                closed = incident_closed(conn, decision.incident_id)
                if closed or incidents.duration_s(t0, now()) >= wait_s:
                    break
                sleep(poll_s)
        outcome = outcome_of(code, closed)
    except BaseException:
        text = (text or "") + " (bị ngắt giữa chừng)"
        raise
    finally:
        finish(conn, rid, now(), outcome, code, text[-OUTPUT_TAIL:], parse_audit_id(text))
    return next(r for r in list_history(conn) if r["id"] == rid)


# ---------------------------------------------------------------- vòng lặp tự chạy (25C)
LOOP_INTERVAL_S = 5


def cycle(conn, db_path: str, *, run=run_apply, now=maintenance.now,
          sleep=time.sleep, policy: Policy = POLICY):
    """Một vòng: nhìn, quyết, sửa tối đa MỘT việc. Trả về (kết quả hoặc None, ghi chú hoặc None).

    Chốt an toàn: monitor không còn sống thì KHÔNG sửa. Lúc đó danh sách sự cố mở có thể
    đã cũ (không ai cập nhật), và sau khi apply sẽ không ai đóng sự cố để xác nhận."""
    runs.ensure_schema(conn)             # DB mới chưa từng chạy monitor vẫn phải hỏi được
    service.ensure_schema(conn)
    at = now()
    state = service.status(conn, at)["state"]
    if state != "alive":
        return None, f"Monitor không chạy (trạng thái: {state}) nên tạm dừng sửa."
    history = list_history(conn)
    frozen = frozen_nodes(maintenance.windows_at(conn, at))
    decisions = decide(incidents.open_incidents(conn), history, at, policy, frozen)
    fixes = [x for x in decisions if x.action == "fix"]
    if not fixes:
        return None, None
    return execute(conn, fixes[0], db_path, run=run, now=now, sleep=sleep), None


def _log(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def run_loop(db_path: str, interval: float = LOOP_INTERVAL_S, count: int = 0, *,
             run=run_apply, sleep=time.sleep, should_stop=lambda: False) -> int:
    """Chạy vô hạn (count = 0) hoặc đúng `count` vòng. Giữ khóa riêng của remediation.
    Mỗi vòng mở kết nối mới; lỗi bất ngờ ở một vòng được ghi lại rồi đi tiếp, vì một
    dịch vụ nền không được chết chỉ vì một lần hỏng."""
    with service.InstanceLock(db_path, "remediation"):
        conn = incidents.connect(db_path)
        maintenance.ensure_schema(conn)
        ensure_schema(conn)
        service.ensure_schema(conn)
        conn.close()
        _log(f"Remediation tự chạy, chu kỳ {interval:g} giây (PID {os.getpid()}).")
        last_note, n = None, 0
        while not should_stop():
            n += 1
            try:
                conn = incidents.connect(db_path)
                try:
                    res, note = cycle(conn, db_path, run=run, sleep=sleep)
                finally:
                    conn.close()
                if note != last_note and note:
                    _log(note)
                last_note = note
                if res:
                    _log(f"#{res['incident_id']} ({res['node']}): "
                         f"{OUTCOME_TEXT.get(res['outcome'], res['outcome'])}"
                         + (f", kiểm toán #{res['audit_id']}" if res["audit_id"] else ""))
            except Exception as e:          # noqa: BLE001 - dịch vụ nền phải sống tiếp
                _log(f"Lỗi ở vòng {n}: {type(e).__name__}: {e}")
            if count and n >= count:
                break
            if not should_stop():
                sleep(interval)
        _log("Remediation dừng.")
    return 0


# ---------------------------------------------------------------- dòng lệnh
def print_history(conn, limit: int) -> None:
    rows = list_history(conn, limit)
    if not rows:
        print("(chưa có lần sửa nào)")
        return
    print(f"{'ID':<5}{'LÚC':<9}{'SỰ CỐ':<8}{'THIẾT BỊ':<10}{'KẾT QUẢ':<13}{'MÃ':<4}{'KIỂM TOÁN':<10}")
    for r in rows:
        at = r["started_at"][11:19]
        audit = f"#{r['audit_id']}" if r["audit_id"] else "-"
        code = "-" if r["exit_code"] is None else r["exit_code"]
        print(f"{r['id']:<5}{at:<9}#{r['incident_id']:<7}{r['node']:<10}"
              f"{(r['outcome'] or 'đang chạy'):<13}{code!s:<4}{audit:<10}")


def _main_loop(args, run, sleep) -> int:
    """Dừng mềm: SIGINT/SIGTERM chỉ đặt cờ, nên một lần sửa đang chạy được làm xong
    (apply bị giết giữa chừng thì không còn cơ hội tự hoàn tác)."""
    stop = []
    old = {s: signal.signal(s, lambda *_: stop.append(1))
           for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        return run_loop(args.db, args.interval, args.count, run=run, sleep=sleep,
                        should_stop=lambda: bool(stop))
    except service.AlreadyRunning as e:
        print(f"Remediation khác đang chạy trên DB này (PID {e}). Không chạy hai bản.",
              file=sys.stderr)
        return 2
    finally:
        for s, h in old.items():
            signal.signal(s, h)


def main(argv=None, *, run=run_apply, sleep=time.sleep) -> int:
    ap = argparse.ArgumentParser(prog="netauto.remediation")
    ap.add_argument("--db", default=db.DB_PATH)
    ap.add_argument("--reset-breaker", action="store_true", help="mở lại cầu chì đã ngắt")
    ap.add_argument("--yes", action="store_true", help="thật sự sửa (mặc định chỉ xem)")
    ap.add_argument("--loop", action="store_true",
                    help="chạy liên tục và tự sửa (không cần --yes); dừng bằng Ctrl+C")
    ap.add_argument("--interval", type=float, default=LOOP_INTERVAL_S,
                    help="giây nghỉ giữa hai vòng (với --loop)")
    ap.add_argument("--count", type=int, default=0, help="số vòng tối đa (0 = mãi mãi)")
    ap.add_argument("--history", type=int, nargs="?", const=10, metavar="N",
                    help="xem N lần sửa gần nhất (mặc định 10)")
    args = ap.parse_args(argv)
    if args.loop:
        return _main_loop(args, run, sleep)
    conn = incidents.connect(args.db)
    maintenance.ensure_schema(conn)
    ensure_schema(conn)
    at = maintenance.now()
    if args.reset_breaker:
        print(f"Đã mở lại cầu chì (bản ghi #{reset_breaker(conn, at)}).")
        conn.close()
        return 0
    if args.history is not None:
        print_history(conn, args.history)
        conn.close()
        return 0
    p = POLICY
    history = list_history(conn)
    frozen = frozen_nodes(maintenance.windows_at(conn, at))
    decisions = decide(incidents.open_incidents(conn), history, at, p, frozen)
    print(f"Chính sách: chờ ≥ {p.min_age_s:.0f} giây; tối đa {p.max_attempts} lần/"
          f"{p.attempt_window_s / 60:.0f} phút; giãn cách {p.backoff_s:.0f} giây; "
          f"cầu chì ngắt sau {p.breaker_failures} thất bại/{p.breaker_window_s / 60:.0f} phút")
    print("Cầu chì: " + ("NGẮT" if breaker_open(history, at, p) else "đóng (bình thường)"))
    if not decisions:
        print("Không có sự cố gốc nào đang mở.")
    for x in decisions:
        mark = "SẼ SỬA " if x.action == "fix" else "bỏ qua "
        print(f"  #{x.incident_id:<5} {x.node:<8} {x.check_code:<20} {mark} {x.why}")
    fixes = [x for x in decisions if x.action == "fix"]
    code = 0
    if not args.yes:
        print("\n(Chế độ xem: chưa sửa gì. Thêm --yes để sửa thật.)")
    elif not fixes:
        print("\nKhông có gì cần sửa lúc này.")
    else:
        x = fixes[0]
        print(f"\nĐANG SỬA #{x.incident_id} ({x.node}, {x.check_code}): "
              f"chạy apply --node {x.node} --yes ...")
        res = execute(conn, x, args.db, run=run, sleep=sleep)
        print(f"Kết quả: {OUTCOME_TEXT.get(res['outcome'], res['outcome'])}")
        print(f"  apply mã {res['exit_code']}"
              + (f", nhật ký kiểm toán #{res['audit_id']}" if res["audit_id"] else ""))
        if res["outcome"] != "fixed":
            for line in res["detail"].strip().splitlines()[-4:]:
                print(f"  | {line}")
            code = 1
    conn.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())