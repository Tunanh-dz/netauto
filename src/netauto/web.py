"""API HTTP chỉ đọc (FastAPI) cho giao diện web.

Chạy: python3 -m netauto.web [--host 127.0.0.1] [--port 8000]
Tài liệu API tự sinh: http://127.0.0.1:8000/docs

Ngày 27A: chỉ có các đường dẫn GET. Chưa có thao tác ghi (apply, bảo trì) và chưa có đăng nhập,
nên mặc định chỉ lắng nghe trên 127.0.0.1 (máy này).
"""
import argparse
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from netauto import db, webdata

INDEX = Path(__file__).resolve().parent / "static" / "index.html"


def create_app(db_path: str = db.DB_PATH) -> FastAPI:
    app = FastAPI(title="netauto", description="API chỉ đọc của hệ thống Network Automation")

    def query(fn, *args, **kwargs):
        # mỗi yêu cầu mở kết nối riêng: SQLite không cho dùng chung kết nối giữa các luồng
        conn = db.connect(db_path)
        try:
            webdata.ensure_all(conn)
            return fn(conn, *args, **kwargs)
        finally:
            conn.close()

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(INDEX, media_type="text/html")

    @app.get("/api/status")
    def status():
        return query(webdata.overview)

    @app.get("/api/incidents")
    def incident_list(limit: int | None = None, only_open: bool = False):
        return query(webdata.incident_list, limit, only_open)

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

    return app


def main(argv=None) -> None:
    import uvicorn
    ap = argparse.ArgumentParser(prog="netauto.web")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", default=db.DB_PATH)
    args = ap.parse_args(argv)
    uvicorn.run(create_app(args.db), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
