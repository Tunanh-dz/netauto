"""Tự khắc phục, phần quyết định (Ngày 25A). Chưa sửa gì: chỉ trả lời "NÊN làm gì".

Xem quyết định hiện tại:  python3 -m netauto.remediation [--db f]
Mở lại cầu chì đã ngắt:   python3 -m netauto.remediation --reset-breaker

Chính sách là hàm thuần decide(): cùng đầu vào thì cùng quyết định, test được mà không
cần lab. Chỉ sửa sự cố GỐC thuộc danh sách REMEDIABLE, và chỉ khi mọi điều kiện an toàn
đều đạt (đủ tuổi, không bảo trì, chưa quá số lần thử, cầu chì đóng, không có việc khác
đang chạy). Mỗi lần chỉ sửa MỘT việc.
"""
import argparse
from dataclasses import dataclass

from netauto import db, incidents, maintenance

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
    detail      TEXT    NOT NULL DEFAULT ''
);
"""

# Chỉ những lỗi đã được kiểm chứng là `plan` thấy và `apply` sửa được (TN18, Ngày 25).
# Mở rộng từng bước, mỗi bước đo trước. "wrong" cố ý KHÔNG có: sửa sai địa chỉ cần gỡ
# địa chỉ cũ, tức --prune, mà tự động thì không được gỡ.
REMEDIABLE = frozenset({
    ("router.subinterface", "down"), ("router.subinterface", "missing"),
    ("router.uplink", "down"), ("router.uplink", "missing"),
})
FAILURES = frozenset({"failed", "rolled_back", "refused", "ineffective"})
STALE_RUNNING_S = 300      # lần sửa "đang chạy" quá 5 phút coi như đã chết, không chặn mãi


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
def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def start(conn, incident_id: int, node: str, check_code: str, at: str) -> int:
    cur = conn.execute("INSERT INTO remediations (incident_id, node, check_code, started_at) "
                       "VALUES (?, ?, ?, ?)", (incident_id, node, check_code, at))
    conn.commit()
    return cur.lastrowid


def finish(conn, rid: int, at: str, outcome: str, exit_code, detail: str = "") -> None:
    conn.execute("UPDATE remediations SET finished_at = ?, outcome = ?, exit_code = ?, "
                 "detail = ? WHERE id = ?", (at, outcome, exit_code, detail, rid))
    conn.commit()


def list_history(conn, limit: int = 200) -> list[dict]:
    cols = ("id", "incident_id", "node", "check_code", "started_at", "finished_at",
            "outcome", "exit_code", "detail")
    rows = conn.execute("SELECT id, incident_id, node, check_code, started_at, finished_at, "
                        "outcome, exit_code, detail FROM remediations "
                        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(zip(cols, r)) for r in reversed(rows)]


def reset_breaker(conn, at: str) -> int:
    rid = start(conn, 0, "*", "breaker", at)
    finish(conn, rid, at, "reset", 0, "người mở lại cầu chì")
    return rid


# ---------------------------------------------------------------- dòng lệnh
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="netauto.remediation")
    ap.add_argument("--db", default=db.DB_PATH)
    ap.add_argument("--reset-breaker", action="store_true", help="mở lại cầu chì đã ngắt")
    args = ap.parse_args(argv)
    conn = incidents.connect(args.db)
    maintenance.ensure_schema(conn)
    ensure_schema(conn)
    at = maintenance.now()
    if args.reset_breaker:
        print(f"Đã mở lại cầu chì (bản ghi #{reset_breaker(conn, at)}).")
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
    print("\n(Chế độ xem: chưa sửa gì. Bước 25B sẽ thêm --yes để sửa thật.)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())