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

from netauto import db, health, remediation, service, webdata


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


class FakeRunner:
    """Thay cho tiến trình plan/apply thật: ghi lại cách được gọi."""
    def __init__(self):
        self.calls = []

    def run_plan(self, node, db_path):
        self.calls.append(("plan", node))
        return 2, "có thay đổi"

    def run_apply(self, node, db_path):
        self.calls.append(("apply", node))
        return 0, "xong"


@unittest.skipUnless(HAVE, "cần: pip install fastapi uvicorn httpx")
class TestWriteApi(unittest.TestCase):
    TOKEN = "t0k3n-for-tests"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        conn = db.connect(self.path)
        webdata.ensure_all(conn)
        conn.close()
        from netauto import web
        self.runner = FakeRunner()
        app = web.create_app(self.path, token=self.TOKEN, runner=self.runner)
        self.client = TestClient(app, base_url="http://127.0.0.1:8000")
        self.ok = {"X-NetAuto-Token": self.TOKEN}

    def tearDown(self):
        self.tmp.cleanup()

    def test_post_needs_the_token(self):
        for path in ("/api/plan", "/api/apply", "/api/maintenance/open", "/api/maintenance/close"):
            self.assertEqual(self.client.post(path, json={}).status_code, 403, path)
            self.assertEqual(self.client.post(path, json={}, headers={"X-NetAuto-Token": "sai"}).status_code,
                             403, path)
        self.assertEqual(self.runner.calls, [])

    def test_post_from_a_foreign_host_is_refused(self):
        r = self.client.post("/api/plan", json={}, headers={**self.ok, "Host": "evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.runner.calls, [])

    def test_plan(self):
        r = self.client.post("/api/plan", json={"node": "r1"}, headers=self.ok)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["exit_code"], 2)
        self.assertIn("chờ áp dụng", r.json()["meaning"])
        self.assertEqual(self.runner.calls, [("plan", "r1")])

    def test_unknown_node_is_rejected_before_running_anything(self):
        r = self.client.post("/api/plan", json={"node": "r1; reboot"}, headers=self.ok)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.runner.calls, [])

    def test_apply_requires_explicit_confirmation(self):
        r = self.client.post("/api/apply", json={"node": "r1"}, headers=self.ok)
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/apply", json={"node": "r1", "confirm": False}, headers=self.ok)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.runner.calls, [])

    def test_apply_with_confirmation_runs(self):
        r = self.client.post("/api/apply", json={"node": "r1", "confirm": True}, headers=self.ok)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["exit_code"], 0)
        self.assertEqual(self.runner.calls, [("apply", "r1")])

    def test_apply_refused_while_remediation_runs(self):
        conn = db.connect(self.path)
        remediation.ensure_schema(conn)
        from netauto import maintenance
        remediation.start(conn, 1, "r1", "router.uplink", maintenance.now())
        conn.close()
        r = self.client.post("/api/apply", json={"confirm": True}, headers=self.ok)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.runner.calls, [])

    def test_maintenance_open_and_close(self):
        r = self.client.post("/api/maintenance/open", headers=self.ok,
                             json={"nodes": "r1", "reason": "thay cáp", "ttl_s": 120})
        self.assertEqual(r.status_code, 200)
        active = self.client.get("/api/maintenance").json()["active"]
        self.assertEqual(len(active), 1)
        r = self.client.post("/api/maintenance/close", headers=self.ok, json={})
        self.assertEqual(r.status_code, 200)
        w = self.client.get("/api/maintenance").json()["items"][0]
        self.assertEqual(w["close_reason"], "ended")          # cửa sổ đã đóng
        # "active" vẫn liệt kê nó trong 45 giây ân hạn (để mạng kịp hội tụ), nên không kiểm tra rỗng ở đây

    def test_maintenance_bad_input_is_400(self):
        r = self.client.post("/api/maintenance/open", headers=self.ok,
                             json={"nodes": "r1", "reason": "", "ttl_s": 120})
        self.assertEqual(r.status_code, 400)

    def test_nodes_endpoint(self):
        self.assertIn("r1", self.client.get("/api/nodes").json()["nodes"])

    def test_root_page_carries_the_token(self):
        r = self.client.get("/")
        self.assertIn(self.TOKEN, r.text)
        self.assertNotIn("__NETAUTO_TOKEN__", r.text)


if __name__ == "__main__":
    unittest.main()
