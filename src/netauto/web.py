"""API HTTP (FastAPI) và trang web cho hệ thống.

Chạy: python3 -m netauto.web [--host 127.0.0.1] [--port 8000]
Tài liệu API tự sinh: http://127.0.0.1:8000/docs

- Đường GET chỉ đọc (27A, 28A).
- Đường POST (28B) có thể thay đổi mạng, nên có ba lớp bảo vệ:
  1. Mã xác thực ngẫu nhiên sinh mỗi lần khởi động, chỉ nằm trong trang do chính máy chủ này phục vụ
     (trang web ở nguồn khác không đọc được nên không giả mạo được yêu cầu).
  2. Chỉ nhận khi tiêu đề Host là chính máy này (chống DNS rebinding); mở cho máy khác xem vẫn được,
     nhưng các nút thao tác bị từ chối.
  3. Áp dụng thật phải gửi confirm=true, không chạy chồng, không chạy khi bộ tự sửa đang chạy.
"""
import argparse
import hmac
import secrets
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from netauto import actions, auth, configview, db, department, deptexec, maintenance, webauth, webdata
from netauto import intent as intent_mod
from netauto.intent import INTENT_PATH

INDEX = Path(__file__).resolve().parent / "static" / "index.html"
LOGIN = Path(__file__).resolve().parent / "static" / "login.html"
TOKEN_PLACEHOLDER = "__NETAUTO_TOKEN__"
LOCAL_HOSTNAMES = ("127.0.0.1", "localhost", "::1")
OUTPUT_TAIL = 8000          # chỉ trả phần cuối của đầu ra dài
CONFIG_CACHE_S = 3


class PlanBody(BaseModel):
    node: str | None = None


class ApplyBody(BaseModel):
    node: str | None = None
    confirm: bool = False


class OpenBody(BaseModel):
    nodes: str = "*"
    reason: str = ""
    ttl_s: int = maintenance.DEFAULT_TTL_S


class DepartmentBody(BaseModel):
    name: str = ""
    vlan: int | None = None
    router: str = ""
    confirm: bool = False


class RemoveBody(BaseModel):
    vlan: int | None = None
    confirm: bool = False


class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


class UserBody(BaseModel):
    username: str = ""
    password: str = ""
    role: str = ""


class RoleBody(BaseModel):
    role: str = ""


class RenameBody(BaseModel):
    new_name: str = ""


class PasswordBody(BaseModel):
    password: str = ""


class CloseBody(BaseModel):
    id: int | None = None


def create_app(db_path: str = db.DB_PATH, *, token: str | None = None, runner=actions,
               dept=deptexec, intent_path: str = INTENT_PATH, lab_path: str = deptexec.LAB_PATH,
               auth_enabled: bool = False, view=configview) -> FastAPI:
    """auth_enabled=True: mọi yêu cầu (trừ trang đăng nhập) cần phiên đăng nhập và đủ vai trò (33B)."""
    def authz(request: Request):
        request.state.user = None
        if not auth_enabled:
            return
        conn = auth.connect(db_path)
        try:
            res = webauth.authorize(conn, request.cookies.get(webauth.COOKIE), request.method,
                                    request.url.path, maintenance.now())
        finally:
            conn.close()
        request.state.user = res["user"]
        if not res["ok"]:
            raise HTTPException(res["status"], res["detail"])

    app = FastAPI(title="netauto", description="API của hệ thống Network Automation",
                  dependencies=[Depends(authz)])
    token = token or secrets.token_urlsafe(16)
    apply_gate = threading.Lock()                 # tối đa một lần áp dụng từ web cùng lúc

    def query(fn, *args, **kwargs):
        # mỗi yêu cầu mở kết nối riêng: SQLite không cho dùng chung kết nối giữa các luồng
        conn = db.connect(db_path)
        try:
            webdata.ensure_all(conn)
            return fn(conn, *args, **kwargs)
        finally:
            conn.close()

    def check_host(request: Request):
        hostname = urlsplit("//" + request.headers.get("host", "")).hostname
        if hostname not in LOCAL_HOSTNAMES:
            raise HTTPException(403, "Thao tác ghi chỉ được phép từ chính máy chạy hệ thống.")

    def guard(request: Request):
        check_host(request)
        sent = request.headers.get("x-netauto-token", "")
        if not hmac.compare_digest(sent.encode(), token.encode()):
            raise HTTPException(403, "Thiếu hoặc sai mã xác thực. Hãy tải lại trang.")

    def acting(request: Request):
        """Ghi nhớ người đang thao tác (cho nhật ký kiểm toán và cửa sổ bảo trì) trong ngữ cảnh của yêu cầu này."""
        user = getattr(request.state, "user", None)
        if user:
            actions.current_actor.set(f"web:{user['username']}")
        return user

    def noted(request: Request, result: str, detail: dict):
        acting(request)
        try:
            actions.record_audit(db_path, result, maintenance.ALL, detail)
        except Exception:
            pass

    def checked_node(node):
        try:
            return actions.check_node(node, webdata.node_names())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    def shown(kind: str, code: int, text: str) -> dict:
        return {"exit_code": code, "meaning": actions.meaning(kind, code),
                "output": text[-OUTPUT_TAIL:]}

    # ------------------------------------------------------------ trang và đọc dữ liệu
    @app.get("/login", include_in_schema=False)
    def login_page():
        return HTMLResponse(LOGIN.read_text(encoding="utf-8"))

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        if auth_enabled and request.state.user is None:
            return HTMLResponse(LOGIN.read_text(encoding="utf-8"))
        html = INDEX.read_text(encoding="utf-8").replace(TOKEN_PLACEHOLDER, token)
        return HTMLResponse(html)

    @app.get("/api/status")
    def status():
        return query(webdata.overview)

    @app.get("/api/nodes")
    def nodes():
        return {"nodes": webdata.node_names()}

    @app.get("/api/topology")
    def topology():
        return query(webdata.topology)

    @app.get("/api/history")
    def history(range: str | None = None):
        return query(webdata.history, range)

    @app.get("/api/incidents")
    def incident_list(limit: int | None = None, only_open: bool = False):
        return query(webdata.incident_list, limit, only_open)

    @app.get("/api/events")
    def incident_events(limit: int | None = None, only_open: bool = False):
        return query(webdata.incident_events, limit, only_open)

    @app.get("/api/health")
    def health_history(limit: int | None = None):
        return query(webdata.health_history, limit)

    @app.get("/api/remediations")
    def remediation_list(limit: int | None = None):
        return query(webdata.remediation_list, limit)

    @app.get("/api/injections")
    def injection_list(limit: int | None = None):
        return query(webdata.injection_list, limit)

    @app.get("/api/maintenance")
    def maintenance_list(limit: int | None = None):
        return query(webdata.maintenance_list, limit)

    @app.get("/api/audit")
    def audit_list(limit: int | None = None, category: str | None = None):
        try:
            return query(webdata.audit_list, limit, category)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/audit/{entry_id}")
    def audit_detail(entry_id: int):
        entry = query(webdata.audit_detail, entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="không có bản ghi kiểm toán này")
        return entry

    # ------------------------------------------------------------ thao tác ghi (28B)
    @app.post("/api/plan", dependencies=[Depends(guard)])
    def plan(body: PlanBody, request: Request):
        acting(request)
        node = checked_node(body.node)
        try:
            code, text = runner.run_plan(node, db_path)
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "Lập kế hoạch quá lâu nên đã bị dừng.") from None
        return shown("plan", code, text)

    @app.post("/api/apply", dependencies=[Depends(guard)])
    def apply(body: ApplyBody, request: Request):
        acting(request)
        if not body.confirm:
            raise HTTPException(400, "Áp dụng thật phải gửi confirm=true.")
        node = checked_node(body.node)
        if not apply_gate.acquire(blocking=False):
            raise HTTPException(409, "Đang có một lần áp dụng khác chạy.")
        try:
            try:
                query(actions.check_apply_allowed, maintenance.now())
            except actions.Busy as exc:
                raise HTTPException(409, str(exc)) from None
            try:
                code, text = runner.run_apply(node, db_path)
            except subprocess.TimeoutExpired:
                raise HTTPException(504, "apply chạy quá lâu nên đã bị dừng (có thể còn dở dang). "
                                         "Xem tab Kiểm toán.") from None
        finally:
            apply_gate.release()
        return shown("apply", code, text)

    @app.post("/api/maintenance/open", dependencies=[Depends(guard)])
    def maintenance_open(body: OpenBody, request: Request):
        acting(request)
        try:
            wid = query(actions.open_maintenance, body.nodes, body.reason, body.ttl_s,
                        webdata.node_names(), db_path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"window": wid}

    @app.post("/api/maintenance/close", dependencies=[Depends(guard)])
    def maintenance_close(body: CloseBody, request: Request):
        acting(request)
        return {"closed": query(actions.close_maintenance, body.id, db_path)}

    # ------------------------------------------------------------ thêm phòng ban (32B)
    def dept_plan(body: DepartmentBody) -> dict:
        try:
            with open(intent_path, encoding="utf-8") as f1, open(lab_path, encoding="utf-8") as f2:
                intent_text, lab_text = f1.read(), f2.read()
        except OSError as exc:
            raise HTTPException(500, f"Không đọc được intent hoặc topology: {exc}") from None
        if body.vlan is None:
            raise HTTPException(400, "Thiếu số VLAN.")
        return department.plan_department(intent_text, lab_text, body.name, body.vlan, body.router)

    @app.post("/api/department/preview", dependencies=[Depends(guard)])
    def department_preview(body: DepartmentBody):
        plan = dept_plan(body)
        if not plan["ok"]:
            return {"ok": False, "errors": plan["errors"]}
        return {"ok": True, "errors": [], "spec": plan["spec"], "summary": plan["summary"],
                "commands": [" ".join(s["argv"][:6]) + " ..." if len(s["argv"]) > 6 else " ".join(s["argv"])
                             for s in plan["steps"]]}

    @app.post("/api/department", dependencies=[Depends(guard)])
    def department_create(body: DepartmentBody, request: Request):
        acting(request)
        if not body.confirm:
            raise HTTPException(400, "Tạo phòng ban thật phải gửi confirm=true.")
        if not apply_gate.acquire(blocking=False):
            raise HTTPException(409, "Đang có một thao tác thay đổi mạng khác chạy.")
        try:
            plan = dept_plan(body)                     # lập lại kế hoạch: không tin bản xem trước cũ
            if not plan["ok"]:
                raise HTTPException(400, "; ".join(plan["errors"]))
            try:
                query(actions.check_apply_allowed, maintenance.now())
            except actions.Busy as exc:
                raise HTTPException(409, str(exc)) from None
            conn = db.connect(db_path)
            try:
                maintenance.ensure_schema(conn)
                return dept.create_department(plan, conn=conn, db_path=db_path, intent_path=intent_path,
                                              lab_path=lab_path, run_apply=runner.run_apply)
            finally:
                conn.close()
        finally:
            apply_gate.release()

    # ------------------------------------------------------------ xóa phòng ban (32F)
    def read_texts():
        try:
            with open(intent_path, encoding="utf-8") as f1, open(lab_path, encoding="utf-8") as f2:
                return f1.read(), f2.read()
        except OSError as exc:
            raise HTTPException(500, f"Không đọc được intent hoặc topology: {exc}") from None

    @app.get("/api/departments")
    def departments():
        return {"items": department.list_departments(*read_texts())}

    @app.post("/api/department/remove/preview", dependencies=[Depends(guard)])
    def department_remove_preview(body: RemoveBody):
        plan = department.plan_removal(*read_texts(), body.vlan)
        if not plan["ok"]:
            return {"ok": False, "errors": plan["errors"]}
        return {"ok": True, "errors": [], "spec": plan["spec"], "summary": plan["summary"],
                "commands": [" ".join(c) for c in plan["steps"]]}

    @app.post("/api/department/remove", dependencies=[Depends(guard)])
    def department_remove(body: RemoveBody, request: Request):
        acting(request)
        if not body.confirm:
            raise HTTPException(400, "Xóa phòng ban thật phải gửi confirm=true.")
        if not apply_gate.acquire(blocking=False):
            raise HTTPException(409, "Đang có một thao tác thay đổi mạng khác chạy.")
        try:
            plan = department.plan_removal(*read_texts(), body.vlan)   # lập lại, không tin bản xem trước cũ
            if not plan["ok"]:
                raise HTTPException(400, "; ".join(plan["errors"]))
            try:
                query(actions.check_apply_allowed, maintenance.now())
            except actions.Busy as exc:
                raise HTTPException(409, str(exc)) from None
            conn = db.connect(db_path)
            try:
                maintenance.ensure_schema(conn)
                return dept.remove_department(plan, conn=conn, db_path=db_path, intent_path=intent_path,
                                              lab_path=lab_path, run_apply=runner.run_apply)
            finally:
                conn.close()
        finally:
            apply_gate.release()

    # ------------------------------------------------------------ xem cấu hình thiết bị (33D, chỉ đọc)
    config_cache: dict = {}
    config_lock = threading.Lock()

    @app.get("/api/config/{node}")
    def config_of(node: str):
        try:
            intent = intent_mod.load_intent(intent_path)
        except Exception as exc:
            raise HTTPException(500, f"Không đọc được intent: {exc}") from None
        if configview.role_of(node, intent) is None:
            raise HTTPException(404, f"Không có thiết bị '{node}'.")
        with config_lock:                              # tránh dồn dập lệnh vào thiết bị: dùng lại kết quả trong 3 giây
            hit = config_cache.get(node)
            if hit and time.monotonic() - hit[0] < CONFIG_CACHE_S:
                return hit[1]
            result = view.build_view(node, intent)
            config_cache[node] = (time.monotonic(), result)
            return result

    # ------------------------------------------------------------ đăng nhập và tài khoản (33B)
    @app.get("/api/login-info")
    def login_info():
        conn = auth.connect(db_path)
        try:
            return {"auth": auth_enabled, "has_users": auth.count_users(conn) > 0}
        finally:
            conn.close()

    @app.post("/api/login", dependencies=[Depends(check_host)])
    def login(body: LoginBody, response: Response):
        if not auth_enabled:
            raise HTTPException(400, "Đăng nhập đang tắt (máy chủ chạy với --no-auth).")
        conn = auth.connect(db_path)
        try:
            res = webauth.login_attempt(conn, db_path, body.username, body.password, maintenance.now())
        finally:
            conn.close()
        if res["status"] != 200:
            raise HTTPException(res["status"], res["detail"])
        response.set_cookie(webauth.COOKIE, res["token"], max_age=auth.SESSION_HOURS * 3600,
                            httponly=True, samesite="strict", path="/")
        return {"user": res["user"]}

    @app.post("/api/logout", dependencies=[Depends(guard)])
    def logout(request: Request, response: Response):
        conn = auth.connect(db_path)
        try:
            auth.end_session(conn, request.cookies.get(webauth.COOKIE))
        finally:
            conn.close()
        response.delete_cookie(webauth.COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request):
        user = getattr(request.state, "user", None)
        if not auth_enabled:
            return {"auth": False, "user": None, "role": "admin", "role_name": auth.ROLE_NAME["admin"]}
        return {"auth": True, "user": user, "role": user["role"], "role_name": auth.ROLE_NAME[user["role"]]}

    def with_users(fn, request: Request, result: str, detail: dict):
        conn = auth.connect(db_path)
        try:
            fn(conn)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        finally:
            conn.close()
        noted(request, result, detail)
        return {"ok": True}

    @app.get("/api/users")
    def users_list():
        conn = auth.connect(db_path)
        try:
            return {"items": auth.list_users(conn)}
        finally:
            conn.close()

    @app.post("/api/users", dependencies=[Depends(guard)])
    def users_create(body: UserBody, request: Request):
        return with_users(lambda c: auth.create_user(c, body.username, body.password, body.role, maintenance.now()),
                          request, "TÀI KHOẢN TẠO", {"user": body.username.strip().lower(), "role": body.role})

    @app.post("/api/users/{name}/role", dependencies=[Depends(guard)])
    def users_role(name: str, body: RoleBody, request: Request):
        return with_users(lambda c: auth.set_role(c, name, body.role), request, "TÀI KHOẢN ĐỔI QUYỀN",
                          {"user": name, "role": body.role})

    @app.post("/api/users/{name}/rename", dependencies=[Depends(guard)])
    def users_rename(name: str, body: RenameBody, request: Request):
        return with_users(lambda c: auth.rename_user(c, name, body.new_name), request, "TÀI KHOẢN ĐỔI TÊN",
                          {"user": name, "new_name": body.new_name.strip().lower()})

    @app.post("/api/users/{name}/disable", dependencies=[Depends(guard)])
    def users_disable(name: str, request: Request):
        return with_users(lambda c: auth.set_disabled(c, name, True), request, "TÀI KHOẢN KHÓA", {"user": name})

    @app.post("/api/users/{name}/enable", dependencies=[Depends(guard)])
    def users_enable(name: str, request: Request):
        return with_users(lambda c: auth.set_disabled(c, name, False), request, "TÀI KHOẢN MỞ KHÓA", {"user": name})

    @app.post("/api/users/{name}/password", dependencies=[Depends(guard)])
    def users_password(name: str, body: PasswordBody, request: Request):
        return with_users(lambda c: auth.set_password(c, name, body.password), request, "TÀI KHOẢN ĐỔI MẬT KHẨU",
                          {"user": name})

    return app


def main(argv=None) -> None:
    import os
    import uvicorn
    ap = argparse.ArgumentParser(prog="netauto.web")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", default=db.DB_PATH)
    ap.add_argument("--no-auth", action="store_true",
                    help="TẮT đăng nhập (chỉ để thử trên máy riêng; ai mở được trang đều thao tác được)")
    args = ap.parse_args(argv)
    os.environ.setdefault("NETAUTO_ACTOR", actions.ACTOR)
    if args.no_auth:
        print("!! Đăng nhập đang TẮT (--no-auth): mọi người mở được trang đều có toàn quyền.")
    else:
        conn = auth.connect(args.db)
        try:
            if auth.count_users(conn) == 0:
                print("!! Chưa có tài khoản nào, chưa ai đăng nhập được. Tạo tài khoản quản trị bằng:\n"
                      "   python3 -m netauto.users add <tên> --role admin")
        finally:
            conn.close()
    uvicorn.run(create_app(args.db, auth_enabled=not args.no_auth), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
