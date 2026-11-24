"""31A: đường /api/events. Chỉ chạy khi đã cài fastapi và httpx."""
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
class TestEventsApi(unittest.TestCase):
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

    def test_answers_with_events_and_summary(self):
        r = self.client.get("/api/events")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["events"], [])
        self.assertIn("summary", body)

    def test_filters_and_limit_do_not_fail(self):
        self.assertEqual(self.client.get("/api/events?only_open=true&limit=99999").status_code, 200)
        self.assertEqual(self.client.get("/api/events?limit=0").status_code, 200)

    def test_read_only(self):
        self.assertEqual(self.client.post("/api/events").status_code, 405)
