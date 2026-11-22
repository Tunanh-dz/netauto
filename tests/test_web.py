"""27A: lớp HTTP (FastAPI). Chỉ chạy khi đã cài fastapi và httpx."""
import json
import os
import tempfile
import unittest

try:
    from fastapi.testclient import TestClient
    HAVE = True
except ImportError:                                  # chưa cài: bỏ qua thay vì lỗi
    HAVE = False

from netauto import db, health, service, webdata


@unittest.skipUnless(HAVE, "cần: pip install fastapi uvicorn httpx")
class TestApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        conn = db.connect(self.path)
        webdata.ensure_all(conn)
        service.beat(conn, 1, "2026-01-01T10:00:00+07:00", 5)
        conn.close()
        from netauto import web
        self.client = TestClient(web.create_app(self.path))

    def tearDown(self):
        self.tmp.cleanup()

    def test_status_ok_and_json(self):
        r = self.client.get("/api/status")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("monitor", body)
        self.assertIn("open_incidents", body)

    def test_each_list_endpoint_answers(self):
        for path in ("/api/incidents", "/api/health", "/api/remediations",
                     "/api/injections", "/api/maintenance"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn("items", r.json(), path)

    def test_limit_is_clamped_not_rejected(self):
        self.assertEqual(self.client.get("/api/incidents?limit=99999").status_code, 200)
        self.assertEqual(self.client.get("/api/incidents?limit=0").status_code, 200)

    def test_only_open_flag(self):
        r = self.client.get("/api/incidents?only_open=true")
        self.assertEqual(r.status_code, 200)

    def test_root_serves_the_overview_page(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertIn("/api/status", r.text)

    def test_audit_endpoints(self):
        r = self.client.get("/api/audit")
        self.assertEqual(r.status_code, 200)
        self.assertIn("items", r.json())
        self.assertTrue(r.json()["chain"]["ok"])
        self.assertEqual(self.client.get("/api/audit/999").status_code, 404)

    def test_no_write_methods(self):
        for path in ("/api/status", "/api/incidents"):
            self.assertEqual(self.client.post(path).status_code, 405)
            self.assertEqual(self.client.delete(path).status_code, 405)


if __name__ == "__main__":
    unittest.main()
