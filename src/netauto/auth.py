"""33B: tài khoản, mật khẩu, phiên đăng nhập và phân quyền cho giao diện web. Logic thuần trên SQLite, không phụ thuộc web.

- Mật khẩu: băm bằng scrypt có muối ngẫu nhiên (thư viện chuẩn), không bao giờ lưu mật khẩu gốc.
- Phiên: mã ngẫu nhiên 256 bit trong cookie; CSDL chỉ lưu SHA-256 của mã, nên lộ CSDL cũng không dùng lại được phiên.
- Khóa tạm thời tài khoản sau nhiều lần sai liên tiếp (chống dò mật khẩu).
- Ba vai trò xếp bậc: viewer (chỉ xem) < operator (thao tác: áp dụng, phòng ban, bảo trì) < admin (cả quản lý tài khoản).
- Đường chưa được khai báo mặc định đòi quyền admin: quên khai báo thì đóng chứ không mở.
"""
import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta

ROLES = ("viewer", "operator", "admin")
RANK = {r: i for i, r in enumerate(ROLES)}
ROLE_NAME = {"viewer": "Chỉ xem", "operator": "Vận hành", "admin": "Quản trị"}
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
PW_MIN, PW_MAX = 8, 128
MAX_FAILS = 5
LOCK_MINUTES = 5
SESSION_HOURS = 8
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    username     TEXT NOT NULL UNIQUE,
    pw_hash      TEXT NOT NULL,
    role         TEXT NOT NULL,
    disabled     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    fails        INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


def connect(path: str):
    """Kết nối CSDL chung của hệ thống, trả dòng theo tên cột, bảo đảm bảng tài khoản đã có."""
    from netauto import db
    conn = db.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)


def _t(text: str) -> datetime:
    return datetime.fromisoformat(text)


# ------------------------------------------------------------------ mật khẩu
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        got = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(bytes.fromhex(digest)))
        return hmac.compare_digest(got, bytes.fromhex(digest))
    except (AttributeError, ValueError, TypeError):
        return False


_DUMMY = hash_password("không-ai-dùng-mật-khẩu-này")


def check_password_rules(password) -> str | None:
    if not isinstance(password, str) or not PW_MIN <= len(password) <= PW_MAX:
        return f"Mật khẩu phải từ {PW_MIN} đến {PW_MAX} ký tự."
    return None


# ------------------------------------------------------------------ tài khoản
def _public(row) -> dict:
    return {"id": row["id"], "username": row["username"], "role": row["role"],
            "disabled": bool(row["disabled"]), "created_at": row["created_at"]}


def get_user(conn, username: str):
    row = conn.execute("SELECT * FROM users WHERE username = ?", (str(username).lower(),)).fetchone()
    return _public(row) if row else None


def list_users(conn) -> list[dict]:
    return [_public(r) for r in conn.execute("SELECT * FROM users ORDER BY id")]


def count_users(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create_user(conn, username: str, password: str, role: str, now: str) -> dict:
    name = str(username).strip().lower()
    if not USERNAME_RE.match(name):
        raise ValueError("Tên đăng nhập chỉ gồm chữ thường không dấu, số, dấu . _ -; tối đa 32 ký tự.")
    if role not in ROLES:
        raise ValueError(f"Vai trò phải là một trong: {', '.join(ROLES)}.")
    problem = check_password_rules(password)
    if problem:
        raise ValueError(problem)
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (name,)).fetchone():
        raise ValueError(f"Đã có tài khoản '{name}'.")
    conn.execute("INSERT INTO users (username, pw_hash, role, created_at) VALUES (?, ?, ?, ?)",
                 (name, hash_password(password), role, now))
    conn.commit()
    return get_user(conn, name)


def _need_user(conn, username: str) -> dict:
    user = get_user(conn, username)
    if user is None:
        raise ValueError(f"Không có tài khoản '{username}'.")
    return user


def _other_active_admins(conn, user_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0 AND id != ?",
                        (user_id,)).fetchone()[0]


def _end_user_sessions(conn, user_id: int) -> None:
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def rename_user(conn, old: str, new: str) -> None:
    """Đổi tên đăng nhập. Mật khẩu, vai trò, phiên đang mở giữ nguyên (gắn với mã tài khoản, không gắn với tên).
    Nhật ký kiểm toán cũ vẫn ghi tên cũ: nhật ký không bị sửa lại."""
    user = _need_user(conn, old)
    name = str(new).strip().lower()
    if not USERNAME_RE.match(name):
        raise ValueError("Tên đăng nhập chỉ gồm chữ thường không dấu, số, dấu . _ -; tối đa 32 ký tự.")
    if name == user["username"]:
        return
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (name,)).fetchone():
        raise ValueError(f"Đã có tài khoản '{name}'.")
    conn.execute("UPDATE users SET username = ? WHERE id = ?", (name, user["id"]))
    conn.commit()


def set_role(conn, username: str, role: str) -> None:
    user = _need_user(conn, username)
    if role not in ROLES:
        raise ValueError(f"Vai trò phải là một trong: {', '.join(ROLES)}.")
    if user["role"] == "admin" and role != "admin" and not user["disabled"] and _other_active_admins(conn, user["id"]) == 0:
        raise ValueError("Không thể hạ quyền quản trị viên cuối cùng.")
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user["id"]))
    conn.commit()


def set_disabled(conn, username: str, disabled: bool) -> None:
    user = _need_user(conn, username)
    if disabled and user["role"] == "admin" and not user["disabled"] and _other_active_admins(conn, user["id"]) == 0:
        raise ValueError("Không thể khóa quản trị viên cuối cùng.")
    conn.execute("UPDATE users SET disabled = ?, fails = 0, locked_until = NULL WHERE id = ?", (int(bool(disabled)), user["id"]))
    if disabled:
        _end_user_sessions(conn, user["id"])
    conn.commit()


def set_password(conn, username: str, password: str) -> None:
    user = _need_user(conn, username)
    problem = check_password_rules(password)
    if problem:
        raise ValueError(problem)
    conn.execute("UPDATE users SET pw_hash = ?, fails = 0, locked_until = NULL WHERE id = ?",
                 (hash_password(password), user["id"]))
    _end_user_sessions(conn, user["id"])
    conn.commit()


# ------------------------------------------------------------------ đăng nhập
def authenticate(conn, username, password, now: str):
    """Trả (user, None) nếu đúng; (None, "bad") nếu sai (không phân biệt sai tên hay sai mật khẩu);
    (None, "locked") nếu đang bị khóa tạm thời."""
    name = str(username or "").strip().lower()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (name,)).fetchone()
    if row is None:
        verify_password(str(password or ""), _DUMMY)          # tốn thời gian như thật, khó đoán tên nào tồn tại
        return None, "bad"
    if row["locked_until"] and _t(row["locked_until"]) > _t(now):
        return None, "locked"
    if row["disabled"] or not verify_password(str(password or ""), row["pw_hash"]):
        if not row["disabled"]:
            fails = row["fails"] + 1
            if fails >= MAX_FAILS:
                until = (_t(now) + timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds")
                conn.execute("UPDATE users SET fails = 0, locked_until = ? WHERE id = ?", (until, row["id"]))
            else:
                conn.execute("UPDATE users SET fails = ? WHERE id = ?", (fails, row["id"]))
            conn.commit()
        return None, "bad"
    conn.execute("UPDATE users SET fails = 0, locked_until = NULL WHERE id = ?", (row["id"],))
    conn.commit()
    return _public(row), None


# ------------------------------------------------------------------ phiên
def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(conn, user_id: int, now: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = (_t(now) + timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                 (_digest(token), user_id, now, expires))
    conn.commit()
    return token


def session_user(conn, token, now: str):
    if not token or not isinstance(token, str):
        return None
    row = conn.execute(
        "SELECT u.*, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (_digest(token),)).fetchone()
    if row is None or row["disabled"] or _t(row["expires_at"]) < _t(now):
        return None
    return _public(row)


def end_session(conn, token) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_digest(token),))
        conn.commit()


# ------------------------------------------------------------------ phân quyền
OPERATOR_POSTS = frozenset({
    "/api/plan", "/api/apply", "/api/maintenance/open", "/api/maintenance/close",
    "/api/department", "/api/department/preview", "/api/department/remove", "/api/department/remove/preview",
})


def required_role(method: str, path: str) -> str:
    """Vai trò tối thiểu cho một yêu cầu. Chưa khai báo thì đòi admin (đóng theo mặc định)."""
    if path == "/api/users" or path.startswith("/api/users/"):
        return "admin"
    if method in ("GET", "HEAD"):
        return "viewer"
    if method == "POST":
        if path == "/api/logout":
            return "viewer"
        if path in OPERATOR_POSTS:
            return "operator"
    return "admin"


def allows(role, needed: str) -> bool:
    return role in RANK and needed in RANK and RANK[role] >= RANK[needed]
