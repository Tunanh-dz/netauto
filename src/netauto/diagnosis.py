"""Chẩn đoán nguyên nhân gốc ở mức hạ tầng (hàm thuần, không đụng DB).

Có hai kiểu lỗi mà người vận hành cần phân biệt ngay:
  - lỗi CẤU HÌNH (thiếu eth1.10, route sai...): chạy lại apply là sửa được, bộ tự sửa làm việc này;
  - lỗi HẠ TẦNG (thiếu hẳn cổng vật lý eth1 của host hay cổng uplink của router): lệnh cấu hình không tạo
    lại được cổng, phải dựng lại lab (containerlab redeploy) rồi mới áp dụng cấu hình.

Hay gặp nhất khi máy chủ khởi động lại: container lên nhưng các dây nối ảo của Containerlab mất, kéo theo
hàng chục lỗi cấu hình ở các thiết bị đó. Hạn chế: chỉ nhận ra cổng bị MẤT (kind "missing"), cổng còn
nhưng đang tắt ("down") vẫn coi là lỗi cấu hình vì bật lại được bằng lệnh.
"""

INFRA = frozenset({("host.interface", "missing"), ("router.uplink", "missing")})


def is_infra(check_code, kind) -> bool:
    return (check_code, kind) in INFRA


def diagnose(rows: list[dict]) -> dict | None:
    """rows: các sự cố ĐANG MỞ (dict có node, check_code, kind).

    Không có lỗi hạ tầng thì trả None. Ngược lại: {"code", "nodes" (thiết bị mất cổng, đã sắp xếp),
    "ports" (số cổng mất), "others" (số sự cố khác đang mở, nhiều khả năng là hệ quả)}.
    """
    infra = [r for r in rows if is_infra(r["check_code"], r["kind"])]
    if not infra:
        return None
    return {"code": "missing_ports", "nodes": sorted({r["node"] for r in infra}),
            "ports": len(infra), "others": len(rows) - len(infra)}


def headline(monitor_state: str, event_severities: list, open_rows: list, maintenance, breaker_open) -> dict:
    """Một câu trả lời cho câu hỏi đầu tiên của người vận hành: "mạng có ổn không?" (hàm thuần).

    monitor_state: alive | stopped | dead | never (hoặc giá trị lạ, coi như không chạy).
    event_severities: mức độ của từng SỰ KIỆN đang mở (gốc, không đếm triệu chứng) để số khớp tab Sự cố.
    open_rows: mọi sự cố đang mở, kể cả triệu chứng, dùng để nhận ra lỗi hạ tầng.

    Thứ tự ưu tiên của "code": giám sát không chạy (mọi con số đều có thể cũ) > lỗi hạ tầng (phải dựng lại
    lab, sửa cấu hình vô ích) > có sự cố > bình thường. "tone": ok | warn | bad để tô màu.
    """
    critical = sum(1 for s in event_severities if s == "critical")
    warning = sum(1 for s in event_severities if s == "warning")
    infra = diagnose(open_rows)
    infra_nodes = infra["nodes"] if infra else []
    if monitor_state != "alive":
        code, tone = "monitor_off", "bad" if monitor_state == "dead" else "warn"
    elif infra:
        code, tone = "infra", "bad"
    elif event_severities:
        code, tone = "incidents", "bad" if critical else "warn"
    else:
        code, tone = "ok", "ok"
    return {"code": code, "tone": tone, "monitor": monitor_state, "open": len(event_severities),
            "critical": critical, "warning": warning, "infra_nodes": infra_nodes,
            "maintenance": bool(maintenance), "breaker_open": bool(breaker_open)}
