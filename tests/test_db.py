import unittest

from netauto import db


def snap(at, reach=2):
    devices = [{"node": f"n{i}", "reachable": i < reach, "errors": [] if i < reach else ["x"]}
               for i in range(2)]
    return {"taken_at": at, "devices": devices}


class TestDb(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def test_empty(self):
        self.assertIsNone(db.latest_snapshot(self.conn))
        self.assertEqual(db.list_snapshots(self.conn), [])

    def test_save_and_load(self):
        sid = db.save_snapshot(self.conn, snap("t1"))
        self.assertEqual(sid, 1)
        self.assertEqual(db.latest_snapshot(self.conn)["taken_at"], "t1")

    def test_summary_columns(self):
        db.save_snapshot(self.conn, snap("t1", reach=1))
        _, _, devices, reachable, errors = db.list_snapshots(self.conn)[0]
        self.assertEqual((devices, reachable, errors), (2, 1, 1))

    def test_latest_is_newest(self):
        db.save_snapshot(self.conn, snap("t1"))
        db.save_snapshot(self.conn, snap("t2"))
        self.assertEqual(db.latest_snapshot(self.conn)["taken_at"], "t2")

    def test_events(self):
        sid = db.save_snapshot(self.conn, snap("t1"))
        db.save_events(self.conn, sid, "t1", "r1",
                       {"mất": ["interface eth1.10"], "mới": ["route x"]})
        events = db.list_events(self.conn)
        self.assertEqual(len(events), 2)
        self.assertEqual({e[2] for e in events}, {"mất", "mới"})


if __name__ == "__main__":
    unittest.main()
