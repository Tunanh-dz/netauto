"""Sổ ghi "đáp án": mỗi lần cố ý gây lỗi để đo hệ thống.

incidents là BÀI LÀM của hệ thống (nó thấy gì, lúc nào); injections là ĐÁP ÁN (ta đã gây
lỗi gì, lúc nào). So hai bên để tính thời gian phát hiện và tỉ lệ bỏ sót.

Xem:  python3 -m netauto.injections [--limit N] [--db FILE]
"""
import argparse
import statistics
from dataclasses import dataclass

from netauto import db, incidents, timefmt

SCHEMA = """
CREATE TABLE IF NOT EXISTS injections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fault           TEXT    NOT NULL,       -- tên lỗi trong FAULTS
    node            TEXT    NOT NULL,
    expect_check    TEXT    NOT NULL,       -- mã kiểm tra mà monitor phải báo
    injected_at     TEXT    NOT NULL,       -- sự thật: lỗi bắt đầu lúc này
    before_snapshot INTEGER NOT NULL,       -- id snapshot mới nhất ngay trước khi gây lỗi
    restored_at     TEXT,
    status          TEXT    NOT NULL DEFAULT 'running',   -- running|detected|missed|invalid
    incident_id     INTEGER,                -- sự cố khớp (nếu được phát hiện)
    detected_at     TEXT                    -- lúc monitor mở sự cố đó
);
"""


@dataclass(frozen=True)
class Fault:
    name: str
    node: str
    inject: tuple          # lệnh chạy trong container để GÂY lỗi
    restore: tuple         # lệnh để KHÔI PHỤC
    expect_check: str      # sự cố monitor phải mở: (node, expect_check)
    desc: str


FAULTS = {f.name: f for f in [
    Fault("r1-subif-down", "r1", ("ip", "link", "set", "eth1.10", "down"),
          ("ip", "link", "set", "eth1.10", "up"), "router.subinterface",
          "r1 tắt subinterface eth1.10 (gateway VLAN 10)"),
    Fault("r2-subif-down", "r2", ("ip", "link", "set", "eth1.20", "down"),
          ("ip", "link", "set", "eth1.20", "up"), "router.subinterface",
          "r2 tắt subinterface eth1.20 (gateway VLAN 20)"),
    Fault("r1-uplink-down", "r1", ("ip", "link", "set", "eth2", "down"),
          ("ip", "link", "set", "eth2", "up"), "router.uplink",
          "r1 tắt cổng uplink eth2 (liên kết r1-r2)"),
]}


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def start_injection(conn, fault: Fault, at: str, before_snapshot: int) -> int:
    cur = conn.execute(
        "INSERT INTO injections (fault, node, expect_check, injected_at, before_snapshot) "
        "VALUES (?, ?, ?, ?, ?)", (fault.name, fault.node, fault.expect_check, at, before_snapshot))
    conn.commit()
    return cur.lastrowid


def mark_restored(conn, inj_id: int, at: str) -> None:
    conn.execute("UPDATE injections SET restored_at = ? WHERE id = ?", (at, inj_id))
    conn.commit()


def mark_status(conn, inj_id: int, status: str, incident=None) -> None:
    conn.execute("UPDATE injections SET status = ?, incident_id = ?, detected_at = ? WHERE id = ?",
                 (status, incident["id"] if incident else None,
                  incident["opened_at"] if incident else None, inj_id))
    conn.commit()


def find_detection(inj: dict, rows: list[dict]):
    """Hàm thuần. Sự cố khớp lỗi đã gây: cùng thiết bị, cùng mã kiểm tra, và mở ở một snapshot
    SAU snapshot cuối trước khi gây lỗi. Có nhiều sự cố khớp thì lấy cái mở sớm nhất."""
    hits = [r for r in rows
            if r["node"] == inj["node"] and r["check_code"] == inj["expect_check"]
            and r["opened_snapshot"] > inj["before_snapshot"]]
    return min(hits, key=lambda r: (r["opened_snapshot"], r["id"])) if hits else None


def detection_seconds(inj: dict, incident: dict) -> float:
    """Hàm thuần. Thời gian phát hiện (giây). Giờ snapshot chỉ chính xác đến 1 giây nên
    kết quả có thể lệch dưới 1 giây; không bao giờ trả số âm."""
    return max(0.0, incidents.duration_s(inj["injected_at"], incident["opened_at"]))


def summarize(rows: list[dict]) -> dict:
    """Hàm thuần. rows: các dòng injections đã có kết quả (status khác 'running').
    Lần 'invalid' (điều kiện thí nghiệm không sạch) không tính vào tỉ lệ."""
    valid = [r for r in rows if r["status"] in ("detected", "missed")]
    times = [r["seconds"] for r in valid if r["status"] == "detected"]
    out = {"runs": len(valid), "detected": len(times), "missed": len(valid) - len(times),
           "invalid": sum(1 for r in rows if r["status"] == "invalid")}
    out["rate"] = len(times) / len(valid) if valid else None
    if times:
        out.update(min=min(times), max=max(times), mean=statistics.mean(times),
                   median=statistics.median(times))
    return out


COLS = ("id", "fault", "node", "expect_check", "injected_at", "before_snapshot",
        "restored_at", "status", "incident_id", "detected_at")


def list_injections(conn, limit: int = 20) -> list[dict]:
    rows = conn.execute(f"SELECT {', '.join(COLS)} FROM injections ORDER BY id DESC LIMIT ?",
                        (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(zip(COLS, r))
        d["seconds"] = (detection_seconds(d, {"opened_at": d["detected_at"]})
                        if d["detected_at"] else None)
        out.append(d)
    return out


def print_table(rows: list[dict]) -> None:
    print(f"{'ID':<4}{'LỖI':<18}{'GÂY LÚC':<16}{'KẾT QUẢ':<12}{'PHÁT HIỆN SAU':<15}SỰ CỐ")
    for r in reversed(rows):
        took = f"{r['seconds']:.1f} giây" if r["seconds"] is not None else "-"
        inc = f"#{r['incident_id']}" if r["incident_id"] else "-"
        print(f"{r['id']:<4}{r['fault']:<18}{timefmt.stamp(r['injected_at']):<16}"
              f"{r['status']:<12}{took:<15}{inc}")


def print_summary(s: dict) -> None:
    if not s["runs"]:
        print("(chưa có lần đo hợp lệ nào)")
        return
    print(f"\nTổng {s['runs']} lần: phát hiện {s['detected']}, bỏ sót {s['missed']} "
          f"(tỉ lệ phát hiện {s['rate']:.0%})" + (f"; {s['invalid']} lần không hợp lệ" if s["invalid"] else ""))
    if "mean" in s:
        print(f"Thời gian phát hiện: nhỏ nhất {s['min']:.1f}, trung vị {s['median']:.1f}, "
              f"trung bình {s['mean']:.1f}, lớn nhất {s['max']:.1f} giây (độ phân giải ~1 giây)")


def main():
    ap = argparse.ArgumentParser(prog="netauto.injections")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--db", default=db.DB_PATH)
    args = ap.parse_args()
    conn = db.connect(args.db)
    ensure_schema(conn)
    rows = list_injections(conn, args.limit)
    if not rows:
        print("(chưa có lần gây lỗi nào)")
    else:
        print_table(rows)
        print_summary(summarize([r for r in rows if r["status"] != "running"]))
    conn.close()


if __name__ == "__main__":
    main()