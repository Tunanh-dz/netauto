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
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from netauto import actions, db, maintenance, webdata

INDEX = Path(__file__).resolve().parent / "static" / "index.html"
TOKEN_PLACEHOLDER = "__NETAUTO_TOKEN__"
LOCAL_HOSTNAMES = ("127.0.0.1", "localhost", "::1")
OUTPUT_TAIL = 8000          # chỉ trả phần cuối của đầu ra dài


class PlanBody(BaseModel):
    node: str | None = None


class ApplyBody(BaseModel):
    node: str | None = None
    confirm: bool = False


class OpenBody(BaseModel):
    nodes: str = "*"
    reason: str = ""
    ttl_s: int = maintenance.DEFAULT_TTL_S


class CloseBody(BaseModel):
    id: int | None = None


def create_app(db_path: str = db.DB_PATH, *, token: str | None = None, runner=actions) -> FastAPI:
    app = FastAPI(title="netauto", description="API của hệ thống Network Automation")
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

    def guard(request: Request):
        hostname = urlsplit("//" + request.headers.get("host", "")).hostname
        if hostname not in LOCAL_HOSTNAMES:
            raise HTTPException(403, "Thao tác ghi chỉ được phép từ chính máy chạy hệ thống.")
        sent = request.headers.get("x-netauto-token", "")
        if not hmac.compare_digest(sent.encode(), token.encode()):
            raise HTTPException(403, "Thiếu hoặc sai mã xác thực. Hãy tải lại trang.")

    def checked_node(node):
        try:
            return actions.check_node(node, webdata.node_names())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    def shown(kind: str, code: int, text: str) -> dict:
        return {"exit_code": code, "meaning": actions.meaning(kind, code),
                "output": text[-OUTPUT_TAIL:]}

    # ------------------------------------------------------------ trang và đọc dữ liệu
    @app.get("/", include_in_schema=False)
    def index():
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
    def audit_list(limit: int | None = None):
        return query(webdata.audit_list, limit)

    @app.get("/api/audit/{entry_id}")
    def audit_detail(entry_id: int):
        entry = query(webdata.audit_detail, entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="không có bản ghi kiểm toán này")
        return entry

    # ------------------------------------------------------------ thao tác ghi (28B)
    @app.post("/api/plan", dependencies=[Depends(guard)])
    def plan(body: PlanBody):
        node = checked_node(body.node)
        try:
            code, text = runner.run_plan(node, db_path)
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "Lập kế hoạch quá lâu nên đã bị dừng.") from None
        return shown("plan", code, text)

    @app.post("/api/apply", dependencies=[Depends(guard)])
    def apply(body: ApplyBody):
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
    def maintenance_open(body: OpenBody):
        try:
            wid = query(actions.open_maintenance, body.nodes, body.reason, body.ttl_s,
                        webdata.node_names(), db_path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"window": wid}

    @app.post("/api/maintenance/close", dependencies=[Depends(guard)])
    def maintenance_close(body: CloseBody):
        return {"closed": query(actions.close_maintenance, body.id, db_path)}

    return app


def main(argv=None) -> None:
    import os
    import uvicorn
    ap = argparse.ArgumentParser(prog="netauto.web")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", default=db.DB_PATH)
    args = ap.parse_args(argv)
    os.environ.setdefault("NETAUTO_ACTOR", actions.ACTOR)
    uvicorn.run(create_app(args.db), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
