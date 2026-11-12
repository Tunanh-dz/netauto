"""Hiển thị thời gian cho NGƯỜI đọc (hàm thuần).

Nguyên tắc: lưu cho máy, hiển thị cho người.
- Trong CSDL và JSON luôn giữ dạng ISO 8601 (2026-11-12T10:11:17+07:00): không nhầm lẫn,
  có múi giờ, sắp xếp theo chữ là đúng thứ tự thời gian.
- Khi in ra màn hình thì dùng các hàm dưới đây, để giờ:phút:giây đứng đầu và dễ đọc.
Mọi chỗ in thời gian trong dự án nên đi qua module này để trông giống nhau
(giao diện web ở Tuần 6 cũng dùng lại).
"""
from datetime import datetime


def parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def clock(iso) -> str:
    """'10:11:17' (chỉ giờ, dùng khi ngày đã rõ từ ngữ cảnh)."""
    return f"{parse(iso):%H:%M:%S}" if iso else "-"


def day(iso) -> str:
    """'12/11/2026'."""
    return f"{parse(iso):%d/%m/%Y}" if iso else "-"


def stamp(iso) -> str:
    """'10:11:17 12/11' (gọn, cho cột trong bảng)."""
    return f"{parse(iso):%H:%M:%S %d/%m}" if iso else "-"


def full(iso) -> str:
    """'10:11:17 12/11/2026' (đầy đủ, cho câu đứng riêng)."""
    return f"{parse(iso):%H:%M:%S %d/%m/%Y}" if iso else "-"


def span(a: str, b: str) -> str:
    """Hai mốc thời gian. Cùng ngày thì chỉ ghi ngày một lần."""
    ta, tb = parse(a), parse(b)
    if ta.date() == tb.date():
        return f"{ta:%H:%M:%S} → {tb:%H:%M:%S} ngày {tb:%d/%m/%Y}"
    return f"{ta:%H:%M:%S %d/%m} → {tb:%H:%M:%S %d/%m/%Y}"
