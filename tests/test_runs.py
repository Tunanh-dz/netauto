import sqlite3
import unittest

from netauto import runs


class TestGap(unittest.TestCase):
    def test_no_previous_snapshot_no_gap(self):
        self.assertIsNone(runs.detect_gap(None, "2026-11-10T15:00:00+07:00", 5))

    def test_short_pause_is_not_a_gap(self):
        self.assertIsNone(runs.detect_gap("2026-11-10T15:00:00+07:00",
                                          "2026-11-10T15:00:30+07:00", 5))

    def test_threshold_scales_with_interval(self):
        self.assertEqual(runs.gap_threshold(5), 30)
        self.assertEqual(runs.gap_threshold(60), 180)

    def test_long_pause_is_a_gap(self):
        g = runs.detect_gap("2026-11-05T15:06:33+07:00", "2026-11-10T15:35:16+07:00", 5)
        self.assertEqual(g["from"], "2026-11-05T15:06:33+07:00")
        self.assertGreater(g["seconds"], 5 * 86400)

    def test_fmt_span(self):
        self.assertEqual(runs.fmt_span(45), "45 giây")
        self.assertEqual(runs.fmt_span(125), "2 phút 5 giây")
        self.assertEqual(runs.fmt_span(3 * 3600 + 120), "3 giờ 2 phút")
        self.assertEqual(runs.fmt_span(5 * 86400 + 3600), "5 ngày 1 giờ")


class TestRuns(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        runs.ensure_schema(self.conn)

    def test_first_run_without_history(self):
        run_id, gap = runs.start_run(self.conn, 5, None)
        self.assertIsNone(gap)
        runs.stop_run(self.conn, run_id, True)
        row = self.conn.execute("SELECT stopped_cleanly FROM monitor_runs").fetchone()
        self.assertEqual(row[0], 1)

    def test_gap_is_recorded_and_remembers_how_previous_run_ended(self):
        first, _ = runs.start_run(self.conn, 5, None)           # chưa dừng: coi như bị ngắt
        self.conn.execute("UPDATE monitor_runs SET stopped_cleanly = 0 WHERE id = ?", (first,))
        _, gap = runs.start_run(self.conn, 5, "2020-01-01T00:00:00+00:00")
        self.assertIs(gap["previous_clean"], False)
        row = self.conn.execute(
            "SELECT gap_from, gap_to FROM monitor_runs ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row[0], "2020-01-01T00:00:00+00:00")
        self.assertIsNotNone(row[1])
        self.assertIn("KHOẢNG TRỐNG", runs.describe_gap(gap))

    def test_describe_gap_is_human_readable(self):
        gap = {"from": "2026-11-12T10:11:17+07:00", "to": "2026-11-12T10:18:49+07:00",
               "seconds": 452, "previous_clean": True}
        text = runs.describe_gap(gap)
        self.assertIn("10:11:17 → 10:18:49 ngày 12/11/2026", text)
        self.assertIn("7 phút 32 giây", text)
        self.assertNotIn("+07:00", text)

    def test_no_gap_when_last_snapshot_is_recent(self):
        _, gap = runs.start_run(self.conn, 5, runs.now())
        self.assertIsNone(gap)


if __name__ == "__main__":
    unittest.main()