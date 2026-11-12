"""Chỉ số sức khỏe mạng (0 đến 100) từ các finding và kết quả đo ping.

Chạy: python3 -m netauto.health [--history N]
"""
import argparse
from dataclasses import asdict

from netauto import db, probe, timefmt
from netauto.check import check_snapshot
from netauto.collector import collect_all
from netauto.intent import INTENT_PATH, load_intent

# Quy ước tính điểm (do nhóm đồ án chọn, ghi rõ trong báo cáo)
W_CRITICAL = 20          # mỗi lỗi cấu hình nghiêm trọng
W_WARNING = 5            # mỗi cảnh báo
W_FLOW_DOWN = 15         # mỗi luồng mất kết nối
W_FLOW_DEGRADED = 5      # mỗi luồng chập chờn hoặc chậm
RTT_DEGRADED_MS = 10.0   # RTT trung bình vượt ngưỡng này thì coi là chậm

SCHEMA = """
CREATE TABLE IF NOT EXISTS health (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id    INTEGER NOT NULL,
    at             TEXT    NOT NULL,
    score          INTEGER NOT NULL,
    grade          TEXT    NOT NULL,
    critical       INTEGER NOT NULL,
    warning        INTEGER NOT NULL,
    flows_down     INTEGER NOT NULL,
    flows_degraded INTEGER NOT NULL
);
"""


def flow_state(p) -> str:
    if p.received == 0:
        return "down"
    if p.loss_pct > 0 or (p.rtt_avg is not None and p.rtt_avg > RTT_DEGRADED_MS):
        return "degraded"
    return "ok"


def grade_of(value: int) -> str:
    if value >= 90:
        return "TỐT"
    if value >= 70:
        return "CHÚ Ý"
    return "NGUY HIỂM"


def score(findings, probes) -> dict:
    """Hàm thuần: nhận finding và kết quả ping, trả về điểm và chi tiết."""
    crit = sum(1 for f in findings if f.severity == "critical")
    warn = sum(1 for f in findings if f.severity == "warning")
    states = [flow_state(p) for p in probes]
    down = states.count("down")
    degraded = states.count("degraded")

    parts = [
        (crit * W_CRITICAL, f"{crit} lỗi nghiêm trọng × {W_CRITICAL}"),
        (warn * W_WARNING, f"{warn} cảnh báo × {W_WARNING}"),
        (down * W_FLOW_DOWN, f"{down} luồng mất kết nối × {W_FLOW_DOWN}"),
        (degraded * W_FLOW_DEGRADED, f"{degraded} luồng chập chờn/chậm × {W_FLOW_DEGRADED}"),
    ]
    value = max(0, 100 - sum(points for points, _ in parts))
    return {"score": value, "grade": grade_of(value), "critical": crit, "warning": warn,
            "flows_down": down, "flows_degraded": degraded,
            "penalties": [(pts, text) for pts, text in parts if pts]}


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def save_health(conn, snapshot_id: int, at: str, h: dict) -> None:
    conn.execute(
        "INSERT INTO health (snapshot_id, at, score, grade, critical, warning, "
        "flows_down, flows_degraded) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (snapshot_id, at, h["score"], h["grade"], h["critical"], h["warning"],
         h["flows_down"], h["flows_degraded"]))
    conn.commit()


def list_health(conn, limit: int = 20):
    return conn.execute(
        "SELECT at, score, grade, critical, warning, flows_down, flows_degraded "
        "FROM health ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def print_probes(results) -> None:
    print(f"{'LUỒNG':<24}{'MẤT GÓI':<10}{'RTT TB':<11}TRẠNG THÁI")
    for p in results:
        rtt = f"{p.rtt_avg:.2f} ms" if p.rtt_avg is not None else "-"
        flow = f"{p.src} → {p.dst}"
        print(f"{flow:<24}{p.loss_pct:>5.0f}%    {rtt:<11}{flow_state(p).upper()}")
        if p.error:
            print(f"    ! {p.error}")


def main():
    ap = argparse.ArgumentParser(prog="netauto.health")
    ap.add_argument("--intent", default=INTENT_PATH)
    ap.add_argument("--db", default=db.DB_PATH)
    ap.add_argument("--history", type=int, metavar="N", help="xem N điểm gần nhất trong DB")
    args = ap.parse_args()

    if args.history:
        conn = db.connect(args.db)
        ensure_schema(conn)
        print(f"{'THỜI ĐIỂM':<16}{'ĐIỂM':<6}{'MỨC':<11}NGHIÊM TRỌNG  CẢNH BÁO  LUỒNG DOWN  LUỒNG CHẬM")
        for at, sc, grade, crit, warn, down, deg in list_health(conn, args.history):
            print(f"{timefmt.stamp(at):<16}{sc:<6}{grade:<11}{crit:<14}{warn:<10}{down:<13}{deg}")
        conn.close()
        return

    intent = load_intent(args.intent)
    findings = check_snapshot(asdict(collect_all()), intent)
    results = probe.run_probes(intent)

    print_probes(results)
    h = score(findings, results)
    print(f"\nĐiểm sức khỏe: {h['score']}/100 ({h['grade']})")
    for points, text in h["penalties"]:
        print(f"  trừ {points}: {text}")
    if findings:
        print("\nLỗi cấu hình:")
        for f in findings:
            print(f"  [{f.severity}] {f.node}: {f.message}")


if __name__ == "__main__":
    main()