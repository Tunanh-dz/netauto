"""Biến kết quả đo ping thành finding để hệ thống sự cố quản lý như lỗi cấu hình.

Mở nhanh, đóng chậm: luồng 'chập chờn' chỉ hết khi sạch trong cả WINDOW vòng,
tránh sự cố mở rồi đóng liên tục khi mất gói ngẫu nhiên.
"""
from netauto.check import Finding
from netauto.health import flow_state

WINDOW = 3   # số vòng gần nhất được nhớ cho mỗi luồng


def push(history: dict, results) -> None:
    """Ghi trạng thái vòng hiện tại vào lịch sử (chỉ giữ WINDOW vòng cuối)."""
    for p in results:
        states = history.setdefault((p.src, p.dst), [])
        states.append(flow_state(p))
        del states[:-WINDOW]

def _scope(p, intent) -> tuple:
    """VLAN của hai đầu luồng (rỗng nếu không biết)."""
    if not intent:
        return ()
    hosts = intent.get("hosts", {})
    return tuple(sorted({hosts[h]["vlan"] for h in (p.src, p.dst) if h in hosts}))


def perf_findings(results, history, intent=None) -> list[Finding]:
    """Hàm thuần. Thông điệp cố ý KHÔNG chứa số liệu để khóa nhận dạng ổn định."""
    out = []
    for p in results:
        states = history.get((p.src, p.dst), [])
        name = f"luồng {p.src} → {p.dst}"
        subject, scope = f"{p.src}→{p.dst}", _scope(p, intent)
        if states and states[-1] == "down":
            out.append(Finding(p.src, "perf.flow", "critical", "down",
                               f"{name} mất kết nối", subject, scope))
        elif "degraded" in states:
            out.append(Finding(p.src, "perf.flow", "warning", "degraded",
                               f"{name} chập chờn hoặc chậm", subject, scope))
    return out