"""Thao tác GHI từ giao diện web: xem trước kế hoạch, áp dụng, mở/đóng cửa sổ bảo trì.

Nguyên tắc: web không có đường tắt. Mọi thay đổi đi qua đúng các đường đã có
(`netauto.plan`, `netauto.apply` chạy như tiến trình riêng, `maintenance`), nên được hưởng đủ
kiểm chứng, hoàn tác và nhật ký kiểm toán; người thực hiện được ghi là "netauto-web".
Lệnh chạy bằng danh sách tham số (không qua shell), và tên thiết bị được kiểm tra trước.
"""
import contextvars
import os
import subprocess
import sys

from netauto import audit, maintenance, remediation
from netauto.intent import INTENT_PATH

ACTOR = "netauto-web"
# Người thực hiện của yêu cầu đang xử lý ("web:<tên>" khi đã đăng nhập). Mỗi yêu cầu chạy trong ngữ cảnh riêng.
current_actor = contextvars.ContextVar("netauto_actor", default=ACTOR)
PLAN_TIMEOUT_S = 90
APPLY_TIMEOUT_S = 300
REASON_MAX = 200
TTL_MIN_S, TTL_MAX_S = 30, 3600

MEANING = {
    "plan": {0: "Mạng đúng như intent, không có gì để làm.",
             2: "Có thay đổi đang chờ áp dụng."},
    "apply": {0: "Xong (hoặc không có gì để làm).",
              1: "Có bước lỗi hoặc còn lệch (không hoàn tác).",
              2: "Còn thay đổi chờ áp dụng.",
              3: "Bị từ chối: kế hoạch có mục CHẶN (lỗi hạ tầng).",
              4: "Đã hoàn tác về trạng thái trước đó.",
              5: "Bị từ chối: không ghi được nhật ký kiểm toán, chưa thay đổi gì."},
}


class Busy(Exception):
    """Đang có một lần tự sửa chạy apply: không chạy chồng."""


def meaning(kind: str, code: int) -> str:
    return MEANING[kind].get(code, f"mã thoát lạ ({code}), xem phần đầu ra")


def check_node(node, known) -> str | None:
    if node is None:
        return None
    if node not in known:
        raise ValueError(f"Không có thiết bị '{node}'.")
    return node


def _run(args: list[str], timeout_s: float):
    env = dict(os.environ, NETAUTO_ACTOR=current_actor.get())
    cp = subprocess.run([sys.executable, "-m", *args], capture_output=True, text=True,
                        timeout=timeout_s, env=env)
    return cp.returncode, cp.stdout + cp.stderr


def run_plan(node, db_path: str, timeout_s: float = PLAN_TIMEOUT_S):
    """Xem trước: chỉ đọc, không bao giờ có --yes. Trả (mã thoát, đầu ra)."""
    return _run(["netauto.plan", *(["--node", node] if node else [])], timeout_s)


def run_apply(node, db_path: str, timeout_s: float = APPLY_TIMEOUT_S, prune: bool = False):
    """Áp dụng thật (--yes). Cả cửa sổ bảo trì lẫn nhật ký kiểm toán dùng đúng CSDL của web."""
    args = ["netauto.apply", "--yes", "--db", db_path, "--audit-db", db_path]
    if prune:
        args.append("--prune")
    if node:
        args += ["--node", node]
    return _run(args, timeout_s)


def check_apply_allowed(conn, now: str) -> None:
    if remediation.is_busy(remediation.list_history(conn, 200), now):
        raise Busy("Bộ tự sửa đang chạy apply. Thử lại sau vài giây.")


def record_audit(db_path: str, result: str, nodes, detail: dict) -> int:
    c = audit.connect(db_path)
    try:
        entry = audit.new_entry(INTENT_PATH, None if nodes == maintenance.ALL else set(nodes),
                                False, result, 0, 0.0, detail)
        entry["actor"] = current_actor.get()
        return audit.record(c, entry)
    finally:
        c.close()


def open_maintenance(conn, nodes_text: str, reason: str, ttl_s: int, known, db_path: str,
                     record=record_audit) -> int:
    """Mở cửa sổ bảo trì và ghi nhật ký. Không ghi được nhật ký thì đóng lại cửa sổ vừa mở
    (cửa sổ làm im cảnh báo, nên không được tồn tại mà không có dấu vết)."""
    reason = (reason or "").strip()
    if not reason or len(reason) > REASON_MAX:
        raise ValueError(f"Lý do phải có từ 1 đến {REASON_MAX} ký tự.")
    if not TTL_MIN_S <= int(ttl_s) <= TTL_MAX_S:
        raise ValueError(f"Thời gian phải từ {TTL_MIN_S} đến {TTL_MAX_S} giây.")
    if nodes_text.strip() in ("", maintenance.ALL):
        nodes = maintenance.ALL
    else:
        nodes = {check_node(n.strip(), known) for n in nodes_text.split(",") if n.strip()}
    wid = maintenance.open_window(conn, current_actor.get(), reason, nodes, int(ttl_s))
    try:
        record(db_path, "BẢO TRÌ MỞ", nodes, {"window": wid, "reason": reason, "ttl": int(ttl_s)})
    except Exception:
        maintenance.close_window(conn, wid)
        raise
    return wid


def close_maintenance(conn, window_id, db_path: str, record=record_audit) -> list[int]:
    ids = maintenance.close_window(conn, window_id)
    if ids:
        record(db_path, "BẢO TRÌ ĐÓNG", maintenance.ALL, {"windows": ids})
    return ids
