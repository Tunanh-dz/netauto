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
        rows = [{"status": "detected", "seconds": 4.0}, {"status": "detected", "seconds": 8.0},
                {"status": "missed", "seconds": None}, {"status": "invalid", "seconds": None}]
        s = inj.summarize(rows)
        self.assertEqual((s["runs"], s["detected"], s["missed"], s["invalid"]), (3, 2, 1, 1))
        self.assertAlmostEqual(s["rate"], 2 / 3)
        self.assertEqual((s["min"], s["max"], s["mean"], s["median"]), (4.0, 8.0, 6.0, 6.0))

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
        inj.mark_status(conn, i, "detected", row(5))
        inj.mark_restored(conn, i, "2026-01-01T10:01:00+00:00")
        r = inj.list_injections(conn)[0]
        self.assertEqual((r["status"], r["incident_id"], r["fault"]), ("detected", 5, f.name))
        self.assertAlmostEqual(r["seconds"], 5.5)

    def test_every_fault_has_matching_inject_and_restore(self):
        for f in inj.FAULTS.values():
            self.assertEqual(f.inject[:3], f.restore[:3])        # cùng "ip link set"
            self.assertEqual(f.inject[3], f.restore[3])          # cùng cổng
            self.assertEqual((f.inject[4], f.restore[4]), ("down", "up"))


if __name__ == "__main__":
    unittest.main()