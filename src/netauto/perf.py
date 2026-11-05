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


def perf_findings(results, history) -> list[Finding]:
    """Hàm thuần. Thông điệp cố ý KHÔNG chứa số liệu để khóa nhận dạng ổn định."""
    out = []
    for p in results:
        states = history.get((p.src, p.dst), [])
        name = f"luồng {p.src} → {p.dst}"
        if states and states[-1] == "down":
            out.append(Finding(p.src, "perf.flow", "critical", "down",
                               f"{name} mất kết nối"))
        elif "degraded" in states:
            out.append(Finding(p.src, "perf.flow", "warning", "degraded",
                               f"{name} chập chờn hoặc chậm"))
    return out