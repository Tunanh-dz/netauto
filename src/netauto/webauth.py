"""33B: nối đăng nhập vào web, phần LOGIC thuần (web.py chỉ gọi các hàm này).

- `authorize`: một yêu cầu có được đi tiếp không (401 chưa đăng nhập, 403 không đủ quyền).
- `login_attempt`: xử lý một lần đăng nhập, gồm tạo phiên và ghi nhật ký kiểm toán (kể cả đăng nhập sai, bị khóa).
"""
from netauto import actions, auth

COOKIE = "netauto_session"
PUBLIC = frozenset({"/", "/login", "/api/login", "/api/login-info"})
BAD_LOGIN = "Sai tên đăng nhập hoặc mật khẩu."
LOCKED = f"Đăng nhập sai quá {auth.MAX_FAILS} lần: tài khoản bị khóa tạm thời {auth.LOCK_MINUTES} phút."


def authorize(conn, token, method: str, path: str, now: str) -> dict:
    """{"ok", "status", "detail", "user"}. Đường công khai luôn ok (user có nếu phiên hợp lệ)."""
    user = auth.session_user(conn, token, now)
    if path in PUBLIC:
        return {"ok": True, "status": 200, "detail": "", "user": user}
    if user is None:
        return {"ok": False, "status": 401, "detail": "Cần đăng nhập.", "user": None}
    needed = auth.required_role(method, path)
    if not auth.allows(user["role"], needed):
        return {"ok": False, "status": 403, "user": user,
                "detail": f"Tài khoản '{user['username']}' ({auth.ROLE_NAME[user['role']]}) "
                          f"không có quyền thực hiện thao tác này (cần: {auth.ROLE_NAME[needed]})."}
    return {"ok": True, "status": 200, "detail": "", "user": user}


def _audit_name(name) -> str:
    text = str(name or "").strip().lower()
    return text if auth.USERNAME_RE.match(text) else "?"


def _record(db_path: str, name, result: str, detail: dict) -> None:
    ctx = actions.current_actor.set(f"web:{_audit_name(name)}")
    try:
        actions.record_audit(db_path, result, actions.maintenance.ALL, detail)
    except Exception:                       # không ghi được nhật ký thì không chặn người dùng thật
        pass
    finally:
        actions.current_actor.reset(ctx)


def login_attempt(conn, db_path: str, username, password, now: str) -> dict:
    """{"status": 200, "user", "token"} hoặc {"status": 401|429, "detail"}."""
    user, why = auth.authenticate(conn, username, password, now)
    if user:
        token = auth.create_session(conn, user["id"], now)
        _record(db_path, user["username"], "ĐĂNG NHẬP", {"role": user["role"]})
        return {"status": 200, "user": user, "token": token}
    if why == "locked":
        _record(db_path, username, "ĐĂNG NHẬP BỊ KHÓA", {})
        return {"status": 429, "detail": LOCKED}
    _record(db_path, username, "ĐĂNG NHẬP SAI", {})
    return {"status": 401, "detail": BAD_LOGIN}
