"""33D: GET /api/config/{node} (cần fastapi)."""
import os
import tempfile
import unittest

try:
    from fastapi.testclient import TestClient
    HAVE = True
except ImportError:
    HAVE = False

from netauto import auth, maintenance, webdata


class FakeView:
    def __init__(self):
        self.calls = []

    def build_view(self, node, intent, run=None):
        self.calls.append(node)
        return {"node": node, "role": "router", "sections": [], "intended": {"shell": [], "vtysh": ""}, "taken_at": "x"}


@unittest.skipUnless(HAVE, "cần: pip install fastapi uvicorn httpx")
class TestConfigApi(unittest.TestCase):
    def setUp(self):
        from netauto import db, web
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        conn = auth.connect(self.path)
        webdata.ensure_all(conn)
        auth.create_user(conn, "chi", "mat-khau-dai-1", "viewer", maintenance.now())
        conn.close()
        self.view = FakeView()
        self.web, self.db = web, db

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, **kw):
        app = self.web.create_app(self.path, token="t", view=self.view, **kw)
        return TestClient(app, base_url="http://127.0.0.1:8000")

    def test_known_node_ok_unknown_404(self):
        c = self.client()
        self.assertEqual(c.get("/api/config/r1").status_code, 200)
        self.assertEqual(self.view.calls, ["r1"])
        self.assertEqual(c.get("/api/config/khong-co").status_code, 404)
        self.assertEqual(self.view.calls, ["r1"])

    def test_viewer_may_read_but_anonymous_may_not(self):
        c = self.client(auth_enabled=True)
        self.assertEqual(c.get("/api/config/r1").status_code, 401)
        c.post("/api/login", json={"username": "chi", "password": "mat-khau-dai-1"})
        self.assertEqual(c.get("/api/config/r1").status_code, 200)

    def test_post_is_not_allowed(self):
        c = self.client()
        self.assertIn(c.post("/api/config/r1", json={}, headers={"X-NetAuto-Token": "t"}).status_code, (404, 405))


if __name__ == "__main__":
    unittest.main()
