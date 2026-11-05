"""Đo hiệu năng bằng ping giữa các host: độ trễ (RTT) và tỉ lệ mất gói."""
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

from netauto.intent import IntentError
from netauto.transport import run

PING_ARGS = ["-c", "5", "-i", "0.2", "-W", "1"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS probes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    at          TEXT    NOT NULL,
    src         TEXT    NOT NULL,
    dst         TEXT    NOT NULL,
    sent        INTEGER NOT NULL,
    received    INTEGER NOT NULL,
    loss_pct    REAL    NOT NULL,
    rtt_min     REAL,
    rtt_avg     REAL,
    rtt_max     REAL,
    error       TEXT    NOT NULL DEFAULT ''
);
"""

# "5 packets transmitted, 5 received" (iputils) hoặc "... 5 packets received" (busybox)
_COUNTS = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received")
# "rtt min/avg/max/mdev = a/b/c/d ms" hoặc "round-trip min/avg/max = a/b/c ms"
_RTT = re.compile(r"(?:rtt|round-trip) min/avg/max(?:/\w+)? = ([\d.]+)/([\d.]+)/([\d.]+)")


@dataclass
class ProbeResult:
    src: str
    dst: str
    dst_ip: str
    sent: int
    received: int
    rtt_min: Optional[float] = None
    rtt_avg: Optional[float] = None
    rtt_max: Optional[float] = None
    error: str = ""

    @property
    def loss_pct(self) -> float:
        if self.sent == 0:
            return 100.0
        return 100.0 * (self.sent - self.received) / self.sent


def parse_ping(text: str):
    """Trả về (đã gửi, đã nhận, rtt_min, rtt_avg, rtt_max) hoặc None nếu không đọc được."""
    m = _COUNTS.search(text)
    if not m:
        return None
    sent, received = int(m.group(1)), int(m.group(2))
    r = _RTT.search(text)
    rtt = tuple(float(x) for x in r.groups()) if r else (None, None, None)
    return (sent, received, *rtt)


def flows_from_intent(intent: dict) -> list[tuple[str, str, str]]:
    """Đọc mục probes trong intent, trả về danh sách (nguồn, đích, ip đích)."""
    flows = []
    for item in intent.get("probes", []):
        try:
            src, dst = item["src"], item["dst"]
            intent["hosts"][src]
            dst_ip = intent["hosts"][dst]["ip"].split("/")[0]
        except (KeyError, TypeError):
            raise IntentError(f"probes: mục không hợp lệ hoặc host chưa khai báo: {item!r}")
        flows.append((src, dst, dst_ip))
    return flows


def run_probe(src: str, dst: str, dst_ip: str) -> ProbeResult:
    r = run(src, ["ping", *PING_ARGS, dst_ip], timeout=15)
    stats = parse_ping(r.stdout)   # ping trả mã lỗi khi mất gói, nhưng vẫn in thống kê
    if stats is None:
        err = (r.stderr.strip() or "không đọc được kết quả ping")[:120]
        return ProbeResult(src, dst, dst_ip, 0, 0, error=err)
    return ProbeResult(src, dst, dst_ip, *stats)


def run_probes(intent: dict) -> list[ProbeResult]:
    """Chạy các luồng song song, giữ nguyên thứ tự khai báo."""
    flows = flows_from_intent(intent)
    with ThreadPoolExecutor(max_workers=max(1, len(flows))) as pool:
        return list(pool.map(lambda f: run_probe(*f), flows))


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def save_probes(conn, snapshot_id: int, at: str, results: list[ProbeResult]) -> None:
    for p in results:
        conn.execute(
            "INSERT INTO probes (snapshot_id, at, src, dst, sent, received, loss_pct, "
            "rtt_min, rtt_avg, rtt_max, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (snapshot_id, at, p.src, p.dst, p.sent, p.received, p.loss_pct,
             p.rtt_min, p.rtt_avg, p.rtt_max, p.error))
    conn.commit()