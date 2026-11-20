import sqlite3
import unittest

from netauto import injections as inj

INJ = {"id": 1, "node": "r1", "expect_check": "router.subinterface",
       "injected_at": "2026-01-01T10:00:03.500+00:00", "before_snapshot": 10}


def row(i, node="r1", check="router.subinterface", snap=11, opened="2026-01-01T10:00:09+00:00"):
    return {"id": i, "node": node, "check_code": check, "opened_snapshot": snap,
            "opened_at": opened}


class TestFindDetection(unittest.TestCase):
    def test_matches_node_check_and_later_snapshot(self):
        self.assertEqual(inj.find_detection(INJ, [row(5)])["id"], 5)

    def test_other_node_does_not_match(self):
        self.assertIsNone(inj.find_detection(INJ, [row(5, node="r2")]))

    def test_other_check_does_not_match(self):
        self.assertIsNone(inj.find_detection(INJ, [row(5, check="router.route")]))

    def test_incident_opened_before_injection_does_not_match(self):
        self.assertIsNone(inj.find_detection(INJ, [row(5, snap=10)]))

    def test_earliest_wins(self):
        self.assertEqual(inj.find_detection(INJ, [row(7, snap=13), row(6, snap=11)])["id"], 6)


class TestSeconds(unittest.TestCase):
    def test_normal(self):
        self.assertAlmostEqual(inj.detection_seconds(INJ, row(5)), 5.5)

    def test_never_negative(self):
        early = row(5, opened="2026-01-01T10:00:03+00:00")   # làm tròn xuống, sớm hơn lúc gây lỗi
        self.assertEqual(inj.detection_seconds(INJ, early), 0.0)


class TestSummarize(unittest.TestCase):
    def test_stats(self):
        rows = [{"status": "detected", "seconds": 4.0, "observed_seconds": 1.0},
                {"status": "detected", "seconds": 8.0, "observed_seconds": 3.0},
                {"status": "missed", "seconds": None}, {"status": "invalid", "seconds": None}]
        s = inj.summarize(rows)
        self.assertEqual((s["runs"], s["detected"], s["missed"], s["invalid"]), (3, 2, 1, 1))
        self.assertAlmostEqual(s["rate"], 2 / 3)
        self.assertEqual((s["min"], s["max"], s["mean"], s["median"]), (4.0, 8.0, 6.0, 6.0))
        self.assertEqual((s["obs_mean"], s["obs_max"]), (2.0, 3.0))
    def test_fix_stats_only_count_fixed_runs(self):
        rows = [{"status": "detected", "seconds": 4.0, "fix_seconds": 20.0, "closed_at": "x"},
                {"status": "detected", "seconds": 5.0, "fix_seconds": 30.0, "closed_at": "x"},
                {"status": "detected", "seconds": 6.0, "fix_seconds": None, "closed_at": None,
                 "mode_remediate": True},
                {"status": "missed", "seconds": None, "fix_seconds": None, "closed_at": None}]
        s = inj.summarize(rows)
        self.assertEqual(s["fixed"], 2)
        self.assertEqual((s["fix_min"], s["fix_max"], s["fix_mean"], s["fix_median"]),
                         (20.0, 30.0, 25.0, 25.0))

    def test_no_fix_stats_in_detection_only_runs(self):
        s = inj.summarize([{"status": "detected", "seconds": 4.0, "fix_seconds": None,
                            "closed_at": None, "observed_seconds": 1.0}])
        self.assertNotIn("fix_mean", s)
        self.assertEqual(s["fixed"], 0)

    def test_empty(self):
        s = inj.summarize([])
        self.assertEqual(s["runs"], 0)
        self.assertIsNone(s["rate"])


class TestDb(unittest.TestCase):
    def test_lifecycle(self):
        conn = sqlite3.connect(":memory:")
        inj.ensure_schema(conn)
        f = inj.FAULTS["r1-subif-down"]
        i = inj.start_injection(conn, f, "2026-01-01T10:00:03.500+00:00", 10)
        # snapshot thấy lỗi lúc :09, nhưng cảnh báo có mặt trong DB lúc :14.5 (sau khi ping xong)
        inj.mark_status(conn, i, "detected", row(5), seen_at="2026-01-01T10:00:14.500+00:00")
        inj.mark_restored(conn, i, "2026-01-01T10:01:00+00:00")
        r = inj.list_injections(conn)[0]
        self.assertEqual((r["status"], r["incident_id"], r["fault"]), ("detected", 5, f.name))
        self.assertAlmostEqual(r["observed_seconds"], 5.5)
        self.assertAlmostEqual(r["seconds"], 11.0)

    def test_old_table_gets_seen_at_column(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE injections (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "fault TEXT NOT NULL, node TEXT NOT NULL, expect_check TEXT NOT NULL, "
                     "injected_at TEXT NOT NULL, before_snapshot INTEGER NOT NULL, restored_at TEXT, "
                     "status TEXT NOT NULL DEFAULT 'running', incident_id INTEGER, detected_at TEXT)")
        inj.ensure_schema(conn)
        i = inj.start_injection(conn, inj.FAULTS["r1-subif-down"], "2026-01-01T10:00:00+00:00", 1)
        inj.mark_status(conn, i, "detected", row(5), seen_at="2026-01-01T10:00:08+00:00")
        self.assertAlmostEqual(inj.list_injections(conn)[0]["seconds"], 8.0)

    def test_fix_seconds_from_closed_at(self):
        conn = sqlite3.connect(":memory:")
        inj.ensure_schema(conn)
        f = inj.FAULTS["r1-subif-down"]
        i = inj.start_injection(conn, f, "2026-01-01T10:00:03.500+00:00", 10)
        inj.mark_status(conn, i, "detected", row(5), seen_at="2026-01-01T10:00:08+00:00")
        inj.mark_closed(conn, i, "2026-01-01T10:00:23.500+00:00")
        r = inj.list_injections(conn)[0]
        self.assertAlmostEqual(r["fix_seconds"], 20.0)

    def test_open_row_has_no_fix_seconds(self):
        conn = sqlite3.connect(":memory:")
        inj.ensure_schema(conn)
        inj.start_injection(conn, inj.FAULTS["r1-subif-down"], "2026-01-01T10:00:00+00:00", 1)
        self.assertIsNone(inj.list_injections(conn)[0]["fix_seconds"])

    def test_old_table_gets_closed_at_column(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE injections (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "fault TEXT NOT NULL, node TEXT NOT NULL, expect_check TEXT NOT NULL, "
                     "injected_at TEXT NOT NULL, before_snapshot INTEGER NOT NULL, restored_at TEXT, "
                     "status TEXT NOT NULL DEFAULT 'running', incident_id INTEGER, detected_at TEXT, "
                     "seen_at TEXT)")
        inj.ensure_schema(conn)
        i = inj.start_injection(conn, inj.FAULTS["r1-subif-down"], "2026-01-01T10:00:00+00:00", 1)
        inj.mark_closed(conn, i, "2026-01-01T10:00:30+00:00")
        self.assertAlmostEqual(inj.list_injections(conn)[0]["fix_seconds"], 30.0)

    def test_every_fault_has_matching_inject_and_restore(self):
        for f in inj.FAULTS.values():
            self.assertEqual(f.inject[:3], f.restore[:3])        # cùng "ip link set"
            self.assertEqual(f.inject[3], f.restore[3])          # cùng cổng
            self.assertEqual((f.inject[4], f.restore[4]), ("down", "up"))


if __name__ == "__main__":
    unittest.main()

class TestPurge(unittest.TestCase):
    """26A: xóa các dòng đo cũ (đo thử, đo lỗi) trước khi đo chính thức."""

    def make(self):
        conn = sqlite3.connect(":memory:")
        inj.ensure_schema(conn)
        fault = inj.FAULTS["r1-subif-down"]
        ids = [inj.start_injection(conn, fault, "2026-01-01T10:00:0%d.000+00:00" % i, 1)
               for i in range(3)]
        inj.mark_status(conn, ids[0], "detected")
        inj.mark_status(conn, ids[1], "missed")
        return conn, ids

    def test_purge_removes_finished_rows_and_keeps_running(self):
        conn, ids = self.make()
        n = inj.purge(conn)
        self.assertEqual(n, 2)
        left = [r["id"] for r in inj.list_injections(conn, 50)]
        self.assertEqual(left, [ids[2]])                 # dòng "running" được giữ lại

    def test_purge_before_id_only_removes_older(self):
        conn, ids = self.make()
        self.assertEqual(inj.purge(conn, before_id=ids[1]), 1)
        left = sorted(r["id"] for r in inj.list_injections(conn, 50))
        self.assertEqual(left, [ids[1], ids[2]])

    def test_purge_on_empty_table(self):
        conn = sqlite3.connect(":memory:")
        inj.ensure_schema(conn)
        self.assertEqual(inj.purge(conn), 0)
