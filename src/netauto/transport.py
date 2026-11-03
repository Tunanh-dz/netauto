"""Lớp truy cập thiết bị: chạy một lệnh bên trong container và trả về kết quả.

Mọi phần khác của hệ thống (thu thập trạng thái, triển khai, tự sửa lỗi)
sẽ gọi qua hàm run() này. Sau này có thể thay bằng SSH mà không phải
sửa các phần còn lại.
"""
import subprocess
from dataclasses import dataclass

LAB_NAME = "entlab"


@dataclass
class Result:
    """Kết quả của một lệnh chạy trên thiết bị."""
    ok: bool          # True nếu lệnh thành công (mã thoát = 0)
    stdout: str       # nội dung lệnh in ra
    stderr: str       # thông báo lỗi hoặc cảnh báo
    returncode: int   # mã thoát (0 = thành công)


def container_name(node: str) -> str:
    """Đổi tên node (r1) thành tên container (clab-entlab-r1)."""
    return f"clab-{LAB_NAME}-{node}"


def run(node: str, command: list[str], timeout: int = 10) -> Result:
    """Chạy `command` bên trong container của `node`."""
    try:
        proc = subprocess.run(
            ["docker", "exec", container_name(node), *command],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return Result(False, "", f"timeout sau {timeout} giây", -1)
    return Result(proc.returncode == 0, proc.stdout, proc.stderr, proc.returncode)
