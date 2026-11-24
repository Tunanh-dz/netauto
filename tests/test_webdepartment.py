"""32B: API thêm phòng ban (lệnh Docker và apply đều giả)."""
import os
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    HAVE = True
except ImportError:
    HAVE = False

from netauto import db, webdata

INTENT = (Path(__file__).resolve().parent / "fixtures" / "network_base.yml").read_text(encoding="utf-8")
LAB = (Path(__file__).resolve().parent / "fixtures" / "topology_base.clab.yml").read_text(encoding="utf-8")


class FakeDept:
    def __init__(self):
        self.calls = []

    def create_department(self, plan, **kw):
        self.calls.append((plan["spec"]["vlan"], kw["intent_path"], kw["lab_path"]))
        return {"ok": True, "stage": "xong", "message": "ok", "steps": [], "advice": ""}


class FakeRunner:
    def run_apply(self, node, db_path):
        return 0, ""


@unittest.skipUnless(HAVE, "cần: pip install fastapi uvicorn httpx")
class TestDeptApi(unittest.TestCase):
    TOKEN = "tok"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.db = os.path.join(d, "t.db")
        self.ip, self.lp = os.path.join(d, "n.yml"), os.path.join(d, "l.yml")
        Path(self.ip).write_text(INTENT, encoding="utf-8")
        Path(self.lp).write_text(LAB, encoding="utf-8")
        conn = db.connect(self.db)
        webdata.ensure_all(conn)
        conn.close()
        from netauto import web
        self.dept = FakeDept()
        app = web.create_app(self.db, token=self.TOKEN, runner=FakeRunner(), dept=self.dept,
                             intent_path=self.ip, lab_path=self.lp)
        self.client = TestClient(app, base_url="http://127.0.0.1:8000")
        self.h = {"X-NetAuto-Token": self.TOKEN}
        self.body = {"name": "Tài chính", "vlan": 40, "router": "r1"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_needs_token(self):
        for p in ("/api/department/preview", "/api/department"):
            self.assertEqual(self.client.post(p, json=self.body).status_code, 403)
        self.assertEqual(self.dept.calls, [])

    def test_foreign_host_refused(self):
        r = self.client.post("/api/department", json={**self.body, "confirm": True},
                             headers={**self.h, "Host": "evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_preview_changes_nothing(self):
        r = self.client.post("/api/department/preview", json=self.body, headers=self.h)
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertTrue(j["ok"])
        self.assertEqual(j["spec"]["host"], "h-tai-chinh")
        self.assertTrue(j["summary"])
        self.assertEqual(Path(self.ip).read_text(encoding="utf-8"), INTENT)
        self.assertEqual(self.dept.calls, [])

    def test_preview_reports_errors_as_data(self):
        r = self.client.post("/api/department/preview", json={**self.body, "vlan": 10}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["ok"])
        self.assertIn("đã tồn tại", r.json()["errors"][0])

    def test_preview_missing_vlan(self):
        r = self.client.post("/api/department/preview", json={"name": "A", "router": "r1"}, headers=self.h)
        self.assertEqual(r.status_code, 400)

    def test_create_requires_confirm(self):
        r = self.client.post("/api/department", json=self.body, headers=self.h)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.dept.calls, [])

    def test_create_replans_and_runs(self):
        r = self.client.post("/api/department", json={**self.body, "confirm": True}, headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(self.dept.calls, [(40, self.ip, self.lp)])

    def test_create_invalid_request_is_400_and_runs_nothing(self):
        r = self.client.post("/api/department", json={**self.body, "vlan": 10, "confirm": True}, headers=self.h)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.dept.calls, [])

    def test_busy_when_gate_is_taken(self):
        from netauto import web
        import threading
        started, release = threading.Event(), threading.Event()

        class Slow(FakeDept):
            def create_department(s, plan, **kw):
                started.set()
                release.wait(5)
                return super().create_department(plan, **kw)
        app = web.create_app(self.db, token=self.TOKEN, runner=FakeRunner(), dept=Slow(),
                             intent_path=self.ip, lab_path=self.lp)
        c = TestClient(app, base_url="http://127.0.0.1:8000")
        t = threading.Thread(target=lambda: c.post("/api/department", json={**self.body, "confirm": True}, headers=self.h))
        t.start()
        self.assertTrue(started.wait(5))
        r = c.post("/api/department", json={**self.body, "vlan": 41, "confirm": True}, headers=self.h)
        self.assertEqual(r.status_code, 409)
        release.set()
        t.join()


if __name__ == "__main__":
    unittest.main()
