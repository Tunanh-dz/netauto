"""30A: đường /api/history. Chỉ chạy khi đã cài fastapi và httpx."""
import os
import tempfile
import unittest

try:
    from fastapi.testclient import TestClient
    HAVE = True
except ImportError:
    HAVE = False

from netauto import db, webdata


@unittest.skipUnless(HAVE, "cần: pip install fastapi uvicorn httpx")
class TestHistoryApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self.tmp.name, "t.db")
        conn = db.connect(path)
        webdata.ensure_all(conn)
        conn.close()
        from netauto import web
        self.client = TestClient(web.create_app(path))

    def tearDown(self):
        self.tmp.cleanup()

    def test_answers_with_every_part_of_the_history(self):
        r = self.client.get("/api/history?range=6h")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for key in ("range", "from", "to", "health", "incidents", "buckets", "flows", "fix_outcomes"):
            self.assertIn(key, body)
        self.assertEqual(body["range"], "6h")

    def test_default_and_unknown_range_do_not_fail(self):
        self.assertEqual(self.client.get("/api/history").json()["range"], "1h")
        self.assertEqual(self.client.get("/api/history?range=zzz").status_code, 200)

    def test_read_only(self):
        self.assertEqual(self.client.post("/api/history").status_code, 405)
