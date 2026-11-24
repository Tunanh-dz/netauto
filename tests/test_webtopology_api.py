"""29A: đường /api/topology. Chỉ chạy khi đã cài fastapi và httpx."""
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
class TestTopologyApi(unittest.TestCase):
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

    def test_topology_answers_with_nodes_links_flows(self):
        r = self.client.get("/api/topology")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for key in ("nodes", "links", "flows", "links_source", "generated_at"):
            self.assertIn(key, body)
        self.assertEqual(len(body["nodes"]), 8)

    def test_topology_is_read_only(self):
        self.assertEqual(self.client.post("/api/topology").status_code, 405)
